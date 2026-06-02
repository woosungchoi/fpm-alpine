#!/usr/bin/env bash
set -euo pipefail

REPORT_DIR="${REPORT_DIR:-freshness-reports}"
REPORT_MD="${REPORT_DIR}/dependency-freshness.md"
REPORT_JSON="${REPORT_DIR}/dependency-freshness.json"
DOCKERFILE_PATH="${DOCKERFILE_PATH:-Dockerfile}"
PHP_TAGS="${PHP_TAGS:-8.0 8.1 8.2 8.3 8.4 8.5}"
PECL_PACKAGES="${PECL_PACKAGES:-imagick redis apcu}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-woosungchoi/fpm-alpine}"

mkdir -p "$REPORT_DIR"

tmp_json="$(mktemp)"
trap 'rm -f "$tmp_json"' EXIT

python3 - "$DOCKERFILE_PATH" "$tmp_json" <<'PY'
import json
import re
import sys
from pathlib import Path

dockerfile = Path(sys.argv[1])
out = Path(sys.argv[2])
text = dockerfile.read_text()
base_match = re.search(r"^FROM\s+([^\s]+)", text, re.MULTILINE)
imagick_match = re.search(r"^ARG\s+IMAGICK_VERSION=([^\s]+)", text, re.MULTILINE)
uses_gnu_libiconv = "gnu-libiconv" in text and "LD_PRELOAD=/usr/lib/preloadable_libiconv.so" in text

data = {
    "dockerfile": str(dockerfile),
    "baseImage": base_match.group(1) if base_match else None,
    "pinnedImagickVersion": imagick_match.group(1) if imagick_match else None,
    "usesGnuLibiconvWorkaround": uses_gnu_libiconv,
    "pecl": [],
    "images": [],
}
out.write_text(json.dumps(data, indent=2) + "\n")
PY

inspect_digest() {
  local image_ref="$1"
  local output
  if ! output="$(docker buildx imagetools inspect "$image_ref" 2>&1)"; then
    printf '%s' ""
    return 1
  fi
  awk '/^Digest:/ { print $2; exit }' <<< "$output"
}

json_set_digest() {
  local image_ref="$1"
  local digest="$2"
  local status="$3"
  python3 - "$tmp_json" "$image_ref" "$digest" "$status" <<'PY'
import json, sys
path, image_ref, digest, status = sys.argv[1:]
data = json.load(open(path))
data.setdefault("images", []).append({"ref": image_ref, "digest": digest or None, "status": status})
open(path, "w").write(json.dumps(data, indent=2) + "\n")
PY
}

# Inspect current base and published image tags. This is report-only: failures are recorded, not fatal.
base_image="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("baseImage") or "")' "$tmp_json")"
if [ -n "$base_image" ]; then
  if digest="$(inspect_digest "$base_image")"; then
    json_set_digest "$base_image" "$digest" "ok"
  else
    json_set_digest "$base_image" "" "inspect_failed"
  fi
fi

for tag in $PHP_TAGS; do
  image_ref="${IMAGE_REPOSITORY}:${tag}"
  if digest="$(inspect_digest "$image_ref")"; then
    json_set_digest "$image_ref" "$digest" "ok"
  else
    json_set_digest "$image_ref" "" "inspect_failed"
  fi
done

# Query PECL latest release metadata. This is report-only and intentionally does not fail the workflow.
for package in $PECL_PACKAGES; do
  latest=""
  status="ok"
  if ! latest="$(curl --retry 3 --retry-delay 2 --max-time 20 -fsSL "https://pecl.php.net/rest/r/${package}/latest.txt" 2>/dev/null | tr -d '\r\n')"; then
    status="fetch_failed"
  fi
  python3 - "$tmp_json" "$package" "$latest" "$status" <<'PY'
import json, sys
path, package, latest, status = sys.argv[1:]
data = json.load(open(path))
pinned = None
if package == "imagick":
    pinned = data.get("pinnedImagickVersion")
data.setdefault("pecl", []).append({
    "package": package,
    "pinned": pinned,
    "latest": latest or None,
    "status": status,
    "updateAvailable": bool(pinned and latest and pinned != latest),
})
open(path, "w").write(json.dumps(data, indent=2) + "\n")
PY
done

cp "$tmp_json" "$REPORT_JSON"

python3 - "$REPORT_JSON" "$REPORT_MD" <<'PY'
import json
import sys
from pathlib import Path

data = json.load(open(sys.argv[1]))
out = Path(sys.argv[2])
lines = []
lines.append("# fpm-alpine dependency freshness report")
lines.append("")
lines.append("This report is observational only. It does not publish images or change dependency pins.")
lines.append("")
lines.append("## Dockerfile pins")
lines.append("")
lines.append(f"- Dockerfile: `{data.get('dockerfile')}`")
lines.append(f"- Base image: `{data.get('baseImage') or 'unknown'}`")
lines.append(f"- Pinned Imagick: `{data.get('pinnedImagickVersion') or 'unknown'}`")
lines.append(f"- gnu-libiconv workaround present: `{str(data.get('usesGnuLibiconvWorkaround')).lower()}`")
lines.append("")
lines.append("## Image digests")
lines.append("")
for item in data.get("images", []):
    digest = item.get("digest") or "unavailable"
    lines.append(f"- `{item['ref']}`: `{digest}` ({item['status']})")
lines.append("")
lines.append("## PECL latest releases")
lines.append("")
for item in data.get("pecl", []):
    pinned = item.get("pinned") or "not pinned here"
    latest = item.get("latest") or "unavailable"
    marker = "update available" if item.get("updateAvailable") else "ok/report-only"
    lines.append(f"- `{item['package']}`: pinned `{pinned}`, latest `{latest}` ({item['status']}, {marker})")
lines.append("")
if data.get("usesGnuLibiconvWorkaround"):
    lines.append("## Manual review note")
    lines.append("")
    lines.append("The Dockerfile still uses the Alpine edge `gnu-libiconv` workaround with `LD_PRELOAD`. Reassess periodically against the current PHP/Alpine base image before removing it.")
    lines.append("")
out.write_text("\n".join(lines))
PY

cat "$REPORT_MD"

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$REPORT_MD" >> "$GITHUB_STEP_SUMMARY"
fi
