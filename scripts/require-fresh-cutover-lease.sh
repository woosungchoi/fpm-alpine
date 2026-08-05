#!/usr/bin/env bash
set -euo pipefail

source_sha="${1:?expected source SHA required}"
output_dir="${2:?output directory required}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "invalid fresh cutover lease source SHA" >&2
  exit 64
}
[ "$repo" = "woosungchoi/fpm-alpine" ] || {
  echo "unexpected repository for fresh cutover lease: $repo" >&2
  exit 64
}

workspace="$(mktemp -d)"
trap 'rm -rf "$workspace"' EXIT
runs_file="$workspace/runs.json"
selection_file="$workspace/selection.json"
artifact_dir="$workspace/artifact"

gh api \
  "repos/$repo/actions/workflows/legacy-cutover-lease.yml/runs?event=repository_dispatch&branch=main&per_page=100" \
  > "$runs_file"
python3 scripts/select-fresh-cutover-lease.py \
  --runs "$runs_file" \
  --source-sha "$source_sha" \
  --repository "$repo" \
  --output "$selection_file"

readarray -t selected < <(python3 - "$selection_file" <<'PY'
import json, sys
from pathlib import Path
row = json.loads(Path(sys.argv[1]).read_text())
run_id = row.get("runId")
attempt = row.get("runAttempt")
if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
    raise SystemExit("invalid selected cutover lease run")
print(run_id)
print(attempt)
PY
)
[ "${#selected[@]}" -eq 2 ] || {
  echo "fresh cutover lease selection failed" >&2
  exit 65
}
run_id="${selected[0]}"
run_attempt="${selected[1]}"
artifact_name="legacy-cutover-lease-${run_id}-${run_attempt}"

gh run download "$run_id" \
  --repo "$repo" \
  --name "$artifact_name" \
  --dir "$artifact_dir"

readarray -t files < <(python3 - "$artifact_dir" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
if not root.is_dir() or root.is_symlink():
    raise SystemExit("invalid cutover lease artifact directory")
for name in ("cutover-evidence.json", "cutover-evidence.sha256"):
    rows = [path for path in root.rglob(name) if path.is_file() and not path.is_symlink()]
    if len(rows) != 1:
        raise SystemExit(f"cutover lease artifact must contain exactly one {name}")
    print(rows[0])
PY
)
[ "${#files[@]}" -eq 2 ] || {
  echo "fresh cutover lease artifact file selection failed" >&2
  exit 65
}
evidence_file="${files[0]}"
hash_file="${files[1]}"
expected_hash="$(tr -d '\r\n' < "$hash_file")"
[[ "$expected_hash" =~ ^[0-9a-f]{64}$ ]] || {
  echo "invalid cutover lease artifact SHA-256" >&2
  exit 65
}
actual_hash="$(sha256sum "$evidence_file" | cut -d' ' -f1)"
[ "$actual_hash" = "$expected_hash" ] || {
  echo "cutover lease artifact hash mismatch" >&2
  exit 65
}

LEGACY_EVIDENCE_B64="$(python3 - "$evidence_file" <<'PY'
import base64, sys
from pathlib import Path
print(base64.b64encode(Path(sys.argv[1]).read_bytes()).decode())
PY
)"
export LEGACY_EVIDENCE_B64
./scripts/validate-legacy-cutover-evidence.py "$source_sha" "$expected_hash"

mkdir -p "$output_dir"
install -m 0644 "$evidence_file" "$output_dir/cutover-evidence.json"
install -m 0644 "$hash_file" "$output_dir/cutover-evidence.sha256"
python3 - "$output_dir/selection.json" "$source_sha" "$run_id" "$run_attempt" "$expected_hash" <<'PY'
import json, sys
from pathlib import Path
path, source, run_id, attempt, digest = sys.argv[1:]
Path(path).write_text(json.dumps({
    "schemaVersion": 1,
    "sourceSha": source,
    "runId": int(run_id),
    "runAttempt": int(attempt),
    "evidenceSha256": digest,
}, sort_keys=True, separators=(",", ":")) + "\n")
PY
