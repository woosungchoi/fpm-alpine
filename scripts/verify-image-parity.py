#!/usr/bin/env python3
"""Compare two multi-platform image subjects by config and ordered layer digests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

SUBJECT_RE = re.compile(r"^[a-z0-9.-]+/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}$")
PLATFORMS = ("linux/amd64", "linux/arm64")


def raw(subject: str) -> dict:
    output = subprocess.check_output(
        ["docker", "buildx", "imagetools", "inspect", "--raw", subject], text=True
    )
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise RuntimeError(f"manifest is not a JSON object: {subject}")
    return payload


def platform_manifests(subject: str) -> dict[str, dict]:
    index = raw(subject)
    repository = subject.rsplit("@", 1)[0]
    descriptors: dict[str, str] = {}
    for item in index.get("manifests", []):
        platform = item.get("platform") or {}
        key = f"{platform.get('os', '')}/{platform.get('architecture', '')}"
        if key in PLATFORMS:
            if key in descriptors:
                raise RuntimeError(f"duplicate platform descriptor for {subject}: {key}")
            digest = item.get("digest")
            if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise RuntimeError(f"invalid platform digest for {subject}: {key}")
            descriptors[key] = digest
    missing = sorted(set(PLATFORMS) - set(descriptors))
    if missing:
        raise RuntimeError(f"subject is missing required platforms: {subject}: {', '.join(missing)}")
    return {platform: raw(f"{repository}@{digest}") for platform, digest in descriptors.items()}


def compare(left_subject: str, right_subject: str) -> dict:
    left = platform_manifests(left_subject)
    right = platform_manifests(right_subject)
    result = {"left": left_subject, "right": right_subject, "platforms": {}}
    for platform in PLATFORMS:
        left_manifest = left[platform]
        right_manifest = right[platform]
        left_config = (left_manifest.get("config") or {}).get("digest")
        right_config = (right_manifest.get("config") or {}).get("digest")
        left_layers = [item.get("digest") for item in left_manifest.get("layers", [])]
        right_layers = [item.get("digest") for item in right_manifest.get("layers", [])]
        if left_config != right_config or left_layers != right_layers:
            raise RuntimeError(f"platform config/layer parity failed: {platform}")
        result["platforms"][platform] = {"config": left_config, "layers": left_layers}
    result["status"] = "verified"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("left_subject")
    parser.add_argument("right_subject")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for subject in (args.left_subject, args.right_subject):
        if not SUBJECT_RE.fullmatch(subject):
            raise SystemExit(f"invalid digest-qualified subject: {subject}")
    report = compare(args.left_subject, args.right_subject)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("image_platform_parity=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
