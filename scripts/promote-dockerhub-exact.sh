#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 PLAN_FILE PHP_MINOR" >&2
  exit 64
fi

PLAN_FILE="$1"
PHP_MINOR="$2"
DOCKERHUB_REPOSITORY="${DOCKERHUB_REPOSITORY:-docker.io/woosungchoi/fpm-alpine}"

[ "$DOCKERHUB_REPOSITORY" = docker.io/woosungchoi/fpm-alpine ]
[[ "$PHP_MINOR" =~ ^8\.[2-5]$ ]]
command -v crane >/dev/null

./scripts/transaction-journal.py assert-owner "$PLAN_FILE"
target_digest="$(python3 - "$PLAN_FILE" "$PHP_MINOR" <<'PY'
import json
import re
import sys

payload = json.load(open(sys.argv[1]))
minor = sys.argv[2]
if payload.get("operation") != "automatic":
    raise SystemExit("Docker Hub exact promotion requires automatic mode")
units = [unit for unit in payload.get("release_units", []) if unit.get("php_minor") == minor]
if len(units) != 1:
    raise SystemExit("Docker Hub target unit mismatch")
target = units[0].get("target_dockerhub_digest")
if not isinstance(target, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", target) is None:
    raise SystemExit("invalid frozen Docker Hub target digest")
if target != units[0].get("target_ghcr_digest"):
    raise SystemExit("Docker Hub target is not the digest-preserved staged subject")
print(target)
PY
)"

staged="$(crane digest "${DOCKERHUB_REPOSITORY}@${target_digest}")"
[ "$staged" = "$target_digest" ] || {
  echo "Docker Hub untagged staged subject mismatch" >&2
  exit 1
}
./scripts/transaction-journal.py assert-owner "$PLAN_FILE"
crane tag "${DOCKERHUB_REPOSITORY}@${target_digest}" "$PHP_MINOR"
actual="$(./scripts/resolve-image-digest.sh "${DOCKERHUB_REPOSITORY}:${PHP_MINOR}")"
[ "$actual" = "$target_digest" ] || {
  echo "Docker Hub moving alias did not resolve to the frozen target" >&2
  exit 1
}
