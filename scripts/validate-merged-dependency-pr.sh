#!/usr/bin/env bash
set -euo pipefail

source_sha="${1:?source SHA required}"
eligibility_file="${2:?eligibility JSON required}"
output="${3:?output JSON required}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid source SHA" >&2; exit 64; }

eligibility_source="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sourceCommit"])' "$eligibility_file")"
eligibility_flag="$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["eligible"]).lower())' "$eligibility_file")"
[ "$eligibility_source" = "$source_sha" ] && [ "$eligibility_flag" = true ] || {
  echo "eligibility evidence is not bound to this eligible source" >&2
  exit 65
}

pulls="$(gh api -H 'Accept: application/vnd.github+json' "repos/$repo/commits/$source_sha/pulls")"
pulls_file="$(mktemp)"
checks_file="$(mktemp)"
trap 'rm -f "$pulls_file" "$checks_file"' EXIT
printf '%s' "$pulls" > "$pulls_file"
python3 - "$source_sha" "$output" "$pulls_file" <<'PY'
import json, re, sys
source_sha, output, pulls_file = sys.argv[1:]
rows = json.load(open(pulls_file))
valid = [row for row in rows if row.get("merged_at") and row.get("merge_commit_sha") == source_sha]
if len(valid) != 1:
    raise SystemExit("source commit must bind to exactly one merged PR")
row = valid[0]
head = row.get("head") or {}
repo = head.get("repo") or {}
author = (row.get("user") or {}).get("login", "")
branch = head.get("ref", "")
if repo.get("full_name") != (row.get("base", {}).get("repo") or {}).get("full_name"):
    raise SystemExit("merged dependency PR must be same-repository")
if not re.fullmatch(r"automation/(?:base-8\.[2-5]|pecl-(?:imagick|redis|apcu))-[0-9a-f]{12}", branch):
    raise SystemExit("merged PR branch is not an updater branch")
if author == "dependabot[bot]" or not re.fullmatch(r"[A-Za-z0-9_.-]+\[bot\]", author):
    raise SystemExit("merged PR author is not an updater bot")
head_sha = head.get("sha", "")
if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
    raise SystemExit("merged PR head SHA is invalid")
evidence = {
    "schemaVersion": 1,
    "sourceCommit": source_sha,
    "pullRequest": row["number"],
    "pullRequestHeadSha": head_sha,
    "author": author,
    "headRef": branch,
}
open(output, "w").write(json.dumps(evidence, indent=2) + "\n")
print(head_sha)
PY
head_sha="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pullRequestHeadSha"])' "$output")"
checks="$(gh api -H 'Accept: application/vnd.github+json' "repos/$repo/commits/$head_sha/check-runs?check_name=docker-smoke&filter=latest&per_page=100")"
printf '%s' "$checks" > "$checks_file"
python3 - "$head_sha" "$checks_file" <<'PY'
import json, sys
head_sha, checks_file = sys.argv[1:]
rows = json.load(open(checks_file)).get("check_runs", [])
valid = [row for row in rows if row.get("name") == "docker-smoke" and row.get("head_sha") == head_sha and (row.get("app") or {}).get("id") == 15368 and row.get("status") == "completed" and row.get("conclusion") == "success"]
if len(valid) != 1:
    raise SystemExit("exact app-bound docker-smoke success not found for merged PR head")
PY
printf 'merged_dependency_pr=verified source=%s head=%s\n' "$source_sha" "$head_sha"
