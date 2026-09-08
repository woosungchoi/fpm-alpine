"""Structural and executable tests for the merged-main dual-registry publisher."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
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
        self.assertEqual(
            prepare["outputs"]["should_publish"],
            "${{ steps.matrix.outputs.should_publish }}",
        )
        self.assertEqual(
            self.workflow["jobs"]["publish"]["if"],
            "${{ needs.prepare.outputs.should_publish == 'true' }}",
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


class PublisherSelectionTests(unittest.TestCase):
    """Run the actual workflow shell against isolated, real Git histories."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="fpm-publisher-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(ROOT / "scripts", self.root / "scripts")
        shutil.copytree(ROOT / "build", self.root / "build")
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Publisher test")
        self.git("config", "user.email", "publisher@example.invalid")
        self.base = self.commit()
        self.versions = json.loads((self.root / "build/versions.json").read_text())
        row = self.versions["versions"]["8.5"]
        row["base_image"] = row["base_image"][:-1] + (
            "1" if row["base_image"][-1] == "0" else "0"
        )
        workflow = yaml.safe_load(WORKFLOW.read_text())
        self.script = next(
            step["run"] for step in workflow["jobs"]["prepare"]["steps"]
            if step.get("id") == "matrix"
        )

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=self.root, env=self.env, text=True,
            stderr=subprocess.PIPE,
        ).strip()

    def commit(self) -> str:
        self.git("add", ".")
        self.git("commit", "--allow-empty", "-m", "Publisher fixture")
        return self.git("rev-parse", "HEAD")

    def select(self, **overrides: str) -> subprocess.CompletedProcess:
        (self.root / "build/versions.json").write_text(json.dumps(self.versions))
        head = self.commit()
        output = self.root / "output"
        summary = self.root / "summary"
        output.write_text("")
        summary.write_text("")
        env = dict(
            self.env, EVENT_NAME="push", BEFORE_SHA=self.base,
            REQUESTED_VERSION="", GITHUB_REF="refs/heads/main", GITHUB_SHA=head,
            GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary),
            RUNNER_TEMP=str(self.root),
        )
        env.update(overrides)
        result = subprocess.run(
            ["bash", "-c", self.script], cwd=self.root, env=env,
            text=True, capture_output=True, timeout=30,
        )
        self.outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.summary = summary.read_text()
        return result

    def test_version_only_update_selects_one_minor(self) -> None:
        result = self.select()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs["should_publish"], "true")
        rows = json.loads(self.outputs["matrix"])["include"]
        self.assertEqual([row["php_minor"] for row in rows], ["8.5"])

    def test_pr111_mixed_test_changes_skip_with_manual_guidance(self) -> None:
        (self.root / "tests").mkdir()
        for name in ("test_dependency_automation.py", "test_dependency_candidates.py"):
            (self.root / "tests" / name).write_text("# Updated test fixture\n")
        result = self.select()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs["should_publish"], "false")
        self.assertEqual(json.loads(self.outputs["matrix"]), {"include": []})
        self.assertIn("outside dependency auto-promotion allowlist", self.summary)
        self.assertIn("workflow_dispatch", self.summary)
        self.assertIn("::notice::", result.stdout + result.stderr)

    def test_manual_one_and_all_keep_the_existing_entry_point(self) -> None:
        for version, expected in (("8.5", ["8.5"]), ("all", ["8.2", "8.3", "8.4", "8.5"])):
            with self.subTest(version=version):
                result = self.select(EVENT_NAME="workflow_dispatch", REQUESTED_VERSION=version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.outputs["should_publish"], "true")
                rows = json.loads(self.outputs["matrix"])["include"]
                self.assertEqual(sorted(row["php_minor"] for row in rows), expected)

    def test_invalid_source_and_manual_inputs_still_fail_closed(self) -> None:
        for overrides in (
            {"GITHUB_REF": "refs/heads/untrusted"},
            {"GITHUB_SHA": "0" * 40},
            {"BEFORE_SHA": "not-a-commit"},
            {"EVENT_NAME": "workflow_dispatch", "REQUESTED_VERSION": "8.6"},
            {"EVENT_NAME": "pull_request", "REQUESTED_VERSION": "8.5"},
        ):
            with self.subTest(overrides=overrides):
                result = self.select(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(self.outputs.get("should_publish"), "true")

    def test_invalid_manifest_does_not_become_a_successful_skip(self) -> None:
        self.versions["versions"]["8.5"]["base_image"] = "php:latest"
        result = self.select()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(self.outputs.get("should_publish"), "true")

    def test_bad_classifier_results_still_fail(self) -> None:
        for fields in (
            {"eligible": False, "class": "invalid", "affectedMinors": []},
            {"eligible": False, "class": "unknown", "affectedMinors": []},
            {"eligible": True, "class": "base-same-minor", "affectedMinors": ["8.4", "8.5"]},
            {"eligible": True, "class": "base-same-minor", "affectedMinors": ["8.6"]},
        ):
            with self.subTest(fields=fields):
                (self.root / "scripts/evaluate-auto-promotion.py").write_text(
                    "import json, os\nfrom pathlib import Path\n"
                    f"data = {fields!r}\n"
                    "data.update(schemaVersion=1, sourceCommit=os.environ['GITHUB_SHA'])\n"
                    "(Path(os.environ['RUNNER_TEMP']) / 'promotion.json').write_text(json.dumps(data))\n"
                )
                result = self.select()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(self.outputs.get("should_publish"), "true")


if __name__ == "__main__":
    unittest.main(verbosity=2)
