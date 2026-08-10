from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
OWNER_ENV = {
    "EVENT_NAME": "repository_dispatch",
    "EVENT_ACTOR": "woosungchoi",
    "EVENT_ACTOR_ID": "5674610",
    "EVENT_REPOSITORY": "woosungchoi/fpm-alpine",
    "EVENT_REF": "refs/heads/main",
    "EVENT_SHA": "a" * 40,
}


def load_workflow(name: str):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def trigger(workflow: dict):
    return workflow.get("on", workflow.get(True))


def run_envelope(script: str, payload: dict, **overrides: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "github-output"
        env = {
            **os.environ,
            **OWNER_ENV,
            **overrides,
            "EVENT_PAYLOAD_JSON": json.dumps(payload),
            "GITHUB_OUTPUT": str(output),
        }
        return subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )


class RegistryDispatchAuthorityTests(unittest.TestCase):
    def test_required_dependency_safety_runs_this_contract(self) -> None:
        smoke = (WORKFLOWS / "smoke-test.yml").read_text()
        self.assertIn("python3 tests/test_registry_dispatch_authority.py", smoke)

    def test_manual_publisher_is_owner_default_branch_repository_dispatch(self) -> None:
        workflow = load_workflow("publish.yml")
        self.assertEqual(
            trigger(workflow),
            {"repository_dispatch": {"types": ["fpm-manual-publish"]}},
        )
        prepare = workflow["jobs"]["prepare"]
        self.assertEqual(
            [step["name"] for step in prepare["steps"][:2]],
            [
                "Validate owner dispatch envelope before checkout",
                "Checkout exact source revision",
            ],
        )
        envelope = prepare["steps"][0]["run"]
        checkout = prepare["steps"][1]["with"]
        self.assertEqual(checkout["ref"], "${{ github.event.client_payload.source_sha }}")
        self.assertFalse(checkout["persist-credentials"])

        canary = {"channel": "canary", "source_sha": "a" * 40, "version": "8.5"}
        production = {
            "channel": "production",
            "source_sha": "a" * 40,
            "version": "8.5",
            "canary_run_id": 123,
            "canary_run_attempt": 1,
            "prior_canary_run_id": "122",
            "prior_canary_run_attempt": "1",
            "legacy_publisher_disabled": True,
            "legacy_cutover_evidence_sha256": "b" * 64,
        }
        completed = run_envelope(
            envelope,
            canary,
            EVENT_ACTION="fpm-manual-publish",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        invalid = (
            ({**canary, "unknown": "value"}, {}),
            ({**canary, "canary_run_id": 123}, {}),
            (production, {}),
            (production, {"EVENT_ACTOR_ID": "1"}),
        )
        for payload, override in invalid:
            with self.subTest(payload=payload, override=override):
                completed = run_envelope(
                    envelope,
                    payload,
                    EVENT_ACTION="fpm-manual-publish",
                    **override,
                )
                self.assertNotEqual(completed.returncode, 0)

    def test_metadata_sync_is_owner_default_branch_repository_dispatch(self) -> None:
        workflow = load_workflow("sync-dockerhub-metadata.yml")
        self.assertEqual(
            trigger(workflow),
            {"repository_dispatch": {"types": ["fpm-sync-dockerhub-metadata"]}},
        )
        steps = workflow["jobs"]["sync"]["steps"]
        self.assertEqual(steps[0]["name"], "Validate owner dispatch envelope before checkout")
        self.assertEqual(steps[1]["name"], "Checkout trusted main")
        self.assertEqual(steps[1]["with"]["ref"], "${{ github.sha }}")
        self.assertFalse(steps[1]["with"]["persist-credentials"])
        envelope = steps[0]["run"]
        valid = run_envelope(
            envelope,
            {},
            EVENT_ACTION="fpm-sync-dockerhub-metadata",
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        for payload, actor_id in (({"extra": True}, "5674610"), ({}, "1")):
            invalid = run_envelope(
                envelope,
                payload,
                EVENT_ACTION="fpm-sync-dockerhub-metadata",
                EVENT_ACTOR_ID=actor_id,
            )
            self.assertNotEqual(invalid.returncode, 0)

    def test_dependency_backfill_and_recovery_are_owner_bound_before_checkout(self) -> None:
        cases = (
            (
                "dependency-auto-publish.yml",
                "fpm-ghcr-backfill",
                {"operation": "backfill-ghcr", "source_sha": "a" * 40},
            ),
            (
                "dependency-auto-publish.yml",
                "fpm-dependency-publish-replay",
                {"operation": "automatic-replay", "source_sha": "a" * 40},
            ),
            (
                "dependency-publish-recovery.yml",
                "fpm-publish-recover",
                {
                    "operation": "recovery",
                    "original_run_id": 123,
                    "original_run_attempt": 1,
                    "plan_sha256": "b" * 64,
                },
            ),
        )
        for workflow_name, action, payload in cases:
            with self.subTest(workflow=workflow_name):
                workflow = load_workflow(workflow_name)
                prepare = workflow["jobs"]["prepare"]
                first = prepare["steps"][0]
                self.assertEqual(first["name"], "Validate owner dispatch envelope before checkout")
                completed = run_envelope(first["run"], payload, EVENT_ACTION=action)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                for bad_payload, overrides in (
                    ({**payload, "unknown": True}, {}),
                    (payload, {"EVENT_ACTOR_ID": "1"}),
                    (payload, {"EVENT_REPOSITORY": "other/repository"}),
                    (payload, {"EVENT_REF": "refs/heads/topic"}),
                ):
                    rejected = run_envelope(
                        first["run"],
                        bad_payload,
                        EVENT_ACTION=action,
                        **overrides,
                    )
                    self.assertNotEqual(rejected.returncode, 0)

    def test_prune_is_owner_bound_read_only_plan_only(self) -> None:
        workflow = load_workflow("prune-dockerhub-tags.yml")
        self.assertEqual(
            trigger(workflow),
            {"repository_dispatch": {"types": ["fpm-dockerhub-prune"]}},
        )
        self.assertEqual(set(workflow["jobs"]), {"authorize", "plan"})
        authorize = workflow["jobs"]["authorize"]
        envelope = authorize["steps"][0]["run"]
        completed = run_envelope(
            envelope,
            {"mode": "plan"},
            EVENT_ACTION="fpm-dockerhub-prune",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        invalid_payloads = (
            {"mode": "plan", "plan_run_id": 123},
            {"mode": "archive"},
            {"mode": "apply"},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                completed = run_envelope(
                    envelope,
                    payload,
                    EVENT_ACTION="fpm-dockerhub-prune",
                )
                self.assertNotEqual(completed.returncode, 0)

        job = workflow["jobs"]["plan"]
        self.assertEqual(job["needs"], "authorize")
        self.assertIn("needs.authorize.outputs.mode == 'plan'", job["if"])
        checkout = next(step for step in job["steps"] if step["name"] == "Checkout trusted main")
        self.assertEqual(checkout["with"]["ref"], "${{ github.sha }}")
        text = (WORKFLOWS / "prune-dockerhub-tags.yml").read_text()
        self.assertNotIn("workflow_dispatch", text)
        for forbidden in (
            "DOCKERHUB_TOKEN",
            "packages: write",
            "id-token: write",
            "prune-dockerhub-tags.py apply",
            "archive-dockerhub-tags.py",
            "environment:",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
