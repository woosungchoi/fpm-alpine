#!/usr/bin/env bash
set -euo pipefail

if ! digest_output="$(awk '/^Digest:/ { print $2 }')"; then
  echo "failed to extract image digest" >&2
  exit 1
fi
mapfile -t digests <<< "$digest_output"
if [ "${#digests[@]}" -ne 1 ] || [[ ! "${digests[0]}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "expected exactly one valid Digest line" >&2
  exit 1
fi
printf '%s\n' "${digests[0]}"
