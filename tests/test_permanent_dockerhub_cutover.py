"""Tests for the fail-closed Docker Hub permanent-cutover verifier."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify-permanent-dockerhub-cutover.py"
REPOSITORY_ATTESTATION = ROOT / ".github/dockerhub-cutover-attestation.json"
OWNER_STATEMENT = (
    "I observed zero queued and running Docker Hub legacy builds, removed every "
    "source-capable publisher hook and external writer, and rotated a dedicated "
    "GitHub Actions Docker Hub write token."
)
VALID_METADATA = {
    "namespace": "woosungchoi",
    "name": "fpm-alpine",
    "status": 1,
    "is_automated": False,
    "last_updated": "2026-08-10T05:53:44.484575Z",
}
VALID_ATTESTATION = {
    "schemaVersion": 1,
    "status": "active",
    "repository": "woosungchoi/fpm-alpine",
    "owner": {"login": "woosungchoi", "id": 5674610},
    "attestedAt": "2026-08-10T08:06:00Z",
    "queueObservation": {
        "source": "dockerhub-builds-ui-owner-observation",
        "observedAt": "2026-08-10T08:00:00Z",
        "queued": 0,
        "running": 0,
    },
    "activeGitHubHooks": [
        {
            "id": 402842509,
            "name": "web",
            "active": True,
            "events": ["pull_request", "push"],
            "urlHost": "api.snyk.io",
            "urlKind": "github-webhook-uuid",
        }
    ],
    "dockerHubToken": {
        "dedicatedTo": "github-actions:woosungchoi/fpm-alpine",
        "rotatedAt": "2026-08-10T08:05:00Z",
        "externalWritersRemoved": True,
    },
    "ownerStatement": OWNER_STATEMENT,
}


class PermanentDockerHubCutoverTests(unittest.TestCase):
    def run_check(
        self,
        metadata: dict,
        *,
        attestation: dict | None = None,
        expected: dict | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict | None]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata_path = root / "metadata.json"
            attestation_path = root / "attestation.json"
            output_path = root / "result.json"
            metadata_path.write_text(json.dumps(metadata))
            attestation_payload = (
                copy.deepcopy(VALID_ATTESTATION)
                if attestation is None
                else attestation
            )
            attestation_raw = (
                json.dumps(attestation_payload, indent=2, sort_keys=True) + "\n"
            ).encode()
            attestation_path.write_bytes(attestation_raw)
            command = [
                "python3",
                str(SCRIPT),
                "--metadata-file",
                str(metadata_path),
                "--attestation",
                str(attestation_path),
                "--output",
                str(output_path),
            ]
            if expected is not None:
                expected_path = root / "expected.json"
                expected_path.write_text(json.dumps(expected))
                command.extend(("--expected-state", str(expected_path)))
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            payload = json.loads(output_path.read_text()) if output_path.exists() else None
            if payload is not None:
                self.assertEqual(
                    payload["attestation"]["sha256"],
                    hashlib.sha256(attestation_raw).hexdigest(),
                )
            return completed, payload

    def assert_rejected(
        self,
        *,
        metadata: dict | None = None,
        attestation: dict | None = None,
        expected: dict | None = None,
    ) -> None:
        completed, payload = self.run_check(
            copy.deepcopy(VALID_METADATA) if metadata is None else metadata,
            attestation=attestation,
            expected=expected,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIsNone(payload)

    def test_repository_attestation_is_pending_or_strictly_active(self) -> None:
        payload = json.loads(REPOSITORY_ATTESTATION.read_text())
        if payload.get("status") == "pending":
            self.assertIsNone(payload["attestedAt"])
            self.assertIsNone(payload["queueObservation"])
            self.assertEqual(payload["activeGitHubHooks"], [])
            self.assertIsNone(payload["dockerHubToken"])
            self.assertIsNone(payload["ownerStatement"])
            return

        self.assertEqual(payload.get("status"), "active")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata_path = root / "metadata.json"
            output_path = root / "evidence.json"
            metadata_path.write_text(json.dumps(VALID_METADATA))
            result = subprocess.run(
                [
                    str(SCRIPT),
                    "--attestation",
                    str(REPOSITORY_ATTESTATION),
                    "--metadata-file",
                    str(metadata_path),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_active_attestation_and_live_metadata_write_bound_evidence(self) -> None:
        completed, payload = self.run_check(copy.deepcopy(VALID_METADATA))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        assert payload is not None
        self.assertEqual(payload["schemaVersion"], 2)
        self.assertEqual(payload["repository"], "woosungchoi/fpm-alpine")
        self.assertEqual(payload["dockerHub"]["lastUpdated"], VALID_METADATA["last_updated"])
        self.assertEqual(payload["attestation"]["owner"], VALID_ATTESTATION["owner"])
        self.assertEqual(
            payload["attestation"]["queueObservedAt"],
            VALID_ATTESTATION["queueObservation"]["observedAt"],
        )
        self.assertEqual(
            payload["attestation"]["tokenRotatedAt"],
            VALID_ATTESTATION["dockerHubToken"]["rotatedAt"],
        )

    def test_pending_inferred_or_nonzero_queue_is_rejected(self) -> None:
        pending = copy.deepcopy(VALID_ATTESTATION)
        pending["status"] = "pending"
        self.assert_rejected(attestation=pending)
        for field, value in (("queued", 1), ("running", 1), ("queued", False)):
            with self.subTest(field=field, value=value):
                attestation = copy.deepcopy(VALID_ATTESTATION)
                attestation["queueObservation"][field] = value
                self.assert_rejected(attestation=attestation)
        inferred = copy.deepcopy(VALID_ATTESTATION)
        inferred["queueObservation"]["source"] = "automation-disabled-inference"
        self.assert_rejected(attestation=inferred)

    def test_token_rotation_and_external_writer_claims_are_strict(self) -> None:
        cases = []
        wrong_order = copy.deepcopy(VALID_ATTESTATION)
        wrong_order["dockerHubToken"]["rotatedAt"] = "2026-08-10T07:59:59Z"
        cases.append(wrong_order)
        shared = copy.deepcopy(VALID_ATTESTATION)
        shared["dockerHubToken"]["dedicatedTo"] = "shared-token"
        cases.append(shared)
        external = copy.deepcopy(VALID_ATTESTATION)
        external["dockerHubToken"]["externalWritersRemoved"] = False
        cases.append(external)
        wrong_statement = copy.deepcopy(VALID_ATTESTATION)
        wrong_statement["ownerStatement"] = "looks safe"
        cases.append(wrong_statement)
        for index, attestation in enumerate(cases):
            with self.subTest(case=index):
                self.assert_rejected(attestation=attestation)

    def test_exact_owner_repository_and_nonpublisher_hook_set_are_required(self) -> None:
        cases = []
        wrong_owner = copy.deepcopy(VALID_ATTESTATION)
        wrong_owner["owner"]["id"] = 1
        cases.append(wrong_owner)
        wrong_repository = copy.deepcopy(VALID_ATTESTATION)
        wrong_repository["repository"] = "woosungchoi/other"
        cases.append(wrong_repository)
        missing_hook = copy.deepcopy(VALID_ATTESTATION)
        missing_hook["activeGitHubHooks"] = []
        cases.append(missing_hook)
        publisher_hook = copy.deepcopy(VALID_ATTESTATION)
        publisher_hook["activeGitHubHooks"].append(
            {
                "id": 1,
                "name": "web",
                "active": True,
                "events": ["push"],
                "urlHost": "hub.docker.com",
                "urlKind": "publisher",
            }
        )
        cases.append(publisher_hook)
        for index, attestation in enumerate(cases):
            with self.subTest(case=index):
                self.assert_rejected(attestation=attestation)

    def test_live_repository_automation_and_status_are_fail_closed(self) -> None:
        for field, value in (
            ("namespace", "other"),
            ("name", "other"),
            ("is_automated", True),
            ("status", True),
            ("status", 0),
        ):
            with self.subTest(field=field, value=value):
                metadata = copy.deepcopy(VALID_METADATA)
                metadata[field] = value
                self.assert_rejected(metadata=metadata)

    def test_expected_state_binds_attestation_and_allows_only_monotonic_metadata_time(self) -> None:
        first, expected = self.run_check(copy.deepcopy(VALID_METADATA))
        self.assertEqual(first.returncode, 0, first.stderr)
        assert expected is not None

        later = copy.deepcopy(VALID_METADATA)
        later["last_updated"] = "2026-08-10T06:00:00Z"
        second, _ = self.run_check(later, expected=expected)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

        earlier = copy.deepcopy(VALID_METADATA)
        earlier["last_updated"] = "2026-08-10T05:00:00Z"
        self.assert_rejected(metadata=earlier, expected=expected)

        changed_attestation = copy.deepcopy(VALID_ATTESTATION)
        changed_attestation["attestedAt"] = "2026-08-10T08:07:00Z"
        self.assert_rejected(attestation=changed_attestation, expected=expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
