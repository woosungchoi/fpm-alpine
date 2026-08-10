"""Tests for the unattended Docker Hub permanent-cutover verifier."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify-permanent-dockerhub-cutover.py"
VALID = {
    "namespace": "woosungchoi",
    "name": "fpm-alpine",
    "status": 1,
    "is_automated": False,
    "last_updated": "2026-08-10T05:53:44.484575Z",
}


class PermanentDockerHubCutoverTests(unittest.TestCase):
    def run_check(
        self,
        metadata: dict,
        *,
        expected: dict | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict | None]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_path = root / "metadata.json"
            output_path = root / "result.json"
            metadata_path.write_text(json.dumps(metadata))
            command = [
                "python3",
                str(SCRIPT),
                "--metadata-file",
                str(metadata_path),
                "--output",
                str(output_path),
            ]
            if expected is not None:
                expected_path = root / "expected.json"
                expected_path.write_text(json.dumps(expected))
                command.extend(("--expected-state", str(expected_path)))
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            payload = json.loads(output_path.read_text()) if output_path.exists() else None
            return completed, payload

    def test_valid_permanent_cutover_writes_safe_evidence(self) -> None:
        completed, payload = self.run_check(copy.deepcopy(VALID))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        assert payload is not None
        self.assertEqual(payload["namespace"], "woosungchoi")
        self.assertEqual(payload["name"], "fpm-alpine")
        self.assertEqual(payload["status"], 1)
        self.assertIs(payload["isAutomated"], False)
        self.assertEqual(payload["lastUpdated"], VALID["last_updated"])

    def test_unchanged_state_passes_mutation_boundary_recheck(self) -> None:
        first, expected = self.run_check(copy.deepcopy(VALID))
        self.assertEqual(first.returncode, 0, first.stderr)
        assert expected is not None
        second, _ = self.run_check(copy.deepcopy(VALID), expected=expected)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

    def test_legacy_automation_or_invalid_status_is_rejected(self) -> None:
        for field, value in (("is_automated", True), ("status", True), ("status", 0)):
            with self.subTest(field=field, value=value):
                metadata = copy.deepcopy(VALID)
                metadata[field] = value
                completed, payload = self.run_check(metadata)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIsNone(payload)

    def test_wrong_repository_or_external_write_drift_is_rejected(self) -> None:
        wrong = copy.deepcopy(VALID)
        wrong["name"] = "other"
        completed, _ = self.run_check(wrong)
        self.assertNotEqual(completed.returncode, 0)

        first, expected = self.run_check(copy.deepcopy(VALID))
        self.assertEqual(first.returncode, 0, first.stderr)
        assert expected is not None
        changed = copy.deepcopy(VALID)
        changed["last_updated"] = "2026-08-10T06:00:00Z"
        completed, payload = self.run_check(changed, expected=expected)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)