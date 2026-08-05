#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_auto_merge_evaluator import CANDIDATE

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "create-dependency-update-pr.sh"
REPOSITORY = "woosungchoi/fpm-alpine"


class DependencyProposalCreationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.clone = self.root / "clone"
        subprocess.run(
            ["git", "clone", "--bare", str(ROOT), str(self.remote)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "clone", str(self.remote), str(self.clone)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.source = self.git("rev-parse", "HEAD").strip()
        self.candidate_report = self.root / "candidates.json"
        self.candidate_report.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "sourceCommit": self.source,
                    "candidates": [CANDIDATE],
                    "warnings": [],
                }
            )
        )
        self.proposal = self.root / "proposal"
        self.body_capture = self.root / "body.md"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self._write_fake_gh()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.clone,
            check=True,
            text=True,
            capture_output=True,
        ).stdout

    def _write_fake_gh(self) -> None:
        fake = self.bin / "gh"
        fake.write_text(
            """#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] == ['pr', 'list']:
    raise SystemExit(0)
if args[:2] == ['api', 'user']:
    print(json.dumps({'id': 999, 'login': 'fpm-alpine-dependency-updater[bot]', 'type': 'Bot'}))
    raise SystemExit(0)
if args[:2] == ['pr', 'create']:
    body = Path(args[args.index('--body-file') + 1])
    shutil.copy2(body, os.environ['BODY_CAPTURE'])
    print('https://github.com/woosungchoi/fpm-alpine/pull/999')
    raise SystemExit(0)
raise SystemExit('unexpected gh command: ' + ' '.join(args))
"""
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    def test_candidate_commit_push_and_proposal_are_exactly_bound(self) -> None:
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_TOKEN": "test-token",
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_SHA": self.source,
            "GITHUB_RUN_ID": "88",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_SERVER_URL": "https://github.com",
            "DEPENDENCY_UPDATE_APP_ID": "4284917",
            "BODY_CAPTURE": str(self.body_capture),
        }
        result = subprocess.run(
            [str(SCRIPT), str(self.candidate_report), "base-8.2", str(self.proposal)],
            cwd=self.clone,
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        proposal_file = self.proposal / "proposal.json"
        row = json.loads(proposal_file.read_text())
        canonical = json.dumps(CANDIDATE, sort_keys=True, separators=(",", ":")).encode()
        candidate_hash = hashlib.sha256(canonical).hexdigest()
        proposal_hash = hashlib.sha256(proposal_file.read_bytes()).hexdigest()
        branch = f"automation/base-8.2-{candidate_hash[:12]}"

        self.assertEqual(row["sourceCommit"], self.source)
        self.assertEqual(row["candidateSha256"], candidate_hash)
        self.assertEqual(row["updaterUser"]["id"], 999)
        self.assertEqual(row["runId"], 88)
        self.assertEqual(row["runAttempt"], 2)
        self.assertIn(f"<!-- fpm-dependency-proposal:{proposal_hash} -->", self.body_capture.read_text())
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.source)
        self.assertEqual(
            subprocess.run(
                ["git", "--git-dir", str(self.remote), "rev-parse", f"refs/heads/{branch}"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip(),
            self.git("rev-parse", branch).strip(),
        )
        head_versions = subprocess.run(
            ["git", "show", f"{branch}:build/versions.json"],
            cwd=self.clone,
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(hashlib.sha256(head_versions).hexdigest(), row["headVersionsSha256"])


if __name__ == "__main__":
    unittest.main()
