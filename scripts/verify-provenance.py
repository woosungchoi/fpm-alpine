#!/usr/bin/env python3
"""Verify that BuildKit provenance identifies the expected source revision."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PLATFORMS = ("linux/amd64", "linux/arm64")


BUILDKIT_ARGS_PATH = "SLSA.buildDefinition.externalParameters.request.root.request.args"


def buildkit_args(platform_payload: dict[str, object], platform: str) -> dict[str, object]:
    """Return the exact BuildKit SLSA nested build-request argument object."""
    slsa = platform_payload.get("SLSA")
    if not isinstance(slsa, dict):
        raise SystemExit(f"{platform} provenance is missing {BUILDKIT_ARGS_PATH}")
    build_definition = slsa.get("buildDefinition")
    if not isinstance(build_definition, dict):
        raise SystemExit(f"{platform} provenance is missing {BUILDKIT_ARGS_PATH}")
    external_parameters = build_definition.get("externalParameters")
    if not isinstance(external_parameters, dict):
        raise SystemExit(f"{platform} provenance is missing {BUILDKIT_ARGS_PATH}")
    request = external_parameters.get("request")
    if not isinstance(request, dict):
        raise SystemExit(f"{platform} provenance is missing {BUILDKIT_ARGS_PATH}")
    root = request.get("root")
    if not isinstance(root, dict):
        raise SystemExit(f"{platform} provenance is missing {BUILDKIT_ARGS_PATH}")
    root_request = root.get("request")
    if not isinstance(root_request, dict):
        raise SystemExit(f"{platform} provenance is missing {BUILDKIT_ARGS_PATH}")
    args = root_request.get("args")
    if not isinstance(args, dict):
        raise SystemExit(f"{platform} provenance {BUILDKIT_ARGS_PATH} must be an object")
    return args


def normalized_source(value: str) -> str:
    """Normalize common HTTPS and SSH Git source spellings for comparison."""
    normalized = value.strip()
    if normalized.startswith("git@") and ":" in normalized:
        host, path = normalized[4:].split(":", 1)
        normalized = f"{host}/{path}"
    normalized = re.sub(r"^[a-z]+://", "", normalized)
    return normalized.removesuffix(".git").rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("provenance", type=Path)
    parser.add_argument("expected_revision")
    parser.add_argument(
        "--expected-source",
        default="github.com/woosungchoi/fpm-alpine",
        help="repository identity required in every platform vcs:source field",
    )
    args = parser.parse_args()

    expected = args.expected_revision.lower()
    if not SHA_RE.fullmatch(expected):
        parser.error("expected_revision must be an exact 40-character lowercase commit SHA")

    try:
        payload = json.loads(args.provenance.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"failed to read provenance JSON: {error}") from error

    if not isinstance(payload, dict) or not payload:
        raise SystemExit("provenance payload is empty")

    for platform in PLATFORMS:
        platform_payload = payload.get(platform)
        if not isinstance(platform_payload, dict) or not platform_payload:
            raise SystemExit(f"provenance is missing platform payload: {platform}")

        provenance_args = buildkit_args(platform_payload, platform)
        revision = provenance_args.get("vcs:revision")
        if revision != expected:
            raise SystemExit(
                f"{platform} provenance vcs:revision must be exactly {expected}; got: {revision!r}"
            )

        source = provenance_args.get("vcs:source")
        expected_source = normalized_source(args.expected_source)
        if not isinstance(source, str) or normalized_source(source) != expected_source:
            raise SystemExit(
                f"{platform} provenance vcs:source does not identify {args.expected_source}; got: {source!r}"
            )

    print(f"platform provenance source/revision verified: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
