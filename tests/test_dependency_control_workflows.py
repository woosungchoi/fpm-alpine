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
        refs = re.findall(r"^\s*uses:\s*([^\s#]+)", text, re.M)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"^[^@]+@[0-9a-f]{40}$")

    def test_dependency_updater_opens_prs_from_a_schedule(self) -> None:
        text, data = self.load("dependency-update-pr.yml")
        trigger = self.trigger(data)
        self.assertEqual(set(trigger), {"schedule", "workflow_dispatch"})
        self.assertIn("DEPENDENCY_AUTOMATION_ENABLED", data["jobs"]["create-prs"]["if"])
        self.assertIn("create-dependency-update-pr.sh", text)
        self.assert_actions_are_pinned(text)

    def test_successful_smoke_run_enables_native_auto_merge_for_validated_dependency_prs(self) -> None:
        text, data = self.load("dependency-auto-merge.yml")
        trigger = self.trigger(data)
        self.assertEqual(set(trigger), {"workflow_run"})
        self.assertEqual(trigger["workflow_run"]["workflows"], ["smoke-test"])
        self.assertEqual(trigger["workflow_run"]["types"], ["completed"])
        self.assertEqual(data["permissions"], {})
        job = data["jobs"]["enable-native-auto-merge"]
        self.assertEqual(
            job["permissions"],
            {"actions": "read", "contents": "write", "pull-requests": "write"},
        )
        self.assertIn("DEPENDENCY_AUTO_MERGE_ENABLED", job["if"])
        self.assertIn("github.event.workflow_run.conclusion == 'success'", job["if"])
        rendered = yaml.safe_dump(job, sort_keys=False)
        self.assertIn(".github/workflows/smoke-test.yml", rendered)
        self.assertIn("evaluate-auto-merge-pr.sh", rendered)
        self.assertIn("gh pr merge", rendered)
        self.assertIn("--auto", rendered)
        self.assertIn("--match-head-commit", rendered)
        self.assertNotIn("--admin", rendered)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("schedule:", text)
        self.assertNotIn("repository_dispatch:", text)
        self.assert_actions_are_pinned(text)

    def test_versions_merge_publishes_all_minors_directly_to_docker_hub(self) -> None:
        text, data = self.load("dependency-auto-publish.yml")
        trigger = self.trigger(data)
        self.assertEqual(set(trigger), {"push"})
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertEqual(trigger["push"]["paths"], ["build/versions.json"])
        self.assertEqual(data["permissions"], {"contents": "read"})
        prepare = data["jobs"]["prepare"]
        publish = data["jobs"]["publish-dockerhub"]
        self.assertIn("evaluate-auto-promotion.py", yaml.safe_dump(prepare))
        self.assertEqual(publish["strategy"]["matrix"], "${{ fromJSON(needs.prepare.outputs.matrix) }}")
        rendered = yaml.safe_dump(publish, sort_keys=False)
        self.assertIn("secrets.DOCKERHUB_USERNAME", rendered)
        self.assertIn("secrets.DOCKERHUB_TOKEN", rendered)
        self.assertIn("linux/amd64,linux/arm64", rendered)
        self.assertIn("docker/build-push-action@", rendered)
        self.assertIn("push: true", rendered)
        self.assertIn("woosungchoi/fpm-alpine:${{ matrix.php_minor }}", rendered)
        self.assertIn("steps.build.outputs.digest", rendered)
        self.assertNotIn("ghcr.io", text.lower())
        self.assertNotIn("canary", text.lower())
        self.assertNotIn("workflow_dispatch", text)
        self.assert_actions_are_pinned(text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
