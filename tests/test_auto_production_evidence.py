#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "a" * 40


def load_module():
    path = ROOT / "scripts/validate-auto-production-evidence.py"
    spec = importlib.util.spec_from_file_location("validate_auto_production_evidence", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payloads() -> dict[str, dict]:
    affected = ["8.2", "8.3", "8.4", "8.5"]
    return {
        "eligibility.json": {
            "schemaVersion": 1,
            "sourceCommit": SOURCE,
            "eligible": True,
            "class": "pecl-patch",
            "affectedMinors": affected,
            "blockedReasons": [],
        },
        "merged-pr.json": {
            "schemaVersion": 1,
            "sourceCommit": SOURCE,
            "pullRequest": 82,
            "pullRequestHeadSha": "b" * 40,
            "author": "fpm-dependency-updater[bot]",
            "headRef": "automation/pecl-redis-123456789abc",
        },
        "canary-pair.json": {
            "schemaVersion": 1,
            "sourceCommit": SOURCE,
            "affectedMinors": affected,
            "firstCanary": {"runId": 101, "runAttempt": 1, "runNumber": 50},
            "secondCanary": {"runId": 102, "runAttempt": 1, "runNumber": 51},
            "productionAuthorized": True,
        },
    }


class AutoProductionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def validate(self, rows: dict[str, dict] | None = None) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "nested"
            root.mkdir()
            for name, payload in (rows or payloads()).items():
                (root / name).write_text(json.dumps(payload) + "\n")
            return self.module.validate_evidence(Path(directory), SOURCE, 9001, 2)

    def test_valid_evidence_is_bound_to_upstream_and_canary_pair(self) -> None:
        result = self.validate()
        self.assertEqual(result["sourceCommit"], SOURCE)
        self.assertEqual(result["upstreamRunId"], 9001)
        self.assertEqual(result["upstreamRunAttempt"], 2)
        self.assertEqual(result["affectedMinors"], ["8.2", "8.3", "8.4", "8.5"])
        self.assertEqual(result["priorCanary"], {"runId": 101, "runAttempt": 1})
        self.assertEqual(result["currentCanary"], {"runId": 102, "runAttempt": 1})
        self.assertIs(result["productionAuthorized"], True)

    def test_false_production_authorization_is_rejected(self) -> None:
        rows = payloads()
        rows["canary-pair.json"]["productionAuthorized"] = False
        with self.assertRaisesRegex(SystemExit, "not authorized"):
            self.validate(rows)

    def test_boolean_run_identifier_is_rejected(self) -> None:
        rows = payloads()
        rows["canary-pair.json"]["secondCanary"]["runAttempt"] = True
        with self.assertRaisesRegex(SystemExit, "canary identity"):
            self.validate(rows)

    def test_mismatched_affected_minors_are_rejected(self) -> None:
        rows = payloads()
        rows["canary-pair.json"]["affectedMinors"] = ["8.2"]
        with self.assertRaisesRegex(SystemExit, "affected minors"):
            self.validate(rows)

    def test_duplicate_or_unknown_minor_is_rejected(self) -> None:
        for affected in (["8.2", "8.2"], ["8.6"]):
            with self.subTest(affected=affected):
                rows = payloads()
                rows["eligibility.json"]["affectedMinors"] = affected
                rows["canary-pair.json"]["affectedMinors"] = affected
                with self.assertRaisesRegex(SystemExit, "affected minors"):
                    self.validate(rows)

    def test_source_or_merged_pr_identity_mismatch_is_rejected(self) -> None:
        rows = payloads()
        rows["merged-pr.json"]["sourceCommit"] = "c" * 40
        with self.assertRaisesRegex(SystemExit, "merged PR"):
            self.validate(rows)

    def test_duplicate_evidence_filename_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for parent in (root / "one", root / "two"):
                parent.mkdir()
                (parent / "eligibility.json").write_text(
                    json.dumps(payloads()["eligibility.json"])
                )
            (root / "merged-pr.json").write_text(json.dumps(payloads()["merged-pr.json"]))
            (root / "canary-pair.json").write_text(json.dumps(payloads()["canary-pair.json"]))
            with self.assertRaisesRegex(SystemExit, "exactly one eligibility"):
                self.module.validate_evidence(root, SOURCE, 9001, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
