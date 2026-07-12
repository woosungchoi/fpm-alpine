#!/usr/bin/env bash
set -euo pipefail

image="${1:?local image tag required}"
report_dir="${2:-scan-reports}"
platform="${3:?platform required}"
trivy_image="${TRIVY_IMAGE:-aquasec/trivy:0.72.0@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f}"
[[ "$image" != *@* ]] || { echo "local image must be a tag, not a digest" >&2; exit 64; }
[[ "$platform" =~ ^linux/(amd64|arm64)$ ]] || { echo "invalid platform" >&2; exit 64; }
mkdir -p "$report_dir"
report_dir_abs="$(cd "$report_dir" && pwd)"
safe_name="${image//[^A-Za-z0-9_.-]/_}_${platform//\//-}"

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v "$report_dir_abs:/reports" \
  -v trivy-cache:/root/.cache/trivy \
  "$trivy_image" image \
  --scanners vuln \
  --ignore-unfixed \
  --severity HIGH,CRITICAL \
  --format json \
  --output "/reports/${safe_name}.json" \
  "$image"

docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v trivy-cache:/root/.cache/trivy \
  "$trivy_image" image \
  --scanners vuln \
  --ignore-unfixed \
  --severity CRITICAL \
  --exit-code 1 \
  "$image"

printf 'local_trivy_fixable_critical=PASS image=%s platform=%s\n' "$image" "$platform"
