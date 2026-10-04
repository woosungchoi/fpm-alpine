#!/usr/bin/env python3
"""Tests for package/runtime contract drift comparison."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts/compare-image-contract.py"
    spec = importlib.util.spec_from_file_location("compare_image_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = {
    "schemaVersion": 2,
    "platform": "linux/amd64",
    "phpVersion": "8.5.8",
    "packages": ["a", "b"],
    "packageEvidence": [{"name": "a", "version": "1-r0", "architecture": "x86_64"}, {"name": "b", "version": "2-r0", "architecture": "noarch"}],
    "modules": ["Core", "imagick", "redis"],
    "iconv": {"implementation": "libiconv", "version": "1.18"},
    "fpmConfigValid": True,
}


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_patch_change_with_same_contract_passes(self) -> None:
        candidate = {**BASE, "phpVersion": "8.5.9"}
        self.assertEqual(self.module.compare(BASE, candidate, "8.5"), [])

    def test_package_add_remove_fails(self) -> None:
        candidate = {**BASE, "packages": ["a", "c"], "packageEvidence": [BASE["packageEvidence"][0], {"name": "c", "version": "2-r0", "architecture": "noarch"}]}
        errors = self.module.compare(BASE, candidate, "8.5")
        self.assertIn("package set drift", " ".join(errors))

    def test_same_name_version_drift_fails(self):
        candidate = {**BASE, "packageEvidence": [dict(row) for row in BASE["packageEvidence"]]}
        candidate["packageEvidence"][0]["version"] = "1-r1"
        self.assertIn("package version/architecture drift", " ".join(self.module.compare(BASE, candidate, "8.5")))

    def test_reviewed_transition_is_exact_and_architecture_bound(self):
        candidate = {**BASE, "packageEvidence": [dict(row) for row in BASE["packageEvidence"]]}
        candidate["packageEvidence"][0]["version"] = "1-r1"
        approved = (("a", "1-r0", "1-r1", "x86_64"),)
        self.assertEqual(self.module.compare(BASE, candidate, "8.5", approved), [])
        self.assertTrue(self.module.compare(BASE, candidate, "8.5", (("a", "1-r0", "1-r1", "aarch64"),)))
        candidate["packageEvidence"][0]["version"] = "1-r2"
        self.assertTrue(self.module.compare(BASE, candidate, "8.5", approved))
        candidate["packageEvidence"][0]["version"] = "0-r0"
        self.assertTrue(self.module.compare(BASE, candidate, "8.5", approved))

    def test_reviewed_policy_rejects_wildcards_and_duplicates(self):
        import json, tempfile
        path = ROOT / "build/approved-apk-transitions.json"
        policy = json.loads(path.read_text())
        self.assertEqual(len(self.module.load_approved_transitions(path)), 4)
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "policy.json"
            policy["transitions"][0]["to"] = "*"
            target.write_text(json.dumps(policy))
            with self.assertRaises(ValueError): self.module.load_approved_transitions(target)
            policy = json.loads(path.read_text())
            policy["transitions"].append(policy["transitions"][0])
            target.write_text(json.dumps(policy))
            with self.assertRaises(ValueError): self.module.load_approved_transitions(target)

    def test_wrong_apk_architecture_fails(self):
        candidate = {**BASE, "packageEvidence": [dict(row) for row in BASE["packageEvidence"]]}
        candidate["packageEvidence"][0]["architecture"] = "aarch64"
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))

    def test_module_drift_fails(self) -> None:
        candidate = {**BASE, "modules": ["Core", "imagick"]}
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))

    def test_wrong_minor_fails(self) -> None:
        candidate = {**BASE, "phpVersion": "8.6.0"}
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))

    def test_platform_mismatch_fails(self) -> None:
        candidate = {**BASE, "platform": "linux/arm64"}
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))

    def test_boolean_schema_is_rejected(self) -> None:
        candidate = {**BASE, "schemaVersion": True}
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))

    def test_invalid_fpm_contract_fails(self) -> None:
        candidate = {**BASE, "fpmConfigValid": False}
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
