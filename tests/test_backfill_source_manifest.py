from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate-backfill-source-manifest.py"


class BackfillSourceManifestTests(unittest.TestCase):
    def run_validator(self, current: object, source: object) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current_path = root / "current.json"
            source_path = root / "source.json"
            current_path.write_text(json.dumps(current) + "\n")
            source_path.write_text(json.dumps(source) + "\n")
            return subprocess.run(
                [str(SCRIPT), str(current_path), str(source_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

    @staticmethod
    def manifest() -> dict:
        return json.loads((ROOT / "build/versions.json").read_text())

    def test_exact_release_manifest_is_accepted(self) -> None:
        manifest = self.manifest()
        completed = self.run_validator(manifest, manifest)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("exactly matches", completed.stdout)

    def test_dependency_or_version_drift_is_rejected(self) -> None:
        for section, mutate in (
            ("dependencies", lambda value: value["imagick"].update(version="99.0.0")),
            ("versions", lambda value: value["8.5"].update(patch="8.5.999")),
        ):
            with self.subTest(section=section):
                current = self.manifest()
                source = json.loads(json.dumps(current))
                mutate(source[section])
                completed = self.run_validator(current, source)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("does not exactly match", completed.stderr)

    def test_non_object_or_missing_release_sections_fail_closed(self) -> None:
        for source in ([], {"schemaVersion": 2}):
            with self.subTest(source=source):
                completed = self.run_validator(self.manifest(), source)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("invalid", completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
