from __future__ import annotations

import datetime as dt
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "a" * 40
NOW = dt.datetime(2026, 8, 5, 20, 0, tzinfo=dt.timezone.utc)


def load_module():
    path = ROOT / "scripts/select-fresh-cutover-lease.py"
    spec = importlib.util.spec_from_file_location("select_fresh_cutover_lease", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(run_id: int = 100, *, nested: dict[str, object] | None = None, **changes) -> dict:
    row = {
        "id": run_id,
        "run_attempt": 1,
        "path": ".github/workflows/legacy-cutover-lease.yml",
        "event": "repository_dispatch",
        "head_branch": "main",
        "head_sha": SOURCE,
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-08-05T19:58:00Z",
        "actor": {"id": 5674610, "login": "woosungchoi"},
        "repository": {"full_name": "woosungchoi/fpm-alpine"},
        "head_repository": {"full_name": "woosungchoi/fpm-alpine"},
    }
    for key, value in changes.items():
        row[key] = value
    for key, value in (nested or {}).items():
        outer, inner = key.split(".", 1)
        row[outer][inner] = value
    return row


class FreshCutoverLeaseSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def select(self, rows: list[dict]):
        return self.module.select_run(
            {"workflow_runs": rows}, SOURCE, "woosungchoi/fpm-alpine", NOW
        )

    def test_newest_exact_trusted_run_is_selected(self) -> None:
        selected = self.select([run(100), run(101, run_attempt=2)])
        self.assertEqual(selected, {"runId": 101, "runAttempt": 2})

    def test_wrong_source_path_actor_or_repository_is_rejected(self) -> None:
        variants = [
            run(head_sha="b" * 40),
            run(path=".github/workflows/other.yml"),
            run(nested={"actor.id": 1}),
            run(nested={"actor.login": "other"}),
            run(nested={"repository.full_name": "other/repo"}),
            run(nested={"head_repository.full_name": "fork/repo"}),
        ]
        for row in variants:
            with self.subTest(row=row), self.assertRaisesRegex(
                SystemExit, "no fresh trusted"
            ):
                self.select([row])

    def test_bool_attempt_and_stale_or_future_runs_are_rejected(self) -> None:
        variants = [
            run(run_attempt=True),
            run(created_at="2026-08-05T19:40:00Z"),
            run(created_at="2026-08-05T20:02:00Z"),
        ]
        for row in variants:
            with self.subTest(row=row), self.assertRaisesRegex(
                SystemExit, "no fresh trusted"
            ):
                self.select([row])


if __name__ == "__main__":
    unittest.main(verbosity=2)
