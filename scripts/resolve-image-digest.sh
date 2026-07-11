#!/usr/bin/env bash
set -uo pipefail

ref="${1:-}"
if [ -z "$ref" ]; then
  echo "usage: $0 <image-ref>" >&2
  exit 64
fi

output="$(docker buildx imagetools inspect "$ref" 2>&1)"
status=$?
if [ "$status" -ne 0 ]; then
  printf '%s\n' "$output" >&2
  exit 1
fi
printf '%s\n' "$output" | "$(dirname "$0")/extract-image-digest.sh"
