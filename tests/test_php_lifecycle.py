#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check-php-lifecycle.py"
VERSIONS = ROOT / "build/versions.json"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), "--versions", str(VERSIONS), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    result = run(
        "--today", "2026-10-15", "--skip-upstream",
        "--output-json", str(root / "warning.json"),
        "--output-md", str(root / "warning.md"),
    )
    assert result.returncode == 0, result.stderr
    warning = json.loads((root / "warning.json").read_text())
    assert warning["records"][0]["minor"] == "8.2"
    assert warning["records"][0]["state"] == "warning-90"

    result = run(
        "--today", "2027-01-01", "--skip-upstream",
        "--output-json", str(root / "eol.json"),
        "--output-md", str(root / "eol.md"),
    )
    assert result.returncode == 2
    assert "8.2" in json.loads((root / "eol.json").read_text())["eolReached"]

    upstream = root / "upstream"
    upstream.mkdir()
    for minor, patch in (("8.2", "8.2.99"), ("8.3", "8.3.99"), ("8.4", "8.4.99"), ("8.5", "8.5.99")):
        (upstream / minor).write_text(json.dumps({"version": patch}))
    result = run(
        "--today", "2026-07-12", "--upstream-base-url", upstream.as_uri() + "/",
        "--output-json", str(root / "upstream.json"),
        "--output-md", str(root / "upstream.md"),
    )
    assert result.returncode == 0, result.stderr

    (upstream / "8.4").unlink()
    result = run(
        "--today", "2026-07-12", "--upstream-base-url", upstream.as_uri() + "/",
        "--output-json", str(root / "unavailable.json"),
        "--output-md", str(root / "unavailable.md"),
    )
    assert result.returncode == 3
    assert json.loads((root / "unavailable.json").read_text())["upstreamUnavailable"]

    (upstream / "8.4").write_text(json.dumps({"version": "9.0.0"}))
    result = run(
        "--today", "2026-07-12", "--upstream-base-url", upstream.as_uri() + "/",
        "--output-json", str(root / "mismatch.json"),
        "--output-md", str(root / "mismatch.md"),
    )
    assert result.returncode == 2
    assert json.loads((root / "mismatch.json").read_text())["mismatches"]

print("PHP lifecycle tests passed")
