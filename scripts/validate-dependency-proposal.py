#!/usr/bin/env python3
"""Validate the immutable proposal that authorized one dependency PR."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

LOWER_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
CANDIDATE_KEY = re.compile(r"^(?:base-8\.[2-5]|pecl-(?:imagick|redis|apcu))$")


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def exact_int(value: object) -> bool:
    return type(value) is int and value > 0


def validate_proposal(
    *,
    raw: bytes,
    expected_hash: str,
    source_sha: str,
    run_id: int,
    run_attempt: int,
    candidate_key: str,
    head_ref: str,
    author_login: str,
    author_id: int,
    head_versions: bytes,
) -> dict[str, Any]:
    if not LOWER_HEX_64.fullmatch(expected_hash):
        raise SystemExit("invalid proposal SHA-256")
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise SystemExit("dependency proposal hash mismatch")
    if not LOWER_HEX_40.fullmatch(source_sha):
        raise SystemExit("invalid proposal source SHA")
    if not exact_int(run_id) or not exact_int(run_attempt):
        raise SystemExit("invalid proposal run identity")
    if not CANDIDATE_KEY.fullmatch(candidate_key):
        raise SystemExit("invalid proposal candidate key")

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit("invalid dependency proposal JSON") from error
    if type(payload) is not dict or type(payload.get("schemaVersion")) is not int:
        raise SystemExit("invalid dependency proposal schema")
    if payload["schemaVersion"] != 1:
        raise SystemExit("invalid dependency proposal schema")
    if (
        payload.get("sourceCommit") != source_sha
        or payload.get("runId") != run_id
        or payload.get("runAttempt") != run_attempt
        or payload.get("candidateKey") != candidate_key
    ):
        raise SystemExit("dependency proposal identity mismatch")

    candidate = payload.get("candidate")
    candidate_digest = payload.get("candidateSha256")
    if (
        type(candidate) is not dict
        or not isinstance(candidate_digest, str)
        or not LOWER_HEX_64.fullmatch(candidate_digest)
    ):
        raise SystemExit("invalid dependency candidate payload")
    if canonical_hash(candidate) != candidate_digest:
        raise SystemExit("dependency candidate hash mismatch")
    if candidate.get("key") != candidate_key or candidate.get("eligible") is not True:
        raise SystemExit("dependency candidate is not the eligible proposed row")

    expected_branch = f"automation/{candidate_key}-{candidate_digest[:12]}"
    if head_ref != expected_branch:
        raise SystemExit("dependency proposal branch does not match canonical candidate")

    updater_app_id = payload.get("updaterAppId")
    updater_user = payload.get("updaterUser")
    if not exact_int(updater_app_id) or type(updater_user) is not dict:
        raise SystemExit("invalid updater App identity")
    if (
        not exact_int(updater_user.get("id"))
        or updater_user.get("id") != author_id
        or updater_user.get("login") != author_login
        or updater_user.get("type") != "Bot"
        or not isinstance(author_login, str)
        or not author_login.endswith("[bot]")
    ):
        raise SystemExit("updater App identity does not match PR author")

    head_digest = payload.get("headVersionsSha256")
    if not LOWER_HEX_64.fullmatch(str(head_digest)):
        raise SystemExit("invalid head versions SHA-256")
    if hashlib.sha256(head_versions).hexdigest() != head_digest:
        raise SystemExit("head versions do not match dependency proposal")

    return {
        "schemaVersion": 1,
        "sourceCommit": source_sha,
        "candidates": [candidate],
        "warnings": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposal", required=True, type=Path)
    parser.add_argument("--expected-hash", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--candidate-key", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--author-login", required=True)
    parser.add_argument("--author-id", required=True, type=int)
    parser.add_argument("--head-versions", required=True, type=Path)
    parser.add_argument("--candidate-report", required=True, type=Path)
    args = parser.parse_args()

    report = validate_proposal(
        raw=args.proposal.read_bytes(),
        expected_hash=args.expected_hash,
        source_sha=args.source_sha,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        candidate_key=args.candidate_key,
        head_ref=args.head_ref,
        author_login=args.author_login,
        author_id=args.author_id,
        head_versions=args.head_versions.read_bytes(),
    )
    args.candidate_report.write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
