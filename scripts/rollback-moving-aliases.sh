#!/usr/bin/env bash
set -uo pipefail

DOCKERHUB_REPOSITORY="${1:-}"
DOCKERHUB_DIGEST="${2:-}"
GHCR_REPOSITORY="${3:-}"
GHCR_DIGEST="${4:-}"
MINOR="${5:-}"
REPORT_DIR="${6:-rollback-reports}"

if [[ ! "$DOCKERHUB_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || [[ ! "$GHCR_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "rollback requires exact prior sha256 digests for both registries" >&2
  exit 2
fi
if [[ ! "$MINOR" =~ ^8\.[2-5]$ ]]; then
  echo "rollback minor must be one of 8.2, 8.3, 8.4, or 8.5" >&2
  exit 2
fi
if [ -z "$DOCKERHUB_REPOSITORY" ] || [ -z "$GHCR_REPOSITORY" ]; then
  echo "rollback repository arguments are required" >&2
  exit 2
fi

resolve_digest() {
  "$(dirname "$0")/resolve-image-digest.sh" "$1"
}

restore_alias() {
  local repository="$1"
  local expected_digest="$2"
  local label="$3"
  local alias="${repository}:${MINOR}"
  local actual_digest
  if ! docker buildx imagetools create --tag "$alias" "${repository}@${expected_digest}"; then
    echo "$label rollback mutation failed: $alias" >&2
    return 1
  fi
  if ! actual_digest="$(resolve_digest "$alias")"; then
    echo "$label rollback read-back failed: $alias" >&2
    return 1
  fi
  if [ "$actual_digest" != "$expected_digest" ]; then
    echo "$label rollback verification failed: expected $expected_digest, got ${actual_digest:-missing}" >&2
    return 1
  fi
  echo "$label rollback alias restored: $alias@$expected_digest"
}

mkdir -p "$REPORT_DIR"
rollback_status=0
restore_alias "$DOCKERHUB_REPOSITORY" "$DOCKERHUB_DIGEST" DockerHub || rollback_status=1
restore_alias "$GHCR_REPOSITORY" "$GHCR_DIGEST" GHCR || rollback_status=1

if [ "$rollback_status" -ne 0 ]; then
  echo "one or more registry aliases could not be restored; both registries were attempted" >&2
  exit "$rollback_status"
fi

if ! "$(dirname "$0")/verify-rollback-image.sh" \
  "${DOCKERHUB_REPOSITORY}@${DOCKERHUB_DIGEST}" \
  "${GHCR_REPOSITORY}@${GHCR_DIGEST}" \
  "$MINOR" \
  "$REPORT_DIR"; then
  echo "restored aliases failed exact-digest rollback verification" >&2
  exit 1
fi

echo "both registry moving aliases restored and verified"
