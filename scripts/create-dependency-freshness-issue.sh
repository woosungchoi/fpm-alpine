#!/usr/bin/env bash
set -euo pipefail

REPORT_JSON="${FRESHNESS_REPORT_JSON:-freshness-reports/dependency-freshness.json}"
REPORT_MD="${FRESHNESS_REPORT_MD:-freshness-reports/dependency-freshness.md}"
REPO="${GITHUB_REPOSITORY:-}"
RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"
LABEL="dependency-freshness"

if [ -z "$REPO" ]; then
  echo "GITHUB_REPOSITORY is required" >&2
  exit 64
fi

if [ ! -f "$REPORT_JSON" ]; then
  echo "freshness report JSON not found: $REPORT_JSON" >&2
  exit 66
fi

summary_json="$(python3 - "$REPORT_JSON" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text())
updates = [
    item for item in data.get("pecl", [])
    if item.get("updateAvailable") and item.get("status") == "ok"
]
inspect_failures = [
    item for item in data.get("images", [])
    if item.get("status") == "inspect_failed"
]
warnings = data.get("warnings") or []
print(json.dumps({
    "updates": updates,
    "inspectFailures": inspect_failures,
    "warnings": warnings,
    "count": len(updates) + len(inspect_failures) + len(warnings),
}))
PY
)"

signal_count="$(python3 - "$summary_json" <<'PY'
import json, sys
print(json.loads(sys.argv[1])["count"])
PY
)"

if [ "$signal_count" = "0" ]; then
  echo "no dependency freshness issue needed"
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required to create or update dependency freshness issues" >&2
  exit 69
fi

title="Dependency freshness review needed"
report_body="Dependency freshness markdown report was not found at \`${REPORT_MD}\`. Check the workflow artifact."
if [ -f "$REPORT_MD" ]; then
  report_body="$(cat "$REPORT_MD")"
fi

body_file="$(mktemp)"
summary_file="$(mktemp)"
trap 'rm -f "$body_file" "$summary_file"' EXIT
python3 - "$summary_file" "$summary_json" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(sys.argv[2])
lines = []
updates = data.get("updates", [])
inspect_failures = data.get("inspectFailures", [])
warnings = data.get("warnings", [])
if updates:
    lines.append("## PECL updates")
    lines.append("")
    for item in updates:
        package = item.get("package", "unknown")
        pinned = item.get("pinned") or "not pinned here"
        latest = item.get("latest") or "unavailable"
        lines.append(f"- `{package}`: pinned `{pinned}`, latest `{latest}`")
    lines.append("")
if inspect_failures:
    lines.append("## Image inspect failures")
    lines.append("")
    for item in inspect_failures:
        lines.append(f"- `{item.get('ref', 'unknown')}`")
    lines.append("")
if warnings:
    lines.append("## Report warnings")
    lines.append("")
    for warning in warnings:
        lines.append(f"- {warning}")
    lines.append("")
Path(sys.argv[1]).write_text("\n".join(lines).strip() + "\n")
PY

cat > "$body_file" <<EOF
The scheduled dependency freshness report found one or more review signals.

- Workflow run: ${RUN_URL}
- JSON report: \`${REPORT_JSON}\`
- Markdown report: \`${REPORT_MD}\`

$(cat "$summary_file")

## Triage

1. Treat this issue as a manual review queue item, not an automatic dependency update approval.
2. For PECL updates, run branch-specific smoke validation before changing \`IMAGICK_VERSION\` or extension install behavior.
3. For image inspect failures, check Docker Hub/registry availability before changing source.
4. Keep Docker Hub hooks as the publish path unless a separate publish migration is explicitly planned.

## Latest report

${report_body}
EOF

label_args=()
if gh label list --repo "$REPO" --json name --jq '.[].name' | grep -Fxq "$LABEL"; then
  label_args=(--label "$LABEL")
elif gh label create "$LABEL" --repo "$REPO" --description "Dependency freshness review items" --color "1D76DB" >/dev/null 2>&1; then
  label_args=(--label "$LABEL")
else
  echo "warning: could not find or create label $LABEL; creating issue without label" >&2
fi

existing_issue="$(gh issue list \
  --repo "$REPO" \
  --state open \
  --search "\"$title\" in:title" \
  --json number \
  --jq '.[0].number // empty' 2>/dev/null || true)"

if [ -n "$existing_issue" ]; then
  gh issue comment "$existing_issue" --repo "$REPO" --body-file "$body_file"
  echo "updated existing dependency freshness issue #${existing_issue}"
else
  gh issue create \
    --repo "$REPO" \
    --title "$title" \
    --body-file "$body_file" \
    "${label_args[@]}"
fi
