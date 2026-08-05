#!/usr/bin/env bash
set -euo pipefail

pr_number="${1:?pull request number required}"
repo="${2:?repository required}"
output="${3:?output path required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

[[ "$pr_number" =~ ^[1-9][0-9]*$ ]] || exit 64
[ "$repo" = "woosungchoi/fpm-alpine" ] || exit 64

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
pr_json="$workdir/pr.json"
changed_files="$workdir/changed-files.json"
base_json="$workdir/base.json"
head_json="$workdir/head.json"
classification="$workdir/classification.json"
checks_json="$workdir/checks.json"

gh api "repos/$repo/pulls/$pr_number" > "$pr_json"

readarray -t fields < <(python3 - "$pr_json" "$repo" <<'PY'
import base64, json, sys
from pathlib import Path
row = json.loads(Path(sys.argv[1]).read_text())
repo = sys.argv[2]
base = row.get("base") or {}
head = row.get("head") or {}
base_repo = (base.get("repo") or {}).get("full_name")
head_repo = (head.get("repo") or {}).get("full_name")
author = row.get("user") or {}
if (
    row.get("state") != "open"
    or row.get("draft") is not False
    or base.get("ref") != "main"
    or base_repo != repo
    or head_repo != repo
):
    raise SystemExit("pull request trust boundary mismatch")
base_sha = base.get("sha")
head_sha = head.get("sha")
if not isinstance(base_sha, str) or len(base_sha) != 40 or not isinstance(head_sha, str) or len(head_sha) != 40:
    raise SystemExit("invalid pull request commit identity")
author_id = author.get("id")
if type(author_id) is not int or author_id <= 0:
    raise SystemExit("invalid pull request author identity")
for value in (
    row["number"],
    row["html_url"],
    author.get("login", ""),
    author_id,
    base.get("ref", ""),
    base_repo,
    base_sha,
    head.get("ref", ""),
    head_repo,
    head_sha,
    row.get("mergeable_state", ""),
):
    print(value)
body = row.get("body", "")
if not isinstance(body, str):
    raise SystemExit("invalid pull request body")
print(base64.b64encode(body.encode()).decode())
PY
)
[ "${#fields[@]}" -eq 12 ] || exit 65
number="${fields[0]}"
url="${fields[1]}"
author="${fields[2]}"
author_id="${fields[3]}"
base_ref="${fields[4]}"
base_repo="${fields[5]}"
base_sha="${fields[6]}"
head_ref="${fields[7]}"
head_repo="${fields[8]}"
head_sha="${fields[9]}"
merge_state="${fields[10]}"
body="$(printf '%s' "${fields[11]}" | base64 --decode)"

[[ "$base_sha" =~ ^[0-9a-f]{40}$ && "$head_sha" =~ ^[0-9a-f]{40}$ ]] || exit 65
[ "$base_ref" = main ] || exit 66
[ "$base_repo" = "$repo" ] || exit 66
[ "$head_repo" = "$repo" ] || exit 66
case "$merge_state" in
  clean|blocked|unstable) ;;
  *) exit 66 ;;
esac

gh pr diff "$number" --repo "$repo" --name-only \
  | python3 -c 'import json,sys; print(json.dumps(sorted({line.strip() for line in sys.stdin if line.strip()})))' \
  > "$changed_files"

lock_endpoint="repos/$repo/git/ref/heads/automation/conveyor-lock"
if [[ "$head_ref" =~ ^automation/(base-8\.[2-5]|pecl-(imagick|redis|apcu))-[0-9a-f]{12}$ ]]; then
  candidate_key="${BASH_REMATCH[1]}"
  [[ "$body" == *"<!-- fpm-dependency-candidate:${candidate_key} -->"* ]] || exit 67
  lock_sha="$(gh api "$lock_endpoint" --jq '.object.sha')"
  [ "$lock_sha" = "$base_sha" ] || {
    echo "dependency conveyor lock is not bound to the PR base" >&2
    exit 67
  }

  readarray -t proposal_identity < <(BODY="$body" python3 - "$repo" "$candidate_key" <<'PY'
import os, re, sys
repo, key = sys.argv[1:]
body = os.environ["BODY"]
run_rows = re.findall(rf"^- Discovery run: https://github\.com/{re.escape(repo)}/actions/runs/([1-9][0-9]*)$", body, re.M)
attempt_rows = re.findall(r"^- Discovery attempt: `([1-9][0-9]*)`$", body, re.M)
hash_rows = re.findall(r"^- Proposal SHA-256: `([0-9a-f]{64})`$", body, re.M)
marker_rows = re.findall(r"<!-- fpm-dependency-proposal:([0-9a-f]{64}) -->", body)
if len(run_rows) != 1 or len(attempt_rows) != 1 or len(hash_rows) != 1 or marker_rows != hash_rows:
    raise SystemExit("dependency proposal body identity is missing or ambiguous")
print(run_rows[0])
print(attempt_rows[0])
print(hash_rows[0])
PY
  )
  [ "${#proposal_identity[@]}" -eq 3 ] || exit 67
  proposal_run_id="${proposal_identity[0]}"
  proposal_run_attempt="${proposal_identity[1]}"
  proposal_sha256="${proposal_identity[2]}"

  proposal_run="$workdir/proposal-run.json"
  gh api "repos/$repo/actions/runs/$proposal_run_id" > "$proposal_run"
  python3 - "$proposal_run" "$repo" "$base_sha" "$proposal_run_id" "$proposal_run_attempt" <<'PY'
import json, sys
from pathlib import Path
path, repo, source, run_id, attempt = sys.argv[1:]
row = json.loads(Path(path).read_text())
if (
    row.get("id") != int(run_id)
    or row.get("run_attempt") != int(attempt)
    or row.get("path") != ".github/workflows/dependency-update-pr.yml"
    or row.get("event") not in {"schedule", "workflow_dispatch"}
    or row.get("head_branch") != "main"
    or row.get("head_sha") != source
    or row.get("status") != "completed"
    or row.get("conclusion") != "success"
    or (row.get("repository") or {}).get("full_name") != repo
    or (row.get("head_repository") or {}).get("full_name") != repo
):
    raise SystemExit("dependency proposal run trust boundary mismatch")
PY

  proposal_dir="$workdir/proposal"
  gh run download "$proposal_run_id" \
    --repo "$repo" \
    --name "dependency-proposal-${proposal_run_id}-${proposal_run_attempt}" \
    --dir "$proposal_dir"
  proposal_file="$(python3 - "$proposal_dir" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
rows = [path for path in root.rglob("proposal.json") if path.is_file() and not path.is_symlink()]
if len(rows) != 1:
    raise SystemExit("dependency proposal artifact is missing or ambiguous")
print(rows[0])
PY
  )"

  gh api "repos/$repo/contents/build/versions.json?ref=$base_sha" --jq -r '.content' \
    | tr -d '\n' | base64 --decode > "$base_json"
  gh api "repos/$repo/contents/build/versions.json?ref=$head_sha" --jq -r '.content' \
    | tr -d '\n' | base64 --decode > "$head_json"
  candidate_report="$workdir/candidate-report.json"
  python3 scripts/validate-dependency-proposal.py \
    --proposal "$proposal_file" \
    --expected-hash "$proposal_sha256" \
    --source-sha "$base_sha" \
    --run-id "$proposal_run_id" \
    --run-attempt "$proposal_run_attempt" \
    --candidate-key "$candidate_key" \
    --head-ref "$head_ref" \
    --author-login "$author" \
    --author-id "$author_id" \
    --head-versions "$head_json" \
    --candidate-report "$candidate_report"
  expected_head="$workdir/expected-head.json"
  cp "$base_json" "$expected_head"
  python3 scripts/resolve-dependency-candidates.py \
    --versions "$expected_head" \
    --policy build/automation-policy.json \
    --apply-from "$candidate_report" \
    --apply-key "$candidate_key" \
    --apply-output "$expected_head"
  cmp "$expected_head" "$head_json"
elif [[ "$head_ref" =~ ^dependabot/github_actions/[A-Za-z0-9._/-]+$ ]]; then
  [ "$author" = dependabot ] || exit 67
  lock_error="$workdir/lock-error"
  if gh api "$lock_endpoint" >/dev/null 2>"$lock_error"; then
    echo "Dependabot merge is blocked while the dependency conveyor is active" >&2
    exit 67
  fi
  grep -Fq 'HTTP 404' "$lock_error" || {
    cat "$lock_error" >&2
    exit 67
  }
else
  exit 67
fi

python3 - "$changed_files" "$head_ref" <<'PY'
import json, re, sys
from pathlib import Path
files = json.loads(Path(sys.argv[1]).read_text())
head_ref = sys.argv[2]
if head_ref.startswith("dependabot/github_actions/"):
    allowed = {".github/dependabot.yml"}
    if not files or any(not (path.startswith(".github/workflows/") or path in allowed) for path in files):
        raise SystemExit("Dependabot PR changes blocked files")
else:
    if files != ["build/versions.json"]:
        raise SystemExit("dependency updater PR must change only build/versions.json")
PY

if [ ! -s "$base_json" ]; then
  gh api "repos/$repo/contents/build/versions.json?ref=$base_sha" --jq -r '.content' \
    | tr -d '\n' | base64 --decode > "$base_json"
fi
if [ ! -s "$head_json" ]; then
  gh api "repos/$repo/contents/build/versions.json?ref=$head_sha" --jq -r '.content' \
    | tr -d '\n' | base64 --decode > "$head_json"
fi
python3 scripts/classify-dependency-change.py \
  --base-json "$base_json" \
  --head-json "$head_json" \
  --policy build/automation-policy.json \
  --changed-files "$changed_files" \
  --output "$classification"
python3 - "$classification" "$head_ref" <<'PY'
import json, sys
from pathlib import Path
row = json.loads(Path(sys.argv[1]).read_text())
head_ref = sys.argv[2]
if row.get("eligible") is not True:
    raise SystemExit("classifier rejected PR")
if head_ref.startswith("dependabot/github_actions/") and row.get("class") != "actions-patch":
    raise SystemExit("Dependabot PR is not an Actions-only patch")
if head_ref.startswith("automation/") and row.get("class") not in {"base-same-minor", "pecl-patch"}:
    raise SystemExit("dependency updater PR class is not allowed")
PY

gh api "repos/$repo/commits/$head_sha/check-runs?per_page=100" \
  -H 'Accept: application/vnd.github+json' > "$checks_json"
python3 - "$checks_json" <<'PY'
import json, sys
from pathlib import Path
rows = json.loads(Path(sys.argv[1]).read_text()).get("check_runs", [])
matches = [
    row for row in rows
    if row.get("name") == "docker-smoke"
    and (row.get("app") or {}).get("id") == 15368
    and row.get("status") == "completed"
    and row.get("conclusion") == "success"
]
if len(matches) != 1:
    raise SystemExit("exact required check from GitHub Actions App 15368 is missing")
PY

printf '%s\t%s\n' "$number" "$head_sha" > "$output"
echo "eligible auto-merge PR: $url ($head_sha)"
