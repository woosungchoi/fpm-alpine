#!/usr/bin/env bash
set -euo pipefail

IMAGE_REF="${1:-}"
shift || true
EXPECTED_PLATFORMS=("$@")

if [ -z "$IMAGE_REF" ]; then
  echo "usage: $0 <registry-image-ref> [expected-platform ...]" >&2
  exit 64
fi

if [ ${#EXPECTED_PLATFORMS[@]} -eq 0 ]; then
  EXPECTED_PLATFORMS=(linux/amd64 linux/arm64)
fi

safe_name="${IMAGE_REF//[^A-Za-z0-9_.-]/_}"
report_dir="${MANIFEST_REPORT_DIR:-manifest-reports}"
mkdir -p "$report_dir"
raw_file="$report_dir/${safe_name}.raw.json"
json_file="$report_dir/${safe_name}.summary.json"
md_file="$report_dir/${safe_name}.md"

inspect_text="$(docker buildx imagetools inspect "$IMAGE_REF")"
digest="$(awk '/^Digest:/ { print $2; exit }' <<< "$inspect_text")"
if [ -z "$digest" ]; then
  echo "failed to resolve digest for $IMAGE_REF" >&2
  exit 1
fi

docker buildx imagetools inspect --raw "$IMAGE_REF" > "$raw_file"

parser_file="$(mktemp)"
trap 'rm -f "$parser_file"' EXIT
cat > "$parser_file" <<'PY'
import json
import sys
from pathlib import Path

image_ref = sys.argv[1]
digest = sys.argv[2]
json_file = Path(sys.argv[3])
md_file = Path(sys.argv[4])
expected = sys.argv[5:]
manifest = json.load(sys.stdin)

platforms = []
attestations = []
for item in manifest.get("manifests", []):
    platform = item.get("platform") or {}
    os_name = platform.get("os")
    arch = platform.get("architecture")
    variant = platform.get("variant")
    annotations = item.get("annotations") or {}
    media_type = item.get("mediaType", "")
    item_digest = item.get("digest", "")

    if os_name == "unknown" or arch == "unknown":
        attestations.append({
            "digest": item_digest,
            "mediaType": media_type,
            "referenceDigest": annotations.get("vnd.docker.reference.digest"),
            "referenceType": annotations.get("vnd.docker.reference.type"),
        })
        continue

    if os_name and arch:
        base_value = f"{os_name}/{arch}"
        value = base_value
        if variant:
            value = f"{value}/{variant}"
        platforms.append({"platform": value, "digest": item_digest, "mediaType": media_type})

found = {item["platform"] for item in platforms}
for item in platforms:
    parts = item["platform"].split("/")
    if len(parts) == 3:
        found.add("/".join(parts[:2]))
missing = [item for item in expected if item not in found]
summary = {
    "image": image_ref,
    "digest": digest,
    "expectedPlatforms": expected,
    "platforms": sorted(platforms, key=lambda item: item["platform"]),
    "missingPlatforms": missing,
    "attestations": attestations,
}
json_file.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

lines = [
    f"## `{image_ref}`",
    "",
    f"- Digest: `{digest}`",
    f"- Expected platforms: {', '.join(f'`{item}`' for item in expected)}",
    f"- Status: {'✅ passed' if not missing else '❌ missing ' + ', '.join(missing)}",
    "",
    "### Platforms",
    "",
]
for item in summary["platforms"]:
    lines.append(f"- `{item['platform']}` — `{item['digest']}`")
if attestations:
    lines.extend(["", "### Attestation/metadata manifest entries", ""])
    for item in attestations:
        ref = item.get("referenceDigest") or "unknown reference"
        rtype = item.get("referenceType") or "unknown type"
        lines.append(f"- `{rtype}` for `{ref}` — `{item.get('digest')}`")
lines.append("")
md_file.write_text("\n".join(lines))

if missing:
    print(f"missing manifest platform(s) for {image_ref}: {', '.join(missing)}", file=sys.stderr)
    print(f"manifest platforms present: {', '.join(sorted(found))}", file=sys.stderr)
else:
    print(f"manifest report passed for {image_ref}: {', '.join(expected)}")
print(f"digest: {digest}")
PY
python3 "$parser_file" "$IMAGE_REF" "$digest" "$json_file" "$md_file" "${EXPECTED_PLATFORMS[@]}" < "$raw_file"
rm -f "$parser_file"

cat "$md_file"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$md_file" >> "$GITHUB_STEP_SUMMARY"
fi

missing_count="$(python3 - "$json_file" <<'PY'
import json
import sys
from pathlib import Path
summary = json.loads(Path(sys.argv[1]).read_text())
print(len(summary.get("missingPlatforms") or []))
PY
)"
if [ "$missing_count" -ne 0 ]; then
  exit 1
fi
