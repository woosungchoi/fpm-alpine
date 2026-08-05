#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
).strip()


class AutoProductionRunnerTests(unittest.TestCase):
    def run_controller(
        self,
        affected: list[str],
        *,
        current_main: str = SOURCE,
        fail_minor: str = "",
        transient_watch_minor: str = "",
        requested_minor: str = "",
    ) -> tuple[subprocess.CompletedProcess[str], list[str], dict]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            state = root / "state"
            state.mkdir()
            authorization = root / "authorization.json"
            output = root / "production-runs.json"
            authorization.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "sourceCommit": SOURCE,
                        "upstreamRunId": 9001,
                        "upstreamRunAttempt": 2,
                        "affectedMinors": affected,
                        "priorCanary": {"runId": 101, "runAttempt": 1},
                        "currentCanary": {"runId": 102, "runAttempt": 1},
                        "productionAuthorized": True,
                    }
                )
            )
            fake_gh = fake_bin / "gh"
            fake_gh.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, os, pathlib, sys
                    args = sys.argv[1:]
                    state = pathlib.Path(os.environ["FAKE_GH_STATE"])
                    dispatched = state / "dispatched"
                    dispatched.touch(exist_ok=True)
                    rows = [json.loads(line) for line in dispatched.read_text().splitlines() if line]
                    if args[:1] == ["workflow"] and args[1:3] == ["run", "publish.yml"]:
                        version = next(item.split("=", 1)[1] for item in args if item.startswith("version="))
                        correlation = next(item.split("=", 1)[1] for item in args if item.startswith("correlation_id="))
                        with dispatched.open("a") as handle:
                            handle.write(json.dumps({"minor": version, "correlation": correlation}) + "\\n")
                        sys.exit(0)
                    if args[:1] == ["api"]:
                        endpoint = args[1]
                        if endpoint.endswith("/git/ref/heads/main"):
                            print(os.environ["FAKE_CURRENT_MAIN"])
                            sys.exit(0)
                        if "/actions/workflows/publish.yml/runs?" in endpoint:
                            rows = [json.loads(line) for line in dispatched.read_text().splitlines() if line]
                            query = args[args.index("--jq") + 1]
                            matches = [index for index, row in enumerate(rows) if "publish-production-" + row["correlation"] in query]
                            print(801 + matches[-1] if matches else "")
                            sys.exit(0)
                        if "/actions/runs/" in endpoint:
                            run_id = int(endpoint.rsplit("/", 1)[1])
                            row = rows[run_id - 801]
                            conclusion = "failure" if row["minor"] == os.environ.get("FAKE_RUN_FAIL_MINOR") else "success"
                            print(json.dumps({
                                "id": run_id,
                                "event": "workflow_dispatch",
                                "head_branch": "main",
                                "head_sha": os.environ["EXPECTED_SOURCE"],
                                "path": ".github/workflows/publish.yml",
                                "status": "completed",
                                "conclusion": conclusion,
                                "run_attempt": 1,
                                "display_title": "publish-production-" + row["correlation"],
                            }))
                            sys.exit(0)
                    if args[:2] == ["run", "watch"]:
                        run_id = int(args[2])
                        minor = rows[run_id - 801]["minor"]
                        sys.exit(1 if minor == os.environ.get("FAKE_WATCH_FAIL_MINOR") else 0)
                    print("unexpected gh arguments: " + repr(args), file=sys.stderr)
                    sys.exit(90)
                    """
                )
            )
            fake_gh.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "FAKE_GH_STATE": str(state),
                "FAKE_CURRENT_MAIN": current_main,
                "FAKE_WATCH_FAIL_MINOR": fail_minor or transient_watch_minor,
                "FAKE_RUN_FAIL_MINOR": fail_minor,
                "EXPECTED_SOURCE": SOURCE,
                "GITHUB_REPOSITORY": "woosungchoi/fpm-alpine",
                "GH_TOKEN": "test-token",
                "GITHUB_RUN_ATTEMPT": "1",
            }
            result = subprocess.run(
                [
                    str(ROOT / "scripts/run-auto-production.sh"),
                    SOURCE,
                    str(authorization),
                    str(output),
                    requested_minor,
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            dispatched_path = state / "dispatched"
            dispatched = (
                [
                    json.loads(line)["minor"]
                    for line in dispatched_path.read_text().splitlines()
                    if line
                ]
                if dispatched_path.exists()
                else []
            )
            report = json.loads(output.read_text()) if output.exists() else {}
            return result, dispatched, report

    def test_dispatches_affected_minors_sequentially(self) -> None:
        result, dispatched, report = self.run_controller(["8.2", "8.5"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dispatched, ["8.2", "8.5"])
        self.assertEqual([row["minor"] for row in report["productionRuns"]], dispatched)
        self.assertEqual(
            report["productionRuns"][0]["correlation"],
            f"auto-prod-{SOURCE[:12]}-9001-2-1-8.2",
        )
        self.assertTrue(all(row["status"] == "success" for row in report["productionRuns"]))

    def test_stops_before_next_minor_after_failure(self) -> None:
        result, dispatched, report = self.run_controller(
            ["8.2", "8.3", "8.4"], fail_minor="8.2"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(dispatched, ["8.2"])
        self.assertEqual(report["productionRuns"][0]["status"], "failed")

    def test_source_movement_prevents_any_dispatch(self) -> None:
        result, dispatched, _ = self.run_controller(
            ["8.2"], current_main="b" * 40
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(dispatched, [])

    def test_watcher_failure_does_not_override_successful_run_readback(self) -> None:
        result, dispatched, report = self.run_controller(
            ["8.2", "8.5"], transient_watch_minor="8.2"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dispatched, ["8.2", "8.5"])
        self.assertTrue(
            all(row["status"] == "success" for row in report["productionRuns"])
        )

    def test_dispatches_only_the_requested_authorized_release_unit(self) -> None:
        result, dispatched, report = self.run_controller(
            ["8.2", "8.3", "8.4", "8.5"], requested_minor="8.4"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dispatched, ["8.4"])
        self.assertEqual(report["affectedMinors"], ["8.4"])
        self.assertEqual(
            report["authorizedMinors"], ["8.2", "8.3", "8.4", "8.5"]
        )

    def test_rejects_requested_release_unit_outside_authorization(self) -> None:
        result, dispatched, _ = self.run_controller(
            ["8.2"], requested_minor="8.5"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(dispatched, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
