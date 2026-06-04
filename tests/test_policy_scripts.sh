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

branch_report_dir="$(mktemp -d)"
trap 'rm -rf "$branch_report_dir"' EXIT
BRANCH_DRIFT_BRANCHES="8.5" BRANCH_DRIFT_REPORT_DIR="$branch_report_dir" ./scripts/branch-drift-report.sh
assert_file "$branch_report_dir/branch-drift.md"
assert_file "$branch_report_dir/branch-drift.json"
assert_contains "$branch_report_dir/branch-drift.md" "# fpm-alpine branch drift report"
assert_contains "$branch_report_dir/branch-drift.md" "report-only"
assert_contains "$branch_report_dir/branch-drift.md" "8.5"

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

assert_contains scripts/smoke-test-image.sh "run_check \"extension: imagick\""
assert_contains scripts/smoke-test-image.sh "GITHUB_STEP_SUMMARY"
assert_contains scripts/report-manifest.sh "MANIFEST_RETRY_ATTEMPTS"
assert_contains scripts/report-manifest.sh "Docker Hub propagation lag"

bash -n scripts/smoke-test-image.sh
bash -n scripts/report-manifest.sh
bash -n scripts/report-freshness.sh
bash -n scripts/branch-drift-report.sh

echo "policy script tests passed"
