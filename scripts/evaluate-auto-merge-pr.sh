#!/usr/bin/env bash
set -euo pipefail

pr="${1:?PR number required}"
repo="${2:?repository required}"
output="${3:?output path required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
[[ "$pr" =~ ^[1-9][0-9]*$ ]] || { echo "invalid PR number" >&2; exit 64; }
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "invalid repository" >&2; exit 64; }

reject() {
  echo "auto_merge_rejected pr=${pr} reason=$*" >&2
  exit 2
}

metadata="$(gh api "repos/$repo/pulls/$pr")"
readarray -t fields < <(python3 -c '
import base64, json, sys
row=json.load(sys.stdin)
print(row.get("state", ""))
print(str(row.get("draft", "")).lower())
print(row.get("base", {}).get("ref", ""))
print(row.get("base", {}).get("sha", ""))
print(row.get("head", {}).get("ref", ""))
print(row.get("head", {}).get("sha", ""))
print(row.get("head", {}).get("repo", {}).get("full_name", ""))
print(row.get("user", {}).get("login", ""))
print(base64.b64encode((row.get("body") or "").encode()).decode())
' <<< "$metadata")
[ "${#fields[@]}" -ge 9 ] || reject "incomplete PR metadata"
state="${fields[0]}"
draft="${fields[1]}"
base_ref="${fields[2]}"
base_sha="${fields[3]}"
head_ref="${fields[4]}"
head_sha="${fields[5]}"
head_repo="${fields[6]}"
author="${fields[7]}"
body="$(printf '%s' "${fields[8]}" | base64 -d)"
[ "$state" = open ] || reject "PR is not open"
[ "$draft" = false ] || reject "PR is draft"
[ "$base_ref" = main ] || reject "base is not main"
[ "$head_repo" = "$repo" ] || reject "head repository mismatch"
[[ "$base_sha" =~ ^[0-9a-f]{40}$ && "$head_sha" =~ ^[0-9a-f]{40}$ ]] || reject "invalid base/head SHA"

git fetch --no-tags origin "pull/${pr}/head:refs/remotes/automation-pr/${pr}"
[ "$(git rev-parse "refs/remotes/automation-pr/${pr}")" = "$head_sha" ] || reject "fetched head SHA mismatch"
git cat-file -e "${base_sha}^{commit}"

changed="$(git diff --name-only "$base_sha" "$head_sha")"
[ -n "$changed" ] || reject "empty diff"
if [[ "$head_ref" =~ ^automation/(base-8\.[2-5]|pecl-(imagick|redis|apcu))-[0-9a-f]{12}$ ]]; then
  candidate_key="${BASH_REMATCH[1]}"
  [ "$author" != "dependabot[bot]" ] && [[ "$author" =~ \[bot\]$ ]] || reject "automation author mismatch"
  [ "$changed" = build/versions.json ] || reject "automation PR changed blocked files"
  grep -Fq "<!-- fpm-dependency-candidate:${candidate_key} -->" <<< "$body" || reject "candidate body marker mismatch"
  base_file="$(mktemp)"
  head_file="$(mktemp)"
  changed_file="$(mktemp)"
  result_file="$(mktemp)"
  trap 'rm -f "$base_file" "$head_file" "$changed_file" "$result_file"' EXIT
  git show "${base_sha}:build/versions.json" > "$base_file"
  git show "${head_sha}:build/versions.json" > "$head_file"
  python3 -c 'import json,sys; open(sys.argv[1],"w").write(json.dumps(["build/versions.json"])+"\n")' "$changed_file"
  python3 scripts/classify-dependency-change.py \
    --base-json "$base_file" \
    --head-json "$head_file" \
    --policy build/automation-policy.json \
    --changed-files "$changed_file" \
    --output "$result_file"
elif [[ "$head_ref" =~ ^dependabot/github_actions/ ]] && [ "$author" = "dependabot[bot]" ]; then
  python3 scripts/verify-action-update.py --base-sha "$base_sha" --head-sha "$head_sha"
else
  reject "branch or author is not eligible"
fi

checks="$(gh api -H 'Accept: application/vnd.github+json' "repos/$repo/commits/$head_sha/check-runs?check_name=docker-smoke&filter=latest&per_page=100")"
python3 -c '
import json, sys
row=json.load(sys.stdin)
valid=[c for c in row.get("check_runs", []) if c.get("name")=="docker-smoke" and c.get("head_sha")==sys.argv[1] and c.get("app",{}).get("id")==15368 and c.get("status")=="completed" and c.get("conclusion")=="success"]
if len(valid)!=1:
    raise SystemExit("exact app-bound docker-smoke success not found")
' "$head_sha" <<< "$checks" || reject "required docker-smoke evidence missing"

printf '%s\t%s\n' "$pr" "$head_sha" >> "$output"
printf 'auto_merge_eligible pr=%s head_sha=%s\n' "$pr" "$head_sha"
