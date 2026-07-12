#!/usr/bin/env bash
set -euo pipefail

source_sha="${1:?source SHA required}"
eligibility_file="${2:?eligibility JSON required}"
correlation_prefix="${3:?correlation prefix required}"
output="${4:?output JSON required}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid source SHA" >&2; exit 64; }
[[ "$correlation_prefix" =~ ^auto-[0-9a-f]{12}-[1-9][0-9]*$ ]] || { echo "invalid correlation prefix" >&2; exit 64; }
[ "$(git rev-parse HEAD)" = "$source_sha" ] || { echo "checkout/source mismatch" >&2; exit 65; }

readarray -t active_minors < <(python3 -c 'import json; print("\n".join(json.load(open("build/automation-policy.json"))["lifecycle"]))')
[ "${#active_minors[@]}" -eq 4 ] || { echo "active minor matrix must contain four rows" >&2; exit 65; }

wait_for_run() {
  local correlation="$1"
  local attempt=0
  local run_id=""
  while [ "$attempt" -lt 120 ]; do
    run_id="$(gh api "repos/$repo/actions/workflows/publish.yml/runs?event=workflow_dispatch&branch=main&per_page=100" \
      --jq "[.workflow_runs[] | select(.display_title == \"publish-canary-${correlation}\" and .head_sha == \"${source_sha}\")][0].id // empty")"
    if [[ "$run_id" =~ ^[1-9][0-9]*$ ]]; then
      printf '%s\n' "$run_id"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 5
  done
  echo "timed out waiting for correlated publish run" >&2
  return 1
}

validate_run() {
  local run_id="$1"
  local destination="$2"
  local run_json
  local run_file
  run_file="$(mktemp)"
  run_json="$(gh api "repos/$repo/actions/runs/$run_id")"
  printf '%s' "$run_json" > "$run_file"
  python3 - "$source_sha" "$run_file" <<'PY'
import json, sys
source_sha, run_file = sys.argv[1:]
row = json.load(open(run_file))
required = {
    "event": "workflow_dispatch",
    "head_branch": "main",
    "head_sha": source_sha,
    "status": "completed",
    "conclusion": "success",
}
for key, expected in required.items():
    if row.get(key) != expected:
        raise SystemExit(f"run contract mismatch for {key}: {row.get(key)!r}")
if type(row.get("run_attempt")) is not int or row["run_attempt"] < 1:
    raise SystemExit("invalid run attempt")
PY
  local run_attempt
  run_attempt="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_attempt"])' <<< "$run_json")"
  mkdir -p "$destination"
  for minor in "${active_minors[@]}"; do
    artifact="publisher-canary-${minor}-${run_id}-${run_attempt}"
    gh run download "$run_id" --repo "$repo" --name "$artifact" --dir "$destination/$minor" >&2
    patch="$(python3 -c 'import json,sys; print(json.load(open("build/versions.json"))["versions"][sys.argv[1]]["patch"])' "$minor")"
    ./scripts/validate-canary-metadata.py "$destination/$minor" "$source_sha" "$minor" "$patch" "$run_id" "$run_attempt" >&2
  done
  printf '%s\t%s\t%s\n' \
    "$run_id" \
    "$run_attempt" \
    "$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_number"])' <<< "$run_json")"
  rm -f "$run_file"
}

mkdir -p "$(dirname "$output")" auto-canary-evidence
runs=()
for index in 1 2; do
  correlation="${correlation_prefix}-${index}"
  existing="$(gh api "repos/$repo/actions/workflows/publish.yml/runs?event=workflow_dispatch&branch=main&per_page=100" \
    --jq "[.workflow_runs[] | select(.display_title == \"publish-canary-${correlation}\")][0].id // empty")"
  [ -z "$existing" ] || { echo "correlation already exists: $correlation" >&2; exit 66; }
  gh workflow run publish.yml --repo "$repo" --ref main \
    -f channel=canary \
    -f source_sha="$source_sha" \
    -f correlation_id="$correlation"
  run_id="$(wait_for_run "$correlation")"
  timeout 4h gh run watch "$run_id" --repo "$repo" --exit-status
  runs+=("$(validate_run "$run_id" "auto-canary-evidence/$index")")
done

IFS=$'\t' read -r first_id first_attempt first_number <<< "${runs[0]}"
IFS=$'\t' read -r second_id second_attempt second_number <<< "${runs[1]}"
[ "$second_number" -eq $((first_number + 1)) ] || {
  echo "canary runs are not consecutive publish run numbers" >&2
  exit 67
}
python3 - "$eligibility_file" "$output" "$source_sha" \
  "$first_id" "$first_attempt" "$first_number" \
  "$second_id" "$second_attempt" "$second_number" <<'PY'
import json, sys
eligibility_path, output, source_sha, first_id, first_attempt, first_number, second_id, second_attempt, second_number = sys.argv[1:]
eligibility = json.load(open(eligibility_path))
if eligibility.get("eligible") is not True or eligibility.get("sourceCommit") != source_sha:
    raise SystemExit("eligibility evidence mismatch")
data = {
    "schemaVersion": 1,
    "sourceCommit": source_sha,
    "affectedMinors": eligibility["affectedMinors"],
    "firstCanary": {"runId": int(first_id), "runAttempt": int(first_attempt), "runNumber": int(first_number)},
    "secondCanary": {"runId": int(second_id), "runAttempt": int(second_attempt), "runNumber": int(second_number)},
    "productionAuthorized": False,
}
open(output, "w").write(json.dumps(data, indent=2) + "\n")
PY
printf 'auto_canary=PASS source=%s first=%s second=%s production_authorized=false\n' "$source_sha" "$first_id" "$second_id"
