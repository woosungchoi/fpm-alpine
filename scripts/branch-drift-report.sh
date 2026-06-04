#!/usr/bin/env bash
set -euo pipefail

BRANCH_DRIFT_BRANCHES="${BRANCH_DRIFT_BRANCHES:-8.0 8.1 8.2 8.3 8.4 8.5}"
BRANCH_DRIFT_FILES="${BRANCH_DRIFT_FILES:-.github/workflows/smoke-test.yml .github/workflows/verify-published-manifest.yml .github/workflows/dependency-freshness.yml scripts/smoke-test-image.sh scripts/report-manifest.sh scripts/report-freshness.sh Dockerfile BRANCH-AND-TAG-POLICY.md README.md}"
BRANCH_DRIFT_REPORT_DIR="${BRANCH_DRIFT_REPORT_DIR:-branch-drift-reports}"
BRANCH_DRIFT_ALLOWLIST="${BRANCH_DRIFT_ALLOWLIST:-docs/branch-drift-allowlist.tsv}"
BASE_BRANCH="${BRANCH_DRIFT_BASE_BRANCH:-8.5}"

mkdir -p "$BRANCH_DRIFT_REPORT_DIR"
json_file="$BRANCH_DRIFT_REPORT_DIR/branch-drift.json"
md_file="$BRANCH_DRIFT_REPORT_DIR/branch-drift.md"

python3 - "$json_file" "$md_file" "$BASE_BRANCH" "$BRANCH_DRIFT_ALLOWLIST" "$BRANCH_DRIFT_BRANCHES" "$BRANCH_DRIFT_FILES" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

json_file = Path(sys.argv[1])
md_file = Path(sys.argv[2])
base_branch = sys.argv[3]
allowlist_path = Path(sys.argv[4])
branches = sys.argv[5].split()
files = sys.argv[6].split()

allowlist = set()
if allowlist_path.exists():
    for line in allowlist_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("path\t"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            allowlist.add((parts[0], parts[1]))

def git_show(branch, path):
    try:
        return subprocess.check_output(["git", "show", f"origin/{branch}:{path}"], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        try:
            return subprocess.check_output(["git", "show", f"{branch}:{path}"], stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            return None

def digest(content):
    if content is None:
        return None
    return hashlib.sha256(content).hexdigest()[:16]

def extract_baseline(content):
    if content is None:
        return None, None
    text = content.decode("utf-8", errors="replace")
    from_line = None
    imagick = None
    for line in text.splitlines():
        if line.startswith("FROM ") and from_line is None:
            from_line = line.split()[1]
        if line.startswith("ARG IMAGICK_VERSION=") and imagick is None:
            imagick = line.split("=", 1)[1].strip()
    return from_line, imagick

records = []
for path in files:
    base_content = git_show(base_branch, path)
    base_digest = digest(base_content)
    for branch in branches:
        content = git_show(branch, path)
        item_digest = digest(content)
        status = "ok"
        if content is None:
            status = "missing"
        elif base_digest != item_digest:
            status = "allowed-drift" if (path, branch) in allowlist else "drift"
        base_from, base_imagick = extract_baseline(base_content) if path == "Dockerfile" else (None, None)
        item_from, item_imagick = extract_baseline(content) if path == "Dockerfile" else (None, None)
        records.append({
            "path": path,
            "branch": branch,
            "baseBranch": base_branch,
            "status": status,
            "digest": item_digest,
            "baseDigest": base_digest,
            "baseImage": item_from,
            "imagickVersion": item_imagick,
            "baseBranchImage": base_from,
            "baseBranchImagickVersion": base_imagick,
            "allowlisted": (path, branch) in allowlist,
        })

summary = {
    "mode": "report-only",
    "baseBranch": base_branch,
    "branches": branches,
    "files": files,
    "records": records,
    "counts": {},
}
for record in records:
    summary["counts"][record["status"]] = summary["counts"].get(record["status"], 0) + 1
json_file.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

lines = [
    "# fpm-alpine branch drift report",
    "",
    "This workflow is report-only. It detects workflow/script/policy drift across maintained branches and does not modify repository files or create PRs.",
    "",
    f"- Base branch: `{base_branch}`",
    "- Maintained branches: " + ", ".join(f"`{b}`" for b in branches),
    "- Allowlist: `" + str(allowlist_path) + "`",
    "",
    "## Summary",
    "",
]
for key in sorted(summary["counts"]):
    lines.append(f"- `{key}`: {summary['counts'][key]}")
lines.extend(["", "## Drift details", ""])
for record in records:
    if record["status"] == "ok":
        continue
    suffix = ""
    if record["path"] == "Dockerfile":
        suffix = f" — base image `{record.get('baseImage')}`, imagick `{record.get('imagickVersion')}`"
    lines.append(f"- `{record['branch']}` `{record['path']}`: **{record['status']}**{suffix}")
if all(record["status"] == "ok" for record in records):
    lines.append("- No drift detected against the selected base branch.")
lines.extend([
    "",
    "## Dockerfile baseline matrix",
    "",
])
for record in records:
    if record["path"] == "Dockerfile":
        lines.append(f"- `{record['branch']}`: base `{record.get('baseImage') or 'missing'}`, imagick `{record.get('imagickVersion') or 'missing'}`, status `{record['status']}`")
lines.extend([
    "",
    "## Operating note",
    "",
    "Unexpected `drift` means a maintained branch may be missing a workflow/script/policy update. `allowed-drift` should have a short reason in the allowlist. Review manually before syncing branches.",
    "",
])
md_file.write_text("\n".join(lines))
print(md_file.read_text())
PY

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$md_file" >> "$GITHUB_STEP_SUMMARY"
fi
