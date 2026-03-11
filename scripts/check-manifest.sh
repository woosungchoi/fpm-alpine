#!/usr/bin/env bash
set -euo pipefail

IMAGE_REF="${1:-}"
shift || true
EXPECTED_PLATFORMS=("$@")
if [ -z "$IMAGE_REF" ]; then
  echo "usage: $0 <registry-image-ref> [expected-platform ...]" >&2
  echo "example: $0 woosungchoi/fpm-alpine:8.5 linux/amd64 linux/arm64" >&2
  exit 64
fi

if [ ${#EXPECTED_PLATFORMS[@]} -eq 0 ]; then
  EXPECTED_PLATFORMS=(linux/amd64 linux/arm64)
fi

raw_json="$(docker buildx imagetools inspect --raw "$IMAGE_REF")"
printf '%s\n' "$raw_json"

MANIFEST_RAW_JSON="$raw_json" python3 - "$IMAGE_REF" "${EXPECTED_PLATFORMS[@]}" <<'PY'
import json
import os
import sys

image_ref = sys.argv[1]
expected = sys.argv[2:]
raw = os.environ.get('MANIFEST_RAW_JSON', '')
if not raw.strip():
    print(f"empty manifest output for {image_ref}", file=sys.stderr)
    sys.exit(1)

data = json.loads(raw)
platforms = set()
for manifest in data.get("manifests", []):
    platform = manifest.get("platform") or {}
    os_name = platform.get("os")
    arch = platform.get("architecture")
    variant = platform.get("variant")
    if os_name and arch:
        value = f"{os_name}/{arch}"
        platforms.add(value)
        if variant:
            platforms.add(f"{value}/{variant}")

missing = [item for item in expected if item not in platforms]
if missing:
    print(f"manifest platforms present: {sorted(platforms)}", file=sys.stderr)
    print(f"missing manifest platform(s) for {image_ref}: {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)

print(f"manifest check passed for {image_ref}: {', '.join(expected)}")
PY
