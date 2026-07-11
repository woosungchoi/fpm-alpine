#!/usr/bin/env python3
import base64
import datetime as dt
import hashlib
import json
import os
import sys


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <expected-source-sha> <expected-evidence-sha256>")

    expected_source_sha, expected_hash = sys.argv[1:]
    if len(expected_source_sha) != 40 or any(char not in "0123456789abcdef" for char in expected_source_sha):
        raise SystemExit("invalid expected source SHA")
    if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
        raise SystemExit("invalid expected evidence SHA-256")

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

    schema_version = payload.get("schemaVersion")
    if type(schema_version) is not int or schema_version != 1 or payload.get("source_sha") != expected_source_sha:
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
    if dockerhub.get("build_rule_active") is not False or type(in_flight_builds) is not int or in_flight_builds != 0:
        raise SystemExit("Docker Hub legacy publisher is not quiescent")
    if github.get("legacy_webhook_present") is not False:
        raise SystemExit("legacy GitHub webhook is still present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
