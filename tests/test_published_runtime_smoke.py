#!/usr/bin/env python3
"""Regression tests for Docker-Hub-only published runtime verification."""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "published-runtime-smoke.yml"
DOCKERHUB_VERIFIER = ROOT / "scripts" / "verify-published-dockerhub-image.sh"


class PublishedRuntimeSmokeTests(unittest.TestCase):
    def load_workflow(self) -> tuple[str, dict]:
        text = WORKFLOW.read_text()
        data = yaml.safe_load(text)
        return text, data

    @staticmethod
    def trigger(data: dict) -> dict:
        value = data.get("on", data.get(True))
        assert isinstance(value, dict)
        return value

    def test_verification_scope_matches_the_publisher_that_triggered_it(self) -> None:
        _, data = self.load_workflow()
        trigger = self.trigger(data)
        self.assertEqual(
            trigger["workflow_run"]["workflows"],
            ["publish", "dependency-auto-publish"],
        )

        prepare = data["jobs"]["prepare"]
        self.assertEqual(
            prepare["outputs"]["verification_mode"],
            "${{ steps.mode.outputs.verification_mode }}",
        )
        mode = next(step for step in prepare["steps"] if step.get("id") == "mode")
        self.assertEqual(mode["env"]["EVENT_NAME"], "${{ github.event_name }}")
        self.assertEqual(
            mode["env"]["UPSTREAM_WORKFLOW"],
            "${{ github.event.workflow_run.name }}",
        )
        self.assertIn("verification_mode=dockerhub-only", mode["run"])
        self.assertIn('"$UPSTREAM_WORKFLOW" = publish', mode["run"])
        self.assertIn("verification_mode=multi-registry", mode["run"])

        steps = data["jobs"]["verify"]["steps"]
        cosign = next(step for step in steps if step["name"] == "Install Cosign")
        multi = next(
            step
            for step in steps
            if step["name"] == "Verify exact multi-registry runtime and supply chain"
        )
        dockerhub = next(
            step
            for step in steps
            if step["name"] == "Verify exact Docker Hub runtime and supply chain"
        )
        self.assertEqual(
            cosign["if"],
            "needs.prepare.outputs.verification_mode == 'multi-registry'",
        )
        self.assertEqual(
            multi["if"],
            "needs.prepare.outputs.verification_mode == 'multi-registry'",
        )
        self.assertIn("scripts/verify-published-image.sh", multi["run"])
        self.assertIn("ghcr.io/woosungchoi/fpm-alpine", multi["env"]["GHCR_REF"])
        self.assertNotIn("${{", multi["run"])
        self.assertEqual(
            dockerhub["if"],
            "needs.prepare.outputs.verification_mode == 'dockerhub-only'",
        )
        self.assertIn("scripts/verify-published-dockerhub-image.sh", dockerhub["run"])
        self.assertNotIn("ghcr.io", str(dockerhub.get("env", {})))
        self.assertNotIn("${{", dockerhub["run"])

    def test_source_inspection_is_bounded_and_retried(self) -> None:
        _, data = self.load_workflow()
        source = next(
            step
            for step in data["jobs"]["verify"]["steps"]
            if step["name"] == "Resolve published source revision"
        )
        self.assertIn("SOURCE_INSPECT_ATTEMPTS=5", source["run"])
        self.assertIn("source inspect attempt", source["run"])
        self.assertIn("source inspection failed after", source["run"])

    def test_dockerhub_verifier_is_isolated_from_ghcr_and_cosign(self) -> None:
        self.assertTrue(DOCKERHUB_VERIFIER.is_file())
        self.assertTrue(os.access(DOCKERHUB_VERIFIER, os.X_OK))
        text = DOCKERHUB_VERIFIER.read_text()
        for required in (
            "resolve-image-digest.sh",
            "report-manifest.sh",
            "verify-provenance.py",
            "resolve-platform-image.py",
            "smoke-test-image.sh",
            "org.opencontainers.image.revision",
            "linux/amd64",
            "linux/arm64",
            'INSPECT_ATTEMPTS="${INSPECT_ATTEMPTS:-5}"',
            "exact Docker Hub subject",
        ):
            self.assertIn(required, text)
        self.assertNotIn("ghcr.io", text.lower())
        self.assertNotIn("cosign", text.lower())

    def test_dockerhub_verifier_rejects_an_invalid_ref_before_network_use(self) -> None:
        completed = subprocess.run(
            [
                str(DOCKERHUB_VERIFIER),
                "ghcr.io/woosungchoi/fpm-alpine:8.5",
                "a" * 40,
                "8.5.8",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(completed.returncode, 64, completed.stdout)
        self.assertIn("usage:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
