#!/usr/bin/env bash
set -euo pipefail

IMAGE_REF="${1:-}"
IMAGE_DIGEST="${2:-}"
REPORT_DIR="${3:-scan-reports}"
PLATFORM="${4:-}"
TRIVY_IMAGE="${TRIVY_IMAGE:-aquasec/trivy:0.72.0@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f}"

if [ -z "$IMAGE_REF" ] || [[ ! "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || [[ ! "$PLATFORM" =~ ^linux/(amd64|arm64)$ ]]; then
  echo "usage: $0 <registry/repository> <sha256:digest> <report-dir> <linux/amd64|linux/arm64>" >&2
  exit 64
fi
if [[ "$IMAGE_REF" == *@* ]] || [[ "$IMAGE_REF" == *:* ]]; then
  echo "image repository must not include a tag or digest: $IMAGE_REF" >&2
  exit 64
fi

mkdir -p "$REPORT_DIR"
report_dir_abs="$(cd "$REPORT_DIR" && pwd)"
safe_name="${IMAGE_REF//[^A-Za-z0-9_.-]/_}_${IMAGE_DIGEST#sha256:}_${PLATFORM//\//-}"
subject="${IMAGE_REF}@${IMAGE_DIGEST}"

docker_args=(--rm -v "${report_dir_abs}:/reports" -v trivy-cache:/root/.cache/trivy)
docker_config="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
if [ -f "$docker_config" ]; then
  docker_args+=(-v "${docker_config}:/root/.docker/config.json:ro")
fi

# Keep the complete fixable HIGH/CRITICAL report, then enforce the narrower
# production blocker separately so the rollout policy is explicit.
docker run "${docker_args[@]}" "$TRIVY_IMAGE" image \
  --scanners vuln \
  --ignore-unfixed \
  --platform "$PLATFORM" \
  --severity HIGH,CRITICAL \
  --format json \
  --output "/reports/${safe_name}.json" \
  "$subject"

docker run "${docker_args[@]}" "$TRIVY_IMAGE" image \
  --scanners vuln \
  --ignore-unfixed \
  --platform "$PLATFORM" \
  --severity CRITICAL \
  --exit-code 1 \
  "$subject"

printf 'Trivy fixable-CRITICAL gate passed: %s (%s)\n' "$subject" "$PLATFORM"
