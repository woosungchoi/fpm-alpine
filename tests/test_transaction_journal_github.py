from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "scripts" / "transaction-journal.py"
FAKE_GH = ROOT / "tests" / "fixtures" / "fake_gh_git_journal.py"
MINORS = ("8.2", "8.3", "8.4", "8.5")


def digest(value: int) -> str:
    return "sha256:" + f"{value:064x}"


def plan_payload(head: str, run_attempt: int) -> dict:
    units = []
    for index, minor in enumerate(MINORS, start=1):
        target = digest(200 + index)
        previous_dockerhub = digest(100 + index)
        previous_ghcr = digest(300 + index)
        units.append(
            {
                "php_minor": minor,
                "php_patch": f"{minor}.{20 + index}",
                "canary_ref": f"ghcr.io/woosungchoi/fpm-alpine:canary-{minor}",
                "target_ghcr_digest": target,
                "target_dockerhub_digest": target,
                "dockerhub_source_digest": target,
                "previous_dockerhub_digest": previous_dockerhub,
                "previous_ghcr_digest": previous_ghcr,
                "rollback_dockerhub_ref": (
                    f"ghcr.io/woosungchoi/fpm-alpine:rollback-auto-dockerhub-{minor}-123-{run_attempt}"
                ),
                "rollback_dockerhub_backup_digest": previous_dockerhub,
                "rollback_ghcr_ref": (
                    f"ghcr.io/woosungchoi/fpm-alpine:rollback-auto-ghcr-{minor}-123-{run_attempt}"
                ),
                "rollback_ghcr_digest": previous_ghcr,
                "platforms": ["linux/amd64", "linux/arm64"],
            }
        )
    return {
        "schema_version": 1,
        "operation": "automatic",
        "repository": "woosungchoi/fpm-alpine",
        "workflow_path": ".github/workflows/dependency-auto-publish.yml",
        "workflow_sha": head,
        "run_id": 123,
        "run_attempt": run_attempt,
        "source_sha": head,
        "release_units": units,
    }


class GitHubTransactionJournalTests(unittest.TestCase):
    def run_journal(
        self,
        env: dict[str, str],
        *args: str,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(JOURNAL), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=check,
        )

    def write_plan(self, root: Path, head: str, run_attempt: int) -> Path:
        path = root / f"plan-{run_attempt}.json"
        path.write_text(
            json.dumps(plan_payload(head, run_attempt), indent=2, sort_keys=True)
            + "\n"
        )
        return path

    def test_git_data_api_uses_protected_exact_parent_fast_forward_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            gh = fake_bin / "gh"
            shutil.copyfile(FAKE_GH, gh)
            gh.chmod(0o755)
            state_path = root / "github-state.json"
            calls_path = root / "github-calls.jsonl"
            reject_path = root / "reject-next-patch"
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip()
            first = self.write_plan(root, head, 1)
            env = os.environ.copy()
            env.update(
                PATH=f"{fake_bin}:{env['PATH']}",
                GH_TOKEN="test-token",
                GITHUB_REPOSITORY="woosungchoi/fpm-alpine",
                FAKE_GH_STATE=str(state_path),
                FAKE_GH_CALLS=str(calls_path),
                TRANSACTION_JOURNAL_ALLOW_BOOTSTRAP="1",
            )

            started = self.run_journal(env, "begin", str(first))
            self.assertEqual(started.returncode, 0, started.stderr)
            env.pop("TRANSACTION_JOURNAL_ALLOW_BOOTSTRAP")
            pending = json.loads(
                self.run_journal(env, "pending", check=True).stdout
            )
            self.assertEqual(pending["lock_state"], "PREPARED")
            self.assertRegex(pending["lock_commit_sha"], r"^[0-9a-f]{40}$")

            unprotected = {**env, "FAKE_GH_UNPROTECTED": "1"}
            rejected = self.run_journal(unprotected, "begin", str(first))
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("does not forbid", rejected.stderr)

            before_conflict = json.loads(state_path.read_text())["refs"][
                "refs/heads/fpm-transaction-lock"
            ]["sha"]
            reject_path.touch()
            conflict_env = {**env, "FAKE_GH_REJECT_NEXT_PATCH": str(reject_path)}
            conflict = self.run_journal(
                conflict_env,
                "note-failure",
                str(first),
                "signal-term",
            )
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("CAS conflict", conflict.stderr)
            after_conflict = json.loads(state_path.read_text())["refs"][
                "refs/heads/fpm-transaction-lock"
            ]["sha"]
            self.assertEqual(after_conflict, before_conflict)

            self.run_journal(
                env,
                "note-failure",
                str(first),
                "signal-term",
                check=True,
            )
            self.assertEqual(
                json.loads(self.run_journal(env, "pending", check=True).stdout)[
                    "lock_state"
                ],
                "RECOVERY_REQUIRED",
            )
            self.run_journal(env, "recover-begin", str(first), check=True)
            result = root / "recovery-result.json"
            result.write_text('{"status":"restored"}\n')
            self.run_journal(
                env,
                "finish",
                str(first),
                "recovered",
                "--result",
                str(result),
                check=True,
            )
            self.assertEqual(self.run_journal(env, "pending").returncode, 3)

            second = self.write_plan(root, head, 2)
            self.run_journal(env, "begin", str(second), check=True)
            stale = self.run_journal(
                env,
                "finish",
                str(first),
                "recovered",
                "--result",
                str(result),
            )
            self.assertNotEqual(stale.returncode, 0)
            current = json.loads(self.run_journal(env, "pending", check=True).stdout)
            self.assertEqual(current["run_attempt"], 2)
            self.assertEqual(current["lock_state"], "PREPARED")

            calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
            self.assertFalse(any(call["method"] == "DELETE" for call in calls))
            patches = [call for call in calls if call["method"] == "PATCH"]
            self.assertGreaterEqual(len(patches), 5)
            self.assertTrue(all(call["payload"]["force"] is False for call in patches))
            self.assertTrue(
                any(
                    call["endpoint"].endswith(
                        "branches/fpm-transaction-lock/protection"
                    )
                    for call in calls
                )
            )

            state = json.loads(state_path.read_text())
            cursor = state["refs"]["refs/heads/fpm-transaction-lock"]["sha"]
            visited: set[str] = set()
            while cursor in state["commits"]:
                self.assertNotIn(cursor, visited)
                visited.add(cursor)
                parents = state["commits"][cursor]["parents"]
                self.assertEqual(len(parents), 1)
                cursor = parents[0]["sha"]
            self.assertEqual(cursor, head)


if __name__ == "__main__":
    unittest.main()
