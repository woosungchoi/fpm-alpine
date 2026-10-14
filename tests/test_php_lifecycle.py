#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
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
    for today, expected, days, rc in (
        ("2026-10-01", "ok", 91, 0), ("2026-10-02", "warning-90", 90, 0),
        ("2026-12-01", "warning-30", 30, 0), ("2026-12-31", "warning-30", 0, 0),
        ("2027-01-01", "eol", -1, 2),
    ):
        result = run("--today", today, "--skip-upstream", "--output-json", str(root / "date.json"),
                     "--output-md", str(root / "date.md"))
        assert result.returncode == rc, result.stderr
        report = json.loads((root / "date.json").read_text())
        row = report["records"][0]
        assert (row["state"], row["daysUntilEol"]) == (expected, days)
        assert report["upstreamChecked"] is False
        assert "not checked" in (root / "date.md").read_text()

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

    # Exercise the real issue script without contacting GitHub. Capture issue
    # writes and ensure only completed, identical warnings are acknowledged.
    fake_bin = root / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
with open(os.environ['GH_CALLS'],'a') as f: f.write(json.dumps(args)+'\\n')
if args[:2] == ['issue','list']:
    if args[args.index('--state')+1] == 'open':
        print(os.environ.get('OPEN_ISSUE',''))
    else:
        print(os.environ['CLOSED_ISSUES'])
elif args[:2] == ['issue','view']:
    print(os.environ.get('OPEN_BODY',''))
''')
    fake_gh.chmod(0o755)
    calls_path = root / "gh-calls.jsonl"
    def marker(report):
        states = [{k: r.get(k) for k in ('minor', 'support', 'eol', 'state')}
                  for r in report.get('records', [])]
        signal = {'states': states, 'unavailable': report.get('upstreamUnavailable', []),
                  'mismatches': report.get('mismatches', [])}
        fingerprint = hashlib.sha256(json.dumps(signal, sort_keys=True).encode()).hexdigest()
        return f'<!-- php-lifecycle-state:{fingerprint} -->'

    def issue_run(report_name, closed, **extra):
        calls_path.write_text('')
        result = subprocess.run(
            ['bash', str(ROOT / 'scripts/create-php-lifecycle-issue.sh'),
             str(root / (report_name + '.json')), str(root / (report_name + '.md'))],
            env={**os.environ, 'PATH': str(fake_bin) + os.pathsep + os.environ['PATH'],
                 'GH_TOKEN': 'test-only', 'GH_CALLS': str(calls_path),
                 'CLOSED_ISSUES': json.dumps(closed), **extra},
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return [json.loads(line) for line in calls_path.read_text().splitlines()]

    acknowledged = {'number': 130, 'body': marker(warning), 'stateReason': 'COMPLETED'}
    calls = issue_run('warning', [acknowledged])
    assert not any(c[:2] in (['issue', 'create'], ['issue', 'edit']) for c in calls)
    for closed in ([], [{**acknowledged, 'stateReason': 'NOT_PLANNED'}],
                   [{**acknowledged, 'body': 'a different warning'}]):
        assert any(c[:2] == ['issue', 'create'] for c in issue_run('warning', closed))
    calls = issue_run('warning', [], OPEN_ISSUE='130', OPEN_BODY=marker(warning))
    assert not any(c[:2] in (['issue', 'create'], ['issue', 'edit']) for c in calls)
    calls = issue_run('warning', [], OPEN_ISSUE='130', OPEN_BODY='old state')
    assert any(c[:2] == ['issue', 'edit'] for c in calls)
    result = run('--today', '2026-12-01', '--skip-upstream',
                 '--output-json', str(root / 'next.json'), '--output-md', str(root / 'next.md'))
    assert result.returncode == 0
    assert any(c[:2] == ['issue', 'create'] for c in issue_run('next', [acknowledged]))
    for report_name in ('eol', 'mismatch', 'unavailable'):
        report = json.loads((root / (report_name + '.json')).read_text())
        closed = [{**acknowledged, 'body': marker(report)}]
        calls = issue_run(report_name, closed)
        assert any(c[:2] == ['issue', 'create'] for c in calls), report_name
        assert not any('--state' in c and 'closed' in c for c in calls), report_name

print("PHP lifecycle tests passed")
