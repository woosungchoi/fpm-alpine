#!/usr/bin/env python3
"""Structural tests for auto-merge and auto-canary control workflows."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ControlWorkflowTests(unittest.TestCase):
    def load(self, name: str):
        path = ROOT / ".github/workflows" / name
        text = path.read_text()
        return text, yaml.safe_load(text)

    def assert_pinned(self, text: str) -> None:
        refs = re.findall(r"^\s*uses:\s*([^\s#]+)", text, re.M)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"^[^@]+@[0-9a-f]{40}$")

    def test_auto_merge_separates_read_selection_from_write_enablement(self) -> None:
        text, data = self.load("dependency-auto-merge.yml")
        self.assertEqual(data["permissions"], {})
        select = data["jobs"]["select"]
        merge = data["jobs"]["enable-native-auto-merge"]
        self.assertEqual(
            select["permissions"],
            {"contents": "read", "pull-requests": "read", "checks": "read"},
        )
        self.assertEqual(
            merge["permissions"],
            {"contents": "write", "pull-requests": "write", "checks": "read"},
        )
        self.assertIn("DEPENDENCY_AUTO_MERGE_ENABLED", merge["if"])
        rendered = yaml.safe_dump(merge, sort_keys=False)
        self.assertIn("gh pr merge", rendered)
        self.assertIn("--auto", rendered)
        self.assertIn("--match-head-commit", rendered)
        self.assertNotIn("--admin", rendered)
        self.assertNotIn("pull_request_target", text)
        self.assert_pinned(text)

    def test_auto_promote_only_dispatches_canary_and_is_activation_gated(self) -> None:
        text, data = self.load("dependency-auto-promote.yml")
        self.assertEqual(data["permissions"], {})
        evaluate = data["jobs"]["evaluate"]
        canary = data["jobs"]["auto-canary"]
        self.assertEqual(
            evaluate["permissions"],
            {"contents": "read", "pull-requests": "read", "checks": "read"},
        )
        self.assertEqual(
            canary["permissions"], {"actions": "write", "contents": "read"}
        )
        self.assertIn("DEPENDENCY_AUTO_CANARY_ENABLED", canary["if"])
        rendered = yaml.safe_dump(canary, sort_keys=False)
        self.assertIn("run-auto-canary.sh", rendered)
        self.assertNotIn("channel=production", text)
        self.assertNotIn("packages: write", text)
        self.assertNotIn("id-token: write", text)
        self.assertNotIn("pull_request_target", text)
        self.assert_pinned(text)

    def test_exact_check_app_and_production_denial_are_source_contracts(self) -> None:
        merged = (ROOT / "scripts/validate-merged-dependency-pr.sh").read_text()
        merge_eval = (ROOT / "scripts/evaluate-auto-merge-pr.sh").read_text()
        canary = (ROOT / "scripts/run-auto-canary.sh").read_text()
        for text in (merged, merge_eval):
            self.assertIn("15368", text)
            self.assertIn('get("name")', text)
            self.assertIn('"docker-smoke"', text)
        self.assertIn('"productionAuthorized": False', canary)
        self.assertIn("for index in 1 2", canary)
        self.assertIn("first_number + 1", canary)
        self.assertNotIn("channel=production", canary)

    def test_publisher_correlation_is_optional_and_validated(self) -> None:
        text, data = self.load("publish.yml")
        trigger = data.get("on", data.get(True))
        inputs = trigger["workflow_dispatch"]["inputs"]
        self.assertFalse(inputs["correlation_id"]["required"])
        self.assertIn("correlation_id || github.run_id", data["run-name"])
        self.assertIn("CORRELATION_ID", text)
        self.assertIn("^[A-Za-z0-9._-]{1,64}$", text)

    def test_no_auto_production_workflow_exists_before_bake_gate(self) -> None:
        self.assertFalse(
            (ROOT / ".github/workflows/dependency-auto-production.yml").exists()
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
