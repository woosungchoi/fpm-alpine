#!/usr/bin/env python3
"""Structural safety tests for the dependency updater workflow."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
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
        self.assertEqual(set(trigger), {"schedule", "workflow_dispatch", "workflow_run"})
        self.assertEqual(
            trigger["workflow_run"],
            {
                "workflows": ["dependency-auto-publish"],
                "types": ["completed"],
                "branches": ["main"],
            },
        )
        self.assertEqual(data["permissions"], {})
        jobs = data["jobs"]
        self.assertEqual(
            jobs["discover"]["permissions"],
            {"actions": "read", "contents": "read"},
        )
        for binding in (
            ".github/workflows/dependency-auto-publish.yml",
            "'event': 'push'",
            "'status': 'completed'",
            "'conclusion': 'success'",
            "'head_branch': 'main'",
            "'head_sha': expected_sha",
            "head_repository",
        ):
            self.assertIn(binding, text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", jobs["discover"]["if"])
        continuation = next(
            step for step in jobs["discover"]["steps"]
            if step.get("id") == "publisher"
        )
        self.assertIn("--paginate --slurp", continuation["run"])
        self.assertIn("/attempts/$UPSTREAM_RUN_ATTEMPT/jobs", continuation["run"])
        self.assertEqual(
            continuation["env"]["UPSTREAM_RUN_ATTEMPT"],
            "${{ github.event.workflow_run.run_attempt }}",
        )
        for name in ("Discover dependency candidates", "Validate candidate summary", "Upload candidate evidence"):
            step = next(step for step in jobs["discover"]["steps"] if step.get("name") == name)
            self.assertIn("steps.publisher.outputs.did_publish == 'true'", step["if"])
        create = jobs["create-prs"]
        self.assertIn("needs.discover.outputs.eligible_count != ''", create["if"])
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
        download = next(
            step
            for step in create["steps"]
            if step["name"] == "Download candidate evidence"
        )
        self.assertEqual(
            download["with"]["path"], "${{ runner.temp }}/dependency-candidates"
        )
        create_prs = next(
            step
            for step in create["steps"]
            if step["name"] == "Create only the next eligible pull request"
        )
        candidate_file = create_prs["env"]["CANDIDATE_FILE"]
        self.assertEqual(
            candidate_file,
            "${{ runner.temp }}/dependency-candidates/candidates.json",
        )
        self.assertIn("Path(os.environ['CANDIDATE_FILE'])", create_prs["run"])
        self.assertIn('"$CANDIDATE_FILE" "$candidate_key"', create_prs["run"])
        self.assertIn("eligible[0]", create_prs["run"])
        self.assertNotIn("while IFS= read", create_prs["run"])
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

    def test_pr_script_restores_trusted_source_for_next_candidate(self) -> None:
        text = SCRIPT.read_text()
        create_index = text.index("gh pr create")
        restore_index = text.index('git switch --detach "$source_sha"')
        self.assertGreater(restore_index, create_index)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", text)

    def test_every_action_is_full_sha_pinned(self) -> None:
        text = WORKFLOW.read_text()
        refs = re.findall(r"^\s*uses:\s*([^\s#]+)", text, re.MULTILINE)
        self.assertTrue(refs)
        for ref in refs:
            self.assertRegex(ref, r"^[^@]+@[0-9a-f]{40}$")


class PublisherContinuationTests(unittest.TestCase):
    """Exercise the actual workflow validator with paginated GitHub API fixtures."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="fpm-continuation-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        workflow = yaml.safe_load(WORKFLOW.read_text())
        step = next(
            step for step in workflow["jobs"]["discover"]["steps"]
            if step.get("id") == "publisher"
        )
        self.script = step["run"].split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
        self.run = {
            "id": 123, "run_attempt": 2,
            "path": ".github/workflows/dependency-auto-publish.yml",
            "event": "push", "status": "completed", "conclusion": "success",
            "head_branch": "main", "head_sha": "a" * 40,
            "head_repository": {"full_name": "woosungchoi/fpm-alpine"},
        }

    def validate(self, jobs: list[dict]) -> subprocess.CompletedProcess:
        (self.root / "upstream-publisher-run.json").write_text(json.dumps(self.run))
        (self.root / "upstream-publisher-jobs.json").write_text(json.dumps([
            {"jobs": [{"name": "Select changed PHP minor", "conclusion": "success"}]},
            {"jobs": jobs},
        ]))
        output = self.root / "output"
        output.write_text("")
        result = subprocess.run(
            [sys.executable, "-c", self.script], text=True, capture_output=True,
            env=dict(
                os.environ, UPSTREAM_RUN_ID="123", UPSTREAM_RUN_ATTEMPT="2",
                EXPECTED_SHA="a" * 40, GITHUB_REPOSITORY="woosungchoi/fpm-alpine",
                GITHUB_OUTPUT=str(output), RUNNER_TEMP=str(self.root),
            ), timeout=10,
        )
        self.output = output.read_text()
        return result

    def test_only_an_actual_successful_publish_job_continues(self) -> None:
        for conclusion, expected in (("success", "true"), ("skipped", "false"), ("failure", "false")):
            with self.subTest(conclusion=conclusion):
                result = self.validate([{
                    "name": "Publish 8.5 to Docker Hub and GHCR",
                    "status": "completed", "conclusion": conclusion,
                }])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.output, f"did_publish={expected}\n")

    def test_prepare_success_alone_does_not_continue(self) -> None:
        result = self.validate([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output, "did_publish=false\n")
        self.assertIn("::notice::", result.stdout)

    def test_run_identity_mismatches_remain_errors(self) -> None:
        for key, value in (("run_attempt", 1), ("head_sha", "b" * 40), ("event", "workflow_dispatch")):
            with self.subTest(key=key):
                previous = self.run[key]
                self.run[key] = value
                result = self.validate([])
                self.run[key] = previous
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
