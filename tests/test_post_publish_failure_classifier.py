#!/usr/bin/env python3
"""Mutation tests for post-publish failure classification."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts/classify-post-publish-failure.py"
    spec = importlib.util.spec_from_file_location("classify_post_publish_failure", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = {
    "schemaVersion": 1,
    "stage": "post-publish",
    "failureClass": "runtime-contract",
    "registryMutationStarted": True,
    "previousDigestValid": True,
    "sourceCommit": "a" * 40,
}


class FailureClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_deterministic_post_publish_failure_allows_rollback(self) -> None:
        result = self.module.classify(BASE)
        self.assertTrue(result["rollbackAuthorized"])
        self.assertEqual(result["action"], "rollback")

    def test_pre_publish_never_rolls_back(self) -> None:
        result = self.module.classify(
            {**BASE, "stage": "pre-publish", "registryMutationStarted": False}
        )
        self.assertFalse(result["rollbackAuthorized"])
        self.assertEqual(result["action"], "stop")

    def test_missing_previous_digest_freezes(self) -> None:
        result = self.module.classify({**BASE, "previousDigestValid": False})
        self.assertFalse(result["rollbackAuthorized"])
        self.assertEqual(result["action"], "freeze")

    def test_policy_permission_and_unknown_failures_freeze(self) -> None:
        for failure in ("policy", "permission", "unknown", "network"):
            with self.subTest(failure=failure):
                result = self.module.classify({**BASE, "failureClass": failure})
                self.assertFalse(result["rollbackAuthorized"])
                self.assertEqual(result["action"], "freeze")

    def test_boolean_schema_or_flags_rejected(self) -> None:
        for mutation in (
            {"schemaVersion": True},
            {"registryMutationStarted": 1},
            {"previousDigestValid": 1},
            {"sourceCommit": "bad"},
        ):
            with self.subTest(mutation=mutation):
                result = self.module.classify({**BASE, **mutation})
                self.assertEqual(result["action"], "invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
