#!/usr/bin/env python3
"""Read-only, fail-closed gate for all checks on an exact dependency PR head."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time


class CheckError(RuntimeError):
    pass


def gh_json(*args: str):
    result = subprocess.run(
        ["gh", *args], check=True, text=True, capture_output=True, timeout=60,
    )
    return json.loads(result.stdout)


def validate_pr(row: dict, repository: str, head_sha: str) -> str:
    if not isinstance(row, dict):
        raise CheckError("invalid PR metadata")
    if row.get("state") != "open" or row.get("draft") is not False:
        raise CheckError("PR must be open and not draft")
    head, base = row.get("head") or {}, row.get("base") or {}
    if (head.get("sha") != head_sha or base.get("ref") != "main"
            or (head.get("repo") or {}).get("full_name") != repository
            or (base.get("repo") or {}).get("full_name") != repository):
        raise CheckError("PR head, base branch, or repository changed")
    base_sha = base.get("sha", "")
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise CheckError("invalid PR base SHA")
    return base_sha


def check_states(checks: list) -> list[str]:
    if not isinstance(checks, list):
        raise CheckError("invalid check list")
    pending = []
    names = set()
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("name"), str):
            raise CheckError("invalid check metadata")
        name, state = check["name"], check.get("state")
        names.add(name)
        if state in {"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "EXPECTED"}:
            pending.append(name)
        elif state != "SUCCESS":
            # gh --json exits zero even for failed checks; never trust exit alone.
            # Unlike GitHub's native gate, neutral/skipped are not successes here.
            raise CheckError(f"check not successful: {name} ({state})")
    if "docker-smoke" not in names:
        pending.append("missing docker-smoke")
    return pending


def latest_runs(pages: list, head_sha: str) -> list[dict]:
    if not isinstance(pages, list) or not pages:
        raise CheckError("invalid workflow pages")
    latest = {}
    seen = set()
    expected = pages[0].get("total_count") if isinstance(pages[0], dict) else None
    if type(expected) is not int or expected < 0:
        raise CheckError("invalid workflow count")
    for page in pages:
        if (not isinstance(page, dict) or not isinstance(page.get("workflow_runs"), list)
                or page.get("total_count") != expected):
            raise CheckError("invalid workflow list")
        for run in page["workflow_runs"]:
            if (not isinstance(run, dict) or run.get("head_sha") != head_sha
                    or type(run.get("id")) is not int
                    or type(run.get("workflow_id")) is not int
                    or not isinstance(run.get("event"), str)):
                raise CheckError("invalid workflow identity")
            seen.add(run["id"])
            key = (run["workflow_id"], run["event"])
            if key not in latest or run["id"] > latest[key]["id"]:
                latest[key] = run
    if len(seen) != expected:
        # The API caps filtered searches at 1,000; never silently omit checks.
        raise CheckError("workflow pagination incomplete or changed during query")
    return sorted(latest.values(), key=lambda run: run["id"])


def inspect(repository: str, pr: int, head_sha: str) -> tuple[list[str], str]:
    endpoint = f"repos/{repository}/pulls/{pr}"
    base_sha = validate_pr(gh_json("api", endpoint), repository, head_sha)
    # gh pr checks paginates the PR rollup, including CheckRun + StatusContext.
    # Do not use --required: optional CI must also finish successfully.
    checks = gh_json("pr", "checks", str(pr), "--repo", repository,
                     "--json", "name,state,link")
    pending = check_states(checks)
    # Queued workflows can exist before any check jobs are created.
    runs = latest_runs(gh_json(
        "api", "--paginate", "--slurp",
        f"repos/{repository}/actions/runs?head_sha={head_sha}&per_page=100",
    ), head_sha)
    if not any(run.get("path") == ".github/workflows/smoke-test.yml"
               and run["event"] == "pull_request" for run in runs):
        pending.append("missing pull-request smoke workflow")
    for run in runs:
        status, conclusion = run.get("status"), run.get("conclusion")
        name = f"workflow {run['id']} ({run.get('name', run['workflow_id'])})"
        if status in {"queued", "in_progress", "waiting", "requested", "pending"}:
            pending.append(name)
        elif status != "completed" or conclusion != "success":
            raise CheckError(f"workflow not successful: {name} ({status}/{conclusion})")
    if validate_pr(gh_json("api", endpoint), repository, head_sha) != base_sha:
        raise CheckError("PR base changed while checking")
    fingerprint = json.dumps({
        "base": base_sha,
        "checks": sorted(checks, key=lambda check: (check["name"], check.get("link", ""))),
        "runs": [(run["id"], run.get("run_attempt"), run.get("status"), run.get("conclusion"))
                 for run in runs],
    }, sort_keys=True)
    return pending, fingerprint


def wait(repository: str, pr: int, head_sha: str, timeout: int, interval: int,
         once: bool = False) -> None:
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        pending, fingerprint = inspect(repository, pr, head_sha)
        if not pending and (once or previous == fingerprint):
            print(f"all_pr_checks=PASS pr={pr} head_sha={head_sha}", flush=True)
            return
        if once or time.monotonic() >= deadline:
            raise CheckError("checks incomplete or unstable: " + ", ".join(pending))
        previous = fingerprint if not pending else None
        message = ", ".join(pending) if pending else "confirming stable all-success snapshot"
        print(f"all_pr_checks=WAIT pr={pr} {message}", flush=True)
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository)
            or not re.fullmatch(r"[0-9a-f]{40}", args.head_sha) or args.pr < 1
            or args.timeout < 0 or args.interval < 1):
        parser.error("invalid repository, PR, SHA, or timing")
    try:
        wait(args.repository, args.pr, args.head_sha, args.timeout, args.interval, args.once)
    except (CheckError, subprocess.SubprocessError, ValueError) as exc:
        print(f"all_pr_checks=BLOCKED {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
