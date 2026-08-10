#!/usr/bin/env bash
set -euo pipefail

DOCKERHUB_REF="${1:-}"
EXPECTED_REVISION="${2:-}"
EXPECTED_VERSION="${3:-}"
REPORT_DIR="${4:-published-runtime-reports}"
EXPECTED_SOURCE="${EXPECTED_SOURCE:-https://github.com/woosungchoi/fpm-alpine}"
EXPECTED_LICENSES="${EXPECTED_LICENSES:-GPL-2.0-only}"
INSPECT_ATTEMPTS="${INSPECT_ATTEMPTS:-5}"
INSPECT_RETRY_DELAY_SECONDS="${INSPECT_RETRY_DELAY_SECONDS:-2}"
EXPECTED_PLATFORMS=(linux/amd64 linux/arm64)
VERSIONS_FILE="${AUTO_PROMOTION_VERSIONS_FILE:-build/versions.json}"

if [[ ! "$DOCKERHUB_REF" =~ ^(docker\.io/)?woosungchoi/fpm-alpine(:8\.[2-5]|@sha256:[0-9a-f]{64})$ ]] ||
   [[ ! "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]] ||
   [[ ! "$EXPECTED_VERSION" =~ ^8\.[2-5]\.[0-9]+$ ]]; then
  echo "usage: $0 <dockerhub-ref> <40-char-source-sha> <php-patch> [report-dir]" >&2
  exit 64
fi
[[ "$INSPECT_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || { echo "INSPECT_ATTEMPTS must be a positive integer" >&2; exit 64; }
[[ "$INSPECT_RETRY_DELAY_SECONDS" =~ ^[0-9]+$ ]] || { echo "INSPECT_RETRY_DELAY_SECONDS must be a non-negative integer" >&2; exit 64; }
[ -r "$VERSIONS_FILE" ] || { echo "versions manifest is not readable: $VERSIONS_FILE" >&2; exit 66; }
for command in docker python3; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is required" >&2; exit 69; }
done

mkdir -p "$REPORT_DIR/manifests" "$REPORT_DIR/verification" \
  "$REPORT_DIR/provenance" "$REPORT_DIR/sbom" "$REPORT_DIR/smoke"

repository_from_ref() {
  local ref="$1"
  if [[ "$ref" == *@* ]]; then
    printf '%s\n' "${ref%@*}"
  else
    printf '%s\n' "${ref%:*}"
  fi
}

retry_delay() {
  local attempt="$1"
  if [ "$attempt" -lt "$INSPECT_ATTEMPTS" ] && [ "$INSPECT_RETRY_DELAY_SECONDS" -gt 0 ]; then
    sleep "$INSPECT_RETRY_DELAY_SECONDS"
  fi
}

resolve_digest_with_retry() {
  local attempt digest
  for ((attempt = 1; attempt <= INSPECT_ATTEMPTS; attempt++)); do
    echo "digest resolve attempt ${attempt}/${INSPECT_ATTEMPTS}: $DOCKERHUB_REF" >&2
    if digest="$(./scripts/resolve-image-digest.sh "$DOCKERHUB_REF")" &&
       [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
      printf '%s\n' "$digest"
      return 0
    fi
    retry_delay "$attempt"
  done
  echo "Docker Hub digest resolution failed after ${INSPECT_ATTEMPTS} attempts: $DOCKERHUB_REF" >&2
  return 1
}

inspect_to_file() {
  local label="$1" output="$2"
  shift 2
  local attempt temporary="${output}.tmp"
  for ((attempt = 1; attempt <= INSPECT_ATTEMPTS; attempt++)); do
    rm -f "$temporary"
    echo "${label} inspect attempt ${attempt}/${INSPECT_ATTEMPTS}" >&2
    if docker "$@" > "$temporary"; then
      mv "$temporary" "$output"
      return 0
    fi
    retry_delay "$attempt"
  done
  rm -f "$temporary"
  echo "${label} inspection failed after ${INSPECT_ATTEMPTS} attempts" >&2
  return 1
}

resolve_platform_with_retry() {
  local subject="$1" platform="$2" attempt resolved
  for ((attempt = 1; attempt <= INSPECT_ATTEMPTS; attempt++)); do
    echo "${platform} subject resolve attempt ${attempt}/${INSPECT_ATTEMPTS}" >&2
    if resolved="$(./scripts/resolve-platform-image.py "$subject" "$platform")" &&
       [[ "$resolved" =~ ^docker\.io/woosungchoi/fpm-alpine@sha256:[0-9a-f]{64}$ ]]; then
      printf '%s\n' "$resolved"
      return 0
    fi
    retry_delay "$attempt"
  done
  echo "${platform} subject resolution failed after ${INSPECT_ATTEMPTS} attempts" >&2
  return 1
}

dockerhub_digest="$(resolve_digest_with_retry)"
repository="$(repository_from_ref "$DOCKERHUB_REF")"
dockerhub_subject="${repository}@${dockerhub_digest}"
echo "exact Docker Hub subject: $dockerhub_subject"

PUBLISHER_MODE=github-actions MANIFEST_REPORT_DIR="$REPORT_DIR/manifests" \
  ./scripts/report-manifest.sh "$dockerhub_subject" "${EXPECTED_PLATFORMS[@]}"

inspect_to_file "Docker Hub index" "$REPORT_DIR/verification/dockerhub.index.json" \
  buildx imagetools inspect --raw "$dockerhub_subject"
inspect_to_file "Docker Hub image" "$REPORT_DIR/verification/dockerhub.image.json" \
  buildx imagetools inspect "$dockerhub_subject" --format '{{ json .Image }}'
inspect_to_file "Docker Hub provenance" "$REPORT_DIR/provenance/dockerhub.json" \
  buildx imagetools inspect "$dockerhub_subject" --format '{{ json .Provenance }}'
inspect_to_file "Docker Hub SBOM" "$REPORT_DIR/sbom/dockerhub.json" \
  buildx imagetools inspect "$dockerhub_subject" --format '{{ json .SBOM }}'

./scripts/verify-provenance.py "$REPORT_DIR/provenance/dockerhub.json" "$EXPECTED_REVISION"

python3 - "$REPORT_DIR" "$EXPECTED_SOURCE" "$EXPECTED_REVISION" "$EXPECTED_VERSION" "$EXPECTED_LICENSES" <<'PY'
import json
import re
import sys
from collections import Counter
from pathlib import Path

report_dir = Path(sys.argv[1])
expected_labels = {
    "org.opencontainers.image.source": sys.argv[2],
    "org.opencontainers.image.revision": sys.argv[3],
    "org.opencontainers.image.version": sys.argv[4],
    "org.opencontainers.image.licenses": sys.argv[5],
}
platforms = ("linux/amd64", "linux/arm64")
verification = report_dir / "verification"
index = json.loads((verification / "dockerhub.index.json").read_text())
images = json.loads((verification / "dockerhub.image.json").read_text())
sbom = json.loads((report_dir / "sbom/dockerhub.json").read_text())

observed = Counter()
for descriptor in index.get("manifests", []):
    platform = descriptor.get("platform") or {}
    key = f"{platform.get('os', '')}/{platform.get('architecture', '')}"
    if key in platforms:
        digest = descriptor.get("digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise SystemExit(f"invalid platform digest for {key}")
        observed[key] += 1
for platform in platforms:
    if observed[platform] != 1:
        raise SystemExit(
            f"expected exactly one {platform} descriptor, found {observed[platform]}"
        )

for platform in platforms:
    image = images.get(platform)
    if not isinstance(image, dict):
        raise SystemExit(f"missing image metadata for Docker Hub {platform}")
    labels = (
        (image.get("config") or {}).get("Labels")
        or (image.get("config") or {}).get("labels")
        or {}
    )
    for key, expected in expected_labels.items():
        if labels.get(key) != expected:
            raise SystemExit(f"label mismatch for Docker Hub {platform}: {key}")
    created = labels.get("org.opencontainers.image.created", "")
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})",
        created,
    ):
        raise SystemExit(
            f"invalid OCI creation label for Docker Hub {platform}: {created!r}"
        )
    if not sbom.get(platform):
        raise SystemExit(f"missing SBOM attestation for Docker Hub {platform}")
print("Docker Hub platform descriptors, labels, provenance, and SBOM verified")
PY

minor="${EXPECTED_VERSION%.*}"
runtime_values_output=
if ! runtime_values_output="$(python3 - "$minor" "$VERSIONS_FILE" <<'PY'
import json
import sys

data = json.load(open(sys.argv[2]))
minor = sys.argv[1]
deps = data["dependencies"]
iconv = data["runtimeContracts"]["libiconv"]
for value in (
    deps["imagick"]["version"], deps["redis"]["version"], deps["apcu"]["version"],
    iconv["implementation"], iconv["version"], iconv["package"], iconv["packageVersion"],
    iconv["ownerPath"], iconv["target"],
):
    print(value)
PY
)"; then
  echo "failed to load runtime expectations" >&2
  exit 1
fi
mapfile -t runtime_values <<< "$runtime_values_output"
[ "${#runtime_values[@]}" -eq 9 ] || { echo "failed to load runtime expectations" >&2; exit 1; }
export EXPECTED_PHP_MINOR="$minor"
export EXPECTED_IMAGICK_VERSION="${runtime_values[0]}"
export EXPECTED_REDIS_VERSION="${runtime_values[1]}"
export EXPECTED_APCU_VERSION="${runtime_values[2]}"
export EXPECTED_ICONV_IMPLEMENTATION="${runtime_values[3]}"
export EXPECTED_ICONV_VERSION="${runtime_values[4]}"
export EXPECTED_ICONV_PACKAGE="${runtime_values[5]}"
export EXPECTED_ICONV_PACKAGE_VERSION="${runtime_values[6]}"
export EXPECTED_ICONV_OWNER_PATH="${runtime_values[7]}"
export EXPECTED_ICONV_TARGET="${runtime_values[8]}"

for platform in "${EXPECTED_PLATFORMS[@]}"; do
  platform_subject="$(resolve_platform_with_retry "$dockerhub_subject" "$platform")"
  EXPECTED_PLATFORM="$platform" \
  SMOKE_REPORT_MD="$REPORT_DIR/smoke/dockerhub-${platform//\//-}.md" \
    ./scripts/smoke-test-image.sh "$platform_subject"
done

cat > "$REPORT_DIR/verification-summary.md" <<EOF
# Verified Docker Hub image

- Docker Hub: \`${dockerhub_subject}\`
- Source revision: \`${EXPECTED_REVISION}\`
- PHP version: \`${EXPECTED_VERSION}\`
- Platforms: \`${EXPECTED_PLATFORMS[*]}\`
- Gates: manifest, exact-digest platform descriptors, provenance, SBOM, OCI labels, runtime smoke
EOF
cat "$REPORT_DIR/verification-summary.md"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$REPORT_DIR/verification-summary.md" >> "$GITHUB_STEP_SUMMARY"
fi
