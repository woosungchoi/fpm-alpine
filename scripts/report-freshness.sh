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
apk_packages = []
lines = text.splitlines()
idx = 0
while idx < len(lines):
    line = lines[idx]
    if "apk add" not in line:
        idx += 1
        continue
    block = [line]
    while ";" not in lines[idx] and idx + 1 < len(lines):
        nxt = lines[idx + 1]
        if re.match(r"^(RUN|ENV|ARG|FROM|COPY|ADD|CMD|ENTRYPOINT)\b", nxt):
            break
        idx += 1
        block.append(lines[idx])
    uncommented = "\n".join(part.split("#", 1)[0] for part in block)
    body = uncommented
    for raw in re.split(r"\s+", body.replace("\\", " ")):
        token = raw.strip().strip(";,#\"'")
        if not token or token.startswith("-") or token.startswith("$"):
            continue
        if token in {"RUN", "apk", "add", "set", "eux", "ex", "in", "theory", "is", "but", "priority"}:
            continue
        if token.startswith("https://") or token.startswith("http://"):
            continue
        if token.startswith("PHP") or token in {"dl-cdn.alpinelinux.org", "alpine", "edge", "community"}:
            continue
        if "/" in token or "=" in token:
            continue
        apk_packages.append(token)
    idx += 1
pecl_installs = []
for line in text.splitlines():
    if "pecl install" in line:
        cleaned = line.replace(";", " ").replace("\\", " ")
        parts = cleaned.split()
        if "install" in parts:
            idx = parts.index("install")
            pecl_installs.extend(part for part in parts[idx + 1:] if not part.startswith("-"))

data = {
    "dockerfile": str(dockerfile),
    "baseImage": base_match.group(1) if base_match else None,
    "pinnedImagickVersion": imagick_match.group(1) if imagick_match else None,
    "usesGnuLibiconvWorkaround": uses_gnu_libiconv,
    "apkPackageSignals": sorted(set(apk_packages)),
    "peclInstallSignals": sorted(set(pecl_installs)),
    "pecl": [],
    "images": [],
    "warnings": [],
}
if not data["pinnedImagickVersion"]:
    data["warnings"].append("IMAGICK_VERSION baseline missing")
elif not re.fullmatch(r"\d+\.\d+\.\d+(?:[A-Za-z0-9._-]*)?", data["pinnedImagickVersion"]):
    data["warnings"].append("IMAGICK_VERSION baseline is not a recognizable semver-like value")
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
pinned = data.get("pinnedImagickVersion") if package == "imagick" else None
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
lines.append("This report is observational only. It does not publish images, open PRs, or change dependency pins.")
lines.append("")
lines.append("## Dockerfile pins")
lines.append("")
lines.append(f"- Dockerfile: `{data.get('dockerfile')}`")
lines.append(f"- Base image: `{data.get('baseImage') or 'unknown'}`")
lines.append(f"- Pinned Imagick: `{data.get('pinnedImagickVersion') or 'unknown'}`")
lines.append(f"- gnu-libiconv workaround present: `{str(data.get('usesGnuLibiconvWorkaround')).lower()}`")
lines.append("")
lines.append("## Installed package signals")
lines.append("")
apk = data.get("apkPackageSignals") or []
pecl = data.get("peclInstallSignals") or []
lines.append("- APK/runtime signals: " + (", ".join(f"`{item}`" for item in apk) if apk else "`none detected`"))
lines.append("- PECL install signals: " + (", ".join(f"`{item}`" for item in pecl) if pecl else "`none detected`"))
lines.append("")
lines.append("## Image digests")
lines.append("")
for item in data.get("images", []):
    digest = item.get("digest") or "unavailable"
    lines.append(f"- `{item['ref']}`: `{digest}` ({item['status']})")
if not data.get("images"):
    lines.append("- No image digest checks requested in this run.")
lines.append("")
lines.append("## PECL latest releases")
lines.append("")
for item in data.get("pecl", []):
    pinned = item.get("pinned") or "not pinned here"
    latest = item.get("latest") or "unavailable"
    marker = "update available" if item.get("updateAvailable") else "ok/report-only"
    lines.append(f"- `{item['package']}`: pinned `{pinned}`, latest `{latest}` ({item['status']}, {marker})")
if not data.get("pecl"):
    lines.append("- No PECL latest checks requested in this run.")
lines.append("")
lines.append("## Manual follow-up guide")
lines.append("")
lines.append("- Treat `inspect_failed` as an observation first: check Docker Hub/registry availability before changing source.")
lines.append("- Treat PECL latest changes as manual review prompts, not automatic Dockerfile updates.")
lines.append("- Keep `imagick-3.8.1` unless a branch-specific smoke test proves a newer baseline is safe.")
lines.append("- Reassess `gnu-libiconv` only with branch-by-branch image smoke coverage.")
if data.get("warnings"):
    lines.append("")
    lines.append("## Warnings")
    lines.append("")
    for warning in data["warnings"]:
        lines.append(f"- ⚠️ {warning}")
if data.get("usesGnuLibiconvWorkaround"):
    lines.append("")
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
