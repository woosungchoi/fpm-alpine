#!/usr/bin/env bash
set -euo pipefail

candidate_file="${1:?candidate JSON path required}"
candidate_key="${2:?candidate key required}"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
source_sha="${GITHUB_SHA:?GITHUB_SHA is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

[[ "$candidate_key" =~ ^(base-8\.[2-5]|pecl-(imagick|redis|apcu))$ ]] || {
  echo "invalid candidate key: $candidate_key" >&2
  exit 64
}
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "GITHUB_SHA must be 40 lowercase hex characters" >&2
  exit 64
}
[ "$(git rev-parse HEAD)" = "$source_sha" ] || {
  echo "checked out source does not match GITHUB_SHA" >&2
  exit 65
}
[ -z "$(git status --short)" ] || {
  echo "worktree must be clean before applying a candidate" >&2
  exit 65
}

readarray -t metadata < <(python3 - "$candidate_file" "$candidate_key" "$source_sha" <<'PY'
import hashlib, json, re, sys
from pathlib import Path
path, key, source_sha = sys.argv[1:]
data = json.loads(Path(path).read_text())
if data.get("schemaVersion") != 1 or data.get("sourceCommit") != source_sha:
    raise SystemExit("candidate report is not bound to this source commit")
rows = [row for row in data.get("candidates", []) if row.get("key") == key]
if len(rows) != 1 or rows[0].get("eligible") is not True:
    raise SystemExit("candidate is not eligible or is ambiguous")
row = rows[0]
canonical = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
suffix = hashlib.sha256(canonical).hexdigest()[:12]
new = row.get("new") or {}
version = new.get("patch") or new.get("version") or suffix
if not re.fullmatch(r"[0-9A-Za-z._-]+", str(version)):
    raise SystemExit("candidate version is unsafe for branch/title")
print(suffix)
print(version)
print(row.get("class", "unknown"))
print(",".join(row.get("affectedMinors", [])))
PY
)
[ "${#metadata[@]}" -eq 4 ] || {
  echo "candidate metadata extraction failed" >&2
  exit 65
}
suffix="${metadata[0]}"
version="${metadata[1]}"
update_class="${metadata[2]}"
affected="${metadata[3]}"
branch="automation/${candidate_key}-${suffix}"
title="chore(deps): update ${candidate_key} to ${version}"

existing_pr="$(gh pr list --repo "$repo" --state open --head "$branch" --json number --jq '.[0].number // empty')"
if [ -n "$existing_pr" ]; then
  echo "dependency PR already exists: #${existing_pr}"
  exit 0
fi
if git ls-remote --exit-code --heads origin "refs/heads/$branch" >/dev/null 2>&1; then
  echo "refusing to reuse an existing remote branch without an open PR: $branch" >&2
  exit 66
fi

base_file="$(mktemp)"
changed_files="$(mktemp)"
classification="$(mktemp)"
body_file="$(mktemp)"
trap 'rm -f "$base_file" "$changed_files" "$classification" "$body_file"' EXIT
git show HEAD:build/versions.json > "$base_file"
python3 scripts/resolve-dependency-candidates.py \
  --versions build/versions.json \
  --policy build/automation-policy.json \
  --apply-from "$candidate_file" \
  --apply-key "$candidate_key" \
  --apply-output build/versions.json
./scripts/validate-versions.py
python3 - "$changed_files" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps(["build/versions.json"]) + "\n")
PY
python3 scripts/classify-dependency-change.py \
  --base-json "$base_file" \
  --head-json build/versions.json \
  --policy build/automation-policy.json \
  --changed-files "$changed_files" \
  --output "$classification"
python3 - "$classification" "$candidate_key" <<'PY'
import json, sys
from pathlib import Path
row = json.loads(Path(sys.argv[1]).read_text())
if row.get("eligible") is not True:
    raise SystemExit("candidate is not eligible after application")
if not row.get("affectedMinors"):
    raise SystemExit("eligible candidate has no affected minors")
PY
[ "$(git diff --name-only)" = "build/versions.json" ] || {
  echo "candidate application changed a blocked file" >&2
  exit 67
}

git switch -c "$branch"
git config user.name "fpm-alpine dependency updater[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add build/versions.json
git commit -m "$title"
git push origin "HEAD:refs/heads/$branch"

python3 - "$body_file" "$candidate_key" "$update_class" "$affected" "$source_sha" "$GITHUB_SERVER_URL/$repo/actions/runs/$GITHUB_RUN_ID" <<'PY'
import sys
from pathlib import Path
path, key, update_class, affected, source_sha, run_url = sys.argv[1:]
Path(path).write_text(f"""Automated dependency-only update generated from trusted `main`.

- Candidate: `{key}`
- Class: `{update_class}`
- Affected minors: `{affected}`
- Source SHA: `{source_sha}`
- Discovery run: {run_url}

The PR is not merged directly. Native auto-merge may be enabled only after the trusted classifier accepts the exact diff; branch protection remains authoritative.

<!-- fpm-dependency-candidate:{key} -->
""")
PY

gh pr create \
  --repo "$repo" \
  --base main \
  --head "$branch" \
  --title "$title" \
  --body-file "$body_file"
