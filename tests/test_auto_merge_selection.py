#!/usr/bin/env python3
"""Tests for auto-merge PR metadata selection."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

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

    def test_dependabot_graphql_app_actor_selected(self) -> None:
        row = {
            **BASE,
            "number": 3,
            "author": {"login": "app/dependabot"},
            "headRefName": "dependabot/github_actions/actions/checkout-7.0.1",
        }
        self.assertEqual(self.module.select([row], "woosungchoi/fpm-alpine")[0], [3])

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


class WorkflowPreselectionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="fpm-auto-merge-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/select-auto-merge-prs.py", self.root / "scripts")
        (self.root / "bin").mkdir()
        gh = self.root / "bin/gh"
        gh.write_text('#!/bin/sh\ncat "$PR_FIXTURE"\nexit "${GH_EXIT_CODE:-0}"\n')
        gh.chmod(0o755)
        workflow = yaml.safe_load((ROOT / ".github/workflows/dependency-auto-merge.yml").read_text())
        self.steps = workflow["jobs"]["enable-native-auto-merge"]["steps"]
        self.step = next(step for step in self.steps if step.get("id") == "candidate")

    def test_non_candidates_skip_but_dependency_candidates_continue(self) -> None:
        for rows, expected in (
            ([BASE], "true"),
            ([{**BASE, "headRefName": "fix/publisher-manual-skip", "author": {"login": "maintainer"}}], "false"),
            ([{**BASE, "isDraft": True}], "false"),
            ([], "false"),
        ):
            with self.subTest(rows=rows):
                fixture = self.root / "pr.json"
                fixture.write_text(json.dumps(rows))
                output = self.root / "output"
                output.write_text("")
                result = subprocess.run(
                    ["bash", "-c", self.step["run"]], cwd=self.root,
                    env=dict(os.environ, PATH=str(self.root / "bin") + os.pathsep + os.environ["PATH"],
                             PR_FIXTURE=str(fixture), GITHUB_OUTPUT=str(output),
                             PR_NUMBER="1", GITHUB_REPOSITORY="woosungchoi/fpm-alpine"),
                    text=True, capture_output=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_text(), f"selected={expected}\n")

    def test_api_errors_do_not_become_normal_skips(self) -> None:
        output = self.root / "output"
        output.write_text("")
        result = subprocess.run(
            ["bash", "-c", self.step["run"]], cwd=self.root,
            env=dict(os.environ, PATH=str(self.root / "bin") + os.pathsep + os.environ["PATH"],
                     PR_FIXTURE="/dev/null", GH_EXIT_CODE="1", GITHUB_OUTPUT=str(output),
                     PR_NUMBER="1", GITHUB_REPOSITORY="woosungchoi/fpm-alpine"),
            text=True, capture_output=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_text(), "")

    def test_validation_token_and_merge_require_positive_preselection(self) -> None:
        for name in ("Validate dependency-only PR", "Create updater app token", "Enable native auto-merge"):
            step = next(step for step in self.steps if step.get("name") == name)
            self.assertEqual(step["if"], "steps.candidate.outputs.selected == 'true'")
        validation = next(step for step in self.steps if step.get("name") == "Validate dependency-only PR")
        self.assertIn("evaluate-auto-merge-pr.sh", validation["run"])
        self.assertIn('test "$eligible_sha" = "$CHECKED_HEAD_SHA"', validation["run"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
