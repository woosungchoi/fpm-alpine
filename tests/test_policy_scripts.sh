#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_file() {
  local path="$1"
  [ -f "$path" ] || fail "expected file $path to exist"
}

assert_executable() {
  local path="$1"
  [ -x "$path" ] || fail "expected $path to be executable"
}

assert_contains() {
  local path="$1"
  local needle="$2"
  grep -Fq "$needle" "$path" || fail "expected $path to contain: $needle"
}

assert_file docs/ci-operations.md
assert_contains docs/ci-operations.md "Required status check"
assert_contains docs/ci-operations.md "Docker Hub hooks remain the publish path"
assert_contains docs/ci-operations.md "Rollback"

assert_file docs/branch-drift-allowlist.tsv
assert_contains docs/branch-drift-allowlist.tsv "path"

assert_file scripts/branch-drift-report.sh
assert_executable scripts/branch-drift-report.sh
assert_file scripts/plan-branch-sync.sh
assert_executable scripts/plan-branch-sync.sh
assert_file scripts/create-branch-sync-prs.sh
assert_executable scripts/create-branch-sync-prs.sh
assert_file .github/workflows/branch-sync-pr.yml
assert_contains .github/workflows/branch-sync-pr.yml "actions/create-github-app-token@v2"
assert_contains .github/workflows/branch-sync-pr.yml "permission-pull-requests: write"
assert_contains .github/workflows/branch-sync-pr.yml "permission-actions: write"
assert_contains .github/workflows/branch-sync-pr.yml "BRANCH_SYNC_ENABLE_AUTO_MERGE: \"1\""
assert_contains .github/workflows/branch-sync-pr.yml "BRANCH_SYNC_DISPATCH_WORKFLOW: \"smoke-test.yml\""
assert_contains .github/workflows/branch-sync-pr.yml "actions/checkout@v6.0.2"
assert_contains .github/workflows/branch-sync-pr.yml "type: choice"
assert_contains .github/workflows/branch-sync-pr.yml "TARGET_BRANCH:"

branch_report_dir="$(mktemp -d)"
branch_sync_dir="$(mktemp -d)"
trap 'rm -rf "$branch_report_dir" "$branch_sync_dir"' EXIT
BRANCH_DRIFT_BRANCHES="8.5" BRANCH_DRIFT_REPORT_DIR="$branch_report_dir" ./scripts/branch-drift-report.sh
assert_file "$branch_report_dir/branch-drift.md"
assert_file "$branch_report_dir/branch-drift.json"
assert_contains "$branch_report_dir/branch-drift.md" "# fpm-alpine branch drift report"
assert_contains "$branch_report_dir/branch-drift.md" "report-only"
assert_contains "$branch_report_dir/branch-drift.md" "8.5"

BRANCH_SYNC_TARGETS="8.4" BRANCH_SYNC_OUTPUT_DIR="$branch_sync_dir" BRANCH_SYNC_DRY_RUN=1 ./scripts/plan-branch-sync.sh
assert_file "$branch_sync_dir/8.4.json"
assert_file "$branch_sync_dir/summary.md"
assert_contains "$branch_sync_dir/summary.md" "safe-files-only"
assert_contains "$branch_sync_dir/summary.md" "Blocked/manual files"
assert_contains "$branch_sync_dir/summary.md" "Dockerfile"
python3 - "$branch_sync_dir/8.4.json" <<'PY'
import json
import sys
from pathlib import Path
plan = json.loads(Path(sys.argv[1]).read_text())
assert plan["baseBranch"] == "8.5"
assert plan["targetBranch"] == "8.4"
assert plan["mode"] == "safe-files-only"
assert "Dockerfile" not in plan["filesToSync"]
assert any(item["path"] == "Dockerfile" for item in plan["blockedFiles"])
PY

BRANCH_SYNC_TARGETS="8.4" BRANCH_SYNC_OUTPUT_DIR="$branch_sync_dir" DRY_RUN=1 ./scripts/create-branch-sync-prs.sh
assert_file "$branch_sync_dir/prs.md"
assert_contains "$branch_sync_dir/prs.md" "Docker Hub hooks are unchanged"
assert_contains "$branch_sync_dir/prs.md" 'Required check: `docker-smoke`'
assert_contains "$branch_sync_dir/prs.md" "Blocked/manual files"
assert_contains "$branch_sync_dir/prs.md" "maintenance, branch-sync, safe-sync"

unsafe_allowlist="$branch_sync_dir/unsafe-safe-files.txt"
cat > "$unsafe_allowlist" <<'EOF'
Dockerfile
hooks/build
scripts/branch-drift-report.sh
EOF
unsafe_dir="$branch_sync_dir/unsafe"
BRANCH_SYNC_TARGETS="8.4" BRANCH_SYNC_OUTPUT_DIR="$unsafe_dir" BRANCH_SYNC_SAFE_FILES_FILE="$unsafe_allowlist" BRANCH_SYNC_DRY_RUN=1 ./scripts/plan-branch-sync.sh
python3 - "$unsafe_dir/8.4.json" <<'PY'
import json
import sys
from pathlib import Path
plan = json.loads(Path(sys.argv[1]).read_text())
assert "Dockerfile" not in plan["filesToSync"]
assert "hooks/build" not in plan["filesToSync"]
assert any(item["path"] == "Dockerfile" for item in plan["blockedFiles"])
PY
if grep -Fq "git add -A" scripts/create-branch-sync-prs.sh; then
  fail "create-branch-sync-prs.sh must stage only planned safe files, not git add -A"
fi
assert_contains scripts/create-branch-sync-prs.sh "git diff --cached --quiet"
assert_contains scripts/create-branch-sync-prs.sh "gh workflow run"

fixture_dir="$(mktemp -d)"
cat > "$fixture_dir/Dockerfile" <<'DOCKERFILE'
ARG IMAGICK_VERSION=3.8.1
FROM php:8.5-fpm-alpine
RUN apk add --no-cache bash ffmpeg imagemagick ghostscript
RUN pecl install redis apcu
RUN apk add --no-cache --repository https://dl-cdn.alpinelinux.org/alpine/edge/community/ --allow-untrusted gnu-libiconv
ENV LD_PRELOAD=/usr/lib/preloadable_libiconv.so
DOCKERFILE
REPORT_DIR="$fixture_dir/reports" DOCKERFILE_PATH="$fixture_dir/Dockerfile" PHP_TAGS="" PECL_PACKAGES="" ./scripts/report-freshness.sh
assert_contains "$fixture_dir/reports/dependency-freshness.md" "Installed package signals"
assert_contains "$fixture_dir/reports/dependency-freshness.md" "Manual follow-up guide"
assert_contains "$fixture_dir/reports/dependency-freshness.md" 'Pinned Imagick: `3.8.1`'

assert_file scripts/create-dependency-freshness-issue.sh
assert_executable scripts/create-dependency-freshness-issue.sh
assert_contains scripts/create-dependency-freshness-issue.sh "gh issue create"
assert_contains scripts/create-dependency-freshness-issue.sh "gh issue comment"
assert_contains scripts/create-dependency-freshness-issue.sh "dependency-freshness"
assert_contains .github/workflows/dependency-freshness.yml "issues: write"
assert_contains .github/workflows/dependency-freshness.yml "Create issue when dependency updates are available"
assert_contains .github/workflows/dependency-freshness.yml "scripts/create-dependency-freshness-issue.sh"
assert_contains docs/ci-operations.md 'opens or updates a `dependency-freshness` issue'

freshness_issue_dir="$fixture_dir/freshness-issue"
mkdir -p "$freshness_issue_dir/bin" "$freshness_issue_dir/reports"
cat > "$freshness_issue_dir/reports/dependency-freshness.json" <<'JSON'
{
  "dockerfile": "Dockerfile",
  "baseImage": "php:8.5-fpm-alpine",
  "pinnedImagickVersion": "3.8.1",
  "pecl": [
    {"package": "imagick", "pinned": "3.8.1", "latest": "3.8.2", "status": "ok", "updateAvailable": true},
    {"package": "redis", "pinned": null, "latest": "6.2.0", "status": "ok", "updateAvailable": false}
  ],
  "images": []
}
JSON
cat > "$freshness_issue_dir/reports/dependency-freshness.md" <<'MD'
# fpm-alpine dependency freshness report

- `imagick`: pinned `3.8.1`, latest `3.8.2` (ok, update available)
MD
cat > "$freshness_issue_dir/bin/gh" <<'GH'
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "$GH_FAKE_LOG"
case "$*" in
  "issue list"*) echo '' ;;
  "label list"*) echo '' ;;
  "label create"*) exit 0 ;;
  "issue create"*) echo "https://github.com/woosungchoi/fpm-alpine/issues/123" ;;
  *) echo "unexpected gh call: $*" >&2; exit 64 ;;
esac
GH
chmod +x "$freshness_issue_dir/bin/gh"
GH_FAKE_LOG="$freshness_issue_dir/gh.log" \
PATH="$freshness_issue_dir/bin:$PATH" \
GITHUB_REPOSITORY="woosungchoi/fpm-alpine" \
GITHUB_RUN_ID="123456" \
FRESHNESS_REPORT_JSON="$freshness_issue_dir/reports/dependency-freshness.json" \
FRESHNESS_REPORT_MD="$freshness_issue_dir/reports/dependency-freshness.md" \
./scripts/create-dependency-freshness-issue.sh
assert_contains "$freshness_issue_dir/gh.log" "issue create"
assert_contains "$freshness_issue_dir/gh.log" "dependency-freshness"

freshness_existing_dir="$fixture_dir/freshness-existing-issue"
mkdir -p "$freshness_existing_dir/bin" "$freshness_existing_dir/reports"
cp "$freshness_issue_dir/reports/dependency-freshness.json" "$freshness_existing_dir/reports/dependency-freshness.json"
cp "$freshness_issue_dir/reports/dependency-freshness.md" "$freshness_existing_dir/reports/dependency-freshness.md"
cat > "$freshness_existing_dir/bin/gh" <<'GH'
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "$GH_FAKE_LOG"
case "$*" in
  "issue list"*) echo '456' ;;
  "label list"*) echo 'dependency-freshness' ;;
  "issue comment"*) exit 0 ;;
  *) echo "unexpected gh call: $*" >&2; exit 64 ;;
esac
GH
chmod +x "$freshness_existing_dir/bin/gh"
GH_FAKE_LOG="$freshness_existing_dir/gh.log" \
PATH="$freshness_existing_dir/bin:$PATH" \
GITHUB_REPOSITORY="woosungchoi/fpm-alpine" \
GITHUB_RUN_ID="123456" \
FRESHNESS_REPORT_JSON="$freshness_existing_dir/reports/dependency-freshness.json" \
FRESHNESS_REPORT_MD="$freshness_existing_dir/reports/dependency-freshness.md" \
./scripts/create-dependency-freshness-issue.sh
assert_contains "$freshness_existing_dir/gh.log" "issue comment 456"
if grep -Fq "issue create" "$freshness_existing_dir/gh.log"; then
  fail "existing dependency-freshness issue should be commented on, not duplicated"
fi

freshness_no_signal_dir="$fixture_dir/freshness-no-signal"
mkdir -p "$freshness_no_signal_dir/bin" "$freshness_no_signal_dir/reports"
cat > "$freshness_no_signal_dir/reports/dependency-freshness.json" <<'JSON'
{"pecl":[{"package":"imagick","pinned":"3.8.1","latest":"3.8.1","status":"ok","updateAvailable":false}],"images":[],"warnings":[]}
JSON
cat > "$freshness_no_signal_dir/bin/gh" <<'GH'
#!/usr/bin/env bash
echo "gh should not be called for no-signal freshness reports" >&2
exit 99
GH
chmod +x "$freshness_no_signal_dir/bin/gh"
PATH="$freshness_no_signal_dir/bin:$PATH" \
GITHUB_REPOSITORY="woosungchoi/fpm-alpine" \
FRESHNESS_REPORT_JSON="$freshness_no_signal_dir/reports/dependency-freshness.json" \
./scripts/create-dependency-freshness-issue.sh

assert_contains scripts/smoke-test-image.sh "run_check \"extension: imagick\""
assert_contains scripts/smoke-test-image.sh "GITHUB_STEP_SUMMARY"
assert_contains scripts/report-manifest.sh "MANIFEST_RETRY_ATTEMPTS"
assert_contains scripts/report-manifest.sh "Docker Hub propagation lag"
assert_file scripts/create-manifest-failure-issue.sh
assert_executable scripts/create-manifest-failure-issue.sh
assert_contains scripts/create-manifest-failure-issue.sh "gh issue create"
assert_contains scripts/create-manifest-failure-issue.sh "gh issue comment"
assert_contains scripts/create-manifest-failure-issue.sh "manifest-failure"
assert_contains .github/workflows/verify-published-manifest.yml "issues: write"
assert_contains .github/workflows/verify-published-manifest.yml "Create issue on manifest verification failure"
assert_contains .github/workflows/verify-published-manifest.yml "scripts/create-manifest-failure-issue.sh"
assert_contains .github/workflows/verify-published-manifest.yml "manifest-failure"
assert_contains docs/ci-operations.md 'opens or updates a `manifest-failure` issue'

bash -n scripts/smoke-test-image.sh
bash -n scripts/create-manifest-failure-issue.sh
bash -n scripts/report-manifest.sh
bash -n scripts/report-freshness.sh
bash -n scripts/branch-drift-report.sh
bash -n scripts/plan-branch-sync.sh
bash -n scripts/create-branch-sync-prs.sh
python3 - <<'PY'
from pathlib import Path
import yaml
p = Path('.github/workflows/branch-sync-pr.yml')
data = yaml.safe_load(p.read_text())
assert data.get('jobs')
text = p.read_text()
assert 'Dockerfile' not in text
assert 'hooks/' not in text
PY

echo "policy script tests passed"
