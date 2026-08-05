#!/usr/bin/env python3
"""Require durable legacy cutover evidence plus live Docker Hub autobuild disablement."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

COMMIT = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MINIMUM_SETTLED_SECONDS = 24 * 60 * 60


def validate_state(
    raw: bytes,
    expected_hash: str,
    metadata: Any,
    repository: str,
    now: dt.datetime,
) -> dict[str, Any]:
    if not DIGEST.fullmatch(expected_hash):
        raise SystemExit("invalid legacy cutover evidence hash")
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise SystemExit("legacy cutover evidence hash mismatch")
    if not REPOSITORY.fullmatch(repository):
        raise SystemExit("invalid Docker Hub repository")
    if now.tzinfo is None:
        raise SystemExit("current timestamp must be timezone-aware")

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit("invalid legacy cutover evidence JSON") from error
    if type(payload) is not dict:
        raise SystemExit("invalid legacy cutover evidence object")
    if type(payload.get("schemaVersion")) is not int or payload.get("schemaVersion") != 1:
        raise SystemExit("invalid legacy cutover evidence schema")
    if not COMMIT.fullmatch(str(payload.get("source_sha", ""))):
        raise SystemExit("invalid legacy cutover source identity")

    captured_value = payload.get("captured_at")
    if type(captured_value) is not str:
        raise SystemExit("legacy publisher cutover is not settled")
    try:
        captured = dt.datetime.fromisoformat(captured_value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit("legacy publisher cutover is not settled") from error
    if captured.tzinfo is None:
        raise SystemExit("legacy publisher cutover is not settled")
    settled_seconds = int(
        (now.astimezone(dt.timezone.utc) - captured.astimezone(dt.timezone.utc)).total_seconds()
    )
    if settled_seconds < MINIMUM_SETTLED_SECONDS:
        raise SystemExit("legacy publisher cutover is not settled")

    dockerhub = payload.get("dockerhub")
    github = payload.get("github")
    if type(dockerhub) is not dict or type(github) is not dict:
        raise SystemExit("historical legacy publisher state is not quiescent")
    in_flight = dockerhub.get("in_flight_builds")
    if (
        dockerhub.get("build_rule_active") is not False
        or type(in_flight) is not int
        or in_flight != 0
    ):
        raise SystemExit("historical legacy publisher state is not quiescent")
    if github.get("legacy_webhook_present") is not False:
        raise SystemExit("historical legacy GitHub webhook was present")

    owner, name = repository.split("/", 1)
    if type(metadata) is not dict:
        raise SystemExit("invalid live Docker Hub metadata")
    status = metadata.get("status")
    if (
        metadata.get("namespace") != owner
        or metadata.get("name") != name
        or type(status) is not int
        or status != 1
    ):
        raise SystemExit("invalid live Docker Hub metadata")
    if metadata.get("is_automated") is not False:
        raise SystemExit("Docker Hub repository is still automated")

    return {
        "schemaVersion": 1,
        "repository": repository,
        "historicalSourceCommit": payload["source_sha"],
        "cutoverCapturedAt": captured.astimezone(dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "settledSeconds": settled_seconds,
        "isAutomated": False,
    }


def _fetch_metadata(repository: str) -> dict[str, Any]:
    url = f"https://hub.docker.com/v2/repositories/{repository}/"
    request = urllib.request.Request(url, headers={"User-Agent": "fpm-alpine-publisher/1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise SystemExit("failed to read live Docker Hub publisher state") from error
    if type(payload) is not dict:
        raise SystemExit("invalid live Docker Hub metadata")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-evidence-sha256", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()

    if os.environ.get("LEGACY_PUBLISHER_DISABLED") != "true":
        raise SystemExit("legacy publisher administrative gate is closed")
    try:
        raw = base64.b64decode(os.environ["LEGACY_EVIDENCE_B64"], validate=True)
    except (KeyError, ValueError) as error:
        raise SystemExit("invalid legacy cutover evidence encoding") from error

    result = validate_state(
        raw,
        args.expected_evidence_sha256,
        _fetch_metadata(args.repository),
        args.repository,
        dt.datetime.now(dt.timezone.utc),
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
