#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_file() { [ -f "$1" ] || fail "expected file $1 to exist"; }
assert_executable() { [ -x "$1" ] || fail "expected $1 to be executable"; }
assert_contains() { grep -Fq -- "$2" "$1" || fail "expected $1 to contain: $2"; }
assert_not_contains() { ! grep -Fq -- "$2" "$1" || fail "expected $1 not to contain: $2"; }

assert_file .github/workflows/publish.yml
for script in scripts/verify-published-image.sh scripts/verify-rollback-image.sh scripts/rollback-moving-aliases.sh scripts/scan-image.sh scripts/promote-image.sh scripts/validate-canary-metadata.py scripts/validate-legacy-cutover-evidence.py scripts/resolve-platform-image.py; do
  assert_file "$script"
  assert_executable "$script"
done
assert_file scripts/verify-provenance.py
assert_executable scripts/verify-provenance.py

python3 - <<'PY'
from pathlib import Path
import re
import yaml

path = Path('.github/workflows/publish.yml')
text = path.read_text()
validator_text = Path('scripts/validate-legacy-cutover-evidence.py').read_text()
data = yaml.safe_load(text)
trigger = data.get('on', data.get(True))
assert set(trigger) == {'workflow_dispatch'}, trigger
inputs = trigger['workflow_dispatch']['inputs']
assert inputs['channel']['type'] == 'choice'
assert inputs['channel']['options'] == ['canary', 'production']
assert inputs['channel']['default'] == 'canary'
assert inputs['source_sha']['required'] is True
assert inputs['canary_run_id']['required'] is False
assert inputs['canary_run_attempt']['required'] is False
assert inputs['prior_canary_run_id']['required'] is False
assert inputs['prior_canary_run_attempt']['required'] is False
assert inputs['legacy_cutover_evidence_sha256']['required'] is False
assert data['permissions'] == {}
assert data['concurrency']['cancel-in-progress'] is False
assert data['concurrency']['group'] == "publish-${{ github.event.inputs.channel }}"
assert ':latest' not in text.lower()
assert not re.search(r'(?<![0-9.])8\.[01](?![0-9.])', text)

uses = re.findall(r'^\s*uses:\s*([^\s#]+)(?:\s+#\s*(\S+))?\s*$', text, re.M)
assert uses, 'publisher must use pinned actions'
for ref, comment in uses:
    assert re.fullmatch(r'[^@\s]+@[0-9a-f]{40}', ref), ref
    assert comment, f'missing release comment for {ref}'

jobs = data['jobs']
for name in ('prepare', 'canary', 'production-preflight', 'bootstrap-ghcr-rollback', 'production', 'report-failure'):
    assert name in jobs, name
assert jobs['prepare']['permissions'] == {'actions': 'read', 'contents': 'read'}
assert jobs['canary']['permissions'] == {'contents': 'read', 'packages': 'write', 'id-token': 'write'}
assert jobs['production']['permissions'] == {'actions': 'read', 'contents': 'read', 'packages': 'write'}
assert jobs['production']['environment'] == 'fpm-production'
assert jobs['production-preflight']['permissions'] == {'actions': 'read', 'contents': 'read'}
assert jobs['bootstrap-ghcr-rollback']['environment'] == 'fpm-production'
assert jobs['bootstrap-ghcr-rollback']['permissions'] == {'contents': 'read', 'packages': 'write'}
assert jobs['report-failure']['permissions'] == {'actions': 'read', 'contents': 'read', 'issues': 'write'}

canary = yaml.safe_dump(jobs['canary'], sort_keys=False)
production = yaml.safe_dump(jobs['production'], sort_keys=False)
for required in ('docker/login-action@', 'docker/build-push-action@', 'provenance: mode=max', 'sbom: true',
                 'scripts/verify-published-image.sh', 'scripts/scan-image.sh', 'cosign sign --yes',
                 'github.run_attempt'):
    assert required in canary, required
assert 'refusing to overwrite existing canary tag' in text
assert 'github.event.inputs.channel == \'canary\'' in canary
assert 'docker/build-push-action@' not in production
assert 'github.event.inputs.channel == \'production\'' in production
for required in ('scripts/verify-published-image.sh', 'scripts/promote-image.sh', 'scripts/rollback-moving-aliases.sh',
                 'canary_run_id', 'canary_run_attempt', 'steps.canary.outputs.dockerhub_digest',
                 'steps.canary.outputs.ghcr_digest'):
    assert required in production, required
assert 'test "$SOURCE_SHA" = "$DISPATCH_SHA"' in text
assert "test \"$DISPATCH_REF\" = 'refs/heads/8.5'" in text
assert 'actions/runs/${CANARY_RUN_ID}' in text
assert 'actions/runs/${PRIOR_CANARY_RUN_ID}' in text
assert 'run.get("run_number") != prior.get("run_number", -2) + 1' in text
assert 'current canary is not a complete active matrix' in text
assert 'prior successful canary has no unexpired PHP 8.5 evidence artifact' in text
assert '["versions"].items()' in text
assert 'LEGACY_CUTOVER_EVIDENCE_SHA256' in text
assert 'legacy cutover evidence is not within the 15-minute lease' in validator_text
assert 'Docker Hub legacy publisher is not quiescent' in validator_text
assert 'publisher-bootstrap-${{ github.run_id }}-${{ github.run_attempt }}' in text
assert text.count('./scripts/validate-legacy-cutover-evidence.py') == 3
assert 'bootstrap-evidence.json' in text
for field in ('source_sha', 'dockerhub_resolution_status', 'dockerhub_inspect_exit_code', 'dockerhub_digest_parse_exit_code', 'dockerhub_digest', 'baseline_state', 'baseline_inspect_exit_code', 'cutover_validation_status', 'cutover_validation_exit_code', 'create_status', 'create_exit_code', 'readback_status', 'readback_exit_code', 'readback_digest_parse_exit_code', 'readback_digest', 'verifier_status', 'verifier_exit_code', 'final_status', 'created_at', 'updated_at'):
    assert field in text, field
bootstrap_run = next(step['run'] for step in jobs['bootstrap-ghcr-rollback']['steps'] if step.get('name') == 'Establish idempotent GHCR rollback baselines')
assert bootstrap_run.index('./scripts/validate-legacy-cutover-evidence.py') < bootstrap_run.index('docker buildx imagetools create')
promotion_run = next(step['run'] for step in jobs['production']['steps'] if step.get('name') == 'Promote verified canary digests without rebuilding')
assert promotion_run.index('./scripts/validate-legacy-cutover-evidence.py') < promotion_run.index('./scripts/promote-image.sh "$DOCKERHUB_REPOSITORY"')
production_step_names = [step.get('name') for step in jobs['production']['steps']]
assert 'Re-verify exact canary subjects before promotion' not in production_step_names
assert production_step_names.index('Promote verified canary digests without rebuilding') == production_step_names.index('Load and bind verified canary metadata') + 1
assert './scripts/scan-image.sh' not in promotion_run
metadata_load_run = next(step['run'] for step in jobs['production']['steps'] if step.get('name') == 'Load and bind verified canary metadata')
assert metadata_load_run.index('./scripts/validate-canary-metadata.py') < metadata_load_run.index('output.write(f"dockerhub_digest=')
assert "imagetools inspect \"$1\" | awk '/^Digest:/" not in text
assert '[[ "$REQUESTED_VERSION" =~ ^8\\.[2-5]$ ]]' in text
assert 'gh run download "$CANARY_RUN_ID" --repo "$GITHUB_REPOSITORY"' in text
failure = yaml.safe_dump(jobs['report-failure'], sort_keys=False)
assert 'scripts/create-manifest-failure-issue.sh' in failure
assert "github.ref == 'refs/heads/8.5'" in failure
assert "needs.prepare.result == 'success'" in failure
assert 'active-matrix' not in failure
assert 'failure-minors.txt' in failure
assert 'payload["versions"].items()' in text
assert '"security-only"' in text
assert 'production-preflight' in jobs['production']['needs']
assert 'bootstrap-ghcr-rollback' in jobs['production']['needs']
assert 'Require anonymous GHCR manifest and runtime access' in canary
anonymous_run = next(step['run'] for step in jobs['canary']['steps'] if step.get('name') == 'Require anonymous GHCR manifest and runtime access')
assert 'DOCKER_CONFIG="$anonymous_config" ./scripts/resolve-platform-image.py "$GHCR_SUBJECT" "$platform"' in anonymous_run
assert '--entrypoint php "$platform_subject"' in anonymous_run
assert '--entrypoint php "$GHCR_SUBJECT"' not in anonymous_run
for verifier in ('scripts/verify-published-image.sh', 'scripts/verify-rollback-image.sh'):
    verifier_text = Path(verifier).read_text()
    assert 'resolve-platform-image.py' in verifier_text
    assert '"$platform_subject"' in verifier_text
preflight_run = next(step['run'] for step in jobs['production-preflight']['steps'] if step.get('name') == 'Require verified canary evidence for every production target')
assert 'build/versions.json' in preflight_run
assert 'active-production-targets.tsv' in preflight_run
assert preflight_run.count('./scripts/validate-canary-metadata.py') == 2
assert 'PRIOR_CANARY_RUN_ID' in preflight_run
assert 'prior-8.5' in preflight_run
PY

python3 - <<'PY'
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

workflow = Path(".github/workflows/publish.yml").read_text()
marker = 'python3 - /tmp/canary-run.json /tmp/prior-canary-run.json /tmp/canary-artifacts.json /tmp/prior-canary-artifacts.json'
tail = workflow.split(marker, 1)[1].split("<<'PY'\n", 1)[1]
code = textwrap.dedent(tail.split("\n          PY", 1)[0])
compile(code, "production-canary-contract.py", "exec")

sha = "0123456789abcdef0123456789abcdef01234567"
current = {"id": 102, "conclusion": "success", "event": "workflow_dispatch", "head_sha": sha, "head_branch": "8.5", "path": ".github/workflows/publish.yml", "run_attempt": 1, "run_number": 11}
prior = {"id": 101, "conclusion": "success", "event": "workflow_dispatch", "head_sha": sha, "head_branch": "8.5", "path": ".github/workflows/publish.yml", "run_attempt": 2, "run_number": 10}
current_artifacts = {"artifacts": [{"name": f"publisher-canary-{minor}-102-1", "expired": False} for minor in ("8.2", "8.3", "8.4", "8.5")]}
prior_artifacts = {"artifacts": [{"name": "publisher-canary-8.5-101-2", "expired": False}]}

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    script = root / "contract.py"
    script.write_text(code)
    payloads = [current, prior, current_artifacts, prior_artifacts]
    paths = []
    for index, payload in enumerate(payloads):
        path = root / f"payload-{index}.json"
        path.write_text(json.dumps(payload))
        paths.append(str(path))
    command = [sys.executable, str(script), *paths, sha, "1", "2"]
    subprocess.run(command, check=True)
    current["run_number"] = 12
    Path(paths[0]).write_text(json.dumps(current))
    if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("production canary contract accepted non-consecutive runs")
    current["run_number"] = 11
    Path(paths[0]).write_text(json.dumps(current))
    current_artifacts["artifacts"].pop()
    Path(paths[2]).write_text(json.dumps(current_artifacts))
    if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("production canary contract accepted an incomplete active matrix")
    current_artifacts["artifacts"].append({"name": "publisher-canary-8.5-102-1", "expired": False})
    Path(paths[2]).write_text(json.dumps(current_artifacts))
    prior_artifacts["artifacts"] = [{"name": "publisher-canary-8.2-101-2", "expired": False}]
    Path(paths[3]).write_text(json.dumps(prior_artifacts))
    if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("production canary contract accepted an 8.2-only prior run")
PY

python3 - <<'PY'
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path(".github/workflows/publish.yml").read_text())
steps = workflow["jobs"]["production-preflight"]["steps"]
run_block = next(step["run"] for step in steps if step.get("name") == "Require verified canary evidence for every production target")
source_sha = "0123456789abcdef0123456789abcdef01234567"
current_run, current_attempt = "200", "1"
prior_run, prior_attempt = "100", "2"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    shutil.copytree("scripts", root / "scripts")
    (root / "build").mkdir()
    shutil.copy("build/versions.json", root / "build/versions.json")
    binary = root / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_text(r'''#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

args = sys.argv[1:]
if args[:2] != ["run", "download"]:
    raise SystemExit(97)
run_id = args[2]
name = args[args.index("--name") + 1]
destination = Path(args[args.index("--dir") + 1])
match = re.fullmatch(r"publisher-canary-(8\.[2-5])-([1-9][0-9]*)-([1-9][0-9]*)", name)
if not match or match.group(2) != run_id:
    raise SystemExit(98)
minor, artifact_run, attempt = match.groups()
versions = json.load(open(os.environ["MOCK_VERSIONS"]))["versions"]
payload = {
    "channel": "canary",
    "source_sha": "bad" if name == os.environ.get("MOCK_CORRUPT_ARTIFACT") else os.environ["MOCK_SOURCE_SHA"],
    "php_minor": minor,
    "php_patch": versions[minor]["patch"],
    "run_id": int(artifact_run),
    "run_attempt": int(attempt),
    "dockerhub_digest": "sha256:" + "1" * 64,
    "ghcr_digest": "sha256:" + "2" * 64,
}
destination.mkdir(parents=True, exist_ok=True)
(destination / "canary-metadata.json").write_text(json.dumps(payload))
with open(os.environ["MOCK_DOWNLOAD_LOG"], "a") as log:
    log.write(name + "\n")
''')
    gh.chmod(0o755)
    script = root / "preflight.sh"
    script.write_text("#!/usr/bin/env bash\n" + run_block)
    script.chmod(0o755)
    log = root / "downloads.log"
    env = os.environ.copy()
    env.update({
        "PATH": f"{binary}:{env['PATH']}",
        "GITHUB_REPOSITORY": "example/image",
        "CANARY_RUN_ID": current_run,
        "CANARY_RUN_ATTEMPT": current_attempt,
        "PRIOR_CANARY_RUN_ID": prior_run,
        "PRIOR_CANARY_RUN_ATTEMPT": prior_attempt,
        "EXPECTED_SOURCE_SHA": source_sha,
        "MOCK_SOURCE_SHA": source_sha,
        "MOCK_VERSIONS": str(root / "build/versions.json"),
        "MOCK_DOWNLOAD_LOG": str(log),
    })
    result = subprocess.run([str(script)], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        raise SystemExit("aggregate canary content preflight rejected valid evidence")
    expected_names = {f"publisher-canary-{minor}-{current_run}-{current_attempt}" for minor in ("8.2", "8.3", "8.4", "8.5")}
    expected_names.add(f"publisher-canary-8.5-{prior_run}-{prior_attempt}")
    observed_names = set(log.read_text().splitlines())
    if observed_names != expected_names:
        raise SystemExit(f"aggregate preflight downloads mismatch: {sorted(observed_names)}")

    shutil.rmtree(root / "production-preflight")
    log.unlink()
    env["MOCK_CORRUPT_ARTIFACT"] = f"publisher-canary-8.3-{current_run}-{current_attempt}"
    result = subprocess.run([str(script)], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode == 0:
        raise SystemExit("aggregate preflight accepted corrupt current 8.3 metadata content")
PY

python3 - <<'PY'
import json
import subprocess
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    evidence = Path(tmp)
    payload = {
        "channel": "canary",
        "source_sha": "0123456789abcdef0123456789abcdef01234567",
        "php_minor": "8.5",
        "php_patch": "8.5.8",
        "run_id": 123,
        "run_attempt": True,
        "dockerhub_digest": "sha256:" + "1" * 64,
        "ghcr_digest": "sha256:" + "2" * 64,
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

payload = {
    "schemaVersion": 1,
    "source_sha": sha,
    "captured_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    "dockerhub": {"build_rule_active": False, "in_flight_builds": 0},
    "github": {"legacy_webhook_present": False},
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
payload["schemaVersion"] = True
if run(payload) == 0:
    raise SystemExit("boolean true schema version was accepted")
payload["schemaVersion"] = 1
payload["captured_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=16)).isoformat().replace("+00:00", "Z")
if run(payload) == 0:
    raise SystemExit("stale legacy cutover evidence was accepted")
PY

python3 - <<'PY'
import base64
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path(".github/workflows/publish.yml").read_text())
steps = workflow["jobs"]["bootstrap-ghcr-rollback"]["steps"]
run_block = next(step["run"] for step in steps if step.get("name") == "Establish idempotent GHCR rollback baselines")
source_sha = "0123456789abcdef0123456789abcdef01234567"
evidence = {
    "schemaVersion": 1,
    "source_sha": source_sha,
    "captured_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    "dockerhub": {"build_rule_active": False, "in_flight_builds": 0},
    "github": {"legacy_webhook_present": False},
}
raw = json.dumps(evidence, separators=(",", ":"), sort_keys=True).encode()
digest = hashlib.sha256(raw).hexdigest()

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    shutil.copytree("scripts", root / "scripts")
    binary = root / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(r'''#!/usr/bin/env bash
set -uo pipefail
if [ "$1 $2 $3" = "buildx imagetools inspect" ]; then
  ref="${@: -1}"
  if [[ "$ref" == docker.io/* ]]; then
    [ "${MOCK_MODE:-success}" = dockerhub-fail ] && exit 7
    printf 'Digest: sha256:%064d\n' 1
    exit 0
  fi
  if [ -f "$MOCK_STATE" ]; then
    printf 'Digest: sha256:%064d\n' 1
    [ "${MOCK_MODE:-success}" = readback-fail ] && exit 1
    [ "${MOCK_MODE:-success}" = readback-exit-2 ] && exit 2
    [ "${MOCK_MODE:-success}" = readback-exit-9 ] && exit 9
    exit 0
  fi
  printf 'ERROR: %s: not found\n' "$ref" >&2
  exit 1
fi
if [ "$1 $2 $3" = "buildx imagetools create" ]; then
  : > "$MOCK_STATE"
  exit 0
fi
exit 97
''')
    docker.chmod(0o755)
    script = root / "bootstrap.sh"
    script.write_text("#!/usr/bin/env bash\n" + run_block)
    script.chmod(0o755)
    env = os.environ.copy()
    env.update({
        "PATH": f"{binary}:{env['PATH']}",
        "MOCK_STATE": str(root / "created"),
        "MOCK_MODE": "readback-fail",
        "MATRIX_JSON": json.dumps({"include": [{"php_minor": "8.5"}]}),
        "DOCKERHUB_REPOSITORY": "docker.io/example/image",
        "GHCR_REPOSITORY": "ghcr.io/example/image",
        "EXPECTED_SOURCE_SHA": source_sha,
        "LEGACY_EVIDENCE_SHA256_INPUT": digest,
        "LEGACY_EVIDENCE_SHA256_VARIABLE": digest,
        "LEGACY_EVIDENCE_B64": base64.b64encode(raw).decode(),
        "RUN_ID": "123",
        "RUN_ATTEMPT": "2",
    })
    result = subprocess.run([str(script)], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode == 0:
        raise SystemExit("bootstrap mock unexpectedly accepted failed post-create read-back")
    record = json.loads((root / "publisher-reports/bootstrap-8.5/bootstrap-evidence.json").read_text())
    expected = {
        "run_id": 123,
        "run_attempt": 2,
        "source_sha": source_sha,
        "minor": "8.5",
        "dockerhub_resolution_status": "success",
        "dockerhub_inspect_exit_code": 0,
        "dockerhub_digest_parse_exit_code": 0,
        "baseline_state": "absent",
        "baseline_inspect_exit_code": 1,
        "create_status": "success",
        "create_exit_code": 0,
        "readback_status": "failed",
        "readback_exit_code": 1,
        "readback_digest_parse_exit_code": None,
        "readback_digest": None,
        "verifier_status": "not_run",
        "final_status": "failed",
    }
    for key, value in expected.items():
        if record.get(key) != value:
            raise SystemExit(f"bootstrap evidence mismatch for {key}: {record.get(key)!r}")

    (root / "created").unlink()
    for raw_exit in (2, 9):
        env["MOCK_MODE"] = f"readback-exit-{raw_exit}"
        result = subprocess.run([str(script)], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != raw_exit:
            raise SystemExit(f"bootstrap did not preserve raw read-back exit {raw_exit}: {result.returncode}")
        record = json.loads((root / "publisher-reports/bootstrap-8.5/bootstrap-evidence.json").read_text())
        if record.get("readback_exit_code") != raw_exit or record.get("readback_digest_parse_exit_code") is not None or record.get("final_status") != "failed":
            raise SystemExit(f"bootstrap evidence did not preserve raw read-back exit {raw_exit}")
        (root / "created").unlink()

    env["MOCK_MODE"] = "dockerhub-fail"
    result = subprocess.run([str(script)], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 7:
        raise SystemExit(f"bootstrap did not preserve early Docker Hub exit 7: {result.returncode}")
    record = json.loads((root / "publisher-reports/bootstrap-8.5/bootstrap-evidence.json").read_text())
    expected = {
        "dockerhub_resolution_status": "failed",
        "dockerhub_inspect_exit_code": 7,
        "dockerhub_digest_parse_exit_code": None,
        "dockerhub_digest": None,
        "baseline_state": "not_started",
        "create_status": "not_attempted",
        "readback_status": "not_attempted",
        "verifier_status": "not_run",
        "final_status": "failed",
    }
    for key, value in expected.items():
        if record.get(key) != value:
            raise SystemExit(f"early bootstrap evidence mismatch for {key}: {record.get(key)!r}")

    verifier = root / "scripts/verify-rollback-image.sh"
    verifier.write_text("#!/usr/bin/env bash\nset -euo pipefail\nmkdir -p \"$4\"\nprintf '{\"status\":\"passed\"}\\n' > \"$4/verifier.json\"\n")
    verifier.chmod(0o755)
    env["MOCK_MODE"] = "success"
    result = subprocess.run([str(script)], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        raise SystemExit("bootstrap full-success mock failed")
    record = json.loads((root / "publisher-reports/bootstrap-8.5/bootstrap-evidence.json").read_text())
    expected = {
        "baseline_state": "absent",
        "baseline_inspect_exit_code": 1,
        "dockerhub_resolution_status": "success",
        "dockerhub_inspect_exit_code": 0,
        "dockerhub_digest_parse_exit_code": 0,
        "create_status": "success",
        "create_exit_code": 0,
        "readback_status": "success",
        "readback_exit_code": 0,
        "readback_digest_parse_exit_code": 0,
        "readback_digest": "sha256:" + "0" * 63 + "1",
        "verifier_status": "success",
        "verifier_exit_code": 0,
        "final_status": "success",
    }
    for key, value in expected.items():
        if record.get(key) != value:
            raise SystemExit(f"bootstrap success evidence mismatch for {key}: {record.get(key)!r}")
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
assert_contains scripts/verify-published-image.sh '@refs/heads/8\.5$'
assert_contains scripts/verify-rollback-image.sh 'rollback registry platform config/layer parity verified'
assert_not_contains scripts/verify-rollback-image.sh 'build/versions.json'
assert_contains scripts/rollback-moving-aliases.sh 'both registries were attempted'
assert_contains scripts/rollback-moving-aliases.sh 'both registry moving aliases restored and verified'
assert_contains scripts/rollback-moving-aliases.sh '${DOCKERHUB_REPOSITORY}@${DOCKERHUB_DIGEST}'
assert_contains scripts/rollback-moving-aliases.sh '${GHCR_REPOSITORY}@${GHCR_DIGEST}'
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
  PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only \
    registry.example/fpm "$source_digest" "$minor" "$patch" "$source_sha" 20260711 >/dev/null
  assert_contains "$MOCK_DOCKER_LOG" "registry.example/fpm:sha-${minor}-${source_sha:0:12}-${digest_hex}"
done
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'
export MOCK_CONFLICT_REF="registry.example/fpm:8.5.8-20260711-${digest_hex}"
if PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only \
  registry.example/fpm "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/conflict.out" 2>&1; then
  fail "promotion preflight accepted a conflicting immutable tag"
fi
assert_contains "$fixture_dir/conflict.out" 'immutable tag already points to another digest'
unset MOCK_CONFLICT_REF
: > "$MOCK_DOCKER_LOG"
if MOCK_AUTH_ERROR=1 PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only \
  registry.example/fpm "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/auth-error.out" 2>&1; then
  fail "promotion preflight treated a credential-helper error as tag absence"
fi
assert_contains "$fixture_dir/auth-error.out" 'credential helper binary not found'
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'
: > "$MOCK_DOCKER_LOG"
if MOCK_AUTH_ERROR=1 MOCK_AUTH_ERROR_STATUS=2 PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only \
  registry.example/fpm "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/auth-error-exit2.out" 2>&1; then
  fail "promotion preflight treated a credential-helper exit 2 as tag absence"
fi
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'
: > "$MOCK_DOCKER_LOG"
if MOCK_MIXED_ERROR=1 PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only \
  registry.example/fpm "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/mixed-error.out" 2>&1; then
  fail "promotion preflight accepted mixed auth/not-found output"
fi
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'
: > "$MOCK_DOCKER_LOG"
if MOCK_MULTIPLE_EXISTING=1 PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only \
  registry.example/fpm "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/multiple-existing.out" 2>&1; then
  fail "promotion preflight accepted ambiguous multiple-digest output"
fi
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'
: > "$MOCK_DOCKER_LOG"
if MOCK_UNRELATED_ERROR=1 PATH="$mock_bin:$PATH" ./scripts/promote-image.sh --check-only \
  registry.example/fpm "$source_digest" 8.5 8.5.8 "$source_sha" 20260711 \
  >"$fixture_dir/unrelated-error.out" 2>&1; then
  fail "promotion preflight accepted not-found output for an unrelated ref"
fi
assert_not_contains "$MOCK_DOCKER_LOG" 'imagetools create'

cat > "$mock_bin/docker" <<'SH'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${MOCK_DOCKER_LOG:?}"
if [ "${1:-}" = buildx ] && [ "${2:-}" = imagetools ] && [ "${3:-}" = create ]; then
  if [ "${MOCK_ROLLBACK_DH_FAIL:-0}" = 1 ] && [[ "$*" == *dockerhub.example/fpm:8.5* ]]; then
    exit 1
  fi
  if [ "${MOCK_ROLLBACK_GHCR_FAIL:-0}" = 1 ] && [[ "$*" == *ghcr.example/fpm:8.5* ]]; then
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
  if [ "${MOCK_MULTIPLE_DIGESTS:-0}" = 1 ]; then
    printf 'Digest: sha256:%064d\n' 8
  fi
  if [ "${MOCK_INSPECT_FAIL_WITH_DIGEST:-0}" = 1 ]; then
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
  dockerhub.example/fpm "$source_digest" ghcr.example/fpm "$source_digest" 8.5 "$fixture_dir/rollback" \
  >"$fixture_dir/rollback-failure.out" 2>&1; then
  fail "rollback unexpectedly succeeded when Docker Hub restore failed"
fi
assert_contains "$MOCK_DOCKER_LOG" 'dockerhub.example/fpm:8.5'
assert_contains "$MOCK_DOCKER_LOG" 'ghcr.example/fpm:8.5'
assert_contains "$fixture_dir/rollback-failure.out" 'both registries were attempted'
: > "$MOCK_DOCKER_LOG"
if MOCK_ROLLBACK_GHCR_FAIL=1 PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  dockerhub.example/fpm "$source_digest" ghcr.example/fpm "$source_digest" 8.5 "$fixture_dir/rollback" \
  >"$fixture_dir/rollback-ghcr-failure.out" 2>&1; then
  fail "rollback unexpectedly succeeded when GHCR restore failed"
fi
assert_contains "$MOCK_DOCKER_LOG" 'dockerhub.example/fpm:8.5'
assert_contains "$MOCK_DOCKER_LOG" 'ghcr.example/fpm:8.5'
: > "$MOCK_DOCKER_LOG"
if MOCK_INSPECT_FAIL_WITH_DIGEST=1 PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  dockerhub.example/fpm "$source_digest" ghcr.example/fpm "$source_digest" 8.5 "$fixture_dir/rollback" \
  >"$fixture_dir/rollback-readback-failure.out" 2>&1; then
  fail "rollback accepted digest output from a failed alias inspect"
fi
assert_contains "$fixture_dir/rollback-readback-failure.out" 'rollback read-back failed'
assert_not_contains "$fixture_dir/rollback-readback-failure.out" 'both registry moving aliases restored and verified'
: > "$MOCK_DOCKER_LOG"
if MOCK_MULTIPLE_DIGESTS=1 PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  dockerhub.example/fpm "$source_digest" ghcr.example/fpm "$source_digest" 8.5 "$fixture_dir/rollback" \
  >"$fixture_dir/rollback-multiple-digests.out" 2>&1; then
  fail "rollback accepted ambiguous multiple-digest read-back"
fi
assert_not_contains "$fixture_dir/rollback-multiple-digests.out" 'both registry moving aliases restored and verified'
: > "$MOCK_DOCKER_LOG"
if PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  dockerhub.example/fpm "$source_digest" ghcr.example/fpm "$source_digest" 8.5 "$fixture_dir/rollback" \
  >"$fixture_dir/rollback-verifier-failure.out" 2>&1; then
  fail "rollback ignored an exact-digest verifier failure"
fi
assert_contains "$MOCK_DOCKER_LOG" "dockerhub.example/fpm@${source_digest}"
assert_not_contains "$fixture_dir/rollback-verifier-failure.out" 'both registry moving aliases restored and verified'
: > "$MOCK_DOCKER_LOG"
if ! MOCK_FULL_SUCCESS=1 MANIFEST_RETRY_ATTEMPTS=1 PATH="$mock_bin:$PATH" ./scripts/rollback-moving-aliases.sh \
  dockerhub.example/fpm "$source_digest" ghcr.example/fpm "$source_digest" 8.5 "$fixture_dir/rollback-success" \
  >"$fixture_dir/rollback-success.out" 2>&1; then
  fail "rollback full success path failed"
fi
assert_contains "$fixture_dir/rollback-success.out" 'both registry moving aliases restored and verified'
assert_contains "$MOCK_DOCKER_LOG" "dockerhub.example/fpm@${source_digest}"
assert_contains "$MOCK_DOCKER_LOG" "ghcr.example/fpm@${source_digest}"

if ./scripts/scan-image.sh registry.example/fpm "sha256:$(printf '%064d' 1)" "$fixture_dir/scans" '' >/dev/null 2>&1; then
  fail "Trivy wrapper accepted a missing platform"
fi

assert_contains scripts/create-manifest-failure-issue.sh 'Registry:'
assert_contains scripts/create-manifest-failure-issue.sh 'Digest:'
assert_contains docs/ci-operations.md 'manual-only'
assert_contains docs/ci-operations.md 'verified canary digest'
assert_contains .github/workflows/publish.yml 'LEGACY_DISABLED_VARIABLE'
assert_contains .github/workflows/publish.yml 'legacy_publisher_disabled'
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

echo "publisher policy tests passed"
