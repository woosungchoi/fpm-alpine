#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate-auto-merge-pr.sh"
REPOSITORY = "woosungchoi/fpm-alpine"
BASE_SHA = "b" * 40
HEAD_SHA = "c" * 40
RUN_ID = 88
RUN_ATTEMPT = 2
AUTHOR_ID = 999

CANDIDATE = {
    "key": "base-8.2",
    "class": "base-same-minor",
    "eligible": True,
    "affectedMinors": ["8.2"],
    "old": {
        "patch": "8.2.32",
        "base_image": "php:8.2-fpm-alpine@sha256:41ddda74d95c43518c3e4414e6c1c99f9c062d397f0c7a2d8cadf8d1f035d196",
    },
    "new": {
        "patch": "8.2.33",
        "base_image": "php:8.2-fpm-alpine@sha256:b57d486fdfb1bbee188d834714bba623d954c3c9dc5d4468fb0afb34ef7d0c07",
    },
    "evidence": {
        "floatingRef": "php:8.2-fpm-alpine",
        "resolvedDigest": "sha256:b57d486fdfb1bbee188d834714bba623d954c3c9dc5d4468fb0afb34ef7d0c07",
    },
}


class AutoMergeEvaluatorIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.artifact = self.root / "artifact"
        self.bin.mkdir()
        self.artifact.mkdir()
        self.base = self.root / "base.json"
        self.head = self.root / "head.json"
        shutil.copy2(ROOT / "build" / "versions.json", self.base)
        shutil.copy2(self.base, self.head)

        report = self.root / "candidate-report.json"
        report.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "sourceCommit": BASE_SHA,
                    "candidates": [CANDIDATE],
                    "warnings": [],
                }
            )
        )
        subprocess.run(
            [
                "python3",
                "scripts/resolve-dependency-candidates.py",
                "--versions",
                str(self.head),
                "--policy",
                "build/automation-policy.json",
                "--apply-from",
                str(report),
                "--apply-key",
                "base-8.2",
                "--apply-output",
                str(self.head),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        canonical_candidate = json.dumps(
            CANDIDATE, sort_keys=True, separators=(",", ":")
        ).encode()
        candidate_hash = hashlib.sha256(canonical_candidate).hexdigest()
        self.head_ref = f"automation/base-8.2-{candidate_hash[:12]}"
        proposal = {
            "schemaVersion": 1,
            "sourceCommit": BASE_SHA,
            "runId": RUN_ID,
            "runAttempt": RUN_ATTEMPT,
            "candidateKey": "base-8.2",
            "candidateSha256": candidate_hash,
            "headVersionsSha256": hashlib.sha256(self.head.read_bytes()).hexdigest(),
            "updaterAppId": 4_284_917,
            "updaterUser": {
                "id": AUTHOR_ID,
                "login": "fpm-alpine-dependency-updater[bot]",
                "type": "Bot",
            },
            "candidate": CANDIDATE,
        }
        proposal_raw = (
            json.dumps(proposal, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        self.proposal_hash = hashlib.sha256(proposal_raw).hexdigest()
        (self.artifact / "proposal.json").write_bytes(proposal_raw)
        self.pr = self.root / "pr.json"
        self._write_pr(AUTHOR_ID)
        self._write_api_fixtures()
        self._write_fake_gh()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_pr(self, author_id: int) -> None:
        body = f"""Automated dependency-only update.

- Discovery run: https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}
- Discovery attempt: `{RUN_ATTEMPT}`
- Proposal SHA-256: `{self.proposal_hash}`

<!-- fpm-dependency-candidate:base-8.2 -->
<!-- fpm-dependency-proposal:{self.proposal_hash} -->
"""
        self.pr.write_text(
            json.dumps(
                {
                    "number": 1,
                    "html_url": f"https://github.com/{REPOSITORY}/pull/1",
                    "state": "open",
                    "draft": False,
                    "user": {
                        "login": "fpm-alpine-dependency-updater[bot]",
                        "id": author_id,
                    },
                    "base": {
                        "ref": "main",
                        "sha": BASE_SHA,
                        "repo": {"full_name": REPOSITORY},
                    },
                    "head": {
                        "ref": self.head_ref,
                        "sha": HEAD_SHA,
                        "repo": {"full_name": REPOSITORY},
                    },
                    "mergeable_state": "clean",
                    "body": body,
                }
            )
        )

    def _write_api_fixtures(self) -> None:
        (self.root / "run.json").write_text(
            json.dumps(
                {
                    "id": RUN_ID,
                    "run_attempt": RUN_ATTEMPT,
                    "event": "schedule",
                    "status": "completed",
                    "conclusion": "success",
                    "path": ".github/workflows/dependency-update-pr.yml",
                    "head_branch": "main",
                    "head_sha": BASE_SHA,
                    "repository": {"full_name": REPOSITORY},
                    "head_repository": {"full_name": REPOSITORY},
                }
            )
        )
        (self.root / "checks.json").write_text(
            json.dumps(
                {
                    "check_runs": [
                        {
                            "name": "docker-smoke",
                            "app": {"id": 15368},
                            "status": "completed",
                            "conclusion": "success",
                        }
                    ]
                }
            )
        )

    def _write_fake_gh(self) -> None:
        fake = self.bin / "gh"
        fake.write_text(
            """#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
root = Path(os.environ['EVALUATOR_FIXTURE'])
if args[:2] == ['pr', 'diff']:
    print('build/versions.json')
    raise SystemExit(0)
if args[:2] == ['run', 'download']:
    target = Path(args[args.index('--dir') + 1])
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / 'artifact' / 'proposal.json', target / 'proposal.json')
    raise SystemExit(0)
if not args or args[0] != 'api':
    raise SystemExit('unexpected gh command: ' + ' '.join(args))
endpoint = args[1]
if endpoint.endswith('/pulls/1'):
    sys.stdout.write((root / 'pr.json').read_text())
elif endpoint.endswith('/git/ref/heads/automation/conveyor-lock'):
    if '--jq' in args:
        print(os.environ['BASE_SHA'])
    else:
        print(json.dumps({'object': {'sha': os.environ['BASE_SHA']}}))
elif f'/actions/runs/{os.environ["RUN_ID"]}' in endpoint:
    sys.stdout.write((root / 'run.json').read_text())
elif '/contents/build/versions.json?ref=' in endpoint:
    ref = endpoint.rsplit('=', 1)[1]
    source = root / ('base.json' if ref == os.environ['BASE_SHA'] else 'head.json')
    print(__import__('base64').b64encode(source.read_bytes()).decode())
elif '/check-runs?' in endpoint:
    sys.stdout.write((root / 'checks.json').read_text())
else:
    raise SystemExit('unexpected gh api endpoint: ' + endpoint)
"""
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    def run_evaluator(self) -> subprocess.CompletedProcess[str]:
        output = self.root / "eligible.tsv"
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_TOKEN": "test-token",
            "GITHUB_SERVER_URL": "https://github.com",
            "EVALUATOR_FIXTURE": str(self.root),
            "BASE_SHA": BASE_SHA,
            "RUN_ID": str(RUN_ID),
        }
        result = subprocess.run(
            [str(SCRIPT), "1", REPOSITORY, str(output)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        self.output = output
        return result

    def test_exact_proposal_and_current_pr_are_accepted(self) -> None:
        result = self.run_evaluator()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_text(), f"1\t{HEAD_SHA}\n")

    def test_rest_author_id_mismatch_is_rejected(self) -> None:
        self._write_pr(AUTHOR_ID + 1)
        result = self.run_evaluator()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
