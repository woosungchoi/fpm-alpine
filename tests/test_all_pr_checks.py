#!/usr/bin/env python3
"""Regression tests for strict all-checks merging (no live writes)."""

from __future__ import annotations

import copy
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("all_pr_checks", ROOT / "scripts/wait-for-pr-checks.py")
assert SPEC is not None and SPEC.loader is not None
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)
REPO = "owner/repo"
SHA = "a" * 40
BASE = "b" * 40
PR = {
    "state": "open", "draft": False,
    "head": {"sha": SHA, "repo": {"full_name": REPO}},
    "base": {"ref": "main", "sha": BASE, "repo": {"full_name": REPO}},
}
CHECKS = [{"name": "docker-smoke", "state": "SUCCESS", "link": "https://example.test/1"}]
RUN = {
    "id": 10, "workflow_id": 1, "event": "pull_request", "head_sha": SHA,
    "path": ".github/workflows/smoke-test.yml", "status": "completed",
    "conclusion": "success", "run_attempt": 2,
}


class GateTests(unittest.TestCase):
    def inspect(self, checks=None, runs=None, after=None):
        replies = [PR, CHECKS if checks is None else checks,
                   [{"total_count": 1 if runs is None else len(runs),
                     "workflow_runs": [RUN] if runs is None else runs}],
                   PR if after is None else after]
        with patch.object(GATE, "gh_json", side_effect=copy.deepcopy(replies)) as gh:
            result = GATE.inspect(REPO, 1, SHA)
        return result, gh.call_args_list

    def test_all_success_and_all_checks_not_just_required_are_fetched(self):
        (pending, _), calls = self.inspect()
        self.assertEqual(pending, [])
        self.assertNotIn("--required", calls[1].args)
        self.assertIn("--paginate", calls[2].args)
        self.assertIn("--slurp", calls[2].args)
        self.assertIn(f"head_sha={SHA}", calls[2].args[-1])

    def test_every_pending_state_blocks_optional_checks(self):
        for state in ("PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED", "EXPECTED"):
            with self.subTest(state=state):
                (pending, _), _ = self.inspect(checks=CHECKS + [{"name": "extra", "state": state}])
                self.assertIn("extra", pending)

    def test_failures_skipped_neutral_and_unknown_never_pass(self):
        for state in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "SKIPPED", "NEUTRAL", "STALE", "UNKNOWN", None):
            with self.subTest(state=state), self.assertRaises(GATE.CheckError):
                self.inspect(checks=CHECKS + [{"name": "extra", "state": state}])

    def test_missing_checks_or_required_aggregate_waits(self):
        for checks in ([], [{"name": "extra", "state": "SUCCESS"}]):
            (pending, _), _ = self.inspect(checks=checks)
            self.assertIn("missing docker-smoke", pending)

    def test_absent_smoke_workflow_cannot_pass(self):
        (pending, _), _ = self.inspect(runs=[])
        self.assertIn("missing pull-request smoke workflow", pending)

    def test_queued_workflow_without_any_jobs_blocks(self):
        run = {**RUN, "id": 11, "workflow_id": 2, "path": "extra.yml", "status": "queued", "conclusion": None}
        (pending, _), _ = self.inspect(runs=[RUN, run])
        self.assertIn("workflow 11", pending[0])

    def test_failed_or_skipped_latest_workflow_blocks(self):
        for conclusion in ("failure", "cancelled", "skipped", "neutral", None):
            with self.subTest(conclusion=conclusion), self.assertRaises(GATE.CheckError):
                self.inspect(runs=[{**RUN, "conclusion": conclusion}])

    def test_successful_retry_supersedes_old_failed_workflow(self):
        old = {**RUN, "id": 9, "conclusion": "failure"}
        (pending, _), _ = self.inspect(runs=[RUN, old])
        self.assertEqual(pending, [])

    def test_newer_workflow_cannot_use_old_success(self):
        newer = {**RUN, "id": 11, "status": "in_progress", "conclusion": None}
        (pending, _), _ = self.inspect(runs=[RUN, newer])
        self.assertTrue(pending)

    def test_workflow_events_are_independent(self):
        push = {**RUN, "id": 11, "event": "push", "status": "queued", "conclusion": None}
        (pending, _), _ = self.inspect(runs=[RUN, push])
        self.assertTrue(pending)

    def test_later_page_run_is_checked(self):
        newer = {**RUN, "id": 11, "status": "queued", "conclusion": None}
        rows = GATE.latest_runs([{"total_count": 2, "workflow_runs": [RUN]},
                                {"total_count": 2, "workflow_runs": [newer]}], SHA)
        self.assertEqual(rows, [newer])

    def test_malformed_or_wrong_sha_workflows_fail_closed(self):
        for pages in (None, [], [{}], [{"total_count": 1, "workflow_runs": [None]}],
                      [{"total_count": 1, "workflow_runs": [{**RUN, "head_sha": BASE}]}],
                      [{"total_count": 1, "workflow_runs": [{**RUN, "id": True}]}],
                      [{"total_count": 1001, "workflow_runs": [RUN]}]):
            with self.subTest(pages=pages), self.assertRaises(GATE.CheckError):
                GATE.latest_runs(pages, SHA)

    def test_changed_head_base_fork_closed_or_draft_pr_blocks(self):
        mutations = (
            {"state": "closed"}, {"draft": True},
            {"head": {**PR["head"], "sha": BASE}},
            {"head": {**PR["head"], "repo": {"full_name": "fork/repo"}}},
            {"base": {**PR["base"], "ref": "other"}},
            {"base": {**PR["base"], "sha": "c" * 40}},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(GATE.CheckError):
                self.inspect(after={**PR, **mutation})

    def test_api_errors_never_become_success(self):
        with patch.object(GATE, "gh_json", side_effect=subprocess.CalledProcessError(1, "gh")):
            with self.assertRaises(subprocess.CalledProcessError):
                GATE.inspect(REPO, 1, SHA)

    def test_json_command_success_is_not_check_success(self):
        with patch.object(GATE.subprocess, "run", return_value=subprocess.CompletedProcess(
            ["gh"], 0, '[{"name":"docker-smoke","state":"FAILURE"}]', "",
        )):
            data = GATE.gh_json("pr", "checks")
        with self.assertRaises(GATE.CheckError):
            GATE.check_states(data)

    def test_wait_requires_two_identical_success_snapshots(self):
        snapshots = [(["queued"], "a"), ([], "a"), ([], "b"), ([], "b")]
        with patch.object(GATE, "inspect", side_effect=snapshots) as inspect, patch.object(GATE.time, "sleep"):
            GATE.wait(REPO, 1, SHA, timeout=60, interval=10)
        self.assertEqual(inspect.call_count, 4)

    def test_final_once_recheck_does_not_wait_or_queue_merge(self):
        with patch.object(GATE, "inspect", return_value=(["queued"], "a")), patch.object(GATE.time, "sleep") as sleep:
            with self.assertRaises(GATE.CheckError):
                GATE.wait(REPO, 1, SHA, timeout=60, interval=10, once=True)
            sleep.assert_not_called()


class MergeStepTests(unittest.TestCase):
    def test_failed_final_gate_never_calls_merge_and_success_keeps_exact_sha(self):
        data = yaml.safe_load((ROOT / ".github/workflows/dependency-auto-merge.yml").read_text())
        step = next(step for step in data["jobs"]["enable-native-auto-merge"]["steps"]
                    if step.get("name") == "Merge fully checked dependency PR")
        with tempfile.TemporaryDirectory(prefix="fpm-merge-step-") as directory:
            root = Path(directory)
            gate = root / "python3"
            gate.write_text('#!/bin/sh\n[ "$GH_TOKEN" = read-only ] || exit 91\nexit "$GATE_EXIT"\n')
            gate.chmod(0o755)
            gh = root / "gh"
            gh.write_text('#!/bin/sh\n[ "$GH_TOKEN" = merge-only ] || exit 92\nprintf "%s\\n" "$@" > "$MERGE_LOG"\n')
            gh.chmod(0o755)
            for exit_code in (1, 0):
                with self.subTest(exit_code=exit_code):
                    log = root / f"merge-{exit_code}"
                    env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"],
                               GH_TOKEN="read-only", MERGE_TOKEN="merge-only", GATE_EXIT=str(exit_code),
                               GITHUB_REPOSITORY=REPO, PR_NUMBER="1", CHECKED_HEAD_SHA=SHA,
                               MERGE_LOG=str(log))
                    result = subprocess.run(["bash", "-c", step["run"]], env=env,
                                            text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, exit_code, result.stderr)
                    if exit_code:
                        self.assertFalse(log.exists())
                    else:
                        args = log.read_text().splitlines()
                        self.assertIn("--match-head-commit", args)
                        self.assertEqual(args[-1], SHA)
                        self.assertNotIn("--auto", args)
                        self.assertNotIn("--admin", args)

    def test_timeout_and_failure_stop_immediately(self):
        with patch.object(GATE, "inspect", return_value=(["queued"], "a")):
            with self.assertRaises(GATE.CheckError):
                GATE.wait(REPO, 1, SHA, timeout=0, interval=10)
        with patch.object(GATE, "inspect", side_effect=GATE.CheckError("failed")), patch.object(GATE.time, "sleep") as sleep:
            with self.assertRaises(GATE.CheckError):
                GATE.wait(REPO, 1, SHA, timeout=60, interval=10)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
