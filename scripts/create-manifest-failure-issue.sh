#!/usr/bin/env bash
set -euo pipefail

IMAGE_REF="${IMAGE_REF:-${1:-}}"
REPORT_DIR="${MANIFEST_REPORT_DIR:-manifest-reports}"
REPO="${GITHUB_REPOSITORY:-}"
RUN_URL="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}"
LABEL="manifest-failure"

if [ -z "$IMAGE_REF" ]; then
  echo "IMAGE_REF or first argument is required" >&2
  exit 64
fi

if [ -z "$REPO" ]; then
  echo "GITHUB_REPOSITORY is required" >&2
  exit 64
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required to create or update manifest failure issues" >&2
  exit 69
fi

safe_name="${IMAGE_REF//[^A-Za-z0-9_.-]/_}"
report_file="$REPORT_DIR/${safe_name}.md"
title="Manifest verification failed: ${IMAGE_REF}"

report_body="Manifest report artifact was not found at \`${report_file}\`. Check the failed workflow logs and artifacts."
if [ -f "$report_file" ]; then
  report_body="$(cat "$report_file")"
fi

body_file="$(mktemp)"
trap 'rm -f "$body_file"' EXIT
cat > "$body_file" <<EOF
The published Docker image manifest verification failed for \`${IMAGE_REF}\`.

- Workflow run: ${RUN_URL}
- Image ref: \`${IMAGE_REF}\`
- Report path: \`${report_file}\`

## Triage

1. If this happened soon after a push, suspect Docker Hub propagation lag first.
2. Re-run \`verify-published-manifest\` manually for \`${IMAGE_REF}\`.
3. Only change Docker Hub hooks or publish logic after repeated manual checks prove the manifest is genuinely missing or malformed.

## Latest report

${report_body}
EOF

label_args=()
if gh label list --repo "$REPO" --json name --jq '.[].name' | grep -Fxq "$LABEL"; then
  label_args=(--label "$LABEL")
elif gh label create "$LABEL" --repo "$REPO" --description "Published manifest verification failures" --color "B60205" >/dev/null 2>&1; then
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
  echo "updated existing manifest failure issue #${existing_issue} for ${IMAGE_REF}"
else
  gh issue create \
    --repo "$REPO" \
    --title "$title" \
    --body-file "$body_file" \
    "${label_args[@]}"
fi
