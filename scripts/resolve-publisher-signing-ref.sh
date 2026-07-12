#!/usr/bin/env bash
set -euo pipefail

SOURCE_REVISION="${1:-}"
BOUNDARY_TAG='refs/tags/archive/php-8.5-final-branch'
EXPECTED_BOUNDARY_SHA="${EXPECTED_BOUNDARY_SHA:-f941dde2ff8864e1b056c051d330eb4321afb916}"

if [[ ! "$SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: $0 <40-char-source-sha>" >&2
  exit 64
fi
[[ "$EXPECTED_BOUNDARY_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "expected boundary SHA must be an exact lowercase commit SHA" >&2
  exit 64
}
command -v git >/dev/null 2>&1 || { echo "git is required" >&2; exit 69; }

resolved_source="$(git rev-parse --verify "${SOURCE_REVISION}^{commit}" 2>/dev/null)" || {
  echo "published source revision is not available as a commit" >&2
  exit 1
}
[ "$resolved_source" = "$SOURCE_REVISION" ] || {
  echo "published source revision did not resolve exactly" >&2
  exit 1
}
boundary="$(git rev-parse --verify "${BOUNDARY_TAG}^{commit}" 2>/dev/null)" || {
  echo "missing annotated 8.5 control-branch archive tag" >&2
  exit 1
}
[ "$boundary" = "$EXPECTED_BOUNDARY_SHA" ] || {
  echo "8.5 control-branch archive tag does not match the pinned cutover commit" >&2
  exit 1
}
head="$(git rev-parse --verify 'HEAD^{commit}')"

git merge-base --is-ancestor "$SOURCE_REVISION" "$head" || {
  echo "published source revision is not in protected main history" >&2
  exit 1
}

if git merge-base --is-ancestor "$SOURCE_REVISION" "$boundary"; then
  printf '%s\n' '8.5'
elif git merge-base --is-ancestor "$boundary" "$SOURCE_REVISION"; then
  printf '%s\n' 'main'
else
  echo "published source revision is outside the archived cutover lineage" >&2
  exit 1
fi
