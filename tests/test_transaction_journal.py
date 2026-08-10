from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "scripts" / "transaction-journal.py"
MINORS = ("8.2", "8.3", "8.4", "8.5")


def digest(number: int) -> str:
    return f"sha256:{number:064x}"


def plan_payload(operation: str = "automatic") -> dict:
    units = []
    for index, minor in enumerate(MINORS, start=1):
        target_ghcr = digest(200 + index)
        previous_dockerhub = digest(100 + index)
        units.append(
            {
                "php_minor": minor,
                "php_patch": f"8.{index + 1}.1",
                "canary_ref": f"ghcr.io/woosungchoi/fpm-alpine:canary-{minor}-123-1",
                "target_ghcr_digest": target_ghcr,
                "target_dockerhub_digest": (
                    target_ghcr if operation == "automatic" else previous_dockerhub
                ),
                "dockerhub_source_digest": (
                    None if operation == "automatic" else previous_dockerhub
                ),
                "previous_dockerhub_digest": previous_dockerhub,
                "previous_ghcr_digest": digest(300 + index),
                "rollback_dockerhub_ref": (
                    f"ghcr.io/woosungchoi/fpm-alpine:rollback-auto-dockerhub-{minor}-123-1"
                    if operation == "automatic"
                    else None
                ),
                "rollback_dockerhub_backup_digest": (
                    previous_dockerhub if operation == "automatic" else None
                ),
                "rollback_ghcr_ref": f"ghcr.io/woosungchoi/fpm-alpine:rollback-auto-ghcr-{minor}-123-1",
                "rollback_ghcr_digest": digest(300 + index),
                "platforms": ["linux/amd64", "linux/arm64"],
            }
        )
    return {
        "schema_version": 1,
        "operation": operation,
        "repository": "woosungchoi/fpm-alpine",
        "workflow_path": ".github/workflows/dependency-auto-publish.yml",
        "workflow_sha": "e" * 40,
        "run_id": 123,
        "run_attempt": 1,
        "source_sha": "d" * 40,
        "release_units": units,
    }


class TransactionJournalTests(unittest.TestCase):
    def run_journal(
        self,
        root: Path,
        *args: str,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "TRANSACTION_JOURNAL_DIR": str(root / "journal"),
        }
        return subprocess.run(
            [str(JOURNAL), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=check,
        )

    @staticmethod
    def write_plan(root: Path, operation: str = "automatic") -> Path:
        path = root / f"{operation}.json"
        path.write_text(json.dumps(plan_payload(operation), indent=2, sort_keys=True) + "\n")
        return path

    def prepare_and_activate(self, root: Path, plan: Path) -> None:
        payload = json.loads(plan.read_text())
        for unit in payload["release_units"]:
            minor = unit["php_minor"]
            writes = [("pin-ghcr", unit["rollback_ghcr_digest"])]
            if payload["operation"] == "automatic":
                writes.extend(
                    (
                        ("pin-dockerhub-backup", unit["rollback_dockerhub_backup_digest"]),
                        ("stage-dockerhub", unit["target_dockerhub_digest"]),
                    )
                )
            for kind, observed in writes:
                self.run_journal(root, "prepare-attempt", str(plan), minor, kind, check=True)
                self.run_journal(
                    root,
                    "prepare-complete",
                    str(plan),
                    minor,
                    kind,
                    observed,
                    check=True,
                )
        self.run_journal(root, "activate", str(plan), check=True)

    def complete_publication(self, root: Path, plan: Path) -> None:
        payload = json.loads(plan.read_text())
        for unit in payload["release_units"]:
            minor = unit["php_minor"]
            target = unit["target_ghcr_digest"]
            self.run_journal(root, "attempt", str(plan), minor, "ghcr", check=True)
            self.run_journal(
                root, "complete", str(plan), minor, "ghcr", target, check=True
            )
            if payload["operation"] == "automatic":
                self.run_journal(
                    root, "attempt", str(plan), minor, "dockerhub", check=True
                )
                self.run_journal(
                    root,
                    "complete",
                    str(plan),
                    minor,
                    "dockerhub",
                    unit["target_dockerhub_digest"],
                    check=True,
                )
                self.run_journal(
                    root, "referrer-attempt", str(plan), minor, check=True
                )
                self.run_journal(
                    root,
                    "referrer-complete",
                    str(plan),
                    minor,
                    unit["target_dockerhub_digest"],
                    check=True,
                )

    def test_begin_is_single_owner_no_clobber_and_pending_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self.write_plan(root)
            expected_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
            self.run_journal(root, "begin", str(plan), check=True)
            self.run_journal(root, "begin", str(plan), check=True)
            pending = self.run_journal(root, "pending", check=True)
            payload = json.loads(pending.stdout)
            self.assertEqual(payload["kind"], "pending")
            self.assertEqual(payload["plan_sha256"], expected_sha)
            self.assertEqual(payload["run_id"], 123)
            self.run_journal(root, "assert-owner", str(plan), check=True)

            other = self.write_plan(root, "backfill-ghcr")
            rejected = self.run_journal(root, "begin", str(other))
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("owned by another transaction", rejected.stderr)

    def test_per_registry_attempt_and_result_are_exact_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self.write_plan(root)
            self.run_journal(root, "begin", str(plan), check=True)
            self.prepare_and_activate(root, plan)

            before = json.loads(
                self.run_journal(root, "state", str(plan), "8.2", "dockerhub", check=True).stdout
            )
            self.assertEqual(before, {"attempted": False, "completed": False, "target_digest": None})

            self.run_journal(root, "attempt", str(plan), "8.2", "dockerhub", check=True)
            self.run_journal(root, "attempt", str(plan), "8.2", "dockerhub", check=True)
            attempted = json.loads(
                self.run_journal(root, "state", str(plan), "8.2", "dockerhub", check=True).stdout
            )
            expected = plan_payload()["release_units"][0]["target_dockerhub_digest"]
            self.assertEqual(
                attempted,
                {"attempted": True, "completed": False, "target_digest": expected},
            )

            wrong = self.run_journal(root, "complete", str(plan), "8.2", "dockerhub", digest(999))
            self.assertNotEqual(wrong.returncode, 0)
            self.run_journal(root, "complete", str(plan), "8.2", "dockerhub", expected, check=True)
            self.run_journal(root, "complete", str(plan), "8.2", "dockerhub", expected, check=True)
            completed = json.loads(
                self.run_journal(root, "state", str(plan), "8.2", "dockerhub", check=True).stdout
            )
            self.assertEqual(
                completed,
                {"attempted": True, "completed": True, "target_digest": expected},
            )

    def test_backfill_rejects_any_dockerhub_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self.write_plan(root, "backfill-ghcr")
            self.run_journal(root, "begin", str(plan), check=True)
            self.prepare_and_activate(root, plan)
            rejected = self.run_journal(root, "attempt", str(plan), "8.2", "dockerhub")
            self.assertNotEqual(rejected.returncode, 0)
            self.run_journal(root, "attempt", str(plan), "8.2", "ghcr", check=True)

    def test_recovery_progress_is_durable_and_finish_preserves_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self.write_plan(root)
            prior = plan_payload()["release_units"][0]["previous_dockerhub_digest"]
            self.run_journal(root, "begin", str(plan), check=True)
            self.prepare_and_activate(root, plan)
            self.run_journal(root, "attempt", str(plan), "8.2", "dockerhub", check=True)
            self.run_journal(root, "recovery-attempt", str(plan), "8.2", "dockerhub", check=True)
            self.run_journal(
                root, "recovery-complete", str(plan), "8.2", "dockerhub", prior, check=True
            )
            self.run_journal(
                root, "recovery-referrer-attempt", str(plan), "8.2", check=True
            )
            self.run_journal(
                root,
                "recovery-referrer-complete",
                str(plan),
                "8.2",
                prior,
                check=True,
            )
            progress = json.loads(
                self.run_journal(
                    root, "recovery-state", str(plan), "8.2", "dockerhub", check=True
                ).stdout
            )
            self.assertEqual(progress, {"attempted": True, "completed": True})

            result = root / "recovery-result.json"
            result.write_text('{"status":"restored"}\n')
            self.run_journal(
                root,
                "finish",
                str(plan),
                "recovered",
                "--result",
                str(result),
                check=True,
            )
            self.assertNotEqual(self.run_journal(root, "pending").returncode, 0)
            audit_files = list((root / "journal" / "audit").glob("**/*.json"))
            self.assertGreaterEqual(len(audit_files), 4)

            lock_head = root / "journal" / "lock" / "head.json"
            self.assertTrue(lock_head.is_file())
            self.assertEqual(json.loads(lock_head.read_text())["state"], "FREE")

    def test_prepare_writes_are_journaled_before_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self.write_plan(root)
            self.run_journal(root, "begin", str(plan), check=True)
            rejected = self.run_journal(root, "attempt", str(plan), "8.2", "ghcr")
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("PREPARED", rejected.stderr)

            self.prepare_and_activate(root, plan)
            self.run_journal(root, "attempt", str(plan), "8.2", "ghcr", check=True)

    def test_stale_finish_cannot_release_a_new_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.write_plan(root)
            self.run_journal(root, "begin", str(first), check=True)
            result = root / "result.json"
            result.write_text('{"status":"verified"}\n')
            self.run_journal(
                root, "finish", str(first), "recovered", "--result", str(result), check=True
            )

            second = self.write_plan(root, "backfill-ghcr")
            self.run_journal(root, "begin", str(second), check=True)
            stale = self.run_journal(
                root, "finish", str(first), "recovered", "--result", str(result)
            )
            self.assertNotEqual(stale.returncode, 0)
            pending = json.loads(self.run_journal(root, "pending", check=True).stdout)
            self.assertEqual(pending["operation"], "backfill-ghcr")
            self.assertEqual(pending["lock_state"], "PREPARED")

    def test_unknown_failure_freezes_lock_in_blocked_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self.write_plan(root)
            self.run_journal(root, "begin", str(plan), check=True)
            self.prepare_and_activate(root, plan)
            self.run_journal(
                root, "note-failure", str(plan), "classification-unknown", check=True
            )
            pending = json.loads(self.run_journal(root, "pending", check=True).stdout)
            self.assertEqual(pending["lock_state"], "BLOCKED")
            self.assertNotEqual(
                self.run_journal(root, "recover-begin", str(plan)).returncode,
                0,
            )

    def test_committed_receipt_requires_all_results_and_referrers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self.write_plan(root)
            result = root / "transaction-result.json"
            result.write_text('{"status":"verified"}\n')
            self.run_journal(root, "begin", str(plan), check=True)
            self.prepare_and_activate(root, plan)
            incomplete = self.run_journal(
                root,
                "finish",
                str(plan),
                "committed",
                "--result",
                str(result),
            )
            self.assertNotEqual(incomplete.returncode, 0)
            self.assertIn("alias result is incomplete", incomplete.stderr)
            self.complete_publication(root, plan)
            self.run_journal(
                root,
                "finish",
                str(plan),
                "committed",
                "--result",
                str(result),
                check=True,
            )
            self.assertEqual(self.run_journal(root, "pending").returncode, 3)

    def test_commands_fail_without_exact_pending_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self.write_plan(root)
            for command in ("assert-owner", "attempt", "state", "finish"):
                args = [command, str(plan)]
                if command in {"attempt", "state"}:
                    args.extend(("8.2", "ghcr"))
                if command == "finish":
                    args.append("recovered")
                result = self.run_journal(root, *args)
                self.assertNotEqual(result.returncode, 0, command)


if __name__ == "__main__":
    unittest.main()
