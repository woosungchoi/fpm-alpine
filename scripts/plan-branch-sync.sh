#!/usr/bin/env bash
set -euo pipefail

BASE_BRANCH="${BRANCH_SYNC_BASE:-8.5}"
TARGETS="${BRANCH_SYNC_TARGETS:-8.0 8.1 8.2 8.3 8.4}"
OUTPUT_DIR="${BRANCH_SYNC_OUTPUT_DIR:-branch-sync-plans}"
SAFE_FILES_FILE="${BRANCH_SYNC_SAFE_FILES_FILE:-docs/branch-sync-safe-files.txt}"
BLOCKED_FILES="${BRANCH_SYNC_BLOCKED_FILES:-Dockerfile hooks/build hooks/post_push hooks/push .dockerignore}"
MODE="safe-files-only"

usage() {
  cat <<'EOF'
usage: scripts/plan-branch-sync.sh

Environment:
  BRANCH_SYNC_BASE=8.5
  BRANCH_SYNC_TARGETS="8.0 8.1 8.2 8.3 8.4"
  BRANCH_SYNC_OUTPUT_DIR=branch-sync-plans
  BRANCH_SYNC_SAFE_FILES_FILE=docs/branch-sync-safe-files.txt

Creates JSON and Markdown plans for safe branch-drift sync PRs. This script is
report/planning only and never checks out branches, commits, pushes, or creates PRs.
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

mkdir -p "$OUTPUT_DIR"

ref_exists() {
  local ref="$1"
  git rev-parse --verify --quiet "$ref" >/dev/null
}

show_file() {
  local branch="$1"
  local path="$2"
  if ref_exists "origin/${branch}"; then
    git show "origin/${branch}:${path}" 2>/dev/null || true
  else
    git show "${branch}:${path}" 2>/dev/null || true
  fi
}

file_exists_in_branch() {
  local branch="$1"
  local path="$2"
  if ref_exists "origin/${branch}"; then
    git cat-file -e "origin/${branch}:${path}" 2>/dev/null
  else
    git cat-file -e "${branch}:${path}" 2>/dev/null
  fi
}

file_changed() {
  local target="$1"
  local path="$2"
  local base_tmp target_tmp
  base_tmp="$(mktemp)"
  target_tmp="$(mktemp)"
  show_file "$BASE_BRANCH" "$path" > "$base_tmp"
  show_file "$target" "$path" > "$target_tmp"
  if cmp -s "$base_tmp" "$target_tmp"; then
    rm -f "$base_tmp" "$target_tmp"
    return 1
  fi
  rm -f "$base_tmp" "$target_tmp"
  return 0
}

mapfile -t SAFE_FILES < <(grep -Ev '^[[:space:]]*(#|$)' "$SAFE_FILES_FILE")

is_blocked_path() {
  local path="$1"
  case "$path" in
    Dockerfile|.dockerignore|hooks|hooks/*|*.secret|*.pem|*.key)
      return 0
      ;;
  esac
  return 1
}

summary_md="$OUTPUT_DIR/summary.md"
cat > "$summary_md" <<EOF
# fpm-alpine branch sync plan

- Mode: $MODE
- Base branch: $BASE_BRANCH
- Targets: $(printf '%s ' $TARGETS)
- Safe file allowlist: $SAFE_FILES_FILE
- Note: Docker Hub hooks are unchanged; Dockerfile and publish-sensitive files require manual review.

EOF

for target in $TARGETS; do
  files_json="$(mktemp)"
  blocked_json="$(mktemp)"
  printf '[' > "$files_json"
  printf '[' > "$blocked_json"
  first_file=1
  first_blocked=1

  for path in "${SAFE_FILES[@]}"; do
    if is_blocked_path "$path"; then
      continue
    fi
    if ! file_exists_in_branch "$BASE_BRANCH" "$path"; then
      continue
    fi
    if file_changed "$target" "$path"; then
      if [ "$first_file" -eq 0 ]; then printf ',' >> "$files_json"; fi
      python3 -c 'import json,sys; print(json.dumps(sys.argv[1]), end="")' "$path" >> "$files_json"
      first_file=0
    fi
  done

  for path in $BLOCKED_FILES; do
    if file_changed "$target" "$path"; then
      if [ "$first_blocked" -eq 0 ]; then printf ',' >> "$blocked_json"; fi
      python3 -c 'import json,sys; print(json.dumps({"path": sys.argv[1], "reason": "manual review required"}), end="")' "$path" >> "$blocked_json"
      first_blocked=0
    fi
  done

  printf ']' >> "$files_json"
  printf ']' >> "$blocked_json"

  python3 - "$OUTPUT_DIR/$target.json" "$BASE_BRANCH" "$target" "$MODE" "$files_json" "$blocked_json" <<'PY'
import json
import sys
from pathlib import Path
out, base, target, mode, files_path, blocked_path = sys.argv[1:]
files = json.loads(Path(files_path).read_text())
blocked = json.loads(Path(blocked_path).read_text())
plan = {
    "baseBranch": base,
    "targetBranch": target,
    "mode": mode,
    "filesToSync": files,
    "blockedFiles": blocked,
    "hasSafeChanges": bool(files),
}
Path(out).write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
PY
  rm -f "$files_json" "$blocked_json"

  {
    echo "## Target $target"
    echo ""
    echo "### Safe files to sync"
    echo ""
    python3 - "$OUTPUT_DIR/$target.json" <<'PY'
import json, sys
from pathlib import Path
plan = json.loads(Path(sys.argv[1]).read_text())
items = plan["filesToSync"]
if not items:
    print("- No safe drift detected.")
else:
    for item in items:
        print(f"- `{item}`")
PY
    echo ""
    echo "### Blocked/manual files"
    echo ""
    python3 - "$OUTPUT_DIR/$target.json" <<'PY'
import json, sys
from pathlib import Path
plan = json.loads(Path(sys.argv[1]).read_text())
items = plan["blockedFiles"]
if not items:
    print("- No blocked drift detected.")
else:
    for item in items:
        print(f"- `{item['path']}` — {item['reason']}")
PY
    echo ""
  } >> "$summary_md"
done

cat "$summary_md"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$summary_md" >> "$GITHUB_STEP_SUMMARY"
fi
