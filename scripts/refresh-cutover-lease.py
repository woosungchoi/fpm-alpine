#!/usr/bin/env python3
"""Capture and dispatch fresh owner-attested legacy publisher cutover evidence."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, NoReturn

REPOSITORY = "woosungchoi/fpm-alpine"
OWNER_LOGIN = "woosungchoi"
OWNER_ID = 5674610
DOCKERHUB_NAMESPACE = "woosungchoi"
DOCKERHUB_REPOSITORY = "fpm-alpine"
ALLOWED_HOOK = {
    "id": 402842509,
    "name": "web",
    "active": True,
    "events": ["pull_request", "push"],
    "url_host": "api.snyk.io",
    "url_kind": "github-webhook-uuid",
}
ACTIVE_STATUSES = {"queued", "in_progress", "requested", "waiting", "pending"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def _gh_json(endpoint: str) -> Any:
    completed = subprocess.run(
        ["gh", "api", endpoint],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _url_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "fpm-alpine-cutover/1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _current_main_sha() -> str:
    payload = _gh_json(f"repos/{REPOSITORY}/git/ref/heads/main")
    sha = payload.get("object", {}).get("sha")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
        fail("GitHub main ref did not return one exact commit SHA")
    return sha


def _owner_identity() -> None:
    payload = _gh_json("user")
    if payload.get("login") != OWNER_LOGIN or payload.get("id") != OWNER_ID:
        fail("cutover evidence must be captured by the pinned repository owner")


def _active_publisher_for(source_sha: str) -> bool:
    payload = _gh_json(
        f"repos/{REPOSITORY}/actions/runs?branch=main&per_page=100"
    )
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        fail("publisher workflow run response is malformed")
    allowed = {
        (".github/workflows/dependency-auto-publish.yml", "push"),
        (".github/workflows/dependency-publish-recovery.yml", "repository_dispatch"),
        (".github/workflows/dependency-publish-recovery.yml", "schedule"),
    }
    return any(
        isinstance(run, dict)
        and run.get("head_sha") == source_sha
        and run.get("status") in ACTIVE_STATUSES
        and (run.get("path"), run.get("event")) in allowed
        for run in runs
    )


def _validated_dockerhub() -> dict[str, Any]:
    payload = _url_json(
        "https://hub.docker.com/v2/repositories/"
        f"{DOCKERHUB_NAMESPACE}/{DOCKERHUB_REPOSITORY}/"
    )
    if payload.get("namespace") != DOCKERHUB_NAMESPACE:
        fail("Docker Hub namespace mismatch")
    if payload.get("name") != DOCKERHUB_REPOSITORY:
        fail("Docker Hub repository mismatch")
    if type(payload.get("status")) is not int or payload.get("status") != 1:
        fail("Docker Hub repository is not active")
    if payload.get("is_automated") is not False:
        fail("Docker Hub Automatic Builds are not proven disabled")
    last_updated = payload.get("last_updated")
    if not isinstance(last_updated, str) or not last_updated:
        fail("Docker Hub metadata is missing last_updated")
    return {
        "build_rule_active": False,
        "public_is_automated": False,
        "repository_last_updated": last_updated,
    }


def _validated_hooks() -> list[dict[str, Any]]:
    hooks = _gh_json(f"repos/{REPOSITORY}/hooks?per_page=100")
    if not isinstance(hooks, list):
        fail("GitHub hook response is malformed")
    normalized = []
    for hook in hooks:
        if not isinstance(hook, dict):
            fail("GitHub hook entry is malformed")
        raw_config = hook.get("config")
        if not isinstance(raw_config, dict):
            fail("GitHub hook config is malformed")
        config: dict[str, Any] = raw_config
        raw_url = config.get("url")
        if not isinstance(raw_url, str):
            fail("GitHub hook URL is malformed")
        parsed_url = urllib.parse.urlsplit(raw_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.netloc != "api.snyk.io"
            or not re.fullmatch(r"/webhook/github/[0-9a-f-]{36}", parsed_url.path)
            or parsed_url.query
            or parsed_url.fragment
            or config.get("content_type") != "json"
            or str(config.get("insecure_ssl")) != "0"
        ):
            fail("GitHub hook is not the pinned secure Snyk endpoint shape")
        normalized.append(
            {
                "id": hook.get("id"),
                "name": hook.get("name"),
                "active": hook.get("active"),
                "events": sorted(hook.get("events", [])),
                "url_host": parsed_url.hostname,
                "url_kind": "github-webhook-uuid",
            }
        )
    expected = dict(ALLOWED_HOOK)
    expected["events"] = sorted(expected["events"])
    if normalized != [expected]:
        fail("GitHub hook set is not the exact nonpublisher allowlist")
    return normalized


def capture(
    source_sha: str,
    *,
    in_flight_builds: int,
    queue_evidence: str,
) -> bytes:
    if not SHA_RE.fullmatch(source_sha):
        fail("source SHA must be 40 lowercase hex characters")
    _owner_identity()
    if _current_main_sha() != source_sha:
        fail("source SHA is not the current default-branch commit")
    if type(in_flight_builds) is not int or in_flight_builds != 0:
        fail("owner-attested Docker Hub in-flight build count must be exactly zero")
    if queue_evidence != "dockerhub-ui-owner-observation":
        fail("unsupported Docker Hub queue evidence")
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    dockerhub = _validated_dockerhub()
    dockerhub.update(
        {
            "in_flight_builds": in_flight_builds,
            "queue_evidence": queue_evidence,
            "queue_observed_at": now,
        }
    )
    evidence = {
        "schema_version": 2,
        "captured_at": now,
        "source_sha": source_sha,
        "dockerhub": dockerhub,
        "github": {
            "repository": REPOSITORY,
            "legacy_webhook_present": False,
            "active_hooks": _validated_hooks(),
        },
    }
    return (json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _dispatch(source_sha: str, raw: bytes) -> None:
    digest = hashlib.sha256(raw).hexdigest()
    encoded = base64.b64encode(raw).decode("ascii")
    subprocess.run(
        [
            "gh", "api", "--method", "POST", f"repos/{REPOSITORY}/dispatches",
            "-f", "event_type=legacy-cutover-lease",
            "-F", f"client_payload[source_sha]={source_sha}",
            "-F", f"client_payload[evidence_b64]={encoded}",
            "-F", f"client_payload[evidence_sha256]={digest}",
        ],
        check=True,
    )
    print(f"dispatched exact-source cutover lease: source={source_sha} sha256={digest}")


def _write_no_clobber(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--capture-only", type=Path)
    action.add_argument("--dispatch", action="store_true")
    parser.add_argument("--source-sha")
    parser.add_argument("--if-publisher-active", action="store_true")
    parser.add_argument("--in-flight-builds", type=int, required=True)
    parser.add_argument(
        "--queue-evidence",
        choices=("dockerhub-ui-owner-observation",),
        required=True,
    )
    args = parser.parse_args()

    source_sha = args.source_sha or _current_main_sha()
    if args.if_publisher_active and not _active_publisher_for(source_sha):
        return 0
    raw = capture(
        source_sha,
        in_flight_builds=args.in_flight_builds,
        queue_evidence=args.queue_evidence,
    )
    if args.capture_only:
        _write_no_clobber(args.capture_only, raw)
        digest = hashlib.sha256(raw).hexdigest()
        print(f"captured cutover evidence: source={source_sha} sha256={digest}")
    else:
        _dispatch(source_sha, raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
