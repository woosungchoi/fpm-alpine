#!/usr/bin/env python3
"""Validate strict GHCR-only publisher canary metadata schema v2."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPOSITORY = "ghcr.io/woosungchoi/fpm-alpine"
EXPECTED_PLATFORMS = ["linux/amd64", "linux/arm64"]
EXPECTED_KEYS = {
    "schema_version",
    "channel",
    "source_sha",
    "php_minor",
    "php_patch",
    "run_id",
    "run_attempt",
    "canonical_registry",
    "canonical_repository",
    "canonical_ref",
    "ghcr_digest",
    "platforms",
}


def main() -> int:
    if len(sys.argv) != 7:
        raise SystemExit(
            f"usage: {sys.argv[0]} <evidence-dir> <source-sha> <php-minor> <php-patch> <run-id> <run-attempt>"
        )
    evidence_dir, source_sha, php_minor, php_patch, run_id_text, run_attempt_text = sys.argv[1:]
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise SystemExit("invalid expected source SHA")
    if not re.fullmatch(r"8\.[2-5]", php_minor):
        raise SystemExit("invalid expected PHP minor")
    if not re.fullmatch(rf"{re.escape(php_minor)}\.[0-9]+", php_patch):
        raise SystemExit("invalid expected PHP patch")
    if not run_id_text.isdigit() or int(run_id_text) < 1:
        raise SystemExit("invalid expected run ID")
    if not run_attempt_text.isdigit() or int(run_attempt_text) < 1:
        raise SystemExit("invalid expected run attempt")

    files = list(Path(evidence_dir).glob("**/canary-metadata.json"))
    if len(files) != 1:
        raise SystemExit(f"expected one canary metadata file, found {len(files)}")
    payload = json.loads(files[0].read_text())
    if not isinstance(payload, dict) or set(payload) != EXPECTED_KEYS:
        raise SystemExit("canary metadata keys do not match strict schema v2")
    expected = {
        "schema_version": 2,
        "channel": "canary",
        "source_sha": source_sha,
        "php_minor": php_minor,
        "php_patch": php_patch,
        "run_id": int(run_id_text),
        "run_attempt": int(run_attempt_text),
        "canonical_registry": "ghcr.io",
        "canonical_repository": REPOSITORY,
        "canonical_ref": f"{REPOSITORY}:canary-{php_minor}-{run_id_text}-{run_attempt_text}",
        "platforms": EXPECTED_PLATFORMS,
    }
    for key, value in expected.items():
        observed = payload.get(key)
        if type(observed) is not type(value) or observed != value:
            raise SystemExit(f"canary metadata mismatch for {key}")
    digest = payload.get("ghcr_digest")
    if type(digest) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise SystemExit("invalid canary metadata digest: ghcr_digest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
