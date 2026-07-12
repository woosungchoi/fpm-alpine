#!/usr/bin/env python3
"""Tests for auto-merge PR metadata selection."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts/select-auto-merge-prs.py"
    spec = importlib.util.spec_from_file_location("select_auto_merge_prs", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = {
    "number": 1,
    "author": {"login": "fpm-alpine-dependency-updater[bot]"},
    "baseRefName": "main",
    "headRefName": "automation/base-8.5-abc123def456",
    "headRepository": {"nameWithOwner": "woosungchoi/fpm-alpine"},
    "isCrossRepository": False,
    "isDraft": False,
    "reviewDecision": "",
}


class SelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_same_repo_automation_branch_selected(self) -> None:
        selected, rejected = self.module.select([BASE], "woosungchoi/fpm-alpine")
        self.assertEqual(selected, [1])
        self.assertEqual(rejected, {})

    def test_dependabot_actions_branch_selected(self) -> None:
        row = {
            **BASE,
            "number": 2,
            "author": {"login": "dependabot[bot]"},
            "headRefName": "dependabot/github_actions/actions/checkout-8",
        }
        self.assertEqual(self.module.select([row], "woosungchoi/fpm-alpine")[0], [2])

    def test_spoofed_dependabot_author_rejected(self) -> None:
        row = {**BASE, "headRefName": "dependabot/github_actions/evil"}
        selected, rejected = self.module.select([row], "woosungchoi/fpm-alpine")
        self.assertEqual(selected, [])
        self.assertIn(1, rejected)

    def test_fork_draft_wrong_base_and_changes_requested_rejected(self) -> None:
        mutations = (
            {"isCrossRepository": True},
            {"isDraft": True},
            {"baseRefName": "other"},
            {"reviewDecision": "CHANGES_REQUESTED"},
            {"headRepository": {"nameWithOwner": "other/repo"}},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                row = {**BASE, **mutation}
                self.assertEqual(
                    self.module.select([row], "woosungchoi/fpm-alpine")[0], []
                )

    def test_unknown_branch_rejected(self) -> None:
        row = {**BASE, "headRefName": "feature/unrelated"}
        self.assertEqual(self.module.select([row], "woosungchoi/fpm-alpine")[0], [])

    def test_malformed_rows_fail_closed(self) -> None:
        selected, rejected = self.module.select(
            [{"number": True}, {"number": 3}], "woosungchoi/fpm-alpine"
        )
        self.assertEqual(selected, [])
        self.assertTrue(rejected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
