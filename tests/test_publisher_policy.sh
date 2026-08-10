#!/usr/bin/env bash
# Assertions below intentionally search for literal shell and Actions expressions.
# shellcheck disable=SC2016
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_file() { [ -f "$1" ] || fail "expected file $1 to exist"; }
assert_executable() { [ -x "$1" ] || fail "expected $1 to be executable"; }
assert_contains() { grep -Fq -- "$2" "$1" || fail "expected $1 to contain: $2"; }
assert_not_contains() { ! grep -Fq -- "$2" "$1" || fail "expected $1 not to contain: $2"; }

assert_file .github/workflows/publish.yml
assert_file .github/workflows/dependency-publish-recovery.yml
for script in scripts/verify-published-image.sh scripts/verify-published-dockerhub-image.sh scripts/verify-canary-image.sh scripts/verify-rollback-image.sh scripts/rollback-moving-aliases.sh scripts/scan-image.sh scripts/promote-image.sh scripts/promote-auto-canaries.sh scripts/assert-image-tag-absent.sh scripts/validate-auto-promotion-plan.py scripts/validate-auto-transaction-result.py scripts/validate-canary-metadata.py scripts/validate-legacy-cutover-evidence.py scripts/resolve-platform-image.py scripts/resolve-publisher-signing-ref.sh scripts/verify-dockerhub-tag-policy.py scripts/prune-dockerhub-tags.py scripts/archive-dockerhub-tags.py scripts/verify-image-parity.py; do
  assert_file "$script"
  assert_executable "$script"
done
assert_file scripts/verify-provenance.py
assert_executable scripts/verify-provenance.py

python3 - <<'PY'
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

workflow_path = Path('.github/workflows/publish.yml')
text = workflow_path.read_text()
data = yaml.safe_load(text)
trigger = data.get('on', data.get(True))
assert trigger == {'repository_dispatch': {'types': ['fpm-manual-publish']}}, trigger
assert data['permissions'] == {}
assert data['concurrency'] == {'group': 'publish-canary', 'cancel-in-progress': False}
uses = re.findall(r'^\s*uses:\s*([^\s#]+)(?:\s+#\s*(\S+))?\s*$', text, re.M)
assert uses, 'publisher must use pinned actions'
for ref, comment in uses:
    assert re.fullmatch(r'[^@\s]+@[0-9a-f]{40}', ref), ref
    assert comment, f'missing release comment for {ref}'

jobs = data['jobs']
assert set(jobs) == {'prepare', 'canary', 'report-failure'}, set(jobs)
assert jobs['prepare']['permissions'] == {'contents': 'read'}
assert jobs['canary']['permissions'] == {'contents': 'read', 'packages': 'write', 'id-token': 'write'}
assert jobs['report-failure']['permissions'] == {'actions': 'read', 'contents': 'read', 'issues': 'write'}
assert 'environment' not in jobs['canary']

envelope = next(step['run'] for step in jobs['prepare']['steps'] if step.get('name') == 'Validate owner dispatch envelope before checkout')
base_env = {
    **os.environ,
    'EVENT_ACTION': 'fpm-manual-publish',
    'EVENT_ACTOR': 'woosungchoi',
    'EVENT_ACTOR_ID': '5674610',
    'EVENT_REPOSITORY': 'woosungchoi/fpm-alpine',
    'EVENT_REF': 'refs/heads/main',
    'EVENT_SHA': 'a' * 40,
}
canary_payload = {'channel': 'canary', 'source_sha': 'a' * 40, 'version': '8.5'}
env = {**base_env, 'EVENT_PAYLOAD_JSON': json.dumps(canary_payload)}
assert subprocess.run(['bash', '-c', envelope], env=env, capture_output=True, text=True).returncode == 0
for payload, overrides in (
    ({**canary_payload, 'unknown': 'value'}, {}),
    ({**canary_payload, 'channel': 'production'}, {}),
    (canary_payload, {'EVENT_ACTOR_ID': '1'}),
):
    env = {**base_env, **overrides, 'EVENT_PAYLOAD_JSON': json.dumps(payload)}
    assert subprocess.run(['bash', '-c', envelope], env=env, capture_output=True, text=True).returncode != 0

canary = yaml.safe_dump(jobs['canary'], sort_keys=False)
for required in (
    'docker/login-action@', 'docker/build-push-action@', 'provenance: mode=max', 'sbom: true',
    'scripts/verify-canary-image.sh', 'scripts/scan-image.sh',
    'cosign sign --yes -a fpm.operation=manual', 'EXPECTED_OPERATION: manual', 'github.run_attempt',
):
    assert required in canary, required
for forbidden in ('DOCKERHUB_REPOSITORY', 'dockerhub_digest', "channel == 'production'", 'scripts/promote-image.sh'):
    assert forbidden not in canary, forbidden
assert "github.event.client_payload.channel == 'canary'" in canary
assert 'refusing to overwrite existing GHCR canary tag' in text
assert 'test "$SOURCE_SHA" = "$DISPATCH_SHA"' in text
assert text.count('SOURCE_DATE_EPOCH=${{ needs.prepare.outputs.source_date_epoch }}') == 1
assert text.count('SOURCE_DATE_EPOCH: ${{ needs.prepare.outputs.source_date_epoch }}') == 1
assert 'git show -s --format=%ct "$GITHUB_SHA"' in text
assert "test \"$DISPATCH_REF\" = 'refs/heads/main'" in text
assert 'production-preflight:' not in text
assert 'bootstrap-ghcr-rollback:' not in text
assert '\n  production:' not in text
assert 'Require anonymous GHCR manifest and runtime access' in canary
anonymous_run = next(step['run'] for step in jobs['canary']['steps'] if step.get('name') == 'Require anonymous GHCR manifest and runtime access')
assert 'DOCKER_CONFIG="$anonymous_config" ./scripts/resolve-platform-image.py "$GHCR_SUBJECT" "$platform"' in anonymous_run
assert '--entrypoint php "$platform_subject"' in anonymous_run
assert '--entrypoint php "$GHCR_SUBJECT"' not in anonymous_run

failure = yaml.safe_dump(jobs['report-failure'], sort_keys=False)
assert 'scripts/create-manifest-failure-issue.sh' in failure
assert "github.ref == 'refs/heads/main'" in failure
assert "needs.prepare.result == 'success'" in failure
assert 'failure-minors.txt' in failure
assert 'failure-jobs.json' in failure
assert 'job.get("conclusion") != "failure"' in text
assert 'payload["versions"].items()' in text
assert '"security-only"' in text
for verifier in ('scripts/verify-published-image.sh', 'scripts/verify-rollback-image.sh'):
    verifier_text = Path(verifier).read_text()
    assert 'resolve-platform-image.py' in verifier_text
    assert '"$platform_subject"' in verifier_text
validator_text = Path('scripts/validate-legacy-cutover-evidence.py').read_text()
assert 'legacy cutover evidence is not within the 15-minute lease' in validator_text
assert 'Docker Hub legacy publisher is not quiescent' in validator_text
PY

python3 - <<'PY'
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

workflow = Path('.github/workflows/publish.yml').read_text()
marker = 'gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}/jobs?per_page=100" > /tmp/failure-jobs.json'
tail = workflow.split(marker, 1)[1].split("python3 - <<'PY' > /tmp/failure-minors.txt\n", 1)[1]
code = textwrap.dedent(tail.split("\n          PY", 1)[0])
compile(code, 'failure-minor-selection.py', 'exec')
with tempfile.TemporaryDirectory() as tmp:
    jobs_path = Path(tmp) / 'jobs.json'
    code = code.replace('/tmp/failure-jobs.json', str(jobs_path))
    jobs_path.write_text(json.dumps({'jobs': [
        {'name': 'canary (8.2, fixture)', 'conclusion': 'success'},
        {'name': 'canary (8.3, fixture)', 'conclusion': 'success'},
        {'name': 'canary (8.4, fixture)', 'conclusion': 'success'},
        {'name': 'canary (8.5, fixture)', 'conclusion': 'failure'},
        {'name': 'prepare', 'conclusion': 'success'},
    ]}))
    result = subprocess.run([sys.executable, '-c', code], check=True, text=True, stdout=subprocess.PIPE)
    assert result.stdout.splitlines() == ['8.5'], result.stdout
PY

python3 - <<'PY'
import json
import subprocess
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    evidence = Path(tmp)
    payload = {
        "schema_version": 2,
        "channel": "canary",
        "source_sha": "0123456789abcdef0123456789abcdef01234567",
        "php_minor": "8.5",
        "php_patch": "8.5.8",
        "run_id": 123,
        "run_attempt": True,
        "canonical_registry": "ghcr.io",
        "canonical_repository": "ghcr.io/woosungchoi/fpm-alpine",
        "canonical_ref": "ghcr.io/woosungchoi/fpm-alpine:canary-8.5-123-1",
        "ghcr_digest": "sha256:" + "2" * 64,
        "platforms": ["linux/amd64", "linux/arm64"],
    }
    (evidence / "canary-metadata.json").write_text(json.dumps(payload))
    result = subprocess.run([
        "./scripts/validate-canary-metadata.py",
        str(evidence),
        payload["source_sha"],
        payload["php_minor"],
        payload["php_patch"],
        "123",
        "1",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode == 0:
        raise SystemExit("strict canary validator accepted boolean run_attempt")
PY

python3 - <<'PY'
import base64
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sha = "0123456789abcdef0123456789abcdef01234567"
script = Path("scripts/validate-legacy-cutover-evidence.py").resolve()

def run(payload):
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    env = os.environ.copy()
    env["LEGACY_EVIDENCE_B64"] = base64.b64encode(raw).decode()
    return subprocess.run([sys.executable, str(script), sha, digest], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode

captured = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
payload = {
    "schema_version": 2,
    "source_sha": sha,
    "captured_at": captured,
    "dockerhub": {
        "build_rule_active": False,
        "in_flight_builds": 0,
        "public_is_automated": False,
        "repository_last_updated": "2026-08-10T00:00:00Z",
        "queue_evidence": "dockerhub-ui-owner-observation",
        "queue_observed_at": captured,
    },
    "github": {
        "repository": "woosungchoi/fpm-alpine",
        "legacy_webhook_present": False,
        "active_hooks": [{
            "id": 402842509,
            "name": "web",
            "active": True,
            "events": ["pull_request", "push"],
            "url_host": "api.snyk.io",
            "url_kind": "github-webhook-uuid",
        }],
    },
}
if run(payload) != 0:
    raise SystemExit("valid fresh legacy cutover evidence was rejected")
payload["dockerhub"]["in_flight_builds"] = 1
if run(payload) == 0:
    raise SystemExit("in-flight legacy build evidence was accepted")
payload["dockerhub"]["in_flight_builds"] = False
if run(payload) == 0:
    raise SystemExit("boolean false in-flight count was accepted")
payload["dockerhub"]["in_flight_builds"] = 0.0
if run(payload) == 0:
    raise SystemExit("floating-point zero in-flight count was accepted")
payload["dockerhub"]["in_flight_builds"] = 0
payload["schema_version"] = True
if run(payload) == 0:
    raise SystemExit("boolean true schema version was accepted")
payload["schema_version"] = 2
payload["captured_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=16)).isoformat().replace("+00:00", "Z")
if run(payload) == 0:
    raise SystemExit("stale legacy cutover evidence was accepted")
PY

mapfile -t failure_minors < <(python3 - <<'PY'
import json

payload = json.load(open("build/versions.json"))
for minor, row in payload["versions"].items():
    if row["support"] in {"active", "security-only"}:
        print(minor)
PY
)
expected_failure_minors=(8.2 8.3 8.4 8.5)
[ "${failure_minors[*]}" = "${expected_failure_minors[*]}" ] || \
  fail "failure reporter selected unexpected minors: ${failure_minors[*]}"

assert_contains scripts/verify-published-image.sh 'linux/amd64'
assert_contains scripts/verify-published-image.sh 'linux/arm64'
assert_contains scripts/verify-published-image.sh 'config.digest'
assert_contains scripts/verify-published-image.sh 'ordered layer digests'
assert_contains scripts/verify-published-image.sh 'org.opencontainers.image.revision'
assert_contains scripts/verify-published-image.sh 'cosign verify'
assert_contains scripts/verify-published-image.sh 'scripts/verify-provenance.py'
assert_contains scripts/verify-published-dockerhub-image.sh 'scripts/verify-provenance.py'
assert_contains scripts/verify-published-dockerhub-image.sh 'INSPECT_ATTEMPTS'
assert_contains scripts/verify-published-dockerhub-image.sh 'exact Docker Hub subject'
assert_not_contains scripts/verify-published-dockerhub-image.sh 'ghcr.io'
assert_not_contains scripts/verify-published-dockerhub-image.sh 'cosign'
assert_contains scripts/scan-image.sh 'aquasec/trivy:0.72.0@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f'
assert_contains scripts/scan-image.sh '--ignore-unfixed'
assert_contains scripts/scan-image.sh '--platform "$PLATFORM"'
assert_contains scripts/scan-image.sh '--severity HIGH,CRITICAL'
assert_contains scripts/scan-image.sh '--severity CRITICAL'
assert_contains scripts/scan-image.sh '--exit-code 1'
assert_contains scripts/promote-image.sh 'immutable tag already points to another digest'
assert_contains scripts/promote-image.sh 'sha-${MINOR}-${short_sha}-${digest_hex}'
assert_contains scripts/promote-image.sh 'docker buildx imagetools create'
assert_not_contains scripts/promote-image.sh ':latest'
assert_contains scripts/verify-published-image.sh 'EXPECTED_SIGNING_REF'
assert_contains scripts/verify-published-image.sh 'EXPECTED_PUBLISHER_WORKFLOW'
assert_contains scripts/verify-published-image.sh 'dependency-auto-publish\.yml'
assert_contains scripts/verify-canary-image.sh 'EXPECTED_PUBLISHER_WORKFLOW'
assert_contains scripts/verify-published-image.sh '@refs/heads/${EXPECTED_SIGNING_REF}$'
assert_contains scripts/promote-auto-canaries.sh 'preflight-baseline'
assert_contains scripts/promote-auto-canaries.sh 'rollback_all'
assert_contains scripts/promote-auto-canaries.sh 'recover_transaction'
assert_contains scripts/promote-auto-canaries.sh 'no moving aliases were modified'
assert_contains scripts/promote-auto-canaries.sh 'rollback_dockerhub_backup'
assert_contains scripts/promote-auto-canaries.sh 'rollback_ghcr_digest'
assert_contains scripts/promote-auto-canaries.sh 'scripts/rollback-moving-aliases.sh'
assert_contains scripts/rollback-moving-aliases.sh '[ "$actual" = "$PREVIOUS_DOCKERHUB_DIGEST" ]'
assert_contains scripts/rollback-moving-aliases.sh 'DOCKERHUB_ROLLBACK_FALLBACK_SOURCE'
assert_contains .github/workflows/dependency-auto-publish.yml 'environment: fpm-auto-production'
assert_contains .github/workflows/dependency-auto-publish.yml 'group: fpm-production-promotion'
assert_contains .github/workflows/dependency-auto-publish.yml 'types: [fpm-ghcr-backfill]'
assert_contains .github/workflows/dependency-auto-publish.yml 'scripts/evaluate-auto-promotion.py'
assert_contains .github/workflows/dependency-auto-publish.yml "if: steps.mode.outputs.mode == 'automatic'"
assert_not_contains .github/workflows/dependency-auto-publish.yml 'workflow_dispatch:'
assert_contains .github/workflows/dependency-publish-recovery.yml 'types: [fpm-publish-recover]'
assert_contains .github/workflows/dependency-publish-recovery.yml 'group: fpm-production-promotion'
for workflow in \
  .github/workflows/dependency-auto-publish.yml \
  .github/workflows/dependency-publish-recovery.yml \
  .github/workflows/publish.yml \
  .github/workflows/published-runtime-smoke.yml; do
  assert_contains "$workflow" 'cosign-release: v3.1.2'
done
assert_contains .github/workflows/dependency-publish-recovery.yml 'plan_sha256'
assert_not_contains .github/workflows/publish.yml 'fpm-production-promotion'
assert_not_contains .github/workflows/publish.yml 'publisher-reports/preflight-baseline'
assert_not_contains .github/workflows/publish.yml 'production-preflight:'
assert_contains .github/workflows/dependency-auto-publish.yml 'publisher-auto-canary-*-${{ github.run_id }}-${{ github.run_attempt }}'
assert_contains .github/workflows/dependency-auto-publish.yml 'promotion-plan.json'
assert_contains .github/workflows/dependency-auto-publish.yml 'rollback-auto-dockerhub-'
assert_contains .github/workflows/dependency-auto-publish.yml 'rollback-auto-ghcr-'
assert_contains .github/workflows/dependency-auto-publish.yml 'scripts/promote-auto-canaries.sh'
assert_not_contains .github/workflows/dependency-auto-publish.yml 'name: Build and push Docker Hub image'
assert_contains .github/workflows/published-runtime-smoke.yml 'scripts/resolve-publisher-signing-ref.sh'
assert_contains .github/workflows/published-runtime-smoke.yml 'steps.multi.outputs.signing_ref'
assert_not_contains .github/workflows/published-runtime-smoke.yml 'steps.source.outputs.signing_ref'
assert_contains .github/workflows/published-runtime-smoke.yml 'steps.source.outputs.dockerhub_subject'
assert_contains .github/workflows/published-runtime-smoke.yml 'steps.source.outputs.dockerhub_digest'
assert_contains .github/workflows/published-runtime-smoke.yml 'steps.multi.outputs.ghcr_subject'
assert_contains .github/workflows/published-runtime-smoke.yml 'scripts/validate-auto-transaction-result.py'
assert_contains .github/workflows/published-runtime-smoke.yml 'publisher-auto-production-${{ github.event.workflow_run.id }}'
assert_not_contains .github/workflows/published-runtime-smoke.yml 'digest=$(./scripts/resolve-image-digest.sh "$DOCKERHUB_REF")'
assert_contains .github/workflows/smoke-test.yml 'python3 tests/test_dependency_auto_publish.py'
assert_contains .github/workflows/smoke-test.yml 'python3 tests/test_auto_promotion_transaction.py'
assert_contains .github/workflows/smoke-test.yml 'python3 tests/test_auto_transaction_evidence.py'
assert_contains .github/workflows/smoke-test.yml 'python3 tests/test_image_tag_absence.py'
assert_contains .github/workflows/smoke-test.yml 'python3 tests/test_rollback_sources.py'
assert_contains .github/workflows/smoke-test.yml 'python3 tests/test_published_runtime_smoke.py'
assert_contains .github/workflows/published-runtime-smoke.yml 'fetch-tags: true'
assert_contains scripts/verify-rollback-image.sh 'rollback registry-local manifest and runtime verification passed'
assert_not_contains scripts/verify-rollback-image.sh 'rollback registry parity failed'
assert_not_contains scripts/verify-rollback-image.sh 'build/versions.json'
assert_contains scripts/rollback-moving-aliases.sh 'both registries were attempted'
assert_contains scripts/rollback-moving-aliases.sh 'registry-specific baselines and verified'
assert_contains scripts/rollback-moving-aliases.sh 'DOCKERHUB_ROLLBACK_SOURCE'
assert_contains scripts/rollback-moving-aliases.sh 'GHCR_ROLLBACK_SOURCE'
assert_contains scripts/rollback-moving-aliases.sh 'COSIGN_SIGN_DESTINATION'
assert_contains scripts/report-manifest.sh 'GitHub Actions publisher subject; verification is digest-qualified.'

fixture_dir="$(mktemp -d)"
trap 'rm -rf "$fixture_dir"' EXIT
cat > "$fixture_dir/provenance.json" <<'JSON'
{
  "linux/amd64": {
    "SLSA": {
      "buildDefinition": {
        "externalParameters": {
          "request": {
            "root": {
              "request": {
                "args": {
                  "vcs:revision": "0123456789abcdef0123456789abcdef01234567",
                  "vcs:source": "git@github.com:woosungchoi/fpm-alpine.git"
                }
              }
            }
          }
        }
      }
    }
  },
  "linux/arm64": {
    "SLSA": {
      "buildDefinition": {
        "externalParameters": {
          "request": {
            "root": {
              "request": {
                "args": {
                  "vcs:revision": "0123456789abcdef0123456789abcdef01234567",
                  "vcs:source": "https://github.com/woosungchoi/fpm-alpine.git"
                }
              }
            }
          }
        }
      }
    }
  }
}
JSON
./scripts/verify-provenance.py "$fixture_dir/provenance.json" 0123456789abcdef0123456789abcdef01234567
if ./scripts/verify-provenance.py "$fixture_dir/provenance.json" ffffffffffffffffffffffffffffffffffffffff >/dev/null 2>&1; then
  fail "provenance verifier accepted the wrong revision"
fi
printf '{}\n' > "$fixture_dir/empty.json"
if ./scripts/verify-provenance.py "$fixture_dir/empty.json" 0123456789abcdef0123456789abcdef01234567 >/dev/null 2>&1; then
  fail "provenance verifier accepted missing provenance"
fi
python3 - "$fixture_dir/provenance.json" "$fixture_dir/mixed.json" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text())
payload["linux/amd64"]["SLSA"]["buildDefinition"]["externalParameters"]["request"]["root"]["request"]["args"]["vcs:revision"] = "f" * 40
payload["linux/amd64"]["unrelated"] = {"sha1": "0123456789abcdef0123456789abcdef01234567"}
Path(sys.argv[2]).write_text(json.dumps(payload))
PY
if ./scripts/verify-provenance.py "$fixture_dir/mixed.json" 0123456789abcdef0123456789abcdef01234567 >/dev/null 2>&1; then
  fail "provenance verifier accepted a conflicting platform vcs:revision"
fi
python3 - "$fixture_dir/provenance.json" "$fixture_dir/wrong-source.json" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text())
payload["linux/arm64"]["SLSA"]["buildDefinition"]["externalParameters"]["request"]["root"]["request"]["args"]["vcs:source"] = "https://github.com/example/other.git"
Path(sys.argv[2]).write_text(json.dumps(payload))
PY
if ./scripts/verify-provenance.py "$fixture_dir/wrong-source.json" 0123456789abcdef0123456789abcdef01234567 >/dev/null 2>&1; then
  fail "provenance verifier accepted the wrong source repository"
fi
python3 - "$fixture_dir/provenance.json" "$fixture_dir/relocated.json" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text())
for platform in ("linux/amd64", "linux/arm64"):
    args = payload[platform]["SLSA"]["buildDefinition"]["externalParameters"]["request"]["root"]["request"].pop("args")
    payload[platform]["unrelated"] = args
Path(sys.argv[2]).write_text(json.dumps(payload))
PY
if ./scripts/verify-provenance.py "$fixture_dir/relocated.json" 0123456789abcdef0123456789abcdef01234567 >/dev/null 2>&1; then
  fail "provenance verifier accepted vcs keys relocated outside the BuildKit SLSA args path"
fi

mock_bin="$fixture_dir/bin"
mkdir -p "$mock_bin"
cat > "$mock_bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${MOCK_DOCKER_LOG:?}"
if [ "${1:-}" = buildx ] && [ "${2:-}" = imagetools ] && [ "${3:-}" = inspect ]; then
  ref="${*: -1}"
  if [[ "$ref" == ghcr.io/woosungchoi/fpm-alpine@sha256:* ]]; then
    printf 'Digest: %s\n' "${ref##*@}"
    exit 0
  fi
  if [ "${MOCK_AUTH_ERROR:-0}" = 1 ]; then
    echo 'error getting credentials: docker credential helper binary not found' >&2
    exit "${MOCK_AUTH_ERROR_STATUS:-1}"
  fi
  if [ "${MOCK_MIXED_ERROR:-0}" = 1 ]; then
    echo 'error getting credentials: unauthorized' >&2
    echo "ERROR: $ref: manifest unknown" >&2
    exit 1
  fi
  if [ "${MOCK_UNRELATED_ERROR:-0}" = 1 ]; then
    echo 'ERROR: unrelated-helper: not found' >&2
    exit 1
  fi
  if [ "${MOCK_MULTIPLE_EXISTING:-0}" = 1 ]; then
    printf 'Digest: sha256:%064d\n' 8
    printf 'Digest: sha256:%064d\n' 9
    exit 0
  fi
  if [ -n "${MOCK_CONFLICT_REF:-}" ] && [ "$ref" = "$MOCK_CONFLICT_REF" ]; then
    printf 'Digest: sha256:%064d\n' 9
    exit 0
  fi
  echo "ERROR: $ref: not found" >&2
  exit 1
fi
if [ "${1:-}" = buildx ] && [ "${2:-}" = imagetools ] && [ "${3:-}" = create ]; then
  exit 0
fi
exit 64
SH
chmod +x "$mock_bin/docker"
export MOCK_DOCKER_LOG="$fixture_dir/docker.log"
source_sha=0123456789abcdef0123456789abcdef01234567
source_digest="sha256:$(printf '%064d' 1)"
digest_hex="${source_digest#sha256:}"
for minor_patch in '8.2 8.2.32' '8.3 8.3.32' '8.4 8.4.23' '8.5 8.5.8'; do
  read -r minor patch <<< "$minor_patch"
  PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only --policy evidence \
    ghcr.io/woosungchoi/fpm-alpine ghcr.io/woosungchoi/fpm-alpine "$source_digest" "$minor" "$patch" "$source_sha" 20260711 >/dev/null
  assert_contains "$MOCK_DOCKER_LOG" "ghcr.io/woosungchoi/fpm-alpine:sha-${minor}-${source_sha:0:12}-${digest_hex}"
done
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'
export MOCK_CONFLICT_REF="ghcr.io/woosungchoi/fpm-alpine:8.5.8-20260711-${digest_hex}"
if PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only --policy evidence \
  ghcr.io/woosungchoi/fpm-alpine ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/conflict.out" 2>&1; then
  fail "promotion preflight accepted a conflicting immutable tag"
fi
assert_contains "$fixture_dir/conflict.out" 'immutable tag already points to another digest'
unset MOCK_CONFLICT_REF
: > "$MOCK_DOCKER_LOG"
if MOCK_AUTH_ERROR=1 PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only --policy evidence \
  ghcr.io/woosungchoi/fpm-alpine ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/auth-error.out" 2>&1; then
  fail "promotion preflight treated a credential-helper error as tag absence"
fi
assert_contains "$fixture_dir/auth-error.out" 'credential helper binary not found'
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'
: > "$MOCK_DOCKER_LOG"
if MOCK_AUTH_ERROR=1 MOCK_AUTH_ERROR_STATUS=2 PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only --policy evidence \
  ghcr.io/woosungchoi/fpm-alpine ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/auth-error-exit2.out" 2>&1; then
  fail "promotion preflight treated a credential-helper exit 2 as tag absence"
fi
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'
: > "$MOCK_DOCKER_LOG"
if MOCK_MIXED_ERROR=1 PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only --policy evidence \
  ghcr.io/woosungchoi/fpm-alpine ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/mixed-error.out" 2>&1; then
  fail "promotion preflight accepted mixed auth/not-found output"
fi
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'
: > "$MOCK_DOCKER_LOG"
if MOCK_MULTIPLE_EXISTING=1 PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only --policy evidence \
  ghcr.io/woosungchoi/fpm-alpine ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/multiple-existing.out" 2>&1; then
  fail "promotion preflight accepted ambiguous multiple-digest output"
fi
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'
: > "$MOCK_DOCKER_LOG"
if MOCK_UNRELATED_ERROR=1 PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only --policy evidence \
  ghcr.io/woosungchoi/fpm-alpine ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/unrelated-error.out" 2>&1; then
  fail "promotion preflight accepted not-found output for an unrelated ref"
fi
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'

cat > "$mock_bin/docker" <<'SH'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${MOCK_DOCKER_LOG:?}"
if [ "${1:-}" = buildx ] && [ "${2:-}" = imagetools ] && [ "${3:-}" = create ]; then
  if [ "${MOCK_ROLLBACK_DH_FAIL:-0}" = 1 ] && [[ "$*" == *docker.io/woosungchoi/fpm-alpine:8.5* ]]; then
    exit 1
  fi
  if [ "${MOCK_ROLLBACK_GHCR_FAIL:-0}" = 1 ] && [[ "$*" == *ghcr.io/woosungchoi/fpm-alpine:8.5* ]]; then
    exit 1
  fi
  exit 0
fi
if [ "${1:-}" = buildx ] && [ "${2:-}" = imagetools ] && [ "${3:-}" = inspect ]; then
  ref="${*: -1}"
  if [ "${MOCK_FULL_SUCCESS:-0}" = 1 ] && [[ " $* " == *" --raw "* ]]; then
    case "$ref" in
      *@"${ROLLBACK_DIGEST:?}")
        printf '{"manifests":[{"digest":"sha256:%064d","platform":{"os":"linux","architecture":"amd64"}},{"digest":"sha256:%064d","platform":{"os":"linux","architecture":"arm64"}}]}\n' 2 3
        ;;
      *@sha256:*2)
        printf '{"config":{"digest":"sha256:%064d"},"layers":[{"digest":"sha256:%064d"}]}\n' 4 5
        ;;
      *@sha256:*3)
        printf '{"config":{"digest":"sha256:%064d"},"layers":[{"digest":"sha256:%064d"}]}\n' 6 7
        ;;
      *) exit 64 ;;
    esac
    exit 0
  fi
  printf 'Digest: %s\n' "${ROLLBACK_DIGEST:?}"
  if [ "${MOCK_MULTIPLE_DIGESTS:-0}" = 1 ] && [[ "$ref" != *@* ]]; then
    printf 'Digest: sha256:%064d\n' 8
  fi
  if [ "${MOCK_INSPECT_FAIL_WITH_DIGEST:-0}" = 1 ] && [[ "$ref" != *@* ]]; then
    exit 1
  fi
  exit 0
fi
if [ "${1:-}" = run ] && [ "${MOCK_FULL_SUCCESS:-0}" = 1 ]; then
  exit 0
fi
exit 64
SH
chmod +x "$mock_bin/docker"
: > "$MOCK_DOCKER_LOG"
export ROLLBACK_DIGEST="$source_digest"
if MOCK_ROLLBACK_DH_FAIL=1 PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  docker.io/woosungchoi/fpm-alpine "$source_digest" ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 "$fixture_dir/rollback" \
  >"$fixture_dir/rollback-failure.out" 2>&1; then
  fail "rollback unexpectedly succeeded when Docker Hub restore failed"
fi
assert_contains "$MOCK_DOCKER_LOG" 'docker.io/woosungchoi/fpm-alpine:8.5'
assert_contains "$MOCK_DOCKER_LOG" 'ghcr.io/woosungchoi/fpm-alpine:8.5'
assert_contains "$fixture_dir/rollback-failure.out" 'both registries were attempted'
: > "$MOCK_DOCKER_LOG"
if MOCK_ROLLBACK_GHCR_FAIL=1 PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  docker.io/woosungchoi/fpm-alpine "$source_digest" ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 "$fixture_dir/rollback" \
  >"$fixture_dir/rollback-ghcr-failure.out" 2>&1; then
  fail "rollback unexpectedly succeeded when GHCR restore failed"
fi
assert_contains "$MOCK_DOCKER_LOG" 'docker.io/woosungchoi/fpm-alpine:8.5'
assert_contains "$MOCK_DOCKER_LOG" 'ghcr.io/woosungchoi/fpm-alpine:8.5'
: > "$MOCK_DOCKER_LOG"
if MOCK_INSPECT_FAIL_WITH_DIGEST=1 PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  docker.io/woosungchoi/fpm-alpine "$source_digest" ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 "$fixture_dir/rollback" \
  >"$fixture_dir/rollback-readback-failure.out" 2>&1; then
  fail "rollback accepted digest output from a failed alias inspect"
fi
assert_not_contains "$fixture_dir/rollback-readback-failure.out" 'both registry moving aliases restored from registry-specific baselines and verified'
: > "$MOCK_DOCKER_LOG"
if MOCK_MULTIPLE_DIGESTS=1 PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  docker.io/woosungchoi/fpm-alpine "$source_digest" ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 "$fixture_dir/rollback" \
  >"$fixture_dir/rollback-multiple-digests.out" 2>&1; then
  fail "rollback accepted ambiguous multiple-digest read-back"
fi
assert_not_contains "$fixture_dir/rollback-multiple-digests.out" 'both registry moving aliases restored from registry-specific baselines and verified'
: > "$MOCK_DOCKER_LOG"
if PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  docker.io/woosungchoi/fpm-alpine "$source_digest" ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 "$fixture_dir/rollback" \
  >"$fixture_dir/rollback-verifier-failure.out" 2>&1; then
  fail "rollback ignored an exact-digest verifier failure"
fi
assert_contains "$MOCK_DOCKER_LOG" "docker.io/woosungchoi/fpm-alpine@${source_digest}"
assert_not_contains "$fixture_dir/rollback-verifier-failure.out" 'both registry moving aliases restored from registry-specific baselines and verified'
: > "$MOCK_DOCKER_LOG"
if ! MOCK_FULL_SUCCESS=1 MANIFEST_RETRY_ATTEMPTS=1 PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  docker.io/woosungchoi/fpm-alpine "$source_digest" ghcr.io/woosungchoi/fpm-alpine "$source_digest" 8.5 "$fixture_dir/rollback-success" \
  >"$fixture_dir/rollback-success.out" 2>&1; then
  fail "rollback full success path failed"
fi
assert_contains "$fixture_dir/rollback-success.out" 'both registry moving aliases restored from registry-specific baselines and verified'
assert_contains "$MOCK_DOCKER_LOG" "docker.io/woosungchoi/fpm-alpine@${source_digest}"
assert_contains "$MOCK_DOCKER_LOG" "ghcr.io/woosungchoi/fpm-alpine@${source_digest}"

if ./scripts/scan-image.sh registry.example/fpm "sha256:$(printf '%064d' 1)" "$fixture_dir/scans" '' >/dev/null 2>&1; then
  fail "Trivy wrapper accepted a missing platform"
fi

assert_contains scripts/create-manifest-failure-issue.sh 'Registry:'
assert_contains scripts/create-manifest-failure-issue.sh 'Digest:'
assert_contains docs/ci-operations.md 'typed `fpm-ghcr-backfill` `repository_dispatch`'
assert_contains docs/ci-operations.md 'registry-specific exact subjects'
assert_contains docs/ci-operations.md 'fpm-publish-recover'
assert_not_contains .github/workflows/publish.yml 'LEGACY_DISABLED_VARIABLE'
assert_not_contains .github/workflows/publish.yml 'legacy_publisher_disabled'
assert_contains scripts/verify-rollback-image.sh 'fsockopen'
assert_contains README.md 'GitHub Actions publisher'

platform_resolver_dir="$fixture_dir/platform-resolver"
platform_resolver_bin="$platform_resolver_dir/bin"
mkdir -p "$platform_resolver_bin"
python3 - "$platform_resolver_dir/index.json" <<'PY'
import json
import sys
from pathlib import Path

payload = {
    "schemaVersion": 2,
    "manifests": [
        {"digest": "sha256:" + "a" * 64, "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": "sha256:" + "b" * 64, "platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}},
        {"digest": "sha256:" + "c" * 64, "platform": {"os": "unknown", "architecture": "unknown"}},
    ],
}
Path(sys.argv[1]).write_text(json.dumps(payload))
PY
cat > "$platform_resolver_bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[ "$*" = 'buildx imagetools inspect --raw registry.example/fpm@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd' ]
status="${MOCK_INSPECT_STATUS:-0}"
if [ "$status" -ne 0 ]; then
  echo 'inspect transport failed' >&2
  exit "$status"
fi
python3 - "$MOCK_INDEX_FILE" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).read_text(), end="")
PY
SH
chmod +x "$platform_resolver_bin/docker"
index_subject="registry.example/fpm@sha256:$(printf 'd%.0s' {1..64})"
amd64_subject="$(MOCK_INDEX_FILE="$platform_resolver_dir/index.json" PATH="$platform_resolver_bin:$PATH" ./scripts/resolve-platform-image.py "$index_subject" linux/amd64)"
arm64_subject="$(MOCK_INDEX_FILE="$platform_resolver_dir/index.json" PATH="$platform_resolver_bin:$PATH" ./scripts/resolve-platform-image.py "$index_subject" linux/arm64)"
[ "$amd64_subject" = "registry.example/fpm@sha256:$(printf 'a%.0s' {1..64})" ] || fail "wrong amd64 platform subject"
[ "$arm64_subject" = "registry.example/fpm@sha256:$(printf 'b%.0s' {1..64})" ] || fail "wrong arm64 platform subject"
[ "$amd64_subject" != "$arm64_subject" ] || fail "multi-platform resolver reused the index digest"
python3 - "$platform_resolver_dir/index.json" "$platform_resolver_dir/duplicate.json" "$platform_resolver_dir/missing.json" <<'PY'
import json
import sys
from pathlib import Path

source = json.loads(Path(sys.argv[1]).read_text())
duplicate = json.loads(json.dumps(source))
duplicate["manifests"].append(duplicate["manifests"][0])
Path(sys.argv[2]).write_text(json.dumps(duplicate))
missing = json.loads(json.dumps(source))
missing["manifests"] = [item for item in missing["manifests"] if (item.get("platform") or {}).get("architecture") != "amd64"]
Path(sys.argv[3]).write_text(json.dumps(missing))
PY
for invalid_index in duplicate missing; do
  if MOCK_INDEX_FILE="$platform_resolver_dir/${invalid_index}.json" PATH="$platform_resolver_bin:$PATH" \
      ./scripts/resolve-platform-image.py "$index_subject" linux/amd64 >/dev/null 2>&1; then
    fail "platform resolver accepted ${invalid_index} descriptor set"
  fi
done
printf '{not-json' > "$platform_resolver_dir/malformed.json"
if MOCK_INDEX_FILE="$platform_resolver_dir/malformed.json" PATH="$platform_resolver_bin:$PATH" \
    ./scripts/resolve-platform-image.py "$index_subject" linux/amd64 >/dev/null 2>&1; then
  fail "platform resolver accepted malformed index JSON"
fi
if MOCK_INSPECT_STATUS=7 MOCK_INDEX_FILE="$platform_resolver_dir/index.json" PATH="$platform_resolver_bin:$PATH" \
    ./scripts/resolve-platform-image.py "$index_subject" linux/amd64 >/dev/null 2>&1; then
  fail "platform resolver ignored inspect transport failure"
fi

signing_repo="$fixture_dir/signing-ref"
mkdir -p "$signing_repo"
git -C "$signing_repo" init -q
git -C "$signing_repo" config user.name fixture
git -C "$signing_repo" config user.email fixture@example.invalid
printf 'pre\n' > "$signing_repo/state"
git -C "$signing_repo" add state
git -C "$signing_repo" commit -qm pre
pre_cutover="$(git -C "$signing_repo" rev-parse HEAD)"
printf 'boundary\n' > "$signing_repo/state"
git -C "$signing_repo" commit -qam boundary
boundary="$(git -C "$signing_repo" rev-parse HEAD)"
git -C "$signing_repo" tag -a archive/php-8.5-final-branch -m 'final 8.5 control branch' "$boundary"
printf 'post\n' > "$signing_repo/state"
git -C "$signing_repo" commit -qam post
post_cutover="$(git -C "$signing_repo" rev-parse HEAD)"
[ "$(cd "$signing_repo" && EXPECTED_BOUNDARY_SHA="$boundary" "$repo_root/scripts/resolve-publisher-signing-ref.sh" "$pre_cutover")" = 8.5 ] || \
  fail "pre-cutover signing identity was not 8.5"
[ "$(cd "$signing_repo" && EXPECTED_BOUNDARY_SHA="$boundary" "$repo_root/scripts/resolve-publisher-signing-ref.sh" "$boundary")" = 8.5 ] || \
  fail "cutover-boundary signing identity was not 8.5"
[ "$(cd "$signing_repo" && EXPECTED_BOUNDARY_SHA="$boundary" "$repo_root/scripts/resolve-publisher-signing-ref.sh" "$post_cutover")" = main ] || \
  fail "post-cutover signing identity was not main"
git -C "$signing_repo" tag -f archive/php-8.5-final-branch "$pre_cutover" >/dev/null
if (cd "$signing_repo" && EXPECTED_BOUNDARY_SHA="$boundary" "$repo_root/scripts/resolve-publisher-signing-ref.sh" "$pre_cutover") >/dev/null 2>&1; then
  fail "signing identity resolver accepted a moved archive tag"
fi
git -C "$signing_repo" tag -fa archive/php-8.5-final-branch -m 'final 8.5 control branch' "$boundary" >/dev/null
git -C "$signing_repo" switch --orphan unrelated >/dev/null
printf 'unrelated\n' > "$signing_repo/state"
git -C "$signing_repo" add state
git -C "$signing_repo" commit -qm unrelated
unrelated="$(git -C "$signing_repo" rev-parse HEAD)"
if (cd "$signing_repo" && EXPECTED_BOUNDARY_SHA="$boundary" "$repo_root/scripts/resolve-publisher-signing-ref.sh" "$unrelated") >/dev/null 2>&1; then
  fail "signing identity resolver accepted unrelated history"
fi

set +e
./scripts/verify-published-image.sh \
  docker.io/example/fpm:8.5 ghcr.io/example/fpm:8.5 \
  "$(printf 'a%.0s' {1..40})" 8.5.8 "$fixture_dir/invalid-signing-ref" invalid-ref \
  >/dev/null 2>&1
invalid_signing_ref_status=$?
set -e
[ "$invalid_signing_ref_status" -eq 64 ] || fail "published verifier did not reject an invalid signing ref at input validation"

python3 - <<'PY'
import re
pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})"
for value in ("2026-07-13T11:42:01Z", "2026-07-13T20:42:01+09:00", "2026-07-13T06:12:01-05:30"):
    assert re.fullmatch(pattern, value), value
for value in ("2026-07-13T11:42:01", "linux/amd64", "2026-07-13"):
    assert not re.fullmatch(pattern, value), value
for path in ("scripts/verify-canary-image.sh", "scripts/verify-published-image.sh"):
    text = open(path).read()
    assert r"(?:Z|[+-]\d{2}:\d{2})" in text, path
PY

echo "publisher policy tests passed"
