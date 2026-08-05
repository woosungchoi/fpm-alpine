#!/usr/bin/env python3
"""Validate bytes produced by an eligible dependency auto-promotion run."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

COMMIT = re.compile(r"^[0-9a-f]{40}$")
UPDATER_BRANCH = re.compile(
    r"^automation/(?:(?:base-(8\.[2-5]))|pecl-(imagick|redis|apcu))-[0-9a-f]{12}$"
)
UPDATER_BOT = re.compile(r"^[A-Za-z0-9_.-]+\[bot\]$")
ACTIVE_MINORS = ["8.2", "8.3", "8.4", "8.5"]
CANONICAL_REPOSITORY = "woosungchoi/fpm-alpine"
ELIGIBLE_CLASSES = {"base-same-minor", "pecl-patch"}


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _load_single(root: Path, name: str, label: str) -> dict[str, Any]:
    matches = sorted(path for path in root.rglob(name) if path.is_file())
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {label} evidence file, found {len(matches)}")
    try:
        payload = json.loads(matches[0].read_text())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid {label} evidence JSON") from error
    if type(payload) is not dict:
        raise SystemExit(f"invalid {label} evidence object")
    return payload


def _validate_minors(value: Any) -> list[str]:
    if type(value) is not list or not value:
        raise SystemExit("invalid affected minors")
    if any(type(item) is not str for item in value):
        raise SystemExit("invalid affected minors")
    selected = set(value)
    if len(selected) != len(value) or not selected.issubset(ACTIVE_MINORS):
        raise SystemExit("invalid affected minors")
    canonical = [minor for minor in ACTIVE_MINORS if minor in selected]
    if value != canonical:
        raise SystemExit("invalid affected minors")
    return canonical


def _validate_canary(value: Any, label: str) -> dict[str, int]:
    if type(value) is not dict or set(value) != {"runId", "runAttempt", "runNumber"}:
        raise SystemExit(f"invalid {label} canary identity")
    if not all(_positive_int(value.get(key)) for key in value):
        raise SystemExit(f"invalid {label} canary identity")
    return {
        "runId": value["runId"],
        "runAttempt": value["runAttempt"],
        "runNumber": value["runNumber"],
    }


def validate_evidence(
    evidence_dir: Path,
    source_sha: str,
    upstream_run_id: int,
    upstream_run_attempt: int,
) -> dict[str, Any]:
    if not COMMIT.fullmatch(source_sha):
        raise SystemExit("invalid source SHA")
    if not _positive_int(upstream_run_id) or not _positive_int(upstream_run_attempt):
        raise SystemExit("invalid upstream run identity")
    if not evidence_dir.is_dir():
        raise SystemExit("auto-production evidence directory does not exist")

    eligibility = _load_single(evidence_dir, "eligibility.json", "eligibility")
    merged = _load_single(evidence_dir, "merged-pr.json", "merged PR")
    pair = _load_single(evidence_dir, "canary-pair.json", "canary pair")

    if type(eligibility.get("schemaVersion")) is not int or eligibility.get("schemaVersion") != 1:
        raise SystemExit("invalid eligibility schema")
    if eligibility.get("sourceCommit") != source_sha or eligibility.get("eligible") is not True:
        raise SystemExit("eligibility is not bound to the authorized source")
    kind = eligibility.get("class")
    if type(kind) is not str or kind not in ELIGIBLE_CLASSES:
        raise SystemExit("dependency class is not eligible for auto-production")
    affected = _validate_minors(eligibility.get("affectedMinors"))
    if eligibility.get("blockedReasons") != []:
        raise SystemExit("eligible evidence contains blocked reasons")

    if type(merged.get("schemaVersion")) is not int or merged.get("schemaVersion") != 1:
        raise SystemExit("invalid merged PR evidence schema")
    if merged.get("sourceCommit") != source_sha:
        raise SystemExit("merged PR evidence source mismatch")
    if merged.get("baseRef") != "main":
        raise SystemExit("merged PR base is not main")
    if merged.get("baseRepository") != CANONICAL_REPOSITORY:
        raise SystemExit("merged PR base repository is not canonical")
    if not _positive_int(merged.get("pullRequest")) or not COMMIT.fullmatch(
        str(merged.get("pullRequestHeadSha", ""))
    ):
        raise SystemExit("invalid merged PR identity")
    author = merged.get("author")
    branch = merged.get("headRef")
    if type(author) is not str or author == "dependabot[bot]" or not UPDATER_BOT.fullmatch(author):
        raise SystemExit("invalid merged PR updater identity")
    if type(branch) is not str:
        raise SystemExit("invalid merged PR updater branch")
    branch_match = UPDATER_BRANCH.fullmatch(branch)
    if branch_match is None:
        raise SystemExit("invalid merged PR updater branch")
    base_minor, pecl_name = branch_match.groups()
    if kind == "base-same-minor" and (base_minor is None or affected != [base_minor]):
        raise SystemExit("merged PR branch and affected minors disagree")
    if kind == "pecl-patch" and (pecl_name is None or affected != ACTIVE_MINORS):
        raise SystemExit("merged PR branch and affected minors disagree")

    if type(pair.get("schemaVersion")) is not int or pair.get("schemaVersion") != 1:
        raise SystemExit("invalid canary pair schema")
    if pair.get("sourceCommit") != source_sha:
        raise SystemExit("canary pair source mismatch")
    pair_affected = _validate_minors(pair.get("affectedMinors"))
    if pair_affected != affected:
        raise SystemExit("canary pair affected minors mismatch")
    if pair.get("productionAuthorized") is not True:
        raise SystemExit("canary pair is not authorized for production")

    first = _validate_canary(pair.get("firstCanary"), "first")
    second = _validate_canary(pair.get("secondCanary"), "second")
    if first["runId"] == second["runId"]:
        raise SystemExit("canary runs must be distinct")
    if second["runNumber"] != first["runNumber"] + 1:
        raise SystemExit("canary runs are not consecutive")

    return {
        "schemaVersion": 1,
        "sourceCommit": source_sha,
        "upstreamRunId": upstream_run_id,
        "upstreamRunAttempt": upstream_run_attempt,
        "dependencyClass": kind,
        "pullRequest": merged["pullRequest"],
        "pullRequestHeadSha": merged["pullRequestHeadSha"],
        "affectedMinors": affected,
        "priorCanary": {
            "runId": first["runId"],
            "runAttempt": first["runAttempt"],
        },
        "currentCanary": {
            "runId": second["runId"],
            "runAttempt": second["runAttempt"],
        },
        "productionAuthorized": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--upstream-run-id", required=True, type=int)
    parser.add_argument("--upstream-run-attempt", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    result = validate_evidence(
        args.evidence_dir,
        args.source_sha,
        args.upstream_run_id,
        args.upstream_run_attempt,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        "auto_production_evidence=verified "
        f"source={result['sourceCommit']} affected={','.join(result['affectedMinors'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
