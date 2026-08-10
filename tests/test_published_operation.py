from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/resolve-published-operation.sh"
SUBJECT = "ghcr.io/woosungchoi/fpm-alpine@sha256:" + "a" * 64
DOCKERHUB_SUBJECT = "docker.io/woosungchoi/fpm-alpine@sha256:" + "a" * 64


class PublishedOperationResolverTests(unittest.TestCase):
    def run_resolver(self, valid_modes: str, signing_ref: str = "main") -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = root / "cosign"
            fake.write_text("""#!/usr/bin/env python3
import os, sys
argv = sys.argv[1:]
args = ' '.join(argv)
valid = set(filter(None, os.environ.get('VALID_MODES', '').split(',')))
identity = argv[argv.index('--certificate-identity-regexp') + 1]
if r'github\\.com' not in identity or r'github\\\\.com' in identity:
    raise SystemExit(3)
if 'fpm.operation=backfill-ghcr' in args and r'dependency-auto-publish\\.yml' in args:
    raise SystemExit(0 if 'backfill-ghcr' in valid else 1)
if 'fpm.operation=automatic' in args and r'dependency-auto-publish\\.yml' in args:
    raise SystemExit(0 if 'automatic' in valid else 1)
if 'fpm.operation=manual' in args and r'publish\\.yml' in args:
    raise SystemExit(0 if 'manual' in valid else 1)
if 'fpm.operation=recovery' in args and r'dependency-publish-recovery\\.yml' in args:
    raise SystemExit(0 if 'recovery-workflow' in valid else 1)
if 'fpm.operation=recovery' in args and r'dependency-auto-publish\\.yml' in args:
    raise SystemExit(0 if 'recovery-auto' in valid else 1)
raise SystemExit(2)
""")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            env = {
                **os.environ,
                "PATH": f"{root}:{os.environ['PATH']}",
                "VALID_MODES": valid_modes,
            }
            return subprocess.run(
                [str(SCRIPT), SUBJECT, signing_ref],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_each_exact_signed_operation_is_classified(self) -> None:
        expected = {
            "backfill-ghcr": "backfill-ghcr\tdependency-auto-publish.yml\tmain\n",
            "automatic": "automatic\tdependency-auto-publish.yml\tmain\n",
            "manual": "manual\tpublish.yml\tmain\n",
            "recovery-workflow": "recovery\tdependency-publish-recovery.yml\tmain\n",
            "recovery-auto": "recovery\tdependency-auto-publish.yml\tmain\n",
        }
        for mode, output in expected.items():
            with self.subTest(mode=mode):
                completed = self.run_resolver(mode)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, output)

    def test_unsigned_or_ambiguous_operation_fails_closed(self) -> None:
        for modes in ("", "backfill-ghcr,automatic"):
            with self.subTest(modes=modes):
                completed = self.run_resolver(modes)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("exactly one", completed.stderr)

    def test_dockerhub_exact_subject_is_classified_with_the_same_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = root / "cosign"
            fake.write_text("#!/usr/bin/env bash\n[[ \" $* \" == *\" fpm.operation=recovery \"* && \"$*\" == *\"dependency-publish-recovery\\.yml\"* ]]\n")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            completed = subprocess.run(
                [str(SCRIPT), DOCKERHUB_SUBJECT, "main"],
                cwd=ROOT,
                env={**os.environ, "PATH": f"{root}:{os.environ['PATH']}"},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("recovery", completed.stdout)

    def test_invalid_subject_or_signing_ref_is_rejected_before_cosign(self) -> None:
        for subject, signing_ref in (("ghcr.io/repo:tag", "main"), (SUBJECT, "topic")):
            with self.subTest(subject=subject, signing_ref=signing_ref):
                completed = subprocess.run(
                    [str(SCRIPT), subject, signing_ref],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
