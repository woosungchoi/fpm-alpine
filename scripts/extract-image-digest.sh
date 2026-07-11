#!/usr/bin/env bash
set -euo pipefail

mapfile -t digests < <(awk '/^Digest:/ { print $2 }')
if [ "${#digests[@]}" -ne 1 ] || [[ ! "${digests[0]}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "expected exactly one valid Digest line" >&2
  exit 1
fi
printf '%s\n' "${digests[0]}"
