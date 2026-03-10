#!/usr/bin/env bash
set -euo pipefail

IMAGE_REF="${1:-}"
if [ -z "$IMAGE_REF" ]; then
  echo "usage: $0 <registry-image-ref>" >&2
  echo "example: $0 woosungchoi/fpm-alpine:8.5" >&2
  exit 64
fi

raw="$(docker buildx imagetools inspect "$IMAGE_REF")"
printf '%s\n' "$raw"

for platform in linux/amd64 linux/arm64; do
  if ! grep -q "$platform" <<<"$raw"; then
    echo "missing manifest platform: $platform" >&2
    exit 1
  fi
done

echo "manifest check passed for $IMAGE_REF"
