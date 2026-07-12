#!/usr/bin/env python3
"""Mutation tests for dependency automation policy and classifier."""

from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = json.loads((ROOT / "build/versions.json").read_text())
POLICY_PATH = ROOT / "build/automation-policy.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DependencyAutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads(POLICY_PATH.read_text())
        cls.classifier = load_module(
            ROOT / "scripts/classify-dependency-change.py", "dependency_classifier"
        )
        cls.validator = load_module(
            ROOT / "scripts/validate-versions.py", "versions_validator"
        )

    def classify(self, head: dict, files: list[str] | None = None) -> dict:
        return self.classifier.classify(
            copy.deepcopy(VERSIONS),
            head,
            copy.deepcopy(self.policy),
            files or ["build/versions.json"],
        )

    def test_current_manifest_validates_against_human_policy(self) -> None:
        self.assertEqual(
            self.validator.validate(copy.deepcopy(VERSIONS), self.policy), []
        )

    def test_same_minor_base_patch_and_digest_is_eligible(self) -> None:
        head = copy.deepcopy(VERSIONS)
        head["versions"]["8.5"]["patch"] = "8.5.9"
        head["versions"]["8.5"]["base_image"] = "php:8.5-fpm-alpine@sha256:" + "a" * 64
        result = self.classify(head)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["class"], "base-same-minor")
        self.assertEqual(result["affectedMinors"], ["8.5"])

    def test_same_patch_new_base_digest_is_eligible(self) -> None:
        head = copy.deepcopy(VERSIONS)
        head["versions"]["8.4"]["base_image"] = "php:8.4-fpm-alpine@sha256:" + "b" * 64
        result = self.classify(head)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["affectedMinors"], ["8.4"])

    def test_pecl_patch_is_eligible_for_all_active_minors(self) -> None:
        head = copy.deepcopy(VERSIONS)
        head["dependencies"]["imagick"] = {
            "version": "3.8.2",
            "url": "https://pecl.php.net/get/imagick-3.8.2.tgz",
            "sha256": "c" * 64,
        }
        result = self.classify(head)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["class"], "pecl-patch")
        self.assertEqual(result["affectedMinors"], ["8.2", "8.3", "8.4", "8.5"])

    def test_support_and_eol_changes_are_manual_only(self) -> None:
        for field, value in (("support", "active"), ("eol", "2099-12-31")):
            with self.subTest(field=field):
                head = copy.deepcopy(VERSIONS)
                head["versions"]["8.2"][field] = value
                result = self.classify(head)
                self.assertFalse(result["eligible"])
                self.assertIn("manual-only", " ".join(result["blockedReasons"]))

    def test_runtime_contract_change_is_manual_only(self) -> None:
        head = copy.deepcopy(VERSIONS)
        head["runtimeContracts"]["libiconv"]["version"] = "9.9"
        result = self.classify(head)
        self.assertFalse(result["eligible"])

    def test_pecl_minor_bump_is_manual_only(self) -> None:
        head = copy.deepcopy(VERSIONS)
        head["dependencies"]["redis"] = {
            "version": "6.4.0",
            "url": "https://pecl.php.net/get/redis-6.4.0.tgz",
            "sha256": "d" * 64,
        }
        result = self.classify(head)
        self.assertFalse(result["eligible"])
        self.assertIn("patch-level", " ".join(result["blockedReasons"]))

    def test_wrong_dependency_host_is_rejected(self) -> None:
        head = copy.deepcopy(VERSIONS)
        head["dependencies"]["apcu"] = {
            "version": "5.1.29",
            "url": "https://example.invalid/apcu-5.1.29.tgz",
            "sha256": "e" * 64,
        }
        self.assertFalse(self.classify(head)["eligible"])
        self.assertTrue(self.validator.validate(head, self.policy))

    def test_unknown_changed_file_blocks_automation(self) -> None:
        head = copy.deepcopy(VERSIONS)
        head["versions"]["8.5"]["base_image"] = "php:8.5-fpm-alpine@sha256:" + "f" * 64
        result = self.classify(head, ["build/versions.json", "Dockerfile"])
        self.assertFalse(result["eligible"])
        self.assertIn("changed file", " ".join(result["blockedReasons"]))

    def test_mixed_base_and_dependency_change_is_rejected(self) -> None:
        head = copy.deepcopy(VERSIONS)
        head["versions"]["8.5"]["base_image"] = "php:8.5-fpm-alpine@sha256:" + "1" * 64
        head["dependencies"]["apcu"] = {
            "version": "5.1.29",
            "url": "https://pecl.php.net/get/apcu-5.1.29.tgz",
            "sha256": "2" * 64,
        }
        result = self.classify(head)
        self.assertFalse(result["eligible"])
        self.assertIn("mixed", " ".join(result["blockedReasons"]))

    def test_empty_change_is_not_eligible(self) -> None:
        result = self.classify(copy.deepcopy(VERSIONS))
        self.assertFalse(result["eligible"])
        self.assertEqual(result["class"], "none")

    def test_policy_rejects_boolean_schema_version(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["schemaVersion"] = True
        errors = self.validator.validate(copy.deepcopy(VERSIONS), policy)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
