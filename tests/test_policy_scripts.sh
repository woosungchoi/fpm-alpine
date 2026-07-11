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

assert_not_file() {
  local path="$1"
  [ ! -e "$path" ] || fail "expected $path not to exist"
}

assert_executable() {
  local path="$1"
  [ -x "$path" ] || fail "expected $path to be executable"
}

assert_contains() {
  local path="$1"
  local needle="$2"
  grep -Fq -- "$needle" "$path" || fail "expected $path to contain: $needle"
}

assert_not_contains() {
  local path="$1"
  local needle="$2"
  ! grep -Fq -- "$needle" "$path" || fail "expected $path not to contain: $needle"
}

assert_regex() {
  local path="$1"
  local pattern="$2"
  grep -Eq -- "$pattern" "$path" || fail "expected $path to match regex: $pattern"
}

assert_not_regex() {
  local path="$1"
  local pattern="$2"
  ! grep -Eiq -- "$pattern" "$path" || fail "expected $path not to match regex: $pattern"
}

assert_file SUPPORT.md
assert_regex SUPPORT.md '^\| PHP 8\.0 \(`8\.0`\) \| EOL, frozen, unsupported \| 2023-11-26 \|$'
assert_regex SUPPORT.md '^\| PHP 8\.1 \(`8\.1`\) \| EOL, frozen, unsupported \| 2025-12-31 \|$'
assert_regex SUPPORT.md '^\| PHP 8\.2 \(`8\.2`\) \| security-only \| 2026-12-31 \|$'
assert_regex SUPPORT.md '^\| PHP 8\.3 \(`8\.3`\) \| security-only \| 2027-12-31 \|$'
assert_regex SUPPORT.md '^\| PHP 8\.4 \(`8\.4`\) \| active support, then security support \| 2028-12-31 \|$'
assert_regex SUPPORT.md '^\| PHP 8\.5 \(`8\.5`\) \| active support, then security support; primary branch \| 2029-12-31 \|$'
assert_regex SUPPORT.md 'The `8\.0` and `8\.1` tags are retained.*frozen and \*\*never rebuilt\*\*'
assert_contains SUPPORT.md "Running these tags is unsupported legacy use"
assert_contains SUPPORT.md 'The Docker Hub `this` tag is an unsupported legacy/accidental tag; it is not a supported version contract, receives no rebuilds, updates, or support, and must not be used.'
assert_contains SUPPORT.md 'There is intentionally no `latest` tag.'

assert_file LICENSE
license_sha256="c894d4253148e8ce9803b6a114a6bb330e65ac358afe03e1f39e851d3ebf03c6"
actual_license_sha256="$(sha256sum -- LICENSE | cut -d ' ' -f 1)"
[ "$actual_license_sha256" = "$license_sha256" ] || fail "LICENSE SHA-256 mismatch: expected $license_sha256, got $actual_license_sha256"
assert_contains LICENSE "SPDX-License-Identifier: GPL-2.0-only"
assert_regex LICENSE '^WordPress Docker Official Image\. docker-library/wordpress is licensed under$'
assert_regex LICENSE '^GPL-2\.0\. It also builds on the PHP Docker Official Image packaging\.$'
assert_regex LICENSE '^docker-library/php is licensed under the MIT License\.'
assert_contains LICENSE "GNU GENERAL PUBLIC LICENSE"
assert_contains LICENSE "Version 2, June 1991"
assert_contains LICENSE "TERMS AND CONDITIONS FOR COPYING, DISTRIBUTION AND MODIFICATION"
assert_contains LICENSE "0. This License applies to any program or other work"
assert_contains LICENSE "12. IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW"
assert_contains LICENSE "END OF TERMS AND CONDITIONS"

private_advisory_url="https://github.com/woosungchoi/fpm-alpine/security/advisories/new"
support_url="https://github.com/woosungchoi/fpm-alpine/blob/HEAD/SUPPORT.md"
security_policy_url="https://github.com/woosungchoi/fpm-alpine/security/policy"
assert_file .github/PULL_REQUEST_TEMPLATE.md
assert_contains .github/PULL_REQUEST_TEMPLATE.md "Docker Hub hooks and publishing behavior are unchanged"
assert_contains .github/PULL_REQUEST_TEMPLATE.md "No vulnerability details or secrets are included"
assert_contains .github/PULL_REQUEST_TEMPLATE.md "$private_advisory_url"
assert_contains .github/PULL_REQUEST_TEMPLATE.md "$support_url"
assert_contains .github/PULL_REQUEST_TEMPLATE.md "never in a public issue or PR"
assert_file .github/ISSUE_TEMPLATE/bug-report.yml
assert_contains .github/ISSUE_TEMPLATE/bug-report.yml "Do not disclose vulnerabilities or secrets here"
python3 - .github/ISSUE_TEMPLATE/bug-report.yml <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text().splitlines()
starts = [i for i, line in enumerate(lines) if re.match(r"^  - type: ", line)]
blocks = [lines[start:end] for start, end in zip(starts, starts[1:] + [len(lines)])]
by_id = {}
for block in blocks:
    ids = [re.fullmatch(r"    id: ([A-Za-z0-9_-]+)", line) for line in block]
    ids = [match.group(1) for match in ids if match]
    if len(ids) == 1:
        by_id[ids[0]] = block

for field in ("image", "architecture", "description", "reproduction"):
    block = by_id.get(field)
    if block is None:
        raise SystemExit(f"FAIL: bug form is missing core field id: {field}")
    try:
        validations = block.index("    validations:")
    except ValueError:
        raise SystemExit(f"FAIL: bug form field {field} is missing its validations block")
    if "      required: true" not in block[validations + 1:]:
        raise SystemExit(f"FAIL: bug form field {field} must be individually required")

checks = by_id.get("checks")
if checks is None:
    raise SystemExit("FAIL: bug form is missing safety confirmations id: checks")
if sum(line == "          required: true" for line in checks) != 2:
    raise SystemExit("FAIL: both bug form safety confirmations must be required")
PY
assert_file .github/ISSUE_TEMPLATE/config.yml
assert_regex .github/ISSUE_TEMPLATE/config.yml '^blank_issues_enabled: false$'
assert_contains .github/ISSUE_TEMPLATE/config.yml "$private_advisory_url"
assert_contains .github/ISSUE_TEMPLATE/config.yml "$security_policy_url"
assert_contains SECURITY.md "$private_advisory_url"
assert_contains SECURITY.md "Do not open a public issue for a vulnerability"

assert_not_file PHASE4-PHASE5.md
assert_not_file REFACTORING-TODO.md
for archive in docs/archive/PHASE4-PHASE5.md docs/archive/REFACTORING-TODO.md; do
  assert_file "$archive"
  assert_contains "$archive" "ARCHIVED — NON-AUTHORITATIVE — SUPERSEDED"
  assert_contains "$archive" "MUST NOT be followed"
  assert_contains "$archive" "../../SUPPORT.md"
  assert_contains "$archive" "../../BRANCH-AND-TAG-POLICY.md"
  assert_contains "$archive" "../ci-operations.md"
  assert_contains "$archive" '8.0`/`8.1'
  assert_contains "$archive" 'no `latest` tag is published'
done

for active_doc in README.md SECURITY.md BRANCH-AND-TAG-POLICY.md docs/ci-operations.md; do
  assert_file "$active_doc"
  assert_contains "$active_doc" "SUPPORT.md"
  assert_not_regex "$active_doc" '20[0-9]{2}-[0-9]{2}-[0-9]{2}'
  assert_not_regex "$active_doc" 'latest tag (exists|is published|points|maps|follows)'
  assert_not_regex "$active_doc" '8\.0.*8\.1.*(are|remain) (active|maintained|supported)'
done
assert_contains README.md "canonical lifecycle policy"
assert_contains SECURITY.md "canonical supported-version matrix"
assert_contains BRANCH-AND-TAG-POLICY.md "canonical source for version lifecycle status and EOL dates"
assert_contains docs/ci-operations.md "Lifecycle policy: [SUPPORT.md](../SUPPORT.md) is canonical"
assert_not_contains README.md "future target is version branches"
assert_not_contains BRANCH-AND-TAG-POLICY.md "future target is version branches"
assert_contains docs/ci-operations.md "Required status check"
assert_contains README.md "canonical machine-readable build and matrix input"
assert_contains README.md "coordinated JSON,"
assert_contains README.md "validator, and test approval changes"
assert_contains docs/ci-operations.md "canonical build and matrix"
assert_contains docs/ci-operations.md "independently enforce the approved pin and lifecycle baseline"
assert_contains docs/ci-operations.md 'all eight `docker-smoke-matrix` jobs'
assert_not_contains README.md "single machine-readable source"
assert_not_contains docs/ci-operations.md "single source for PHP"
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
assert_contains .github/workflows/branch-sync-pr.yml "BRANCH_SYNC_DISPATCH_WORKFLOW: \"\""
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
assert_contains scripts/create-branch-sync-prs.sh "git push --force-with-lease origin"

fixture_dir="$(mktemp -d)"
mkdir -p "$fixture_dir/bin"
cat > "$fixture_dir/bin/docker" <<'EOF'
#!/usr/bin/env bash
: > "$REMOTE_MARKER"
exit 99
EOF
cat > "$fixture_dir/bin/curl" <<'EOF'
#!/usr/bin/env bash
: > "$REMOTE_MARKER"
exit 99
EOF
chmod +x "$fixture_dir/bin/docker" "$fixture_dir/bin/curl"
printf '{invalid json\n' > "$fixture_dir/invalid.json"
if REMOTE_MARKER="$fixture_dir/remote-called" PATH="$fixture_dir/bin:$PATH" \
  REPORT_DIR="$fixture_dir/invalid-reports" VERSIONS_PATH="$fixture_dir/invalid.json" \
  ./scripts/report-freshness.sh >"$fixture_dir/invalid.out" 2>&1; then
  fail "freshness report accepted invalid versions JSON"
fi
assert_contains "$fixture_dir/invalid.out" "freshness report aborted: invalid versions metadata"
assert_not_file "$fixture_dir/remote-called"
assert_not_file "$fixture_dir/invalid-reports/dependency-freshness.json"
assert_not_file "$fixture_dir/invalid-reports/dependency-freshness.md"

cat > "$fixture_dir/validator" <<'EOF'
#!/usr/bin/env python3
raise SystemExit(0)
EOF
chmod +x "$fixture_dir/validator"
cat > "$fixture_dir/versions.json" <<'JSON'
{
  "schemaVersion": 2,
  "dependencies": {
    "imagick": {"version":"91.92.93","url":"https://fixture.invalid/imagick-obvious.tgz","sha256":"imagick-fixture-sha"},
    "redis": {"version":"81.82.83","url":"https://fixture.invalid/redis-obvious.tgz","sha256":"redis-fixture-sha"},
    "apcu": {"version":"71.72.73","url":"https://fixture.invalid/apcu-obvious.tgz","sha256":"apcu-fixture-sha"}
  },
  "runtimeContracts": {
    "libiconv": {"implementation":"libiconv","version":"1.18","package":"gnu-libiconv-libs","packageVersion":"1.18-r0","ownerPath":"/usr/lib/libiconv.so.2","target":"/usr/lib/libiconv.so.2.7.0"}
  },
  "versions": {
    "9.9": {"minor":"9.9","patch":"9.9.99","base_image":"fixture.invalid/php:9.9@sha256:obvious-base-ref","support":"fixture-support","eol":"2099-09-09"}
  }
}
JSON
REPORT_DIR="$fixture_dir/reports" VERSIONS_PATH="$fixture_dir/versions.json" \
  VALIDATOR_PATH="$fixture_dir/validator" REPORT_SKIP_REMOTE=1 ./scripts/report-freshness.sh >/dev/null
for value in 9.9.99 fixture.invalid/php:9.9@sha256:obvious-base-ref 91.92.93 https://fixture.invalid/imagick-obvious.tgz imagick-fixture-sha 81.82.83 https://fixture.invalid/redis-obvious.tgz redis-fixture-sha 71.72.73 https://fixture.invalid/apcu-obvious.tgz apcu-fixture-sha gnu-libiconv-libs 1.18-r0 /usr/lib/libiconv.so.2.7.0; do
  assert_contains "$fixture_dir/reports/dependency-freshness.json" "$value"
  assert_contains "$fixture_dir/reports/dependency-freshness.md" "$value"
done
assert_contains "$fixture_dir/reports/dependency-freshness.md" "Validated matrix pins"
assert_contains "$fixture_dir/reports/dependency-freshness.md" "Manual follow-up guide"
assert_not_contains "$fixture_dir/reports/dependency-freshness.md" "Alpine edge"
assert_not_contains "$fixture_dir/reports/dependency-freshness.md" "--allow-untrusted"
assert_not_contains "$fixture_dir/reports/dependency-freshness.md" "not pinned here"

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

assert_contains scripts/smoke-test-image.sh 'run_check "extension: ${extension} ${expected_version}"'
assert_contains scripts/smoke-test-image.sh "EXPECTED_PLATFORM"
assert_contains scripts/smoke-test-image.sh "php-fpm process alive"
assert_contains scripts/smoke-test-image.sh 'while ! pgrep -x php-fpm'
assert_contains scripts/smoke-test-image.sh '"$attempt" -lt 40'
assert_contains scripts/smoke-test-image.sh 'sleep 0.25'
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
./tests/test_reproducible_build_policy.sh
./tests/test_smoke_script.sh
./tests/test_publisher_policy.sh
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
