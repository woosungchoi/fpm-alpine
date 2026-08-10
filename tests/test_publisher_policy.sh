#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
assert_file() { [ -f "$1" ] || fail "missing file: $1"; }
assert_not_file() { [ ! -e "$1" ] || fail "unexpected legacy file: $1"; }
assert_contains() { grep -Fq -- "$2" "$1" || fail "expected $1 to contain: $2"; }
assert_not_contains() { ! grep -Fq -- "$2" "$1" || fail "expected $1 not to contain: $2"; }

workflow=.github/workflows/dependency-auto-publish.yml
assert_file "$workflow"
assert_not_file .github/workflows/dependency-publish-recovery.yml
assert_not_file .github/workflows/legacy-cutover-lease.yml
assert_not_file .github/workflows/published-runtime-smoke.yml
assert_not_file .github/dockerhub-cutover-attestation.json

assert_contains "$workflow" 'branches: ["main"]'
assert_contains "$workflow" 'build/versions.json'
assert_contains "$workflow" 'workflow_dispatch:'
assert_contains "$workflow" 'environment: fpm-auto-production'
assert_contains "$workflow" 'docker/build-push-action@'
assert_contains "$workflow" 'platforms: linux/amd64,linux/arm64'
assert_contains "$workflow" 'push: true'
assert_contains "$workflow" '${{ env.DOCKERHUB_REPOSITORY }}:${{ matrix.php_minor }}'
assert_contains "$workflow" '${{ env.GHCR_REPOSITORY }}:${{ matrix.php_minor }}'
assert_contains "$workflow" 'secrets.DOCKERHUB_TOKEN'
assert_contains "$workflow" 'scripts/evaluate-auto-promotion.py'
assert_contains "$workflow" "data.get('class') != 'base-same-minor'"
assert_contains "$workflow" 'test "$dockerhub_digest" = "$BUILD_DIGEST"'
assert_contains "$workflow" 'test "$ghcr_digest" = "$BUILD_DIGEST"'
assert_contains "$workflow" 'test "$dockerhub_digest" = "$ghcr_digest"'

assert_not_contains "$workflow" ':latest'
for forbidden in repository_dispatch transaction-journal cutover backfill replay rollback promotion-plan; do
  assert_not_contains "$workflow" "$forbidden"
done

python3 - <<'PY'
import re
from pathlib import Path

import yaml

path = Path('.github/workflows/dependency-auto-publish.yml')
text = path.read_text()
workflow = yaml.safe_load(text)
trigger = workflow.get('on', workflow.get(True))
assert set(trigger) == {'push', 'workflow_dispatch'}
assert trigger['push'] == {'branches': ['main'], 'paths': ['build/versions.json']}
assert set(workflow['jobs']) == {'prepare', 'publish'}
publish = workflow['jobs']['publish']
assert publish['environment'] == 'fpm-auto-production'
assert publish['permissions'] == {'contents': 'read', 'packages': 'write'}
refs = re.findall(r'^\s*uses:\s*([^\s#]+)', text, re.MULTILINE)
assert refs and all(re.fullmatch(r'[^@]+@[0-9a-f]{40}', ref) for ref in refs)
PY

assert_contains scripts/resolve-dependency-candidates.py '"class": "pecl-manual-review"'
assert_contains scripts/resolve-dependency-candidates.py '"eligible": False'
assert_contains scripts/classify-dependency-change.py 'PECL updates require manual review'

python3 tests/test_dependency_auto_publish.py
python3 tests/test_dependency_control_workflows.py

printf 'simple publisher policy tests passed\n'
