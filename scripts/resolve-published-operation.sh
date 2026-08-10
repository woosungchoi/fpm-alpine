#!/usr/bin/env bash
set -euo pipefail

SUBJECT="${1:-}"
MANUAL_SIGNING_REF="${2:-}"
ISSUER="https://token.actions.githubusercontent.com"

if [[ ! "$SUBJECT" =~ ^ghcr\.io/woosungchoi/fpm-alpine@sha256:[0-9a-f]{64}$ ]] ||
   [[ ! "$MANUAL_SIGNING_REF" =~ ^(main|8\.5)$ ]]; then
  echo "usage: $0 <exact-ghcr-subject> <main|8.5-manual-signing-ref>" >&2
  exit 64
fi
command -v cosign >/dev/null 2>&1 || { echo "cosign is required" >&2; exit 69; }

dependency_identity='^https://github\.com/woosungchoi/fpm-alpine/\.github/workflows/dependency-auto-publish\.yml@refs/heads/main$'
manual_identity="^https://github\\.com/woosungchoi/fpm-alpine/\\.github/workflows/publish\\.yml@refs/heads/${MANUAL_SIGNING_REF}$"
matches=()

if cosign verify \
  --certificate-identity-regexp "$dependency_identity" \
  --certificate-oidc-issuer "$ISSUER" \
  -a fpm.operation=backfill-ghcr \
  "$SUBJECT" >/dev/null 2>&1; then
  matches+=("backfill-ghcr\tdependency-auto-publish.yml\tmain")
fi
if cosign verify \
  --certificate-identity-regexp "$dependency_identity" \
  --certificate-oidc-issuer "$ISSUER" \
  -a fpm.operation=automatic \
  "$SUBJECT" >/dev/null 2>&1; then
  matches+=("automatic\tdependency-auto-publish.yml\tmain")
fi
if cosign verify \
  --certificate-identity-regexp "$manual_identity" \
  --certificate-oidc-issuer "$ISSUER" \
  "$SUBJECT" >/dev/null 2>&1; then
  matches+=("manual\tpublish.yml\t${MANUAL_SIGNING_REF}")
fi

if [ "${#matches[@]}" -ne 1 ]; then
  echo "exactly one signed publication operation is required; observed=${#matches[@]}" >&2
  exit 1
fi
printf '%b\n' "${matches[0]}"
