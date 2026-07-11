#!/usr/bin/env bash
set -euo pipefail

CHECK_ONLY=0
if [ "${1:-}" = "--check-only" ]; then
  CHECK_ONLY=1
  shift
fi

REPOSITORY="${1:-}"
SOURCE_DIGEST="${2:-}"
MINOR="${3:-}"
PHP_PATCH="${4:-}"
SOURCE_SHA="${5:-}"
RELEASE_DATE="${6:-}"

if [ -z "$REPOSITORY" ] || [[ "$REPOSITORY" == *@* ]] || [[ "$REPOSITORY" == *:* ]]; then
  echo "usage: $0 [--check-only] <registry/repository> <sha256:digest> <minor> <php-patch> <source-sha> <YYYYMMDD>" >&2
  exit 64
fi
[[ "$SOURCE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "invalid source digest" >&2; exit 64; }
[[ "$MINOR" =~ ^8\.[2-5]$ ]] || { echo "minor must be an active publish target (8.2-8.5)" >&2; exit 64; }
[[ "$PHP_PATCH" =~ ^${MINOR//./\.}\.[0-9]+$ ]] || { echo "PHP patch does not match minor" >&2; exit 64; }
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "source SHA must contain exactly 40 lowercase hex characters" >&2; exit 64; }
[[ "$RELEASE_DATE" =~ ^[0-9]{8}$ ]] || { echo "release date must use YYYYMMDD" >&2; exit 64; }

short_sha="${SOURCE_SHA:0:12}"
digest_hex="${SOURCE_DIGEST#sha256:}"
immutable_tags=("${PHP_PATCH}-${RELEASE_DATE}-${digest_hex}" "sha-${MINOR}-${short_sha}-${digest_hex}")
all_tags=("${MINOR}" "${immutable_tags[@]}")

resolve_digest() {
  local ref="$1" output status digest
  set +e
  output="$(docker buildx imagetools inspect "$ref" 2>&1)"
  status=$?
  set -e
  if [ "$status" -eq 0 ]; then
    if ! digest="$(printf '%s\n' "$output" | "$(dirname "$0")/extract-image-digest.sh")"; then
      return 1
    fi
    printf '%s\n' "$digest"
    return 0
  fi
  if printf '%s\n' "$output" | "$(dirname "$0")/is-manifest-absent.sh" "$ref"; then
    return 2
  fi
  printf '%s\n' "$output" >&2
  return 1
}

for tag in "${immutable_tags[@]}"; do
  existing=""
  if existing="$(resolve_digest "${REPOSITORY}:${tag}")"; then
    if [ "$existing" != "$SOURCE_DIGEST" ]; then
      echo "immutable tag already points to another digest: ${REPOSITORY}:${tag} (${existing})" >&2
      exit 1
    fi
  else
    status=$?
    [ "$status" -eq 2 ] || exit "$status"
  fi
done

if [ "$CHECK_ONLY" -eq 1 ]; then
  echo "promotion preflight passed for ${REPOSITORY}:${MINOR}"
  exit 0
fi

create_args=()
for tag in "${all_tags[@]}"; do
  create_args+=(--tag "${REPOSITORY}:${tag}")
done
docker buildx imagetools create "${create_args[@]}" "${REPOSITORY}@${SOURCE_DIGEST}"

for tag in "${all_tags[@]}"; do
  actual="$(resolve_digest "${REPOSITORY}:${tag}")"
  if [ "$actual" != "$SOURCE_DIGEST" ]; then
    echo "promoted tag digest mismatch: ${REPOSITORY}:${tag} expected ${SOURCE_DIGEST}, got ${actual}" >&2
    exit 1
  fi
done

echo "promoted verified digest ${SOURCE_DIGEST} to ${REPOSITORY}: ${all_tags[*]}"
