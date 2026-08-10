#!/usr/bin/env python3
import base64
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) not in {3, 4}:
        raise SystemExit(
            f"usage: {sys.argv[0]} <expected-source-sha> "
            "<expected-evidence-sha256> [evidence-dir]"
        )

    expected_source_sha, expected_hash = sys.argv[1:3]
    if len(expected_source_sha) != 40 or any(char not in "0123456789abcdef" for char in expected_source_sha):
        raise SystemExit("invalid expected source SHA")
    if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        raise SystemExit("invalid expected evidence SHA-256")

    if len(sys.argv) == 4:
        try:
            raw = (Path(sys.argv[3]) / "cutover-evidence.json").read_bytes()
        except OSError as error:
            raise SystemExit("invalid legacy cutover evidence file") from error
    else:
        try:
            raw = base64.b64decode(os.environ["LEGACY_EVIDENCE_B64"], validate=True)
        except (KeyError, ValueError) as error:
            raise SystemExit("invalid legacy cutover evidence encoding") from error

    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise SystemExit("legacy cutover evidence hash mismatch")

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit("invalid legacy cutover evidence JSON") from error

    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version != 2 or payload.get("source_sha") != expected_source_sha:
        raise SystemExit("legacy cutover evidence identity mismatch")

    captured_value = payload.get("captured_at")
    if type(captured_value) is not str:
        raise SystemExit("invalid legacy cutover evidence timestamp")
    try:
        captured = dt.datetime.fromisoformat(captured_value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit("invalid legacy cutover evidence timestamp") from error
    if captured.tzinfo is None:
        raise SystemExit("legacy cutover evidence timestamp must be timezone-aware")
    age = (dt.datetime.now(dt.timezone.utc) - captured.astimezone(dt.timezone.utc)).total_seconds()
    if age < -60 or age > 900:
        raise SystemExit("legacy cutover evidence is not within the 15-minute lease")

    dockerhub = payload.get("dockerhub") or {}
    github = payload.get("github") or {}
    in_flight_builds = dockerhub.get("in_flight_builds")
    queue_observed_value = dockerhub.get("queue_observed_at")
    if type(queue_observed_value) is not str:
        raise SystemExit("invalid Docker Hub queue observation timestamp")
    try:
        queue_observed = dt.datetime.fromisoformat(queue_observed_value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit("invalid Docker Hub queue observation timestamp") from error
    if queue_observed.tzinfo is None:
        raise SystemExit("Docker Hub queue observation timestamp must be timezone-aware")
    queue_age = (
        dt.datetime.now(dt.timezone.utc) - queue_observed.astimezone(dt.timezone.utc)
    ).total_seconds()
    if (
        dockerhub.get("build_rule_active") is not False
        or type(in_flight_builds) is not int
        or in_flight_builds != 0
        or dockerhub.get("public_is_automated") is not False
        or not isinstance(dockerhub.get("repository_last_updated"), str)
        or not dockerhub.get("repository_last_updated")
        or dockerhub.get("queue_evidence") != "dockerhub-ui-owner-observation"
        or queue_age < -60
        or queue_age > 900
    ):
        raise SystemExit("Docker Hub legacy publisher is not quiescent")
    expected_hooks = [{
        "id": 402842509,
        "name": "web",
        "active": True,
        "events": ["pull_request", "push"],
        "url_host": "api.snyk.io",
        "url_kind": "github-webhook-uuid",
    }]
    if (
        github.get("repository") != "woosungchoi/fpm-alpine"
        or github.get("legacy_webhook_present") is not False
        or github.get("active_hooks") != expected_hooks
    ):
        raise SystemExit("legacy GitHub webhook is still present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
