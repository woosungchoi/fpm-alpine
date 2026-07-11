#!/usr/bin/env bash
set -euo pipefail

REPORT_DIR="${REPORT_DIR:-freshness-reports}"
REPORT_MD="${REPORT_DIR}/dependency-freshness.md"
REPORT_JSON="${REPORT_DIR}/dependency-freshness.json"
VERSIONS_PATH="${VERSIONS_PATH:-build/versions.json}"
IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-woosungchoi/fpm-alpine}"
VALIDATOR_PATH="${VALIDATOR_PATH:-scripts/validate-versions.py}"
DIGEST_RESOLVER_PATH="${DIGEST_RESOLVER_PATH:-scripts/resolve-image-digest.sh}"
REPORT_SKIP_REMOTE="${REPORT_SKIP_REMOTE:-0}"

mkdir -p "$REPORT_DIR"
if ! validation_output="$(python3 "$VALIDATOR_PATH" "$VERSIONS_PATH" 2>&1)"; then
  echo "freshness report aborted: invalid versions metadata: $validation_output" >&2
  exit 1
fi

tmp_json="$(mktemp)"
trap 'rm -f "$tmp_json"' EXIT
python3 - "$VERSIONS_PATH" "$tmp_json" <<'PY'
import json, sys
from pathlib import Path
source, out = map(Path, sys.argv[1:])
data = json.loads(source.read_text())
report = {
    "versionsFile": str(source),
    "schemaVersion": data["schemaVersion"],
    "versions": [dict(item) for item in data["versions"].values()],
    "dependencies": {name: dict(item) for name, item in data["dependencies"].items()},
    "runtimeContracts": {name: dict(item) for name, item in data["runtimeContracts"].items()},
    "pecl": [], "images": [], "warnings": [],
}
out.write_text(json.dumps(report, indent=2) + "\n")
PY

inspect_digest() {
  "$DIGEST_RESOLVER_PATH" "$1"
}
append_image() {
  python3 - "$tmp_json" "$1" "$2" "$3" "$4" <<'PY'
import json, sys
path, kind, ref, digest, status = sys.argv[1:]
data = json.load(open(path))
data["images"].append({"kind": kind, "ref": ref, "digest": digest or None, "status": status})
open(path, "w").write(json.dumps(data, indent=2) + "\n")
PY
}

if [ "$REPORT_SKIP_REMOTE" != "1" ]; then
while IFS=$'\t' read -r minor base_image; do
  if digest="$(inspect_digest "$base_image")"; then status=ok; else digest=""; status=inspect_failed; fi
  append_image base "$base_image" "$digest" "$status"
  published="${IMAGE_REPOSITORY}:${minor}"
  if digest="$(inspect_digest "$published")"; then status=ok; else digest=""; status=inspect_failed; fi
  append_image published "$published" "$digest" "$status"
done < <(python3 - "$VERSIONS_PATH" <<'PY'
import json, sys
data=json.load(open(sys.argv[1]))
for minor, item in data["versions"].items(): print(minor, item["base_image"], sep="\t")
PY
)

for package in imagick redis apcu; do
  latest=""; status=ok
  if ! latest="$(curl --retry 3 --retry-delay 2 --max-time 20 -fsSL "https://pecl.php.net/rest/r/${package}/latest.txt" 2>/dev/null | tr -d '\r\n')"; then status=fetch_failed; fi
  python3 - "$tmp_json" "$package" "$latest" "$status" <<'PY'
import json, sys
path, package, latest, status = sys.argv[1:]
data=json.load(open(path)); pinned=data["dependencies"][package]["version"]
data["pecl"].append({"package": package, "pinned": pinned, "latest": latest or None,
                     "status": status, "updateAvailable": bool(latest and latest != pinned)})
open(path, "w").write(json.dumps(data, indent=2) + "\n")
PY
done
fi

cp "$tmp_json" "$REPORT_JSON"
python3 - "$REPORT_JSON" "$REPORT_MD" <<'PY'
import json, sys
from pathlib import Path
data=json.load(open(sys.argv[1])); lines=[
"# fpm-alpine dependency freshness report", "",
"This report is observational only. It does not publish images, open PRs, or change dependency pins.", "",
"## Validated matrix pins", "", f"- Source: `{data['versionsFile']}` (schema `{data['schemaVersion']}`)"]
for item in data["versions"]:
    lines.append(f"- PHP `{item['patch']}` ({item['support']}, EOL `{item['eol']}`): `{item['base_image']}`")
lines += ["", "## Verified source pins", ""]
for name, item in data["dependencies"].items():
    lines.append(f"- `{name}` `{item['version']}`: `{item['url']}`; SHA-256 `{item['sha256']}`")
lines += ["", "## Pinned-base runtime contracts", ""]
iconv=data["runtimeContracts"]["libiconv"]
lines.append(f"- `{iconv['implementation']}` `{iconv['version']}` from `{iconv['package']}={iconv['packageVersion']}`: `{iconv['ownerPath']}` -> `{iconv['target']}`")
lines += ["", "## Image digests", ""]
for item in data["images"]:
    lines.append(f"- {item['kind']} `{item['ref']}`: `{item['digest'] or 'unavailable'}` ({item['status']})")
lines += ["", "## PECL latest releases", ""]
for item in data["pecl"]:
    marker="update available" if item["updateAvailable"] else "ok/report-only"
    lines.append(f"- `{item['package']}`: pinned `{item['pinned']}`, latest `{item['latest'] or 'unavailable'}` ({item['status']}, {marker})")
lines += ["", "## Manual follow-up guide", "",
"- Treat `inspect_failed` as an observation first; check registry availability before changing source.",
"- Treat PECL changes as review prompts. Update `build/versions.json` only after checksum and matrix smoke verification.", ""]
Path(sys.argv[2]).write_text("\n".join(lines))
PY
cat "$REPORT_MD"
[ -z "${GITHUB_STEP_SUMMARY:-}" ] || cat "$REPORT_MD" >> "$GITHUB_STEP_SUMMARY"
