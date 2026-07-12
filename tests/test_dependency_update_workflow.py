#!/usr/bin/env python3
"""Structural safety tests for the dependency updater workflow."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/dependency-update-pr.yml"
SCRIPT = ROOT / "scripts/create-dependency-update-pr.sh"


class UpdaterWorkflowTests(unittest.TestCase):
    def test_workflow_is_disabled_without_explicit_activation(self) -> None:
        text = WORKFLOW.read_text()
        data = yaml.safe_load(text)
        trigger = data.get("on", data.get(True))
        self.assertEqual(set(trigger), {"schedule", "workflow_dispatch"})
        self.assertEqual(data["permissions"], {})
        jobs = data["jobs"]
        self.assertEqual(jobs["discover"]["permissions"], {"contents": "read"})
        create = jobs["create-prs"]
        self.assertEqual(create["environment"], "dependency-updater")
        self.assertIn("DEPENDENCY_AUTOMATION_ENABLED", create["if"])
        self.assertIn("dry_run", create["if"])
        self.assertEqual(create["permissions"], {"contents": "read"})
        rendered = yaml.safe_dump(create, sort_keys=False)
        self.assertIn("DEPENDENCY_UPDATE_APP_ID", rendered)
        self.assertIn("DEPENDENCY_UPDATE_APP_PRIVATE_KEY", rendered)
        self.assertRegex(rendered, r"actions/create-github-app-token@[0-9a-f]{40}")
        self.assertRegex(rendered, r"actions/download-artifact@[0-9a-f]{40}")
        self.assertEqual(rendered.count("persist-credentials: false"), 1)
        self.assertIn("gh auth setup-git", rendered)
        self.assertNotIn("persist-credentials: true", text)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("packages: write", text)
        self.assertNotIn("id-token: write", text)
        discover = yaml.safe_dump(jobs["discover"], sort_keys=False)
        self.assertNotIn("DOCKERHUB_TOKEN", discover)
        self.assertNotIn("create-github-app-token", discover)

    def test_pr_script_creates_but_never_merges(self) -> None:
        text = SCRIPT.read_text()
        for required in (
            "--apply-from",
            "classify-dependency-change.py",
            "gh pr create",
            "git push origin",
            "automation/",
            "build/versions.json",
        ):
            self.assertIn(required, text)
        for forbidden in ("--force", "--admin", "gh pr merge", "git push -f"):
            self.assertNotIn(forbidden, text)
        self.assertRegex(text, r"\[\[ \"\$candidate_key\" =~")
        self.assertIn("candidate is not eligible", text)

    def test_every_action_is_full_sha_pinned(self) -> None:
        text = WORKFLOW.read_text()
        refs = re.findall(r"^\s*uses:\s*([^\s#]+)", text, re.M)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"^[^@]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
