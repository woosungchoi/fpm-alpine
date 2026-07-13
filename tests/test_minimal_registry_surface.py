#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def assert_registry_boundary(jobs: dict) -> None:
    canary = yaml.safe_dump(jobs["canary"], sort_keys=False)
    production = yaml.safe_dump(jobs["production"], sort_keys=False)
    assert "DOCKERHUB_REPOSITORY" not in canary
    assert "docker.io" not in canary
    assert "dockerhub_digest" not in canary
    assert "scripts/verify-canary-image.sh" in canary
    assert "scripts/promote-image.sh --policy moving-only" in production
    assert "scripts/promote-image.sh --policy evidence" in production


class MinimalRegistrySurfaceTests(unittest.TestCase):
    def test_canary_is_ghcr_only_and_production_is_registry_specific(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())
        jobs = workflow["jobs"]
        assert_registry_boundary(jobs)
        canary = yaml.safe_dump(jobs["canary"], sort_keys=False)
        production = yaml.safe_dump(jobs["production"], sort_keys=False)
        self.assertNotIn("DOCKERHUB_REPOSITORY", canary)
        self.assertNotIn("docker.io", canary)
        self.assertNotIn("dockerhub_digest", canary)
        self.assertIn("scripts/verify-canary-image.sh", canary)
        self.assertIn("scripts/promote-image.sh --policy moving-only", production)
        self.assertIn("scripts/promote-image.sh --policy evidence", production)
        self.assertIn("cosign sign --yes", production)
        self.assertEqual(
            jobs["production"]["permissions"],
            {"actions": "read", "contents": "read", "packages": "write", "id-token": "write"},
        )

    def test_registry_boundary_contract_rejects_mutations(self):
        jobs = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())["jobs"]
        canary_mutation = copy.deepcopy(jobs)
        canary_mutation["canary"]["steps"].append(
            {"name": "mutation", "run": "echo $DOCKERHUB_REPOSITORY"}
        )
        with self.assertRaises(AssertionError):
            assert_registry_boundary(canary_mutation)

        public_immutable_mutation = copy.deepcopy(jobs)
        for step in public_immutable_mutation["production"]["steps"]:
            if step.get("name") == "Promote verified GHCR canary without rebuilding":
                step["run"] = step["run"].replace(
                    "--policy moving-only", "--policy public-immutable"
                )
        with self.assertRaises(AssertionError):
            assert_registry_boundary(public_immutable_mutation)

    def test_canary_metadata_v2_rejects_v1_and_dockerhub_fields(self):
        script = ROOT / "scripts/validate-canary-metadata.py"
        source_sha = "0123456789abcdef0123456789abcdef01234567"
        valid = {
            "schema_version": 2,
            "channel": "canary",
            "source_sha": source_sha,
            "php_minor": "8.5",
            "php_patch": "8.5.8",
            "run_id": 123,
            "run_attempt": 1,
            "canonical_registry": "ghcr.io",
            "canonical_repository": "ghcr.io/woosungchoi/fpm-alpine",
            "canonical_ref": "ghcr.io/woosungchoi/fpm-alpine:canary-8.5-123-1",
            "ghcr_digest": "sha256:" + "2" * 64,
            "platforms": ["linux/amd64", "linux/arm64"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "canary-metadata.json"

            def run(payload):
                path.write_text(json.dumps(payload))
                return subprocess.run(
                    [str(script), tmp, source_sha, "8.5", "8.5.8", "123", "1"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode

            self.assertEqual(run(valid), 0)
            v1 = dict(valid)
            v1["schema_version"] = 1
            self.assertNotEqual(run(v1), 0)
            contaminated = dict(valid)
            contaminated["dockerhub_digest"] = "sha256:" + "1" * 64
            self.assertNotEqual(run(contaminated), 0)

    def test_public_verifier_matrix_contains_only_active_minors(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/verify-published-manifest.yml").read_text())
        refs = workflow["jobs"]["verify-maintained-tags"]["strategy"]["matrix"]["image_ref"]
        self.assertEqual(
            refs,
            [
                "woosungchoi/fpm-alpine:8.2",
                "woosungchoi/fpm-alpine:8.3",
                "woosungchoi/fpm-alpine:8.4",
                "woosungchoi/fpm-alpine:8.5",
            ],
        )
        self.assertIn("verify-dockerhub-tag-policy", workflow["jobs"])

    def test_cleanup_workflow_is_manual_protected_and_hash_bound(self):
        path = ROOT / ".github/workflows/prune-dockerhub-tags.yml"
        workflow = yaml.safe_load(path.read_text())
        trigger = workflow.get("on", workflow.get(True))
        self.assertEqual(set(trigger), {"workflow_dispatch"})
        self.assertEqual(set(workflow["jobs"]), {"plan", "apply"})
        self.assertNotIn("environment", workflow["jobs"]["plan"])
        job = workflow["jobs"]["apply"]
        self.assertEqual(job["environment"], "fpm-production")
        self.assertIn("id-token", job["permissions"])
        text = path.read_text()
        for required in (
            "expected_inventory_sha256",
            "expected_deletion_plan_sha256",
            "plan_run_id",
            "DELETE NON-ACTIVE DOCKER HUB TAGS",
            "scripts/archive-dockerhub-tags.py",
            "scripts/prune-dockerhub-tags.py",
        ):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
