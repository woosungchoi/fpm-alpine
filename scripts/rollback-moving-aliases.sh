#!/usr/bin/env bash
set -uo pipefail

DOCKERHUB_REPOSITORY="${1:-}"
PREVIOUS_DOCKERHUB_DIGEST="${2:-}"
GHCR_REPOSITORY="${3:-}"
PREVIOUS_GHCR_DIGEST="${4:-}"
MINOR="${5:-}"
REPORT_DIR="${6:-rollback-reports}"
SIGN_DESTINATION="${COSIGN_SIGN_DESTINATION:-0}"
OIDC_ISSUER="${COSIGN_CERTIFICATE_OIDC_ISSUER:-https://token.actions.githubusercontent.com}"
IDENTITY="^https://github.com/woosungchoi/fpm-alpine/.github/workflows/publish.yml@refs/heads/main$"

for digest in "$PREVIOUS_DOCKERHUB_DIGEST" "$PREVIOUS_GHCR_DIGEST"; do
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "rollback requires exact prior digests" >&2; exit 2; }
done
[[ "$MINOR" =~ ^8\.[2-5]$ ]] || { echo "rollback minor must be active" >&2; exit 2; }
[ -n "$DOCKERHUB_REPOSITORY" ] && [ -n "$GHCR_REPOSITORY" ] || { echo "rollback repositories are required" >&2; exit 2; }

resolve_digest() { "$(dirname "$0")/resolve-image-digest.sh" "$1"; }
source_subject="${GHCR_REPOSITORY}@${PREVIOUS_GHCR_DIGEST}"
if [ "$(resolve_digest "$source_subject")" != "$PREVIOUS_GHCR_DIGEST" ]; then
  echo "durable GHCR rollback subject is unavailable" >&2
  exit 1
fi
mkdir -p "$REPORT_DIR"
rollback_status=0
dockerhub_actual=""
ghcr_actual=""

if docker buildx imagetools create --tag "${DOCKERHUB_REPOSITORY}:${MINOR}" "$source_subject"; then
  dockerhub_actual="$(resolve_digest "${DOCKERHUB_REPOSITORY}:${MINOR}")" || rollback_status=1
  [[ "$dockerhub_actual" =~ ^sha256:[0-9a-f]{64}$ ]] || rollback_status=1
else
  echo "Docker Hub rollback mutation failed" >&2
  rollback_status=1
fi
if docker buildx imagetools create --tag "${GHCR_REPOSITORY}:${MINOR}" "$source_subject"; then
  ghcr_actual="$(resolve_digest "${GHCR_REPOSITORY}:${MINOR}")" || rollback_status=1
  [ "$ghcr_actual" = "$PREVIOUS_GHCR_DIGEST" ] || rollback_status=1
else
  echo "GHCR rollback mutation failed" >&2
  rollback_status=1
fi
if [ "$rollback_status" -ne 0 ]; then
  echo "one or more registry aliases could not be restored; both registries were attempted" >&2
  exit 1
fi

if [ "$SIGN_DESTINATION" = 1 ]; then
  cosign sign --yes "${DOCKERHUB_REPOSITORY}@${dockerhub_actual}" || exit 1
  cosign verify --certificate-identity-regexp "$IDENTITY" --certificate-oidc-issuer "$OIDC_ISSUER" \
    "${DOCKERHUB_REPOSITORY}@${dockerhub_actual}" >/dev/null || exit 1
fi
if ! "$(dirname "$0")/verify-rollback-image.sh" \
  "${DOCKERHUB_REPOSITORY}@${dockerhub_actual}" \
  "$source_subject" \
  "$MINOR" \
  "$REPORT_DIR"; then
  echo "restored aliases failed durable-source rollback verification" >&2
  exit 1
fi
python3 - "$REPORT_DIR/rollback-result.json" "$MINOR" "$PREVIOUS_DOCKERHUB_DIGEST" "$PREVIOUS_GHCR_DIGEST" "$dockerhub_actual" "$ghcr_actual" <<'PY'
import json
import sys
from pathlib import Path
path, minor, previous_dockerhub, previous_ghcr, dockerhub_actual, ghcr_actual = sys.argv[1:]
Path(path).write_text(json.dumps({
    "schema_version": 2,
    "status": "verified",
    "minor": minor,
    "durable_source": f"ghcr.io/woosungchoi/fpm-alpine@{previous_ghcr}",
    "previous_dockerhub_digest": previous_dockerhub,
    "previous_ghcr_digest": previous_ghcr,
    "restored_dockerhub_digest": dockerhub_actual,
    "restored_ghcr_digest": ghcr_actual,
}, indent=2, sort_keys=True) + "\n")
PY
echo "both registry moving aliases restored from durable GHCR and verified"
