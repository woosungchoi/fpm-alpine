from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rollback-moving-aliases.sh"
DOCKERHUB = "docker.io/woosungchoi/fpm-alpine"
GHCR = "ghcr.io/woosungchoi/fpm-alpine"


def digest(number: int) -> str:
    return f"sha256:{number:064x}"


class RollbackSourceTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[dict[str, str], dict[str, str], Path, Path]:
        scripts = root / "scripts"
        fake_bin = root / "bin"
        scripts.mkdir()
        fake_bin.mkdir()
        shutil.copy2(SCRIPT, scripts / SCRIPT.name)
        (scripts / SCRIPT.name).chmod(0o755)

        state_path = root / "state.json"
        log_path = root / "calls.log"
        previous_dockerhub = digest(101)
        previous_ghcr = digest(201)
        backup_digest = previous_dockerhub
        current_dockerhub = digest(401)
        current_ghcr = digest(501)
        primary = f"{DOCKERHUB}@{previous_dockerhub}"
        fallback = f"{GHCR}@{backup_digest}"
        ghcr_source = f"{GHCR}@{previous_ghcr}"
        state = {
            f"{DOCKERHUB}:8.2": current_dockerhub,
            f"{GHCR}:8.2": current_ghcr,
        }
        state_path.write_text(json.dumps(state, sort_keys=True))
        mapping = {
            primary: previous_dockerhub,
            fallback: previous_dockerhub,
            ghcr_source: previous_ghcr,
        }

        self.write_executable(
            scripts / "resolve-image-digest.sh",
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
ref = sys.argv[1]
unavailable = os.environ.get('UNAVAILABLE_REFS', os.environ.get('UNAVAILABLE_REF', '')).split(',')
if ref in unavailable:
    raise SystemExit(1)
if '@' in ref:
    print(ref.rsplit('@', 1)[1])
else:
    print(json.loads(Path(os.environ['STATE_PATH']).read_text())[ref])
""",
        )
        self.write_executable(
            scripts / "verify-rollback-image.sh",
            """#!/usr/bin/env python3
from pathlib import Path
import sys
Path(sys.argv[4]).mkdir(parents=True, exist_ok=True)
Path(sys.argv[4], 'verified').write_text('ok\\n')
""",
        )
        self.write_executable(
            fake_bin / "docker",
            """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:3] != ['buildx', 'imagetools', 'create']:
    raise SystemExit(64)
tag = args[args.index('--tag') + 1]
source = args[-1]
with open(os.environ['LOG_PATH'], 'a') as log:
    log.write(f"{source}|{tag}\\n")
state_path = Path(os.environ['STATE_PATH'])
state = json.loads(state_path.read_text())
mapping = json.loads(os.environ['SOURCE_MAPPING'])
value = mapping[source]
if source == os.environ.get('MISMATCH_SOURCE'):
    value = os.environ['MISMATCH_DIGEST']
state[tag] = value
state_path.write_text(json.dumps(state, sort_keys=True))
""",
        )
        self.write_executable(fake_bin / "cosign", "#!/usr/bin/env bash\nexit 0\n")
        self.write_executable(
            scripts / "resolve-published-operation.sh",
            """#!/usr/bin/env bash
case "${MOCK_EXISTING_OPERATION:-unsigned}" in
  automatic) printf 'automatic\\tdependency-auto-publish.yml\\tmain\\n'; exit 0 ;;
  unsigned) exit 3 ;;
  ambiguous) exit 4 ;;
  *) exit 64 ;;
esac
""",
        )
        plan_path = root / "promotion-plan.json"
        plan_path.write_text('{}\n')
        journal_log = root / "journal.log"
        self.write_executable(
            scripts / "transaction-journal.py",
            """#!/usr/bin/env python3
import os, sys
if sys.argv[1] not in {'recovery-referrer-attempt', 'recovery-referrer-complete'}:
    raise SystemExit(64)
if sys.argv[2] != os.environ['TRANSACTION_PLAN_FILE'] or sys.argv[3] != '8.2':
    raise SystemExit(65)
with open(os.environ['JOURNAL_LOG'], 'a') as log:
    log.write('|'.join(sys.argv[1:]) + '\\n')
""",
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env['PATH']}",
                "STATE_PATH": str(state_path),
                "LOG_PATH": str(log_path),
                "SOURCE_MAPPING": json.dumps(mapping, sort_keys=True),
                "DOCKERHUB_ROLLBACK_SOURCE": primary,
                "DOCKERHUB_ROLLBACK_FALLBACK_SOURCE": fallback,
                "GHCR_ROLLBACK_SOURCE": ghcr_source,
                "COSIGN_SIGN_DESTINATION": "0",
                "TRANSACTION_PLAN_FILE": str(plan_path),
                "JOURNAL_LOG": str(journal_log),
            }
        )
        values = {
            "previous_dockerhub": previous_dockerhub,
            "previous_ghcr": previous_ghcr,
            "primary": primary,
            "fallback": fallback,
            "ghcr_source": ghcr_source,
        }
        return values, env, state_path, log_path

    @staticmethod
    def write_executable(path: Path, content: str) -> None:
        path.write_text(content)
        path.chmod(0o755)

    def run_helper(self, root: Path, values: dict[str, str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(root / "scripts" / SCRIPT.name),
                DOCKERHUB,
                values["previous_dockerhub"],
                GHCR,
                values["previous_ghcr"],
                "8.2",
                str(root / "report"),
            ],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_primary_same_registry_subject_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values, env, state_path, log_path = self.fixture(root)
            result = self.run_helper(root, values, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = log_path.read_text()
            self.assertIn(values["primary"], calls)
            self.assertNotIn(values["fallback"], calls)
            state = json.loads(state_path.read_text())
            self.assertEqual(state[f"{DOCKERHUB}:8.2"], values["previous_dockerhub"])
            self.assertEqual(state[f"{GHCR}:8.2"], values["previous_ghcr"])

    def test_unavailable_primary_uses_frozen_ghcr_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values, env, state_path, log_path = self.fixture(root)
            env["UNAVAILABLE_REF"] = values["primary"]
            result = self.run_helper(root, values, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(values["fallback"], log_path.read_text())
            state = json.loads(state_path.read_text())
            self.assertEqual(state[f"{DOCKERHUB}:8.2"], values["previous_dockerhub"])

    def test_primary_readback_mismatch_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values, env, state_path, log_path = self.fixture(root)
            env["MISMATCH_SOURCE"] = values["primary"]
            env["MISMATCH_DIGEST"] = digest(999)
            result = self.run_helper(root, values, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = log_path.read_text()
            self.assertIn(values["primary"], calls)
            self.assertIn(values["fallback"], calls)
            state = json.loads(state_path.read_text())
            self.assertEqual(state[f"{DOCKERHUB}:8.2"], values["previous_dockerhub"])

    def test_unavailable_optional_fallback_does_not_block_primary_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values, env, state_path, log_path = self.fixture(root)
            env["UNAVAILABLE_REF"] = values["fallback"]
            result = self.run_helper(root, values, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            state = json.loads(state_path.read_text())
            self.assertEqual(state[f"{DOCKERHUB}:8.2"], values["previous_dockerhub"])
            self.assertEqual(state[f"{GHCR}:8.2"], values["previous_ghcr"])
            self.assertNotIn(values["fallback"], log_path.read_text())

    def test_unavailable_dockerhub_sources_do_not_block_ghcr_compensation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values, env, state_path, log_path = self.fixture(root)
            before = json.loads(state_path.read_text())
            env["UNAVAILABLE_REFS"] = f"{values['primary']},{values['fallback']}"
            result = self.run_helper(root, values, env)
            self.assertNotEqual(result.returncode, 0)
            state = json.loads(state_path.read_text())
            self.assertEqual(state[f"{DOCKERHUB}:8.2"], before[f"{DOCKERHUB}:8.2"])
            self.assertEqual(state[f"{GHCR}:8.2"], values["previous_ghcr"])
            self.assertIn(values["ghcr_source"], log_path.read_text())
            payload = json.loads((root / "report/rollback-result.json").read_text())
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["dockerhub_restore_status"], "failed")
            self.assertEqual(payload["ghcr_restore_status"], "verified")

    def test_already_prior_registry_is_verified_without_retagging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values, env, state_path, log_path = self.fixture(root)
            state = json.loads(state_path.read_text())
            state[f"{DOCKERHUB}:8.2"] = values["previous_dockerhub"]
            state_path.write_text(json.dumps(state, sort_keys=True))
            env["RESTORE_DOCKERHUB"] = "0"
            env["RESTORE_GHCR"] = "1"
            result = self.run_helper(root, values, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = log_path.read_text()
            self.assertNotIn(f"{DOCKERHUB}:8.2", calls)
            self.assertIn(f"{GHCR}:8.2", calls)
            payload = json.loads((root / "report/rollback-result.json").read_text())
            self.assertEqual(payload["dockerhub_restore_status"], "unchanged")
            self.assertEqual(payload["ghcr_restore_status"], "verified")

    def test_rollback_evidence_write_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values, env, _, _ = self.fixture(root)
            (root / "report/rollback-result.json").mkdir(parents=True)
            result = self.run_helper(root, values, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("rollback evidence write failed", result.stderr)

    def test_existing_authorized_signature_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            values, env, _, _ = self.fixture(root)
            cosign_log = root / "cosign.log"
            self.write_executable(
                root / "bin/cosign",
                "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$COSIGN_LOG\"\nexit 0\n",
            )
            env.update(
                COSIGN_SIGN_DESTINATION="1",
                MOCK_EXISTING_OPERATION="automatic",
                COSIGN_LOG=str(cosign_log),
            )
            result = self.run_helper(root, values, env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(cosign_log.exists())

    def test_unsigned_destination_is_signed_as_recovery_but_ambiguous_is_blocked(self) -> None:
        for existing, expected_ok in (("unsigned", True), ("ambiguous", False)):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                values, env, _, _ = self.fixture(root)
                cosign_log = root / "cosign.log"
                self.write_executable(
                    root / "bin/cosign",
                    "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$COSIGN_LOG\"\nexit 0\n",
                )
                env.update(
                    COSIGN_SIGN_DESTINATION="1",
                    MOCK_EXISTING_OPERATION=existing,
                    COSIGN_LOG=str(cosign_log),
                )
                result = self.run_helper(root, values, env)
                self.assertEqual(result.returncode == 0, expected_ok, result.stdout + result.stderr)
                if expected_ok:
                    self.assertIn("fpm.operation=recovery", cosign_log.read_text())
                    journal = (root / "journal.log").read_text()
                    self.assertIn("recovery-referrer-attempt", journal)
                    self.assertIn("recovery-referrer-complete", journal)
                else:
                    self.assertFalse(cosign_log.exists())


if __name__ == "__main__":
    unittest.main()
