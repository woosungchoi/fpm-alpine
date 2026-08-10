from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/require-fresh-cutover-lease.sh"
SOURCE = "a" * 40
REPOSITORY = "woosungchoi/fpm-alpine"


class FreshCutoverLeaseLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.artifact = self.root / "artifact"
        self.bin.mkdir()
        self.artifact.mkdir()
        self.runs = self.root / "runs.json"
        self.output = self.root / "output"
        self._write_evidence()
        self._write_runs()
        self._write_fake_gh()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_evidence(self) -> None:
        captured = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {
            "schemaVersion": 1,
            "source_sha": SOURCE,
            "captured_at": captured,
            "dockerhub": {
                "build_rule_active": False,
                "in_flight_builds": 0,
                "public_is_automated": False,
                "repository_last_updated": "2026-08-10T00:00:00Z",
                "queue_basis": "automatic builds disabled and no source-capable GitHub legacy publisher hook",
            },
            "github": {
                "repository": REPOSITORY,
                "legacy_webhook_present": False,
                "active_hooks": [{
                    "id": 402842509,
                    "name": "web",
                    "active": True,
                    "events": ["pull_request", "push"],
                    "url_host": "api.snyk.io",
                    "url_kind": "github-webhook-uuid",
                }],
            },
        }
        raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        (self.artifact / "cutover-evidence.json").write_bytes(raw)
        (self.artifact / "cutover-evidence.sha256").write_text(
            hashlib.sha256(raw).hexdigest() + "\n"
        )

    def _write_runs(self) -> None:
        created = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        self.runs.write_text(json.dumps({"workflow_runs": [{
            "id": 123,
            "run_attempt": 2,
            "event": "repository_dispatch",
            "status": "completed",
            "conclusion": "success",
            "path": ".github/workflows/legacy-cutover-lease.yml",
            "head_branch": "main",
            "head_sha": SOURCE,
            "created_at": created,
            "actor": {"login": "woosungchoi", "id": 5674610},
            "repository": {"full_name": REPOSITORY},
            "head_repository": {"full_name": REPOSITORY},
        }]}))

    def _write_fake_gh(self) -> None:
        fake = self.bin / "gh"
        fake.write_text("""#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
if args and args[0] == 'api':
    sys.stdout.write(Path(os.environ['LEASE_RUNS']).read_text())
    raise SystemExit(0)
if args[:2] == ['run', 'download']:
    target = Path(args[args.index('--dir') + 1])
    target.mkdir(parents=True, exist_ok=True)
    for source in Path(os.environ['LEASE_ARTIFACT']).iterdir():
        shutil.copy2(source, target / source.name)
    raise SystemExit(0)
raise SystemExit('unexpected gh command: ' + ' '.join(args))
""")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    def run_loader(self) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_TOKEN": "test-token",
            "GITHUB_REPOSITORY": REPOSITORY,
            "LEASE_RUNS": str(self.runs),
            "LEASE_ARTIFACT": str(self.artifact),
        }
        return subprocess.run(
            [str(SCRIPT), SOURCE, str(self.output)],
            check=False,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_exact_run_artifact_and_hash_are_loaded(self) -> None:
        result = self.run_loader()
        self.assertEqual(result.returncode, 0, result.stderr)
        selection = json.loads((self.output / "selection.json").read_text())
        self.assertEqual(selection["runId"], 123)
        self.assertEqual(selection["runAttempt"], 2)
        self.assertEqual(selection["sourceSha"], SOURCE)
        self.assertRegex(selection["evidenceSha256"], r"^[0-9a-f]{64}$")

    def test_mismatched_artifact_hash_fails_closed(self) -> None:
        (self.artifact / "cutover-evidence.sha256").write_text("0" * 64 + "\n")
        result = self.run_loader()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
