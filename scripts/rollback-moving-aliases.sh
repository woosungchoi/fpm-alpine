#!/usr/bin/env bash
set -euo pipefail

dockerhub_repository="${1:?Docker Hub repository required}"
previous_dockerhub_digest="${2:?previous Docker Hub digest required}"
ghcr_repository="${3:?GHCR repository required}"
previous_ghcr_digest="${4:?previous GHCR digest required}"
minor="${5:?minor required}"
evidence_dir="${6:-publisher-reports/rollback}"

[[ "$previous_dockerhub_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 64
[[ "$previous_ghcr_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 64
[[ "$minor" =~ ^8\.[2-5]$ ]] || exit 64
mkdir -p "$evidence_dir"

dockerhub_alias="${dockerhub_repository}:${minor}"
ghcr_alias="${ghcr_repository}:${minor}"
dockerhub_source="${dockerhub_repository}@${previous_dockerhub_digest}"
ghcr_source="${ghcr_repository}@${previous_ghcr_digest}"

restore_alias() {
  local registry="$1"
  local destination="$2"
  local source_subject="$3"
  local expected_digest="$4"
  local rc=0
  local observed=""
  local inspect_file
  inspect_file="$(mktemp)"

  if ! docker buildx imagetools create \
    --progress plain \
    --tag "$destination" \
    "$source_subject"; then
    rc=1
  fi
  if [ "$rc" -eq 0 ] && [ "${COSIGN_SIGN_DESTINATION:-0}" = 1 ]; then
    if ! cosign sign --yes "$destination"; then
      rc=1
    fi
  fi
  if [ "$rc" -eq 0 ]; then
    if ! docker buildx imagetools inspect "$destination" >"$inspect_file"; then
      rc=1
    else
      mapfile -t observed_rows < <(awk '/^Digest:/ { print $2 }' "$inspect_file")
      if [ "${#observed_rows[@]}" -ne 1 ]; then
        rc=1
      else
        observed="${observed_rows[0]}"
      fi
    fi
  fi
  if [ "$rc" -eq 0 ] && [ "$observed" != "$expected_digest" ]; then
    rc=1
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$registry" "$destination" "$source_subject" "$expected_digest" "$rc" \
    > "${evidence_dir}/${registry}.tsv"
  rm -f "$inspect_file"
  return "$rc"
}

dockerhub_status=0
ghcr_status=0
set +e
restore_alias dockerhub "$dockerhub_alias" "$dockerhub_source" "$previous_dockerhub_digest"
dockerhub_status=$?
restore_alias ghcr "$ghcr_alias" "$ghcr_source" "$previous_ghcr_digest"
ghcr_status=$?
set -e

if [ "$dockerhub_status" -ne 0 ] || [ "$ghcr_status" -ne 0 ]; then
  echo "rollback was incomplete; both registries were attempted" >&2
  exit 1
fi

./scripts/verify-rollback-image.sh \
  "$dockerhub_source" \
  "$ghcr_source" \
  "$minor" \
  "$evidence_dir/verify"

echo "both registry moving aliases restored from registry-specific immutable subjects and verified"
