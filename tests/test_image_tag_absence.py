from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "assert-image-tag-absent.sh"
DIGEST_PARSER = ROOT / "scripts" / "extract-image-digest.sh"
REF = "ghcr.io/woosungchoi/fpm-alpine:canary-8.5-123-1"


class ImageTagAbsenceTests(unittest.TestCase):
    def run_case(self, status: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docker = root / "docker"
            docker.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f"printf '%b' {stdout!r}\n"
                f"printf '%b' {stderr!r} >&2\n"
                f"exit {status}\n"
            )
            docker.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{root}:{env['PATH']}"
            return subprocess.run(
                [str(SCRIPT), REF],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_exact_ref_manifest_unknown_is_absent(self):
        result = self.run_case(1, stderr=f"ERROR: {REF}: manifest unknown")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("confirmed absent", result.stdout)

    def test_existing_tag_is_collision(self):
        result = self.run_case(0, stdout="Name: existing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to overwrite", result.stderr)

    def test_auth_or_mixed_not_found_is_not_absence(self):
        result = self.run_case(
            1,
            stderr=f"unauthorized while checking {REF}; manifest unknown",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("ambiguous registry failure", result.stderr)

    def test_numeric_server_error_mixed_with_manifest_unknown_is_not_absence(self):
        result = self.run_case(
            1,
            stderr=f"{REF}: manifest unknown; request failed with status 500",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("ambiguous registry failure", result.stderr)

    def test_unrelated_not_found_is_not_absence(self):
        result = self.run_case(
            1,
            stderr="ghcr.io/woosungchoi/other:tag: manifest unknown",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("did not bind absence", result.stderr)

    def test_failed_inspect_that_emits_digest_is_ambiguous(self):
        digest = "sha256:" + "a" * 64
        result = self.run_case(
            1,
            stdout=f"Digest: {digest}\n",
            stderr=f"{REF}: manifest unknown",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("emitted a digest", result.stderr)

    def test_noncanonical_ref_is_rejected_before_docker(self):
        result = subprocess.run(
            [str(SCRIPT), "docker.io/woosungchoi/fpm-alpine:8.5"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 64)

    def test_digest_parser_propagates_producer_failure_even_with_valid_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            awk = fake_bin / "awk"
            awk.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' 'sha256:" + "a" * 64 + "'\n"
                "exit 7\n"
            )
            awk.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            result = subprocess.run(
                [str(DIGEST_PARSER)],
                input="Digest: sha256:" + "a" * 64 + "\n",
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_digest_parser_accepts_exactly_one_valid_digest(self):
        digest = "sha256:" + "b" * 64
        result = subprocess.run(
            [str(DIGEST_PARSER)],
            input=f"Digest: {digest}\n",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, digest + "\n")


if __name__ == "__main__":
    unittest.main()
