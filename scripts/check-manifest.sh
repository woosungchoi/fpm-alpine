#!/usr/bin/env bash
set -euo pipefail

IMAGE_REF="${1:-}"
shift || true
if [ -z "$IMAGE_REF" ]; then
  echo "usage: $0 <registry-image-ref> [expected-platform ...]" >&2
  echo "example: $0 woosungchoi/fpm-alpine:8.5 linux/amd64 linux/arm64" >&2
  exit 64
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$script_dir/report-manifest.sh" "$IMAGE_REF" "$@"
