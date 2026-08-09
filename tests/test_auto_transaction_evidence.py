from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-auto-transaction-result.py"
SOURCE_SHA = "d" * 40
WORKFLOW_SHA = "e" * 40
RUN_ID = 123
RUN_ATTEMPT = 1


class AutoTransactionEvidenceTests(unittest.TestCase):
    def payload(self, operation: str = "backfill-ghcr") -> dict:
        versions = json.loads((ROOT / "build/versions.json").read_text())["versions"]
        units = []
        for index, (minor, row) in enumerate(versions.items(), start=1):
            if row["support"] not in {"active", "security-only"}:
                continue
            units.append(
                {
                    "php_minor": minor,
                    "php_patch": row["patch"],
                    "source_sha": SOURCE_SHA,
                    "dockerhub_digest": f"sha256:{100 + index:064x}",
                    "ghcr_digest": f"sha256:{200 + index:064x}",
                }
            )
        return {
            "schema_version": 2,
            "status": "verified",
            "operation": operation,
            "repository": "woosungchoi/fpm-alpine",
            "workflow_path": ".github/workflows/dependency-auto-publish.yml",
            "workflow_sha": WORKFLOW_SHA,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "source_sha": SOURCE_SHA,
            "plan_sha256": "f" * 64,
            "release_units": units,
        }

    def plan(self, payload: dict) -> dict:
        units = []
        for index, unit in enumerate(payload["release_units"], start=1):
            previous_ghcr = f"sha256:{300 + index:064x}"
            previous_dockerhub = unit["dockerhub_digest"]
            minor = unit["php_minor"]
            if payload["operation"] == "automatic":
                previous_dockerhub = f"sha256:{400 + index:064x}"
                target_dockerhub = None
                dockerhub_source = None
                rollback_dockerhub_ref = (
                    "ghcr.io/woosungchoi/fpm-alpine:"
                    f"rollback-auto-dockerhub-{minor}-{RUN_ID}-{RUN_ATTEMPT}"
                )
                rollback_dockerhub_backup = previous_dockerhub
            else:
                target_dockerhub = unit["dockerhub_digest"]
                dockerhub_source = unit["dockerhub_digest"]
                rollback_dockerhub_ref = None
                rollback_dockerhub_backup = None
            units.append(
                {
                    "php_minor": minor,
                    "php_patch": unit["php_patch"],
                    "canary_ref": (
                        "ghcr.io/woosungchoi/fpm-alpine:"
                        f"canary-{minor}-{RUN_ID}-{RUN_ATTEMPT}"
                    ),
                    "target_ghcr_digest": unit["ghcr_digest"],
                    "target_dockerhub_digest": target_dockerhub,
                    "dockerhub_source_digest": dockerhub_source,
                    "previous_dockerhub_digest": previous_dockerhub,
                    "previous_ghcr_digest": previous_ghcr,
                    "rollback_dockerhub_ref": rollback_dockerhub_ref,
                    "rollback_dockerhub_backup_digest": rollback_dockerhub_backup,
                    "rollback_ghcr_ref": (
                        "ghcr.io/woosungchoi/fpm-alpine:"
                        f"rollback-auto-ghcr-{minor}-{RUN_ID}-{RUN_ATTEMPT}"
                    ),
                    "rollback_ghcr_digest": previous_ghcr,
                    "platforms": ["linux/amd64", "linux/arm64"],
                }
            )
        return {
            "schema_version": 1,
            "operation": payload["operation"],
            "repository": payload["repository"],
            "workflow_path": payload["workflow_path"],
            "workflow_sha": payload["workflow_sha"],
            "run_id": payload["run_id"],
            "run_attempt": payload["run_attempt"],
            "source_sha": payload["source_sha"],
            "release_units": units,
        }

    def write_evidence(self, evidence: Path, payload: dict) -> str:
        plan_bytes = (json.dumps(self.plan(payload), indent=2, sort_keys=True) + "\n").encode()
        plan_hash = hashlib.sha256(plan_bytes).hexdigest()
        payload["plan_sha256"] = plan_hash
        (evidence / "promotion-plan.json").write_bytes(plan_bytes)
        (evidence / "promotion-plan.sha256").write_text(
            f"{plan_hash}  promotion-plan.json\n"
        )
        (evidence / "transaction-result.json").write_text(json.dumps(payload))
        return plan_hash

    def run_validator(
        self, evidence: Path, *, run_id: int = RUN_ID, workflow_sha: str = WORKFLOW_SHA
    ) -> subprocess.CompletedProcess[str]:
        first = self.payload()["release_units"][0]
        return subprocess.run(
            [
                str(VALIDATOR),
                str(evidence),
                "--run-id",
                str(run_id),
                "--run-attempt",
                str(RUN_ATTEMPT),
                "--workflow-sha",
                workflow_sha,
                "--minor",
                first["php_minor"],
                "--patch",
                first["php_patch"],
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_valid_result_emits_registry_specific_exact_digests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            payload = self.payload()
            expected_plan_hash = self.write_evidence(evidence, payload)
            result = self.run_validator(evidence)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            source, dockerhub, ghcr, operation, observed_plan_hash = result.stdout.strip().split("\t")
            self.assertEqual(source, SOURCE_SHA)
            self.assertNotEqual(dockerhub, ghcr)
            self.assertEqual(operation, "backfill-ghcr")
            self.assertEqual(observed_plan_hash, expected_plan_hash)
            self.assertEqual(observed_plan_hash, payload["plan_sha256"])

    def test_valid_automatic_result_accepts_deferred_dockerhub_plan_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            payload = self.payload("automatic")
            self.write_evidence(evidence, payload)
            result = self.run_validator(evidence)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fields = result.stdout.strip().split("\t")
            self.assertEqual(fields[3], "automatic")
            self.assertNotEqual(fields[1], fields[2])

    def test_committed_receipt_requires_exact_set_and_requested_plan_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            payload = self.payload("automatic")
            plan_hash = self.write_evidence(evidence, payload)
            command = [
                str(VALIDATOR),
                str(evidence),
                "--run-id",
                str(RUN_ID),
                "--run-attempt",
                str(RUN_ATTEMPT),
                "--workflow-sha",
                WORKFLOW_SHA,
                "--expected-plan-sha256",
                plan_hash,
                "--exact-set",
            ]
            valid = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            self.assertEqual(valid.stdout, "")

            wrong_hash = subprocess.run(
                [*command[:-2], "0" * 64, "--exact-set"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(wrong_hash.returncode, 0)
            self.assertIn("requested plan", wrong_hash.stderr)

            (evidence / "unexpected.txt").write_text("unexpected\n")
            extra = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(extra.returncode, 0)
            self.assertIn("artifact set mismatch", extra.stderr)

    def test_cross_run_or_workflow_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            self.write_evidence(evidence, self.payload())
            self.assertNotEqual(self.run_validator(evidence, run_id=999).returncode, 0)
            self.assertNotEqual(
                self.run_validator(evidence, workflow_sha="a" * 40).returncode, 0
            )

    def test_duplicate_or_extra_release_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            payload = self.payload()
            self.write_evidence(evidence, payload)
            nested = evidence / "extra"
            nested.mkdir()
            (nested / "transaction-result.json").write_text(json.dumps(payload))
            self.assertNotEqual(self.run_validator(evidence).returncode, 0)

        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            payload = self.payload()
            payload["release_units"].append(dict(payload["release_units"][-1]))
            self.write_evidence(evidence, payload)
            self.assertNotEqual(self.run_validator(evidence).returncode, 0)

    def test_unknown_keys_and_wrong_types_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            payload = self.payload()
            payload["unexpected"] = True
            self.write_evidence(evidence, payload)
            self.assertNotEqual(self.run_validator(evidence).returncode, 0)
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            payload = self.payload()
            payload["run_id"] = str(RUN_ID)
            self.write_evidence(evidence, payload)
            self.assertNotEqual(self.run_validator(evidence).returncode, 0)

    def test_plan_checksum_and_release_unit_binding_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            payload = self.payload()
            self.write_evidence(evidence, payload)
            (evidence / "promotion-plan.sha256").write_text(
                f"{'0' * 64}  promotion-plan.json\n"
            )
            self.assertNotEqual(self.run_validator(evidence).returncode, 0)

        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp)
            payload = self.payload()
            self.write_evidence(evidence, payload)
            plan_path = evidence / "promotion-plan.json"
            plan = json.loads(plan_path.read_text())
            plan["release_units"][0]["target_ghcr_digest"] = "sha256:" + "9" * 64
            plan_bytes = (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode()
            plan_hash = hashlib.sha256(plan_bytes).hexdigest()
            plan_path.write_bytes(plan_bytes)
            (evidence / "promotion-plan.sha256").write_text(
                f"{plan_hash}  promotion-plan.json\n"
            )
            payload["plan_sha256"] = plan_hash
            (evidence / "transaction-result.json").write_text(json.dumps(payload))
            self.assertNotEqual(self.run_validator(evidence).returncode, 0)


if __name__ == "__main__":
    unittest.main()
