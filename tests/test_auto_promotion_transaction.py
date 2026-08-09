from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSACTION = ROOT / "scripts" / "promote-auto-canaries.sh"
PLAN_VALIDATOR = ROOT / "scripts" / "validate-auto-promotion-plan.py"
MINORS = ("8.2", "8.3", "8.4", "8.5")
PATCHES = {"8.2": "8.2.33", "8.3": "8.3.32", "8.4": "8.4.23", "8.5": "8.5.8"}
SOURCE_SHA = "d" * 40
WORKFLOW_SHA = "e" * 40
DOCKERHUB = "docker.io/woosungchoi/fpm-alpine"
GHCR = "ghcr.io/woosungchoi/fpm-alpine"
RUN_ID = 123
RUN_ATTEMPT = 1


def digest(number: int) -> str:
    return f"sha256:{number:064x}"


@dataclass
class Fixture:
    env: dict[str, str]
    plan: Path
    state_path: Path
    log: Path
    baseline: dict[str, tuple[str, str]]
    targets: dict[str, tuple[str, str]]


class AutoPromotionTransactionTests(unittest.TestCase):
    @staticmethod
    def write_executable(path: Path, content: str) -> None:
        path.write_text(content)
        path.chmod(0o755)

    def fixture(self, root: Path, mode: str) -> Fixture:
        scripts = root / "scripts"
        fake_bin = root / "bin"
        build = root / "build"
        scripts.mkdir()
        fake_bin.mkdir()
        build.mkdir()
        for source in (TRANSACTION, PLAN_VALIDATOR):
            shutil.copy2(source, scripts / source.name)
            (scripts / source.name).chmod(0o755)

        versions = {
            "versions": {
                minor: {"patch": PATCHES[minor], "support": "active"}
                for minor in MINORS
            }
        }
        (build / "versions.json").write_text(json.dumps(versions))

        state: dict[str, str] = {}
        units = []
        baseline: dict[str, tuple[str, str]] = {}
        targets: dict[str, tuple[str, str]] = {}
        target_map = {}
        for index, minor in enumerate(MINORS, start=1):
            previous_dockerhub = digest(100 + index)
            previous_ghcr = digest(300 + index)
            target_ghcr = digest(200 + index)
            target_dockerhub = digest(500 + index)
            rollback_dockerhub_backup = previous_dockerhub
            canary_ref = f"{GHCR}:canary-{minor}-{RUN_ID}-{RUN_ATTEMPT}"
            rollback_ghcr_ref = f"{GHCR}:rollback-auto-ghcr-{minor}-{RUN_ID}-{RUN_ATTEMPT}"
            state[f"{DOCKERHUB}:{minor}"] = previous_dockerhub
            state[f"{GHCR}:{minor}"] = previous_ghcr
            state[canary_ref] = target_ghcr
            state[rollback_ghcr_ref] = previous_ghcr
            if mode == "automatic":
                rollback_dockerhub_ref = (
                    f"{GHCR}:rollback-auto-dockerhub-{minor}-{RUN_ID}-{RUN_ATTEMPT}"
                )
                state[rollback_dockerhub_ref] = rollback_dockerhub_backup
                dockerhub_source = None
                frozen_dockerhub_target = None
                frozen_backup = rollback_dockerhub_backup
                target_map[target_ghcr] = target_dockerhub
            else:
                rollback_dockerhub_ref = None
                dockerhub_source = previous_dockerhub
                frozen_dockerhub_target = previous_dockerhub
                frozen_backup = None
                target_dockerhub = previous_dockerhub
            units.append(
                {
                    "php_minor": minor,
                    "php_patch": PATCHES[minor],
                    "canary_ref": canary_ref,
                    "target_ghcr_digest": target_ghcr,
                    "target_dockerhub_digest": frozen_dockerhub_target,
                    "dockerhub_source_digest": dockerhub_source,
                    "previous_dockerhub_digest": previous_dockerhub,
                    "previous_ghcr_digest": previous_ghcr,
                    "rollback_dockerhub_ref": rollback_dockerhub_ref,
                    "rollback_dockerhub_backup_digest": frozen_backup,
                    "rollback_ghcr_ref": rollback_ghcr_ref,
                    "rollback_ghcr_digest": previous_ghcr,
                    "platforms": ["linux/amd64", "linux/arm64"],
                }
            )
            baseline[minor] = (previous_dockerhub, previous_ghcr)
            targets[minor] = (target_dockerhub, target_ghcr)

        plan_payload = {
            "schema_version": 1,
            "operation": mode,
            "repository": "woosungchoi/fpm-alpine",
            "workflow_path": ".github/workflows/dependency-auto-publish.yml",
            "workflow_sha": WORKFLOW_SHA,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "source_sha": SOURCE_SHA,
            "release_units": units,
        }
        state_path = root / "state.json"
        state_path.write_text(json.dumps(state, sort_keys=True))
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(plan_payload, indent=2, sort_keys=True) + "\n")
        target_map_path = root / "target-map.json"
        target_map_path.write_text(json.dumps(target_map, sort_keys=True))
        log_path = root / "operations.log"
        log_path.write_text("")

        self.write_executable(
            scripts / "resolve-image-digest.sh",
            """#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
ref = sys.argv[1]
unavailable = set(filter(None, (
    os.environ.get('MOCK_UNAVAILABLE_REF', '') + ',' +
    os.environ.get('MOCK_UNAVAILABLE_REFS', '')
).split(',')))
if ref in unavailable:
    raise SystemExit(1)
if re.fullmatch(r'.+@sha256:[0-9a-f]{64}', ref):
    print(ref.rsplit('@', 1)[1])
else:
    state = json.loads(Path(os.environ['MOCK_STATE']).read_text())
    value = state.get(ref)
    if value is None:
        raise SystemExit(1)
    print(value)
""",
        )
        self.write_executable(
            scripts / "promote-image.sh",
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
check = bool(args and args[0] == '--check-only')
if check:
    args = args[1:]
if len(args) != 9 or args[0] != '--policy':
    raise SystemExit(64)
policy, target_repo, source_repo, source_digest, minor = args[1:6]
with open(os.environ['MOCK_LOG'], 'a') as log:
    log.write(f"{'check' if check else 'mutate'}|{policy}|{minor}|{source_digest}\\n")
if check:
    raise SystemExit(0)
state_path = Path(os.environ['MOCK_STATE'])
state = json.loads(state_path.read_text())
if policy == 'moving-only':
    mapping = json.loads(Path(os.environ['MOCK_TARGET_MAP']).read_text())
    state[f"{target_repo}:{minor}"] = mapping[source_digest]
else:
    state[f"{target_repo}:{minor}"] = source_digest
state_path.write_text(json.dumps(state, sort_keys=True))
if os.environ.get('MOCK_FAIL_PROMOTE') == f"{policy}:{minor}":
    raise SystemExit(7)
""",
        )
        self.write_executable(
            scripts / "rollback-moving-aliases.sh",
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
_, dockerhub, previous_dockerhub, ghcr, previous_ghcr, minor, report_dir = sys.argv
state_path = Path(os.environ['MOCK_STATE'])
state = json.loads(state_path.read_text())
unavailable = set(filter(None, (
    os.environ.get('MOCK_UNAVAILABLE_REF', '') + ',' +
    os.environ.get('MOCK_UNAVAILABLE_REFS', '')
).split(',')))
status = 0
if os.environ.get('RESTORE_DOCKERHUB', '1') == '1':
    primary = os.environ['DOCKERHUB_ROLLBACK_SOURCE']
    fallback = os.environ.get('DOCKERHUB_ROLLBACK_FALLBACK_SOURCE', '')
    if primary in unavailable and (not fallback or fallback in unavailable):
        status = 1
    else:
        state[f"{dockerhub}:{minor}"] = previous_dockerhub
if os.environ.get('RESTORE_GHCR', '1') == '1':
    if os.environ['GHCR_ROLLBACK_SOURCE'] in unavailable:
        status = 1
    else:
        state[f"{ghcr}:{minor}"] = previous_ghcr
state_path.write_text(json.dumps(state, sort_keys=True))
Path(report_dir).mkdir(parents=True, exist_ok=True)
with open(os.environ['MOCK_LOG'], 'a') as log:
    log.write(f"rollback-dual|{minor}|{previous_dockerhub}|{previous_ghcr}\\n")
if os.environ.get('MOCK_FAIL_ROLLBACK') == minor:
    raise SystemExit(8)
raise SystemExit(status)
""",
        )
        self.write_executable(
            scripts / "verify-published-image.sh",
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
minor = sys.argv[4].rsplit('.', 1)[0]
Path(sys.argv[5]).mkdir(parents=True, exist_ok=True)
with open(os.environ['MOCK_LOG'], 'a') as log:
    signature = os.environ.get('VERIFY_DOCKERHUB_SIGNATURE')
    docker_config = os.environ.get('DOCKER_CONFIG')
    log.write(f"verify|{minor}|{sys.argv[1]}|{sys.argv[2]}|dockerhub-signature={signature}|docker-config={docker_config}\\n")
if os.environ.get('MOCK_LATE_DRIFT') == '1' and minor == '8.5':
    state_path = Path(os.environ['MOCK_STATE'])
    state = json.loads(state_path.read_text())
    state['ghcr.io/woosungchoi/fpm-alpine:8.2'] = 'sha256:' + '9' * 64
    state_path.write_text(json.dumps(state, sort_keys=True))
if os.environ.get('MOCK_FAIL_VERIFY') == minor:
    raise SystemExit(9)
""",
        )
        self.write_executable(
            scripts / "verify-rollback-image.sh",
            """#!/usr/bin/env python3
import os, sys
from pathlib import Path
minor = sys.argv[3]
Path(sys.argv[4]).mkdir(parents=True, exist_ok=True)
with open(os.environ['MOCK_LOG'], 'a') as log:
    log.write(f"parity|{minor}|{sys.argv[1]}|{sys.argv[2]}\\n")
if os.environ.get('MOCK_FAIL_BASELINE_MINOR') == minor:
    raise SystemExit(10)
""",
        )
        self.write_executable(
            scripts / "verify-image-parity.py",
            """#!/usr/bin/env python3
import os, sys
from pathlib import Path
output = Path(sys.argv[sys.argv.index('--output') + 1])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text('{}\\n')
with open(os.environ['MOCK_LOG'], 'a') as log:
    log.write(f"descriptor-parity|{sys.argv[1]}|{sys.argv[2]}\\n")
if os.environ.get('MOCK_FAIL_BACKUP_PARITY') == sys.argv[1]:
    raise SystemExit(12)
""",
        )
        self.write_executable(
            scripts / "verify-published-dockerhub-image.sh",
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(sys.argv[4]).mkdir(parents=True, exist_ok=True)
versions_path = Path(os.environ.get('AUTO_PROMOTION_VERSIONS_FILE', 'build/versions.json'))
versions = json.loads(versions_path.read_text())['versions']
minor = sys.argv[3].rsplit('.', 1)[0]
if versions[minor]['patch'] != sys.argv[3]:
    raise SystemExit(13)
with open(os.environ['MOCK_LOG'], 'a') as log:
    log.write(f"classify-dockerhub|{sys.argv[1]}|versions={versions_path}\\n")
if os.environ.get('MOCK_FAIL_CLASSIFY') == sys.argv[1]:
    raise SystemExit(11)
""",
        )
        self.write_executable(
            fake_bin / "cosign",
            """#!/usr/bin/env python3
import os, sys
with open(os.environ['MOCK_LOG'], 'a') as log:
    log.write('cosign|' + '|'.join(sys.argv[1:]) + '\\n')
""",
        )
        self.write_executable(
            fake_bin / "docker",
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:3] != ['buildx', 'imagetools', 'create'] or '--tag' not in args:
    raise SystemExit(64)
tag = args[args.index('--tag') + 1]
source = args[-1]
source_digest = source.rsplit('@', 1)[1]
state_path = Path(os.environ['MOCK_STATE'])
state = json.loads(state_path.read_text())
state[tag] = source_digest
state_path.write_text(json.dumps(state, sort_keys=True))
with open(os.environ['MOCK_LOG'], 'a') as log:
    log.write(f"restore-ghcr|{tag}|{source_digest}\\n")
""",
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env['PATH']}",
                "MOCK_STATE": str(state_path),
                "MOCK_LOG": str(log_path),
                "MOCK_TARGET_MAP": str(target_map_path),
            }
        )
        return Fixture(env, plan_path, state_path, log_path, baseline, targets)

    @staticmethod
    def run_transaction(
        root: Path, mode: str, fixture: Fixture
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "scripts/promote-auto-canaries.sh",
                mode,
                str(fixture.plan),
                str(root / "reports"),
            ],
            cwd=root,
            env=fixture.env,
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def state(fixture: Fixture) -> dict[str, str]:
        return json.loads(fixture.state_path.read_text())

    def test_automatic_accepts_semantic_parity_with_distinct_registry_digests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            result = self.run_transaction(root, "automatic", fixture)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = self.state(fixture)
            for minor in MINORS:
                dockerhub_target, ghcr_target = fixture.targets[minor]
                self.assertNotEqual(dockerhub_target, ghcr_target)
                self.assertEqual(state[f"{DOCKERHUB}:{minor}"], dockerhub_target)
                self.assertEqual(state[f"{GHCR}:{minor}"], ghcr_target)
            payload = json.loads((root / "reports/transaction-result.json").read_text())
            self.assertEqual(payload["status"], "verified")
            self.assertTrue(
                all(
                    row["dockerhub_digest"] != row["ghcr_digest"]
                    for row in payload["release_units"]
                )
            )
            verify_lines = [
                line for line in fixture.log.read_text().splitlines()
                if line.startswith("verify|")
            ]
            self.assertEqual(len(verify_lines), len(MINORS))
            self.assertTrue(all("|docker-config=None" not in line for line in verify_lines))
            self.assertTrue(all("|docker-config=" in line for line in verify_lines))

    def test_automatic_rejects_failed_semantic_baseline_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            fixture.env["MOCK_FAIL_BASELINE_MINOR"] = "8.3"
            result = self.run_transaction(root, "automatic", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("mutate|", fixture.log.read_text())
            self.assertEqual(self.state(fixture)[f"{DOCKERHUB}:8.3"], fixture.baseline["8.3"][0])

    def test_automatic_rejects_corrupt_dockerhub_backup_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            fixture.env["MOCK_FAIL_BACKUP_PARITY"] = (
                f"{DOCKERHUB}@{fixture.baseline['8.2'][0]}"
            )
            result = self.run_transaction(root, "automatic", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("mutate|", fixture.log.read_text())
            state = self.state(fixture)
            for minor in MINORS:
                self.assertEqual(state[f"{DOCKERHUB}:{minor}"], fixture.baseline[minor][0])
                self.assertEqual(state[f"{GHCR}:{minor}"], fixture.baseline[minor][1])

    def test_automatic_partial_failure_rolls_back_every_attempted_minor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            fixture.env["MOCK_FAIL_PROMOTE"] = "evidence:8.3"
            result = self.run_transaction(root, "automatic", fixture)
            self.assertNotEqual(result.returncode, 0)
            state = self.state(fixture)
            for minor in MINORS:
                self.assertEqual(state[f"{DOCKERHUB}:{minor}"], fixture.baseline[minor][0])
                self.assertEqual(state[f"{GHCR}:{minor}"], fixture.baseline[minor][1])
            log = fixture.log.read_text()
            self.assertIn("rollback-dual|8.2|", log)
            self.assertIn("rollback-dual|8.3|", log)
            self.assertNotIn("rollback-dual|8.4|", log)

    def test_transaction_wide_final_readback_detects_late_alias_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            fixture.env["MOCK_LATE_DRIFT"] = "1"
            result = self.run_transaction(root, "automatic", fixture)
            self.assertNotEqual(result.returncode, 0)
            state = self.state(fixture)
            self.assertEqual(state[f"{GHCR}:8.2"], "sha256:" + "9" * 64)
            for minor in MINORS:
                self.assertEqual(state[f"{DOCKERHUB}:{minor}"], fixture.targets[minor][0])
                if minor != "8.2":
                    self.assertEqual(state[f"{GHCR}:{minor}"], fixture.targets[minor][1])
            self.assertIn("no moving aliases were modified", result.stderr)
            self.assertNotIn("rollback-dual|", fixture.log.read_text())
            self.assertFalse((root / "reports/transaction-result.json").exists())

    def test_failed_or_signalled_exit_removes_verified_result_before_rollback(self) -> None:
        transaction = TRANSACTION.read_text()
        self.assertNotIn("transaction_complete=1", transaction)
        self.assertIn('if [ "$status" -ne 0 ]', transaction)
        self.assertIn('rm -f "$REPORT_DIR/transaction-result.json"', transaction)
        self.assertNotIn(
            "trap - EXIT INT TERM\nprintf 'auto promotion transaction verified",
            transaction,
        )

    def test_transaction_result_write_failure_rolls_back_every_promoted_minor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            (root / "reports/transaction-result.json").mkdir(parents=True)
            result = self.run_transaction(root, "automatic", fixture)
            self.assertNotEqual(result.returncode, 0)
            state = self.state(fixture)
            for minor in MINORS:
                self.assertEqual(state[f"{DOCKERHUB}:{minor}"], fixture.baseline[minor][0])
                self.assertEqual(state[f"{GHCR}:{minor}"], fixture.baseline[minor][1])
                self.assertIn(f"rollback-dual|{minor}|", fixture.log.read_text())

    def test_rollback_classification_write_failure_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            classification = root / "reports/rollback-classification.tsv"
            classification.mkdir(parents=True)
            fixture.env["MOCK_FAIL_PROMOTE"] = "evidence:8.2"
            result = self.run_transaction(root, "automatic", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("classification evidence could not be initialized", result.stderr)
            self.assertNotIn("all known attempted release units rolled back", result.stdout)
            self.assertNotIn("rollback-dual|", fixture.log.read_text())

    def test_backfill_failure_restores_only_ghcr_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "backfill-ghcr")
            fixture.env["MOCK_FAIL_VERIFY"] = "8.3"
            result = self.run_transaction(root, "backfill-ghcr", fixture)
            self.assertNotEqual(result.returncode, 0)
            state = self.state(fixture)
            for minor in MINORS:
                self.assertEqual(state[f"{DOCKERHUB}:{minor}"], fixture.baseline[minor][0])
                self.assertEqual(state[f"{GHCR}:{minor}"], fixture.baseline[minor][1])
            self.assertNotIn("rollback-dual|", fixture.log.read_text())

    def test_successful_backfill_never_signs_dockerhub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "backfill-ghcr")
            result = self.run_transaction(root, "backfill-ghcr", fixture)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            log = fixture.log.read_text()
            self.assertNotIn(f"cosign|sign|--yes|{DOCKERHUB}@", log)
            self.assertIn("dockerhub-signature=0", log)

    def test_recovery_restores_known_partial_runner_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            state = self.state(fixture)
            state[f"{DOCKERHUB}:8.2"], state[f"{GHCR}:8.2"] = fixture.targets["8.2"]
            state[f"{GHCR}:8.3"] = fixture.targets["8.3"][1]
            fixture.state_path.write_text(json.dumps(state, sort_keys=True))
            result = self.run_transaction(root, "recover", fixture)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            recovered = self.state(fixture)
            for minor in MINORS:
                self.assertEqual(recovered[f"{DOCKERHUB}:{minor}"], fixture.baseline[minor][0])
                self.assertEqual(recovered[f"{GHCR}:{minor}"], fixture.baseline[minor][1])
            self.assertTrue((root / "reports/recovery-result.json").is_file())

    def test_recovery_uses_frozen_ghcr_fallback_when_dockerhub_prior_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            previous_dockerhub = fixture.baseline["8.2"][0]
            unavailable = f"{DOCKERHUB}@{previous_dockerhub}"
            fixture.env["MOCK_UNAVAILABLE_REF"] = unavailable
            fixture.env["MOCK_FAIL_BACKUP_PARITY"] = unavailable
            state = self.state(fixture)
            state[f"{DOCKERHUB}:8.2"], state[f"{GHCR}:8.2"] = fixture.targets["8.2"]
            fixture.state_path.write_text(json.dumps(state, sort_keys=True))
            result = self.run_transaction(root, "recover", fixture)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            recovered = self.state(fixture)
            self.assertEqual(recovered[f"{DOCKERHUB}:8.2"], previous_dockerhub)
            self.assertIn("rollback-dual|8.2", fixture.log.read_text())

    def test_recovery_missing_ghcr_source_still_restores_dockerhub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            state = self.state(fixture)
            state[f"{DOCKERHUB}:8.2"], state[f"{GHCR}:8.2"] = fixture.targets["8.2"]
            fixture.state_path.write_text(json.dumps(state, sort_keys=True))
            previous_dockerhub, previous_ghcr = fixture.baseline["8.2"]
            fixture.env["MOCK_UNAVAILABLE_REF"] = f"{GHCR}@{previous_ghcr}"
            result = self.run_transaction(root, "recover", fixture)
            self.assertNotEqual(result.returncode, 0)
            recovered = self.state(fixture)
            self.assertEqual(recovered[f"{DOCKERHUB}:8.2"], previous_dockerhub)
            self.assertEqual(recovered[f"{GHCR}:8.2"], fixture.targets["8.2"][1])
            self.assertIn("final recovery baseline read-back mismatch", result.stderr)
            payload = json.loads((root / "reports/recovery-result.json").read_text())
            self.assertEqual(payload["status"], "failed")

    def test_recovery_unknown_state_is_no_clobber_for_all_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            state = self.state(fixture)
            state[f"{DOCKERHUB}:8.2"], state[f"{GHCR}:8.2"] = fixture.targets["8.2"]
            state[f"{GHCR}:8.3"] = digest(999)
            fixture.state_path.write_text(json.dumps(state, sort_keys=True))
            before = fixture.state_path.read_text()
            result = self.run_transaction(root, "recover", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no moving aliases were modified", result.stderr)
            self.assertEqual(fixture.state_path.read_text(), before)
            self.assertNotIn("rollback-dual|", fixture.log.read_text())

    def test_recovery_registry_read_failure_is_durable_unknown_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            fixture.env["MOCK_UNAVAILABLE_REF"] = f"{GHCR}:8.3"
            before = fixture.state_path.read_text()
            result = self.run_transaction(root, "recover", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fixture.state_path.read_text(), before)
            payload = json.loads((root / "reports/recovery-result.json").read_text())
            self.assertEqual(payload["status"], "unknown")
            self.assertNotIn("rollback-dual|", fixture.log.read_text())

    def test_recovery_uses_original_versions_after_default_branch_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            versions_path = root / "build/versions.json"
            frozen_versions = root / "original-versions.json"
            frozen_versions.write_text(versions_path.read_text())
            current = json.loads(versions_path.read_text())
            current["versions"]["8.2"]["patch"] = "8.2.99"
            versions_path.write_text(json.dumps(current))
            fixture.env["AUTO_PROMOTION_VERSIONS_FILE"] = str(frozen_versions)
            state = self.state(fixture)
            state[f"{DOCKERHUB}:8.2"], state[f"{GHCR}:8.2"] = fixture.targets["8.2"]
            fixture.state_path.write_text(json.dumps(state, sort_keys=True))
            result = self.run_transaction(root, "recover", fixture)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("classify-dockerhub|", fixture.log.read_text())
            self.assertTrue((root / "reports/recovery-result.json").is_file())

    def test_draft_plan_validation_rejects_unfrozen_or_malformed_rollback_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            payload = json.loads(fixture.plan.read_text())
            for unit in payload["release_units"]:
                unit["rollback_dockerhub_ref"] = None
                unit["rollback_dockerhub_backup_digest"] = None
                unit["rollback_ghcr_ref"] = None
                unit["rollback_ghcr_digest"] = None
            draft = root / "draft.json"
            draft.write_text(json.dumps(payload))
            valid = subprocess.run(
                [str(PLAN_VALIDATOR), str(draft), "--operation", "automatic", "--draft"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            payload["release_units"][0]["rollback_ghcr_ref"] = "unexpected"
            draft.write_text(json.dumps(payload))
            invalid = subprocess.run(
                [str(PLAN_VALIDATOR), str(draft), "--operation", "automatic", "--draft"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(invalid.returncode, 0)

    def test_plan_rejects_cross_registry_digest_aliasing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            payload = json.loads(fixture.plan.read_text())
            payload["release_units"][0]["target_dockerhub_digest"] = payload[
                "release_units"
            ][0]["target_ghcr_digest"]
            fixture.plan.write_text(json.dumps(payload))
            result = self.run_transaction(root, "automatic", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("resolved only after copy", result.stderr)
            self.assertNotIn("mutate|", fixture.log.read_text())

    def test_plan_rejects_non_digest_preserving_dockerhub_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.fixture(root, "automatic")
            payload = json.loads(fixture.plan.read_text())
            payload["release_units"][0]["rollback_dockerhub_backup_digest"] = digest(999)
            fixture.plan.write_text(json.dumps(payload))
            result = self.run_transaction(root, "automatic", fixture)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not preserve its exact baseline", result.stderr)
            self.assertNotIn("mutate|", fixture.log.read_text())


if __name__ == "__main__":
    unittest.main()
