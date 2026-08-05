#!/usr/bin/env bash
set -euo pipefail

source_sha="${1:?source SHA required}"
authorization_file="${2:?authorization JSON required}"
output="${3:?output JSON required}"
requested_minor="${4:-}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT is required}"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid source SHA" >&2; exit 64; }
[[ "$GITHUB_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]] || { echo "invalid controller run attempt" >&2; exit 64; }
[ -f "$authorization_file" ] || { echo "authorization evidence is missing" >&2; exit 64; }
[ "$(git rev-parse HEAD)" = "$source_sha" ] || { echo "checkout/source mismatch" >&2; exit 65; }

values="$(mktemp)"
trap 'rm -f "$values"' EXIT
python3 - "$authorization_file" "$output" "$source_sha" "$requested_minor" > "$values" <<'PY'
import json
import re
import sys
from pathlib import Path

authorization_path, output_path, source_sha, requested_minor = sys.argv[1:]
payload = json.load(open(authorization_path))
positive = lambda value: type(value) is int and value > 0
allowed = ["8.2", "8.3", "8.4", "8.5"]
if type(payload) is not dict or type(payload.get("schemaVersion")) is not int or payload.get("schemaVersion") != 1:
    raise SystemExit("invalid auto-production authorization schema")
if payload.get("sourceCommit") != source_sha or payload.get("productionAuthorized") is not True:
    raise SystemExit("auto-production authorization source mismatch")
if not positive(payload.get("upstreamRunId")) or not positive(payload.get("upstreamRunAttempt")):
    raise SystemExit("invalid upstream authorization identity")
affected = payload.get("affectedMinors")
if type(affected) is not list or not affected or affected != [minor for minor in allowed if minor in set(affected)] or len(set(affected)) != len(affected):
    raise SystemExit("invalid authorized minor set")
if requested_minor:
    if requested_minor not in affected:
        raise SystemExit("requested minor is outside auto-production authorization")
    selected = [requested_minor]
else:
    selected = affected
for label in ("priorCanary", "currentCanary"):
    row = payload.get(label)
    if type(row) is not dict or set(row) != {"runId", "runAttempt"} or not all(positive(row.get(key)) for key in row):
        raise SystemExit(f"invalid {label} identity")
if payload["priorCanary"]["runId"] == payload["currentCanary"]["runId"]:
    raise SystemExit("canary identities must be distinct")
report = {
    "schemaVersion": 1,
    "sourceCommit": source_sha,
    "upstreamRunId": payload["upstreamRunId"],
    "upstreamRunAttempt": payload["upstreamRunAttempt"],
    "authorizedMinors": affected,
    "affectedMinors": selected,
    "productionRuns": [],
}
Path(output_path).parent.mkdir(parents=True, exist_ok=True)
Path(output_path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
print(payload["upstreamRunId"])
print(payload["upstreamRunAttempt"])
print(payload["priorCanary"]["runId"])
print(payload["priorCanary"]["runAttempt"])
print(payload["currentCanary"]["runId"])
print(payload["currentCanary"]["runAttempt"])
for minor in selected:
    print(minor)
PY
mapfile -t fields < "$values"
[ "${#fields[@]}" -ge 7 ] || { echo "incomplete auto-production authorization" >&2; exit 65; }
upstream_run_id="${fields[0]}"
upstream_run_attempt="${fields[1]}"
prior_canary_id="${fields[2]}"
prior_canary_attempt="${fields[3]}"
current_canary_id="${fields[4]}"
current_canary_attempt="${fields[5]}"
affected_minors=("${fields[@]:6}")

update_report() {
  local minor="$1"
  local run_id="$2"
  local status="$3"
  local correlation="$4"
  python3 - "$output" "$minor" "$run_id" "$status" "$correlation" <<'PY'
import json
import sys
from pathlib import Path

path, minor, run_id, status, correlation = sys.argv[1:]
target = Path(path)
payload = json.loads(target.read_text())
rows = payload["productionRuns"]
matching = [row for row in rows if row.get("minor") == minor]
if len(matching) > 1:
    raise SystemExit("duplicate production report minor")
row = matching[0] if matching else {"minor": minor}
if not matching:
    rows.append(row)
row["runId"] = int(run_id)
row["status"] = status
row["correlation"] = correlation
temporary = target.with_suffix(".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
temporary.replace(target)
PY
}

current_main() {
  gh api "repos/$repo/git/ref/heads/main" --jq .object.sha
}

wait_for_run() {
  local correlation="$1"
  local attempt=0
  local run_id=""
  while [ "$attempt" -lt 120 ]; do
    run_id="$(gh api "repos/$repo/actions/workflows/publish.yml/runs?event=workflow_dispatch&branch=main&per_page=100" \
      --jq "[.workflow_runs[] | select(.display_title == \"publish-production-${correlation}\" and .head_sha == \"${source_sha}\")][0].id // empty")"
    if [[ "$run_id" =~ ^[1-9][0-9]*$ ]]; then
      printf '%s\n' "$run_id"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 5
  done
  echo "timed out waiting for correlated production run" >&2
  return 1
}

validate_run() {
  local run_id="$1"
  local correlation="$2"
  local run_file
  run_file="$(mktemp)"
  gh api "repos/$repo/actions/runs/$run_id" > "$run_file"
  local validation_status=0
  python3 - "$run_file" "$run_id" "$source_sha" "$correlation" <<'PY' || validation_status=$?
import json
import sys

run_file, expected_id, source_sha, correlation = sys.argv[1:]
row = json.load(open(run_file))
required = {
    "id": int(expected_id),
    "event": "workflow_dispatch",
    "head_branch": "main",
    "head_sha": source_sha,
    "path": ".github/workflows/publish.yml",
    "status": "completed",
    "conclusion": "success",
    "display_title": f"publish-production-{correlation}",
}
failed = [
    key
    for key, expected in required.items()
    if type(row.get(key)) is not type(expected) or row.get(key) != expected
]
if failed:
    raise SystemExit("production run contract mismatch: " + ", ".join(failed))
if type(row.get("run_attempt")) is not int or row["run_attempt"] < 1:
    raise SystemExit("invalid production run attempt")
PY
  rm -f "$run_file"
  return "$validation_status"
}

for minor in "${affected_minors[@]}"; do
  [ "$(current_main)" = "$source_sha" ] || {
    echo "trusted main moved before production dispatch for $minor" >&2
    exit 66
  }
  correlation="auto-prod-${source_sha:0:12}-${upstream_run_id}-${upstream_run_attempt}-${GITHUB_RUN_ATTEMPT}-${minor}"
  existing="$(gh api "repos/$repo/actions/workflows/publish.yml/runs?event=workflow_dispatch&branch=main&per_page=100" \
    --jq "[.workflow_runs[] | select(.display_title == \"publish-production-${correlation}\")][0].id // empty")"
  [ -z "$existing" ] || { echo "production correlation already exists: $correlation" >&2; exit 67; }
  update_report "$minor" 0 dispatching "$correlation"
  if ! gh workflow run publish.yml --repo "$repo" --ref main \
    -f channel=production \
    -f version="$minor" \
    -f source_sha="$source_sha" \
    -f correlation_id="$correlation" \
    -f canary_run_id="$current_canary_id" \
    -f canary_run_attempt="$current_canary_attempt" \
    -f prior_canary_run_id="$prior_canary_id" \
    -f prior_canary_run_attempt="$prior_canary_attempt" \
    -f legacy_publisher_disabled=true \
    -f auto_promotion_run_id="$upstream_run_id" \
    -f auto_promotion_run_attempt="$upstream_run_attempt"; then
    update_report "$minor" 0 failed "$correlation"
    exit 68
  fi
  if ! run_id="$(wait_for_run "$correlation")"; then
    update_report "$minor" 0 failed "$correlation"
    exit 69
  fi
  update_report "$minor" "$run_id" running "$correlation"
  watch_status=0
  timeout 4h gh run watch "$run_id" --repo "$repo" --exit-status || watch_status=$?
  if ! validate_run "$run_id" "$correlation"; then
    update_report "$minor" "$run_id" failed "$correlation"
    printf 'production run failed read-back validation for %s (watch exit %s)\n' "$minor" "$watch_status" >&2
    exit 71
  fi
  if [ "$watch_status" -ne 0 ]; then
    printf 'watcher exited %s but exact production run read-back succeeded for %s\n' "$watch_status" "$minor" >&2
  fi
  update_report "$minor" "$run_id" success "$correlation"
done

printf 'auto_production=PASS source=%s affected=%s\n' "$source_sha" "${affected_minors[*]}"
