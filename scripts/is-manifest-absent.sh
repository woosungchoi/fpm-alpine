#!/usr/bin/env bash
set -euo pipefail

expected_ref="${1:-}"
if [ -z "$expected_ref" ] || [[ "$expected_ref" == *[[:space:]]* ]]; then
  echo "usage: $0 <expected-image-ref>" >&2
  exit 64
fi

output="$(cat)"
[[ -n "$output" ]] || exit 1
[[ "$output" != *$'\n'* ]] || exit 1
for reason in 'not found' 'manifest unknown' manifest_unknown 'no such manifest' 'name unknown' name_unknown; do
  [ "$output" = "ERROR: ${expected_ref}: ${reason}" ] && exit 0
done
exit 1
