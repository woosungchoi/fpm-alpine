#!/usr/bin/env bash
set -euo pipefail

IMAGE_REF="${1:-}"
[[ "$IMAGE_REF" =~ ^ghcr\.io/woosungchoi/fpm-alpine:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || {
  echo "refusing non-canonical GHCR tag: $IMAGE_REF" >&2
  exit 64
}

stdout_file="$(mktemp)"
stderr_file="$(mktemp)"
trap 'rm -f "$stdout_file" "$stderr_file"' EXIT

set +e
docker buildx imagetools inspect "$IMAGE_REF" >"$stdout_file" 2>"$stderr_file"
status=$?
set -e

if [ "$status" -eq 0 ]; then
  echo "refusing to overwrite existing GHCR tag: $IMAGE_REF" >&2
  exit 1
fi

combined="$(<"$stdout_file")$(<"$stderr_file")"

if grep -Eq 'sha256:[0-9a-f]{64}' "$stdout_file" "$stderr_file"; then
  echo "ambiguous failed inspect emitted a digest for $IMAGE_REF" >&2
  exit 2
fi
if grep -Eiq '(unauthori[sz]ed|authentication|required|denied|forbidden|timeout|timed out|too many requests|rate.?limit|tls|certificate|connection|service unavailable|internal server error|request failed|bad gateway|(^|[^0-9])[45][0-9]{2}([^0-9]|$))' <<<"$combined"; then
  printf 'ambiguous registry failure while checking %s:\n%s\n' "$IMAGE_REF" "$combined" >&2
  exit 2
fi
if printf '%s\n' "$combined" | ./scripts/is-manifest-absent.sh "$IMAGE_REF"; then
  printf 'confirmed absent GHCR tag: %s\n' "$IMAGE_REF"
  exit 0
fi

printf 'registry error did not bind absence to exact ref %s:\n%s\n' \
  "$IMAGE_REF" "$combined" >&2
exit 2
