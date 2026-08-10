"""Structural tests for the simple merged-main dual-registry publisher."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "dependency-auto-publish.yml"


class DependencyAutoPublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text()
        cls.workflow = yaml.safe_load(cls.text)
        cls.trigger = cls.workflow.get("on", cls.workflow.get(True))

    def test_only_main_version_merges_and_manual_sync_trigger(self) -> None:
        self.assertEqual(set(self.trigger), {"push", "workflow_dispatch"})
        self.assertEqual(self.trigger["push"]["branches"], ["main"])
        self.assertEqual(self.trigger["push"]["paths"], ["build/versions.json"])
        options = self.trigger["workflow_dispatch"]["inputs"]["version"]["options"]
        self.assertEqual(options, ["all", "8.2", "8.3", "8.4", "8.5"])
        self.assertNotIn("repository_dispatch", self.text)

    def test_prepare_accepts_one_same_minor_php_patch_only(self) -> None:
        prepare = self.workflow["jobs"]["prepare"]
        rendered = yaml.safe_dump(prepare, sort_keys=False)
        self.assertIn("scripts/validate-versions.py", rendered)
        self.assertIn("scripts/evaluate-auto-promotion.py", rendered)
        self.assertIn("base-same-minor", self.text)
        self.assertIn("len(affected) != 1", self.text)
        self.assertIn("8.2,8.3,8.4,8.5", self.text)
        self.assertEqual(
            prepare["outputs"]["matrix"], "${{ steps.matrix.outputs.matrix }}"
        )

    def test_one_build_pushes_the_same_minor_to_both_registries(self) -> None:
        self.assertEqual(set(self.workflow["jobs"]), {"prepare", "publish"})
        publish = self.workflow["jobs"]["publish"]
        self.assertEqual(publish["environment"], "fpm-auto-production")
        self.assertEqual(
            publish["permissions"], {"contents": "read", "packages": "write"}
        )
        self.assertEqual(
            publish["strategy"]["matrix"],
            "${{ fromJSON(needs.prepare.outputs.matrix) }}",
        )
        build = next(
            step for step in publish["steps"]
            if str(step.get("uses", "")).startswith("docker/build-push-action@")
        )
        self.assertTrue(build["with"]["push"])
        self.assertEqual(build["with"]["platforms"], "linux/amd64,linux/arm64")
        tags = build["with"]["tags"]
        self.assertIn("DOCKERHUB_REPOSITORY", tags)
        self.assertIn("GHCR_REPOSITORY", tags)
        self.assertIn("matrix.php_minor", tags)
        self.assertNotIn(":latest", self.text.lower())

    def test_existing_token_and_digest_readback_are_direct(self) -> None:
        publish = self.workflow["jobs"]["publish"]
        rendered = yaml.safe_dump(publish, sort_keys=False)
        self.assertIn("secrets.DOCKERHUB_USERNAME", rendered)
        self.assertIn("secrets.DOCKERHUB_TOKEN", rendered)
        self.assertIn("dockerhub_digest", rendered)
        self.assertIn("ghcr_digest", rendered)
        self.assertIn('test "$dockerhub_digest" = "$BUILD_DIGEST"', self.text)
        self.assertIn('test "$ghcr_digest" = "$BUILD_DIGEST"', self.text)
        self.assertIn('test "$dockerhub_digest" = "$ghcr_digest"', self.text)
        for forbidden in (
            "transaction-journal",
            "cutover",
            "backfill",
            "replay",
            "rollback",
            "promotion-plan",
        ):
            self.assertNotIn(forbidden, self.text.lower())

    def test_all_actions_are_full_sha_pinned(self) -> None:
        refs = re.findall(r"^\s*uses:\s*([^\s#]+)", self.text, re.MULTILINE)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"^[^@]+@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
