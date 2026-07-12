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
    "schemaVersion": 1,
    "platform": "linux/amd64",
    "phpVersion": "8.5.8",
    "packages": ["a", "b"],
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
        candidate = {**BASE, "packages": ["a", "c"]}
        errors = self.module.compare(BASE, candidate, "8.5")
        self.assertIn("package set drift", " ".join(errors))

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
