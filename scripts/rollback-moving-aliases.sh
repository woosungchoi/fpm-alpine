#!/usr/bin/env bash
set -uo pipefail

DOCKERHUB_REPOSITORY="${1:-}"
PREVIOUS_DOCKERHUB_DIGEST="${2:-}"
GHCR_REPOSITORY="${3:-}"
PREVIOUS_GHCR_DIGEST="${4:-}"
MINOR="${5:-}"
REPORT_DIR="${6:-rollback-reports}"
SIGN_DESTINATION="${COSIGN_SIGN_DESTINATION:-0}"
RESTORE_DOCKERHUB="${RESTORE_DOCKERHUB:-1}"
RESTORE_GHCR="${RESTORE_GHCR:-1}"
EXPECTED_PUBLISHER_WORKFLOW="${EXPECTED_PUBLISHER_WORKFLOW:-publish.yml}"
OIDC_ISSUER="${COSIGN_CERTIFICATE_OIDC_ISSUER:-https://token.actions.githubusercontent.com}"
case "$EXPECTED_PUBLISHER_WORKFLOW" in
  publish.yml) publisher_workflow_pattern='publish\.yml' ;;
  dependency-auto-publish.yml) publisher_workflow_pattern='dependency-auto-publish\.yml' ;;
  *) echo "invalid expected publisher workflow for rollback" >&2; exit 2 ;;
esac
IDENTITY="^https://github\\.com/woosungchoi/fpm-alpine/\\.github/workflows/${publisher_workflow_pattern}@refs/heads/main$"
[[ "$RESTORE_DOCKERHUB" =~ ^[01]$ ]] || { echo "invalid Docker Hub restore flag" >&2; exit 2; }
[[ "$RESTORE_GHCR" =~ ^[01]$ ]] || { echo "invalid GHCR restore flag" >&2; exit 2; }

for digest in "$PREVIOUS_DOCKERHUB_DIGEST" "$PREVIOUS_GHCR_DIGEST"; do
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "rollback requires exact prior digests" >&2; exit 2; }
done
[[ "$MINOR" =~ ^8\.[2-5]$ ]] || { echo "rollback minor must be active" >&2; exit 2; }
[ "$DOCKERHUB_REPOSITORY" = docker.io/woosungchoi/fpm-alpine ] || { echo "invalid Docker Hub repository" >&2; exit 2; }
[ "$GHCR_REPOSITORY" = ghcr.io/woosungchoi/fpm-alpine ] || { echo "invalid GHCR repository" >&2; exit 2; }

DOCKERHUB_ROLLBACK_SOURCE="${DOCKERHUB_ROLLBACK_SOURCE:-${DOCKERHUB_REPOSITORY}@${PREVIOUS_DOCKERHUB_DIGEST}}"
DOCKERHUB_ROLLBACK_FALLBACK_SOURCE="${DOCKERHUB_ROLLBACK_FALLBACK_SOURCE:-}"
GHCR_ROLLBACK_SOURCE="${GHCR_ROLLBACK_SOURCE:-${GHCR_REPOSITORY}@${PREVIOUS_GHCR_DIGEST}}"
case "$DOCKERHUB_ROLLBACK_SOURCE" in
  "${DOCKERHUB_REPOSITORY}"@sha256:*|"${GHCR_REPOSITORY}"@sha256:*) ;;
  *) echo "invalid Docker Hub rollback source" >&2; exit 2 ;;
esac
if [ -n "$DOCKERHUB_ROLLBACK_FALLBACK_SOURCE" ]; then
  case "$DOCKERHUB_ROLLBACK_FALLBACK_SOURCE" in
    "${GHCR_REPOSITORY}"@sha256:*) ;;
    *) echo "invalid Docker Hub rollback fallback source" >&2; exit 2 ;;
  esac
fi
case "$GHCR_ROLLBACK_SOURCE" in
  "${GHCR_REPOSITORY}"@sha256:*) ;;
  *) echo "invalid GHCR rollback source" >&2; exit 2 ;;
esac

resolve_digest() { "$(dirname "$0")/resolve-image-digest.sh" "$1"; }
source_digest() { printf '%s\n' "${1##*@}"; }
source_available() {
  local source="$1" expected actual
  expected="$(source_digest "$source")"
  [[ "$expected" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
  actual="$(resolve_digest "$source")" || return 1
  [ "$actual" = "$expected" ]
}

if ! mkdir -p "$REPORT_DIR"; then
  echo "rollback report directory could not be created" >&2
  exit 1
fi
rollback_status=0
dockerhub_actual=""
ghcr_actual=""
dockerhub_source_used=""
dockerhub_restore_status=failed
ghcr_restore_status=failed
primary_available=0
fallback_available=0
ghcr_source_available=0
if [ "$RESTORE_DOCKERHUB" = 1 ]; then
  source_available "$DOCKERHUB_ROLLBACK_SOURCE" && primary_available=1
  if [ -n "$DOCKERHUB_ROLLBACK_FALLBACK_SOURCE" ]; then
    source_available "$DOCKERHUB_ROLLBACK_FALLBACK_SOURCE" && fallback_available=1
  fi
fi
if [ "$RESTORE_GHCR" = 1 ]; then
  source_available "$GHCR_ROLLBACK_SOURCE" && ghcr_source_available=1
fi

restore_dockerhub_from() {
  local source="$1" actual
  docker buildx imagetools create \
    --tag "${DOCKERHUB_REPOSITORY}:${MINOR}" \
    "$source" || return 1
  actual="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${MINOR}")" || return 1
  dockerhub_actual="$actual"
  dockerhub_source_used="$source"
  [ "$actual" = "$PREVIOUS_DOCKERHUB_DIGEST" ]
}

if [ "$RESTORE_DOCKERHUB" = 0 ]; then
  dockerhub_actual="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${MINOR}")" || rollback_status=1
  if [ "$dockerhub_actual" = "$PREVIOUS_DOCKERHUB_DIGEST" ]; then
    dockerhub_restore_status=unchanged
  else
    echo "unchanged Docker Hub alias no longer matches its prior digest" >&2
    rollback_status=1
  fi
elif [ "$primary_available" -eq 1 ] && restore_dockerhub_from "$DOCKERHUB_ROLLBACK_SOURCE"; then
  dockerhub_restore_status=verified
elif [ "$fallback_available" -eq 1 ] && \
     restore_dockerhub_from "$DOCKERHUB_ROLLBACK_FALLBACK_SOURCE"; then
  dockerhub_restore_status=verified
  echo "Docker Hub rollback used the frozen GHCR fallback subject" >&2
else
  echo "Docker Hub rollback mutation/read-back failed for every available source" >&2
  rollback_status=1
fi

if [ "$RESTORE_GHCR" = 0 ]; then
  ghcr_actual="$(resolve_digest "${GHCR_REPOSITORY}:${MINOR}")" || rollback_status=1
  if [ "$ghcr_actual" = "$PREVIOUS_GHCR_DIGEST" ]; then
    ghcr_restore_status=unchanged
  else
    echo "unchanged GHCR alias no longer matches its prior digest" >&2
    rollback_status=1
  fi
elif [ "$ghcr_source_available" -eq 1 ] && docker buildx imagetools create \
    --tag "${GHCR_REPOSITORY}:${MINOR}" \
    "$GHCR_ROLLBACK_SOURCE"; then
  ghcr_actual="$(resolve_digest "${GHCR_REPOSITORY}:${MINOR}")" || rollback_status=1
  if [ "$ghcr_actual" = "$PREVIOUS_GHCR_DIGEST" ]; then
    ghcr_restore_status=verified
  else
    echo "GHCR rollback read-back mismatch" >&2
    rollback_status=1
  fi
else
  echo "durable GHCR rollback subject is unavailable or its mutation failed" >&2
  rollback_status=1
fi

if [ "$rollback_status" -eq 0 ] && [ "$SIGN_DESTINATION" = 1 ] && [ "$RESTORE_DOCKERHUB" = 1 ]; then
  if ! cosign sign --yes "${DOCKERHUB_REPOSITORY}@${dockerhub_actual}" || \
     ! cosign verify --certificate-identity-regexp "$IDENTITY" \
       --certificate-oidc-issuer "$OIDC_ISSUER" \
       "${DOCKERHUB_REPOSITORY}@${dockerhub_actual}" >/dev/null; then
    echo "restored Docker Hub subject signing or verification failed" >&2
    dockerhub_restore_status=failed
    rollback_status=1
  fi
fi
if [ "$rollback_status" -eq 0 ] && ! "$(dirname "$0")/verify-rollback-image.sh" \
  "${DOCKERHUB_REPOSITORY}@${dockerhub_actual}" \
  "${GHCR_REPOSITORY}@${ghcr_actual}" \
  "$MINOR" \
  "$REPORT_DIR"; then
  echo "restored aliases failed registry-specific rollback verification" >&2
  dockerhub_restore_status=failed
  ghcr_restore_status=failed
  rollback_status=1
fi

overall_status=failed
[ "$rollback_status" -eq 0 ] && overall_status=verified
python3 - "$REPORT_DIR/rollback-result.json" "$MINOR" \
  "$overall_status" "$dockerhub_restore_status" "$ghcr_restore_status" \
  "$PREVIOUS_DOCKERHUB_DIGEST" "$PREVIOUS_GHCR_DIGEST" \
  "$dockerhub_actual" "$ghcr_actual" \
  "$DOCKERHUB_ROLLBACK_SOURCE" "$DOCKERHUB_ROLLBACK_FALLBACK_SOURCE" \
  "$dockerhub_source_used" "$GHCR_ROLLBACK_SOURCE" <<'PY'
import json
import os
import sys
from pathlib import Path
(
    path,
    minor,
    status,
    dockerhub_restore_status,
    ghcr_restore_status,
    previous_dockerhub,
    previous_ghcr,
    dockerhub_actual,
    ghcr_actual,
    dockerhub_primary,
    dockerhub_fallback,
    dockerhub_source_used,
    ghcr_source,
) = sys.argv[1:]
payload = json.dumps({
    "schema_version": 5,
    "status": status,
    "minor": minor,
    "dockerhub_restore_status": dockerhub_restore_status,
    "ghcr_restore_status": ghcr_restore_status,
    "dockerhub_rollback_primary_source": dockerhub_primary,
    "dockerhub_rollback_fallback_source": dockerhub_fallback or None,
    "dockerhub_rollback_source_used": dockerhub_source_used or None,
    "ghcr_rollback_source": ghcr_source,
    "previous_dockerhub_digest": previous_dockerhub,
    "previous_ghcr_digest": previous_ghcr,
    "restored_dockerhub_digest": dockerhub_actual or None,
    "restored_ghcr_digest": ghcr_actual or None,
}, indent=2, sort_keys=True) + "\n"
destination = Path(path)
temporary = destination.with_name(f".{destination.name}.tmp")
with temporary.open("w") as handle:
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, destination)
directory_fd = os.open(destination.parent, os.O_RDONLY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
writer_status=$?
if [ "$writer_status" -ne 0 ] || [ ! -s "$REPORT_DIR/rollback-result.json" ]; then
  echo "rollback evidence write failed" >&2
  exit 1
fi
if [ "$rollback_status" -ne 0 ]; then
  echo "one or more registry aliases could not be restored; both registries were attempted independently" >&2
  exit 1
fi
echo "both registry moving aliases restored from registry-specific baselines and verified"
