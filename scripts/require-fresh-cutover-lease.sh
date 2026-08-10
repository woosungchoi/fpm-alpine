#!/usr/bin/env bash
set -euo pipefail

SOURCE_SHA="${1:-}"
OUTPUT_DIR="${2:-}"
REPOSITORY="${GITHUB_REPOSITORY:-}"

if [[ ! "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]] ||
   [ -z "$OUTPUT_DIR" ] ||
   [ "$REPOSITORY" != woosungchoi/fpm-alpine ]; then
  echo "usage: $0 <exact-source-sha> <new-output-dir>" >&2
  exit 64
fi
for command in gh python3 sha256sum; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required" >&2
    exit 69
  }
done
[ -n "${GH_TOKEN:-}" ] || { echo "GH_TOKEN is required" >&2; exit 69; }
[ ! -e "$OUTPUT_DIR" ] || { echo "fresh cutover lease output already exists" >&2; exit 73; }

work="$(mktemp -d)"
output_created=0
completed=0
cleanup() {
  rm -rf "$work"
  if [ "$output_created" -eq 1 ] && [ "$completed" -ne 1 ]; then
    rm -rf -- "$OUTPUT_DIR"
  fi
}
trap cleanup EXIT
runs="$work/runs.json"
selected="$work/selected.json"
values="$work/selected-values"

gh api \
  "repos/$REPOSITORY/actions/workflows/legacy-cutover-lease.yml/runs?branch=main&event=repository_dispatch&status=completed&per_page=100" \
  > "$runs"
./scripts/select-fresh-cutover-lease.py \
  --runs "$runs" \
  --source-sha "$SOURCE_SHA" \
  --repository "$REPOSITORY" \
  --output "$selected"
python3 - "$selected" > "$values" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1]))
run_id = payload.get("runId")
attempt = payload.get("runAttempt")
if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
    raise SystemExit("invalid selected cutover lease identity")
print(run_id)
print(attempt)
PY
mapfile -t selected_values < "$values"
[ "${#selected_values[@]}" -eq 2 ] || { echo "invalid selected cutover lease values" >&2; exit 1; }
run_id="${selected_values[0]}"
attempt="${selected_values[1]}"

mkdir -m 0700 "$OUTPUT_DIR"
output_created=1
artifact_name="legacy-cutover-lease-${run_id}-${attempt}"
if ! gh run download "$run_id" \
  --repo "$REPOSITORY" \
  --name "$artifact_name" \
  --dir "$OUTPUT_DIR"; then
  exit 1
fi

python3 - "$OUTPUT_DIR" "$SOURCE_SHA" "$run_id" "$attempt" > "$work/artifact-values" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path
root = Path(sys.argv[1])
source_sha = sys.argv[2]
run_id = int(sys.argv[3])
attempt = int(sys.argv[4])
expected = {"cutover-evidence.json", "cutover-evidence.sha256"}
observed = {path.name for path in root.iterdir()}
if observed != expected or any(not path.is_file() for path in root.iterdir()):
    raise SystemExit(f"fresh cutover lease artifact exact-set mismatch: {sorted(observed)}")
raw = (root / "cutover-evidence.json").read_bytes()
sha_text = (root / "cutover-evidence.sha256").read_text()
if not re.fullmatch(r"[0-9a-f]{64}\n", sha_text):
    raise SystemExit("invalid fresh cutover evidence hash file")
actual = hashlib.sha256(raw).hexdigest()
expected_sha = sha_text.strip()
if actual != expected_sha:
    raise SystemExit("fresh cutover evidence artifact hash mismatch")
print(actual)
print(json.dumps({
    "runId": run_id,
    "runAttempt": attempt,
    "sourceSha": source_sha,
    "evidenceSha256": actual,
}, sort_keys=True))
PY
mapfile -t artifact_values < "$work/artifact-values"
[ "${#artifact_values[@]}" -eq 2 ] || exit 1
evidence_sha="${artifact_values[0]}"
if ! ./scripts/validate-legacy-cutover-evidence.py \
  "$SOURCE_SHA" "$evidence_sha" "$OUTPUT_DIR"; then
  exit 1
fi
printf '%s\n' "${artifact_values[1]}" > "$OUTPUT_DIR/selection.json"
completed=1
echo "fresh exact-source cutover lease loaded: run=$run_id attempt=$attempt sha256=$evidence_sha"
