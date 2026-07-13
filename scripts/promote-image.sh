#!/usr/bin/env bash
set -euo pipefail

CHECK_ONLY=0
if [ "${1:-}" = "--check-only" ]; then
  CHECK_ONLY=1
  shift
fi
[ "${1:-}" = "--policy" ] || { echo "--policy is required" >&2; exit 64; }
POLICY="${2:-}"
shift 2
TARGET_REPOSITORY="${1:-}"
SOURCE_REPOSITORY="${2:-}"
SOURCE_DIGEST="${3:-}"
MINOR="${4:-}"
PHP_PATCH="${5:-}"
SOURCE_SHA="${6:-}"
RELEASE_DATE="${7:-}"

case "$POLICY" in
  moving-only)
    [ "$TARGET_REPOSITORY" = docker.io/woosungchoi/fpm-alpine ] || { echo "moving-only target must be canonical Docker Hub" >&2; exit 64; }
    [ "$SOURCE_REPOSITORY" = ghcr.io/woosungchoi/fpm-alpine ] || { echo "moving-only source must be canonical GHCR" >&2; exit 64; }
    ;;
  evidence)
    [ "$TARGET_REPOSITORY" = ghcr.io/woosungchoi/fpm-alpine ] || { echo "evidence target must be canonical GHCR" >&2; exit 64; }
    [ "$SOURCE_REPOSITORY" = "$TARGET_REPOSITORY" ] || { echo "evidence promotion must remain in GHCR" >&2; exit 64; }
    ;;
  *) echo "policy must be moving-only or evidence" >&2; exit 64 ;;
esac
[[ "$SOURCE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "invalid source digest" >&2; exit 64; }
[[ "$MINOR" =~ ^8\.[2-5]$ ]] || { echo "minor must be active" >&2; exit 64; }
[[ "$PHP_PATCH" =~ ^${MINOR//./\.}\.[0-9]+$ ]] || { echo "PHP patch does not match minor" >&2; exit 64; }
[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "source SHA must be exact" >&2; exit 64; }
[[ "$RELEASE_DATE" =~ ^[0-9]{8}$ ]] || { echo "release date must use YYYYMMDD" >&2; exit 64; }

resolve_digest() {
  "$(dirname "$0")/resolve-image-digest.sh" "$1"
}
resolve_optional_digest() {
  local ref="$1" output status digest
  set +e
  output="$(docker buildx imagetools inspect "$ref" 2>&1)"
  status=$?
  set -e
  if [ "$status" -eq 0 ]; then
    digest="$(printf '%s\n' "$output" | "$(dirname "$0")/extract-image-digest.sh")" || return 1
    printf '%s\n' "$digest"
    return 0
  fi
  if printf '%s\n' "$output" | "$(dirname "$0")/is-manifest-absent.sh" "$ref"; then
    return 2
  fi
  printf '%s\n' "$output" >&2
  return 1
}
source_subject="${SOURCE_REPOSITORY}@${SOURCE_DIGEST}"
[ "$(resolve_digest "$source_subject")" = "$SOURCE_DIGEST" ] || { echo "source subject digest mismatch" >&2; exit 1; }
short_sha="${SOURCE_SHA:0:12}"
digest_hex="${SOURCE_DIGEST#sha256:}"
immutable_tags=("${PHP_PATCH}-${RELEASE_DATE}-${digest_hex}" "sha-${MINOR}-${short_sha}-${digest_hex}")
all_tags=("${MINOR}")
if [ "$POLICY" = evidence ]; then
  all_tags+=("${immutable_tags[@]}")
  for tag in "${immutable_tags[@]}"; do
    ref="${TARGET_REPOSITORY}:${tag}"
    if existing="$(resolve_optional_digest "$ref")"; then
      [ "$existing" = "$SOURCE_DIGEST" ] || {
        echo "immutable tag already points to another digest: $ref ($existing)" >&2
        exit 1
      }
    else
      status=$?
      [ "$status" -eq 2 ] || exit "$status"
    fi
  done
fi
if [ "$CHECK_ONLY" -eq 1 ]; then
  echo "promotion preflight passed policy=${POLICY} minor=${MINOR}"
  exit 0
fi
create_args=()
for tag in "${all_tags[@]}"; do create_args+=(--tag "${TARGET_REPOSITORY}:${tag}"); done
docker buildx imagetools create "${create_args[@]}" "$source_subject"
for tag in "${all_tags[@]}"; do
  actual="$(resolve_digest "${TARGET_REPOSITORY}:${tag}")"
  [[ "$actual" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "promoted tag has invalid digest: $tag" >&2; exit 1; }
  if [ "$POLICY" = evidence ] && [ "$actual" != "$SOURCE_DIGEST" ]; then
    echo "GHCR evidence tag digest mismatch: ${TARGET_REPOSITORY}:${tag}" >&2
    exit 1
  fi
done
echo "promoted policy=${POLICY} source=${source_subject} tags=${all_tags[*]}"
