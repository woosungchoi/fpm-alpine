#!/usr/bin/env bash
set -euo pipefail

BASE_BRANCH="${BRANCH_SYNC_BASE:-8.5}"
TARGETS="${BRANCH_SYNC_TARGETS:-8.0 8.1 8.2 8.3 8.4}"
OUTPUT_DIR="${BRANCH_SYNC_OUTPUT_DIR:-branch-sync-plans}"
DRY_RUN="${DRY_RUN:-${BRANCH_SYNC_DRY_RUN:-1}}"
LABELS="${BRANCH_SYNC_PR_LABELS:-maintenance,branch-sync,safe-sync}"
LABELS_DISPLAY="$(printf '%s' "$LABELS" | sed 's/,/, /g')"
BRANCH_PREFIX="${BRANCH_SYNC_BRANCH_PREFIX:-sync/branch-drift}"
DISPATCH_WORKFLOW="${BRANCH_SYNC_DISPATCH_WORKFLOW:-}"
ENABLE_AUTO_MERGE="${BRANCH_SYNC_ENABLE_AUTO_MERGE:-0}"
AUTO_MERGE_SUBJECT="${BRANCH_SYNC_AUTO_MERGE_SUBJECT:-ci: sync safe branch guardrails}"
AUTO_MERGE_BODY="${BRANCH_SYNC_AUTO_MERGE_BODY:-Automated safe branch-sync merge after required checks pass.}"
REPO="${GITHUB_REPOSITORY:-woosungchoi/fpm-alpine}"

usage() {
  cat <<'EOF'
usage: scripts/create-branch-sync-prs.sh

Environment:
  DRY_RUN=1                         # default; set 0 to push/create PRs
  BRANCH_SYNC_BASE=8.5
  BRANCH_SYNC_TARGETS="8.0 8.1 8.2 8.3 8.4"
  BRANCH_SYNC_OUTPUT_DIR=branch-sync-plans
  BRANCH_SYNC_PR_LABELS="maintenance,branch-sync,safe-sync"

Creates or summarizes safe branch-drift sync PRs. Dockerfile/hooks/publish files
are never copied by this script; it only uses filesToSync from plan JSON.
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

mkdir -p "$OUTPUT_DIR"
if [ ! -f "$OUTPUT_DIR/summary.md" ]; then
  BRANCH_SYNC_BASE="$BASE_BRANCH" BRANCH_SYNC_TARGETS="$TARGETS" BRANCH_SYNC_OUTPUT_DIR="$OUTPUT_DIR" ./scripts/plan-branch-sync.sh >/dev/null
fi

prs_md="$OUTPUT_DIR/prs.md"
cat > "$prs_md" <<EOF
# fpm-alpine branch sync PR plan

- Base branch: $BASE_BRANCH
- Mode: safe-files-only
- Dry run: $DRY_RUN
- Labels: $LABELS_DISPLAY
- Safety: Docker Hub hooks are unchanged. Dockerfile and publish-sensitive files are not synced automatically.

EOF

create_labels() {
  [ "$DRY_RUN" = "0" ] || return 0
  IFS=',' read -r -a label_array <<< "$LABELS"
  for label in "${label_array[@]}"; do
    label="${label// /}"
    [ -n "$label" ] || continue
    gh label create "$label" --repo "$REPO" --color "ededed" --description "branch sync automation" >/dev/null 2>&1 || true
  done
}

render_pr_body() {
  local plan_json="$1"
  python3 - "$plan_json" "$LABELS" <<'PY'
import json
import sys
from pathlib import Path
plan = json.loads(Path(sys.argv[1]).read_text())
labels = sys.argv[2].replace(',', ', ')
print(f"""## Summary

This auto-generated PR syncs safe branch-drift files from `{plan['baseBranch']}` to `{plan['targetBranch']}`.

## Synced files
""")
if plan["filesToSync"]:
    for item in plan["filesToSync"]:
        print(f"- `{item}`")
else:
    print("- No safe drift detected.")
print("\n## Blocked/manual files\n")
if plan["blockedFiles"]:
    for item in plan["blockedFiles"]:
        print(f"- `{item['path']}` — {item['reason']}")
else:
    print("- No blocked drift detected.")
print(f"""
## Safety

- Docker Hub hooks are unchanged.
- Dockerfile is not synced automatically.
- Required check: `docker-smoke`.
- Labels: {labels}

## Manual checklist

- [ ] Confirm synced files are limited to workflow/script/docs/test guardrails.
- [ ] Confirm no Dockerfile, hooks, or publish behavior changed.
- [ ] Wait for `docker-smoke` to pass on this target branch PR.
""")
PY
}

create_labels

is_blocked_path() {
  local path="$1"
  case "$path" in
    Dockerfile|.dockerignore|hooks|hooks/*|*.secret|*.pem|*.key|branch-sync-plans|branch-sync-plans/*)
      return 0
      ;;
  esac
  return 1
}

validate_pr_for_auto_merge() {
  local pr_ref="$1"
  local expected_target="$2"
  local expected_head="$3"
  local allowed_files="$4"
  local pr_json
  pr_json="$(mktemp)"
  gh pr view "$pr_ref" --repo "$REPO" --json number,baseRefName,headRefName,files,labels,url > "$pr_json"
  python3 - "$pr_json" "$expected_target" "$expected_head" "$LABELS" "$allowed_files" <<'PY'
import json
import sys
from pathlib import Path

pr_path, expected_target, expected_head, labels_csv, allowed_path = sys.argv[1:]
pr = json.loads(Path(pr_path).read_text())
required_labels = {item.strip() for item in labels_csv.split(',') if item.strip()}
actual_labels = {item.get('name') for item in pr.get('labels', [])}
allowed_files = {line.strip() for line in Path(allowed_path).read_text().splitlines() if line.strip()}
changed_files = [item['path'] for item in pr.get('files', [])]
blocked_exact = {'Dockerfile', '.dockerignore', 'hooks', 'branch-sync-plans'}
blocked_suffixes = ('.secret', '.pem', '.key')

errors = []
if pr.get('baseRefName') != expected_target:
    errors.append(f"unexpected base {pr.get('baseRefName')} != {expected_target}")
if pr.get('headRefName') != expected_head:
    errors.append(f"unexpected head {pr.get('headRefName')} != {expected_head}")
missing = required_labels - actual_labels
if missing:
    errors.append(f"missing labels: {sorted(missing)}")
if not changed_files:
    errors.append('no changed files')
for path in changed_files:
    if path in blocked_exact or path.startswith('hooks/') or path.startswith('branch-sync-plans/') or path.endswith(blocked_suffixes):
        errors.append(f"blocked path changed: {path}")
    if path not in allowed_files:
        errors.append(f"path is not in this safe-sync plan: {path}")
if errors:
    raise SystemExit('; '.join(errors))
print(pr['number'])
PY
  rm -f "$pr_json"
}

enable_auto_merge() {
  local pr_ref="$1"
  local expected_target="$2"
  local expected_head="$3"
  local allowed_files="$4"
  [ "$ENABLE_AUTO_MERGE" = "1" ] || return 0

  local pr_number
  pr_number="$(validate_pr_for_auto_merge "$pr_ref" "$expected_target" "$expected_head" "$allowed_files")"
  local output
  if ! output="$(gh pr merge "$pr_number" --repo "$REPO" --squash --auto --subject "$AUTO_MERGE_SUBJECT" --body "$AUTO_MERGE_BODY" 2>&1)"; then
    case "$output" in
      *already*auto*merge*|*Auto-merge*already*)
        echo "Auto-merge already enabled for PR #$pr_number" >> "$prs_md"
        ;;
      *)
        printf '%s\n' "$output" >&2
        return 1
        ;;
    esac
  else
    echo "Enabled auto-merge for PR #$pr_number" >> "$prs_md"
  fi
}

for target in $TARGETS; do
  plan_json="$OUTPUT_DIR/$target.json"
  if [ ! -f "$plan_json" ]; then
    echo "missing plan $plan_json; run scripts/plan-branch-sync.sh first" >&2
    exit 1
  fi

  has_safe_changes="$(python3 -c 'import json,sys; print("1" if json.load(open(sys.argv[1])).get("filesToSync") else "0")' "$plan_json")"
  branch_name="$BRANCH_PREFIX-$target"
  body_file="$OUTPUT_DIR/pr-body-$target.md"
  render_pr_body "$plan_json" > "$body_file"

  {
    echo "## Target $target"
    echo ""
    echo "- Branch: $branch_name"
    echo '- Required check: `docker-smoke`'
    echo "- Labels: $LABELS_DISPLAY"
    echo "- Docker Hub hooks are unchanged."
    echo ""
    echo "### Synced files"
    echo ""
    python3 - "$plan_json" <<'PY'
import json, sys
plan=json.load(open(sys.argv[1]))
if not plan["filesToSync"]:
    print("- No safe drift detected; no PR needed.")
else:
    for item in plan["filesToSync"]:
        print(f"- `{item}`")
PY
    echo ""
    echo "### Blocked/manual files"
    echo ""
    python3 - "$plan_json" <<'PY'
import json, sys
plan=json.load(open(sys.argv[1]))
if not plan["blockedFiles"]:
    print("- No blocked drift detected.")
else:
    for item in plan["blockedFiles"]:
        print(f"- `{item['path']}` — {item['reason']}")
PY
    echo ""
  } >> "$prs_md"

  if [ "$has_safe_changes" != "1" ]; then
    continue
  fi
  if [ "$DRY_RUN" != "0" ]; then
    continue
  fi

  current_branch="$(git branch --show-current)"
  cleanup() { git checkout "$current_branch" >/dev/null 2>&1 || true; }
  trap cleanup RETURN

  git checkout -B "$branch_name" "origin/$target"
  python3 - "$plan_json" <<'PY' > "$OUTPUT_DIR/files-$target.txt"
import json, sys
plan=json.load(open(sys.argv[1]))
for item in plan["filesToSync"]:
    print(item)
PY
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    if is_blocked_path "$path"; then
      echo "Refusing to sync blocked path from plan: $path" >&2
      exit 65
    fi
    mkdir -p "$(dirname "$path")"
    git checkout "origin/$BASE_BRANCH" -- "$path"
  done < "$OUTPUT_DIR/files-$target.txt"

  if git diff --quiet && git diff --cached --quiet; then
    echo "No changes after safe sync for $target" >> "$prs_md"
    git checkout "$current_branch" >/dev/null
    continue
  fi

  while IFS= read -r path; do
    [ -n "$path" ] || continue
    git add -- "$path"
  done < "$OUTPUT_DIR/files-$target.txt"
  git commit -m "ci: sync safe branch guardrails from $BASE_BRANCH"
  git push --force-with-lease origin "HEAD:$branch_name"

  existing_pr="$(gh pr list --repo "$REPO" --head "$branch_name" --base "$target" --state open --json number --jq '.[0].number // empty')"
  pr_ref=""
  if [ -n "$existing_pr" ]; then
    gh pr comment "$existing_pr" --repo "$REPO" --body-file "$body_file" >/dev/null || true
    pr_ref="$existing_pr"
    echo "Updated existing PR #$existing_pr for $target" >> "$prs_md"
  else
    pr_url="$(gh pr create --repo "$REPO" --base "$target" --head "$branch_name" --title "ci: sync safe branch guardrails to $target" --body-file "$body_file")"
    IFS=',' read -r -a label_array <<< "$LABELS"
    for label in "${label_array[@]}"; do
      label="${label// /}"
      [ -n "$label" ] || continue
      gh pr edit "$pr_url" --repo "$REPO" --add-label "$label" >/dev/null 2>&1 || true
    done
    pr_ref="$pr_url"
    echo "Created PR: $pr_url" >> "$prs_md"
  fi

  enable_auto_merge "$pr_ref" "$target" "$branch_name" "$OUTPUT_DIR/files-$target.txt"

  if [ -n "$DISPATCH_WORKFLOW" ]; then
    gh workflow run "$DISPATCH_WORKFLOW" --repo "$REPO" --ref "$branch_name" >/dev/null
    echo "Dispatched validation workflow $DISPATCH_WORKFLOW for $branch_name" >> "$prs_md"
  fi

  git checkout "$current_branch" >/dev/null
  trap - RETURN
done

cat "$prs_md"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$prs_md" >> "$GITHUB_STEP_SUMMARY"
fi
