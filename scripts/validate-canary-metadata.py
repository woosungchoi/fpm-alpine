#!/usr/bin/env python3
"""Validate strict GHCR-only publisher canary metadata schemas."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPOSITORY = "ghcr.io/woosungchoi/fpm-alpine"
EXPECTED_PLATFORMS = ["linux/amd64", "linux/arm64"]
V2_KEYS = {
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
V3_KEYS = V2_KEYS | {"publisher_mode", "dockerhub_source_digest"}


def main() -> int:
    if len(sys.argv) not in {7, 8}:
        raise SystemExit(
            f"usage: {sys.argv[0]} <evidence-dir> <source-sha> <php-minor> <php-patch> <run-id> <run-attempt> [automatic|backfill-ghcr]"
        )
    evidence_dir, source_sha, php_minor, php_patch, run_id_text, run_attempt_text = sys.argv[1:7]
    publisher_mode = sys.argv[7] if len(sys.argv) == 8 else None
    if publisher_mode not in {None, "automatic", "backfill-ghcr"}:
        raise SystemExit("invalid expected publisher mode")
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
    expected_keys = V3_KEYS if publisher_mode is not None else V2_KEYS
    expected_schema = 3 if publisher_mode is not None else 2
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise SystemExit(f"canary metadata keys do not match strict schema v{expected_schema}")
    expected = {
        "schema_version": expected_schema,
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
    if publisher_mode is not None:
        observed_mode = payload.get("publisher_mode")
        if type(observed_mode) is not str or observed_mode != publisher_mode:
            raise SystemExit("canary metadata mismatch for publisher_mode")
        dockerhub_source = payload.get("dockerhub_source_digest")
        if publisher_mode == "automatic":
            if dockerhub_source is not None:
                raise SystemExit("automatic canary must not carry a Docker Hub source digest")
        elif type(dockerhub_source) is not str or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", dockerhub_source
        ):
            raise SystemExit("backfill canary requires an exact Docker Hub source digest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
