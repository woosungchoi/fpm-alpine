from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/run-qemu-runtime-with-retry.sh"
VERIFIERS = (
    ROOT / "scripts/verify-canary-image.sh",
    ROOT / "scripts/verify-published-dockerhub-image.sh",
    ROOT / "scripts/verify-published-image.sh",
    ROOT / "scripts/verify-rollback-image.sh",
)


class QemuRuntimeRetryTests(unittest.TestCase):
    @staticmethod
    def write_executable(path: Path, content: str) -> None:
        path.write_text(content)
        path.chmod(0o755)

    def run_helper(
        self,
        root: Path,
        platform: str,
        command_body: str,
        *,
        attempts: str = "3",
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        helper = root / "run-qemu-runtime-with-retry.sh"
        shutil.copy2(HELPER, helper)
        helper.chmod(0o755)
        command = root / "runtime-command.sh"
        self.write_executable(command, command_body)
        report = root / "runtime-report.md"
        state = root / "attempt-count"
        env = os.environ.copy()
        env.update(
            {
                "ATTEMPT_STATE": str(state),
                "SMOKE_REPORT_MD": str(report),
                "QEMU_RUNTIME_RETRY_ATTEMPTS": attempts,
                "QEMU_RUNTIME_RETRY_BASE_DELAY_SECONDS": "0",
            }
        )
        result = subprocess.run(
            [str(helper), platform, str(report), "--", str(command)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        return result, report, state

    def test_arm64_qemu_sigsegv_is_retried_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, report, state = self.run_helper(
                Path(tmp),
                "linux/arm64",
                """#!/usr/bin/env bash
set -euo pipefail
count=0
[ ! -f "$ATTEMPT_STATE" ] || count="$(<"$ATTEMPT_STATE")"
count=$((count + 1))
printf '%s\n' "$count" > "$ATTEMPT_STATE"
if [ "$count" -eq 1 ]; then
  echo 'qemu-aarch64: QEMU internal SIGSEGV {code=MAPERR}' >&2
  exit 1
fi
printf '# runtime passed\n' > "$SMOKE_REPORT_MD"
echo runtime-passed
""",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(state.read_text().strip(), "2")
            self.assertEqual(report.read_text(), "# runtime passed\n")
            self.assertIn("retrying attempt 2/3", result.stdout)
            self.assertIn("runtime-passed", result.stdout)

    def test_non_qemu_failure_is_not_retried_and_removes_stale_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = root / "runtime-report.md"
            stale.write_text("stale")
            result, report, state = self.run_helper(
                root,
                "linux/arm64",
                """#!/usr/bin/env bash
set -euo pipefail
count=0
[ ! -f "$ATTEMPT_STATE" ] || count="$(<"$ATTEMPT_STATE")"
count=$((count + 1))
printf '%s\n' "$count" > "$ATTEMPT_STATE"
echo 'real runtime contract failure' >&2
exit 7
""",
            )
            self.assertEqual(result.returncode, 7)
            self.assertEqual(state.read_text().strip(), "1")
            self.assertFalse(report.exists())
            self.assertNotIn("retrying", result.stdout)

    def test_qemu_retry_is_bounded_and_arm64_only(self) -> None:
        command = """#!/usr/bin/env bash
set -euo pipefail
count=0
[ ! -f "$ATTEMPT_STATE" ] || count="$(<"$ATTEMPT_STATE")"
count=$((count + 1))
printf '%s\n' "$count" > "$ATTEMPT_STATE"
echo 'qemu-aarch64: QEMU internal SIGSEGV {code=MAPERR}' >&2
exit 1
"""
        with tempfile.TemporaryDirectory() as tmp:
            result, report, state = self.run_helper(Path(tmp), "linux/arm64", command)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(state.read_text().strip(), "3")
            self.assertFalse(report.exists())
            self.assertEqual(result.stdout.count("retrying attempt"), 2)
        with tempfile.TemporaryDirectory() as tmp:
            result, _, state = self.run_helper(Path(tmp), "linux/amd64", command)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(state.read_text().strip(), "1")
            self.assertNotIn("retrying", result.stdout)

    def test_all_registry_runtime_verifiers_use_the_retry_helper(self) -> None:
        for verifier in VERIFIERS:
            with self.subTest(verifier=verifier.name):
                self.assertIn("run-qemu-runtime-with-retry.sh", verifier.read_text())

    def test_retry_contract_runs_in_required_ci(self) -> None:
        workflow = (ROOT / ".github/workflows/smoke-test.yml").read_text()
        self.assertIn("python3 tests/test_qemu_runtime_retry.py", workflow)


if __name__ == "__main__":
    unittest.main()
