#!/usr/bin/env python3
"""Structural tests for auto-merge and auto-canary control workflows."""

from __future__ import annotations

import copy
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

    def assert_auto_merge_uses_trusted_dispatch(self, data: dict) -> None:
        trigger = data.get("on", data.get(True))
        assert isinstance(trigger, dict)
        self.assertEqual(set(trigger), {"schedule", "repository_dispatch"})
        self.assertEqual(
            trigger["repository_dispatch"], {"types": ["dependency-auto-merge"]}
        )

        select = data["jobs"]["select"]
        merge = data["jobs"]["enable-native-auto-merge"]
        self.assertIn("github.ref == 'refs/heads/main'", select["if"])
        checkout = next(
            step for step in select["steps"] if step["name"] == "Checkout trusted main"
        )
        self.assertEqual(checkout["with"]["ref"], "${{ github.sha }}")

        merge_if = merge["if"]
        self.assertIn("github.ref == 'refs/heads/main'", merge_if)
        self.assertIn("github.event_name == 'schedule'", merge_if)
        self.assertIn("github.event_name == 'repository_dispatch'", merge_if)
        self.assertIn("github.event.action == 'dependency-auto-merge'", merge_if)
        self.assertIn(
            "toJSON(github.event.client_payload.report_only) == 'false'", merge_if
        )
        self.assertNotIn("inputs.report_only", merge_if)

    def test_auto_merge_separates_read_selection_from_write_enablement(self) -> None:
        text, data = self.load("dependency-auto-merge.yml")
        self.assert_auto_merge_uses_trusted_dispatch(data)
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

    def test_auto_merge_trusted_dispatch_contract_rejects_mutations(self) -> None:
        _, data = self.load("dependency-auto-merge.yml")
        trigger_key = "on" if "on" in data else True
        variants = []

        non_main = copy.deepcopy(data)
        non_main[trigger_key]["workflow_dispatch"] = {}
        variants.append(("branch-selectable dispatch", non_main))

        wrong_event = copy.deepcopy(data)
        wrong_event[trigger_key]["repository_dispatch"]["types"] = ["other"]
        variants.append(("wrong repository event", wrong_event))

        moving_checkout = copy.deepcopy(data)
        checkout = next(
            step
            for step in moving_checkout["jobs"]["select"]["steps"]
            if step["name"] == "Checkout trusted main"
        )
        checkout["with"]["ref"] = "refs/heads/main"
        variants.append(("moving main checkout", moving_checkout))

        loose_boolean = copy.deepcopy(data)
        loose_boolean["jobs"]["enable-native-auto-merge"]["if"] = loose_boolean[
            "jobs"
        ]["enable-native-auto-merge"]["if"].replace(
            "toJSON(github.event.client_payload.report_only) == 'false'",
            "github.event.client_payload.report_only == false",
        )
        variants.append(("loosely typed report_only", loose_boolean))

        for name, variant in variants:
            with self.subTest(name=name):
                with self.assertRaises(AssertionError):
                    self.assert_auto_merge_uses_trusted_dispatch(variant)

    def test_auto_promote_only_dispatches_canary_and_is_activation_gated(self) -> None:
        text, data = self.load("dependency-auto-promote.yml")
        self.assertEqual(data["permissions"], {})
        evaluate = data["jobs"]["evaluate"]
        canary = data["jobs"]["auto-canary"]
        dispatch = data["jobs"]["dispatch-production-controller"]
        self.assertEqual(
            evaluate["permissions"],
            {"contents": "read", "pull-requests": "read", "checks": "read"},
        )
        self.assertEqual(
            canary["permissions"], {"actions": "write", "contents": "read"}
        )
        self.assertIn("DEPENDENCY_AUTO_CANARY_ENABLED", canary["if"])
        self.assertEqual(dispatch["permissions"], {"contents": "write"})
        self.assertEqual(dispatch["needs"], ["evaluate", "auto-canary"])
        self.assertIn("DEPENDENCY_AUTO_PRODUCTION_ENABLED", dispatch["if"])
        rendered = yaml.safe_dump(canary, sort_keys=False)
        self.assertIn("run-auto-canary.sh", rendered)
        self.assertIn("dependency-auto-production", yaml.safe_dump(dispatch, sort_keys=False))
        self.assertIn("repos/$GITHUB_REPOSITORY/dispatches", text)
        merge_workflow = self.load("dependency-auto-merge.yml")[1]
        evaluate_run = next(
            step["run"]
            for step in merge_workflow["jobs"]["select"]["steps"]
            if step.get("name") == "Revalidate the oldest selected PR only"
        )
        self.assertIn("read -r pr < auto-merge-selection/preselected.txt", evaluate_run)
        self.assertNotIn("while IFS= read -r pr", evaluate_run)
        self.assertNotIn("channel=production", text)
        self.assertNotIn("packages: write", text)
        self.assertNotIn("id-token: write", text)
        self.assertNotIn("pull_request_target", text)
        self.assert_pinned(text)

    def test_exact_check_app_and_production_authorization_are_source_contracts(self) -> None:
        merged = (ROOT / "scripts/validate-merged-dependency-pr.sh").read_text()
        merge_eval = (ROOT / "scripts/evaluate-auto-merge-pr.sh").read_text()
        canary = (ROOT / "scripts/run-auto-canary.sh").read_text()
        for text in (merged, merge_eval):
            self.assertIn("15368", text)
            self.assertIn('get("name")', text)
            self.assertIn('"docker-smoke"', text)
        self.assertIn('"productionAuthorized": True', canary)
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

    def test_auto_production_is_separate_and_activation_gated(self) -> None:
        text, data = self.load("dependency-auto-production.yml")
        trigger = data.get("on", data.get(True))
        self.assertEqual(
            trigger,
            {
                "repository_dispatch": {
                    "types": ["dependency-auto-production"],
                }
            },
        )
        self.assertEqual(data["permissions"], {})
        authorize = data["jobs"]["authorize"]
        production = data["jobs"]["dispatch-production"]
        self.assertEqual(
            authorize["permissions"], {"actions": "read", "contents": "read"}
        )
        self.assertEqual(
            production["permissions"], {"actions": "write", "contents": "read"}
        )
        self.assertIn("DEPENDENCY_AUTO_PRODUCTION_ENABLED", authorize["if"])
        self.assertIn("github.event.action == 'dependency-auto-production'", authorize["if"])
        rendered = yaml.safe_dump(data, sort_keys=False)
        self.assertIn('"event": "push"', text)
        self.assertIn('"head_branch": "main"', text)
        self.assertIn("validate-auto-production-evidence.py", rendered)
        self.assertIn("validate-settled-publisher-state.py", rendered)
        self.assertIn("run-auto-production.sh", rendered)
        self.assertIn("dependency-update-pr.yml", rendered)
        self.assertIn("dry_run=false", rendered)
        self.assertNotIn("DOCKERHUB_TOKEN", text)
        self.assertNotIn("packages: write", text)
        self.assertNotIn("id-token: write", text)
        self.assert_pinned(text)

    def test_publisher_separates_manual_and_automatic_environments(self) -> None:
        text, data = self.load("publish.yml")
        trigger = data.get("on", data.get(True))
        inputs = trigger["workflow_dispatch"]["inputs"]
        self.assertIn("auto_promotion_run_id", inputs)
        self.assertIn("auto_promotion_run_attempt", inputs)
        prepare = data["jobs"]["prepare"]
        self.assertIn("production_environment", prepare["outputs"])
        for job_name in ("bootstrap-ghcr-rollback", "production"):
            self.assertEqual(
                data["jobs"][job_name]["environment"],
                "${{ needs.prepare.outputs.production_environment }}",
            )
        self.assertIn("fpm-auto-production", text)
        self.assertIn("fpm-production", text)
        self.assertIn("validate-auto-production-evidence.py", text)
        self.assertGreaterEqual(text.count("validate-settled-publisher-state.py"), 3)
        self.assertIn("DEPENDENCY_AUTO_PRODUCTION_ENABLED", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
