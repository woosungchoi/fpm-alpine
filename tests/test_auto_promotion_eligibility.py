#!/usr/bin/env python3
"""Tests for trusted-main auto-promotion eligibility."""

from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = json.loads((ROOT / "build/versions.json").read_text())
POLICY = json.loads((ROOT / "build/automation-policy.json").read_text())


def load_module():
    path = ROOT / "scripts/evaluate-auto-promotion.py"
    spec = importlib.util.spec_from_file_location("evaluate_auto_promotion", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PromotionEligibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_base_update_selects_one_minor(self) -> None:
        head = copy.deepcopy(VERSIONS)
        head["versions"]["8.5"]["base_image"] = "php:8.5-fpm-alpine@sha256:" + "b" * 64
        result = self.module.evaluate(
            VERSIONS, head, POLICY, ["build/versions.json"], "a" * 40
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["affectedMinors"], ["8.5"])

    def test_pecl_patch_selects_all_minors(self) -> None:
        head = copy.deepcopy(VERSIONS)
        row = head["dependencies"]["apcu"]
        row["version"] = "5.1.29"
        row["url"] = "https://pecl.php.net/get/apcu-5.1.29.tgz"
        row["sha256"] = "c" * 64
        result = self.module.evaluate(
            VERSIONS, head, POLICY, ["build/versions.json"], "a" * 40
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["affectedMinors"], ["8.2", "8.3", "8.4", "8.5"])

    def test_actions_only_update_is_no_publish(self) -> None:
        result = self.module.evaluate(
            VERSIONS,
            VERSIONS,
            POLICY,
            [".github/workflows/smoke-test.yml"],
            "a" * 40,
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["class"], "actions-no-image-change")

    def test_mixed_or_unknown_files_rejected(self) -> None:
        for files in (
            ["build/versions.json", "README.md"],
            ["Dockerfile"],
            [".github/workflows/x.yml", "scripts/x.py"],
        ):
            with self.subTest(files=files):
                result = self.module.evaluate(
                    VERSIONS, VERSIONS, POLICY, files, "a" * 40
                )
                self.assertFalse(result["eligible"])
                self.assertTrue(result["blockedReasons"])

    def test_boolean_or_malformed_source_sha_rejected(self) -> None:
        result = self.module.evaluate(VERSIONS, VERSIONS, POLICY, [], "bad")
        self.assertFalse(result["eligible"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
