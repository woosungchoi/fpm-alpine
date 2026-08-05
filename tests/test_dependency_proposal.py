#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "a" * 40
RUN_ID = 12345
RUN_ATTEMPT = 2
CANDIDATE = {
    "key": "base-8.2",
    "class": "base-same-minor",
    "eligible": True,
    "affectedMinors": ["8.2"],
    "old": {"patch": "8.2.32", "image": "php:8.2.32-fpm-alpine@sha256:" + "1" * 64},
    "new": {"patch": "8.2.33", "image": "php:8.2.33-fpm-alpine@sha256:" + "2" * 64},
}
HEAD = b'{"versions":{"8.2":{"patch":"8.2.33"}}}\n'


def load_module():
    path = ROOT / "scripts/validate-dependency-proposal.py"
    spec = importlib.util.spec_from_file_location("validate_dependency_proposal", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_hash(row: dict) -> str:
    encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def proposal() -> dict:
    digest = candidate_hash(CANDIDATE)
    return {
        "schemaVersion": 1,
        "sourceCommit": SOURCE,
        "runId": RUN_ID,
        "runAttempt": RUN_ATTEMPT,
        "candidateKey": "base-8.2",
        "candidateSha256": digest,
        "headVersionsSha256": hashlib.sha256(HEAD).hexdigest(),
        "updaterAppId": 4284917,
        "updaterUser": {"id": 9876, "login": "fpm-alpine-updater[bot]", "type": "Bot"},
        "candidate": CANDIDATE,
    }


class DependencyProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def validate(self, payload: dict | None = None, **overrides):
        raw = json.dumps(payload or proposal(), sort_keys=True, separators=(",", ":")).encode()
        expected = hashlib.sha256(raw).hexdigest()
        args = {
            "raw": raw,
            "expected_hash": expected,
            "source_sha": SOURCE,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "candidate_key": "base-8.2",
            "head_ref": "automation/base-8.2-" + candidate_hash(CANDIDATE)[:12],
            "author_login": "fpm-alpine-updater[bot]",
            "author_id": 9876,
            "head_versions": HEAD,
        }
        args.update(overrides)
        return self.module.validate_proposal(**args)

    def test_valid_proposal_binds_candidate_app_and_head_bytes(self) -> None:
        result = self.validate()
        self.assertEqual(result["sourceCommit"], SOURCE)
        self.assertEqual(result["candidates"], [CANDIDATE])

    def test_candidate_mutation_without_new_canonical_hash_is_rejected(self) -> None:
        payload = proposal()
        payload["candidate"]["new"]["image"] = "php:8.2.33-fpm-alpine@sha256:" + "3" * 64
        with self.assertRaisesRegex(SystemExit, "candidate hash"):
            self.validate(payload)

    def test_wrong_app_author_is_rejected(self) -> None:
        with self.assertRaisesRegex(SystemExit, "updater App identity"):
            self.validate(author_login="other-bot[bot]")

    def test_changed_head_versions_are_rejected(self) -> None:
        with self.assertRaisesRegex(SystemExit, "head versions"):
            self.validate(head_versions=HEAD + b" ")

    def test_branch_suffix_must_match_canonical_candidate(self) -> None:
        with self.assertRaisesRegex(SystemExit, "branch"):
            self.validate(head_ref="automation/base-8.2-000000000000")


if __name__ == "__main__":
    unittest.main(verbosity=2)
