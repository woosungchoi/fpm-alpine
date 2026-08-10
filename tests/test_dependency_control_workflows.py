#!/usr/bin/env python3
"""Structural tests for the simple dependency PR -> merge -> Docker Hub flow."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


class DependencyControlWorkflowTests(unittest.TestCase):
    def load(self, name: str) -> tuple[str, dict]:
        text = (WORKFLOWS / name).read_text()
        data = yaml.safe_load(text)
        return text, data

    def trigger(self, data: dict) -> dict:
        value = data.get("on", data.get(True))
        assert isinstance(value, dict)
        return value

    def assert_actions_are_pinned(self, text: str) -> None:
        refs = re.findall(r"^\s*uses:\s*([^\s#]+)", text, re.MULTILINE)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"^[^@]+@[0-9a-f]{40}$")

    def test_dependency_updater_opens_prs_from_a_schedule(self) -> None:
        text, data = self.load("dependency-update-pr.yml")
        trigger = self.trigger(data)
        self.assertEqual(set(trigger), {"schedule", "workflow_dispatch", "workflow_run"})
        self.assertEqual(
            trigger["workflow_run"],
            {
                "workflows": ["dependency-auto-publish"],
                "types": ["completed"],
                "branches": ["main"],
            },
        )
        self.assertIn("DEPENDENCY_AUTOMATION_ENABLED", data["jobs"]["create-prs"]["if"])
        self.assertIn("create-dependency-update-pr.sh", text)
        self.assertIn("Create only the next eligible pull request", text)
        self.assertIn(".github/workflows/dependency-auto-publish.yml", text)
        self.assert_actions_are_pinned(text)

    def test_successful_smoke_run_enables_native_auto_merge_for_validated_dependency_prs(self) -> None:
        text, data = self.load("dependency-auto-merge.yml")
        trigger = self.trigger(data)
        self.assertEqual(set(trigger), {"workflow_run"})
        self.assertEqual(trigger["workflow_run"]["workflows"], ["smoke-test"])
        self.assertEqual(trigger["workflow_run"]["types"], ["completed"])
        self.assertEqual(data["permissions"], {})
        job = data["jobs"]["enable-native-auto-merge"]
        self.assertEqual(job["environment"], "dependency-updater")
        self.assertEqual(
            job["permissions"],
            {"actions": "read", "contents": "read", "pull-requests": "read"},
        )
        self.assertIn("DEPENDENCY_AUTO_MERGE_ENABLED", job["if"])
        self.assertIn("github.event.workflow_run.conclusion == 'success'", job["if"])
        rendered = yaml.safe_dump(job, sort_keys=False)
        self.assertIn(".github/workflows/smoke-test.yml", rendered)
        updater_text = (WORKFLOWS / "dependency-update-pr.yml").read_text()
        updater_token = re.search(r"actions/create-github-app-token@[0-9a-f]{40}", updater_text)
        self.assertIsNotNone(updater_token)
        if updater_token is None:
            return
        self.assertIn(updater_token.group(0), text)
        self.assertIn("actions/create-github-app-token@", rendered)
        self.assertIn("vars.DEPENDENCY_UPDATE_APP_ID", rendered)
        self.assertIn("secrets.DEPENDENCY_UPDATE_APP_PRIVATE_KEY", rendered)
        self.assertIn("permission-contents: write", rendered)
        self.assertIn("permission-pull-requests: write", rendered)
        self.assertIn("evaluate-auto-merge-pr.sh", rendered)
        merge_step = next(step for step in job["steps"] if step.get("name") == "Enable native auto-merge")
        self.assertEqual(merge_step["env"]["GH_TOKEN"], "${{ steps.app-token.outputs.token }}")
        self.assertIn("gh pr merge", merge_step["run"])
        self.assertIn("--auto", merge_step["run"])
        self.assertIn("--match-head-commit", merge_step["run"])
        self.assertNotIn("--admin", merge_step["run"])
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("schedule:", text)
        self.assertNotIn("repository_dispatch:", text)
        self.assert_actions_are_pinned(text)

    def test_versions_merge_publishes_directly_to_both_registries(self) -> None:
        text, data = self.load("dependency-auto-publish.yml")
        trigger = self.trigger(data)
        self.assertEqual(set(trigger), {"push", "workflow_dispatch"})
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertEqual(trigger["push"]["paths"], ["build/versions.json"])
        self.assertEqual(data["permissions"], {"contents": "read"})
        prepare = data["jobs"]["prepare"]
        publisher = data["jobs"]["publish"]
        self.assertIn("validate-versions.py", yaml.safe_dump(prepare))
        self.assertIn("evaluate-auto-promotion.py", yaml.safe_dump(prepare))
        self.assertEqual(
            publisher["strategy"]["matrix"],
            "${{ fromJSON(needs.prepare.outputs.matrix) }}",
        )
        rendered = yaml.safe_dump(publisher, sort_keys=False)
        self.assertEqual(publisher["environment"], "fpm-auto-production")
        self.assertIn("linux/amd64,linux/arm64", rendered)
        self.assertIn("docker/build-push-action@", rendered)
        self.assertIn("push: true", rendered)
        self.assertIn("secrets.DOCKERHUB_TOKEN", rendered)
        self.assertIn("DOCKERHUB_REPOSITORY", rendered)
        self.assertIn("GHCR_REPOSITORY", rendered)
        self.assertNotIn("repository_dispatch", text)
        self.assertNotIn("transaction-journal", text)
        self.assert_actions_are_pinned(text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
