#!/usr/bin/env bash
set -euo pipefail

IMAGE_REF="${1:-}"
shift || true
EXPECTED_PLATFORMS=("$@")
MANIFEST_RETRY_ATTEMPTS="${MANIFEST_RETRY_ATTEMPTS:-5}"
MANIFEST_RETRY_DELAY_SECONDS="${MANIFEST_RETRY_DELAY_SECONDS:-20}"

PUBLISHER_MODE="${PUBLISHER_MODE:-legacy-observation}"

if [ "$PUBLISHER_MODE" = "github-actions" ]; then
  publish_path_note="GitHub Actions publisher subject; verification is digest-qualified."
  propagation_note="This can be caused by registry propagation or network failure, or a genuinely missing tag. Re-run the manual publisher verification before promotion."
  triage_note="If this failed immediately after a publish, first suspect bounded registry propagation or network issues. Re-run exact-subject verification before changing build or promotion logic."
else
  publish_path_note="Legacy Docker Hub published subject; this workflow is observation only."
  propagation_note="This can be caused by Docker Hub propagation lag, registry/network failure, or a genuinely missing tag. Re-run the manual workflow after Docker Hub has finished publishing before changing publish hooks."
  triage_note="If this failed immediately after a push, first suspect Docker Hub propagation lag or registry/network issues. Re-run the manual manifest workflow before changing build or publish logic."
fi

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
inspect_log="$report_dir/${safe_name}.inspect.log"

inspect_text=""
raw_status=""
: > "$inspect_log"
for attempt in $(seq 1 "$MANIFEST_RETRY_ATTEMPTS"); do
  echo "manifest inspect attempt ${attempt}/${MANIFEST_RETRY_ATTEMPTS}: ${IMAGE_REF}" | tee -a "$inspect_log"
  if inspect_text="$(docker buildx imagetools inspect "$IMAGE_REF" 2>&1)"; then
    if docker buildx imagetools inspect --raw "$IMAGE_REF" > "$raw_file" 2>>"$inspect_log"; then
      raw_status="ok"
      break
    fi
  else
    printf '%s\n' "$inspect_text" >> "$inspect_log"
  fi

  if [ "$attempt" -lt "$MANIFEST_RETRY_ATTEMPTS" ]; then
    echo "manifest not available yet; retrying after ${MANIFEST_RETRY_DELAY_SECONDS}s" | tee -a "$inspect_log"
    sleep "$MANIFEST_RETRY_DELAY_SECONDS"
  fi
done

if [ "$raw_status" != "ok" ]; then
  cat > "$md_file" <<EOF
## \`${IMAGE_REF}\`

- Status: ❌ manifest inspect failed after ${MANIFEST_RETRY_ATTEMPTS} attempt(s)
- Expected platforms: $(printf '`%s` ' "${EXPECTED_PLATFORMS[@]}")

${propagation_note}
EOF
  cat "$md_file" >&2
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat "$md_file" >> "$GITHUB_STEP_SUMMARY"; fi
  exit 1
fi

digest="$(printf '%s\n' "$inspect_text" | "$(dirname "$0")/extract-image-digest.sh")"

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
publish_path_note = sys.argv[5]
triage_note = sys.argv[6]
expected = sys.argv[7:]
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
        value = f"{base_value}/{variant}" if variant else base_value
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
    f"- Publish path: {publish_path_note}",
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
lines.extend([
    "",
    "### Triage note",
    "",
    triage_note,
    "",
])
md_file.write_text("\n".join(lines))

if missing:
    print(f"missing manifest platform(s) for {image_ref}: {', '.join(missing)}", file=sys.stderr)
    print(f"manifest platforms present: {', '.join(sorted(found))}", file=sys.stderr)
else:
    print(f"manifest report passed for {image_ref}: {', '.join(expected)}")
print(f"digest: {digest}")
PY
python3 "$parser_file" "$IMAGE_REF" "$digest" "$json_file" "$md_file" \
  "$publish_path_note" "$triage_note" "${EXPECTED_PLATFORMS[@]}" < "$raw_file"
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
