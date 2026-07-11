#!/usr/bin/env bash
set -euo pipefail

DOCKERHUB_REF="${1:-}"
GHCR_REF="${2:-}"
EXPECTED_REVISION="${3:-}"
EXPECTED_VERSION="${4:-}"
REPORT_DIR="${5:-publisher-reports}"
EXPECTED_SOURCE="${EXPECTED_SOURCE:-https://github.com/woosungchoi/fpm-alpine}"
EXPECTED_LICENSES="${EXPECTED_LICENSES:-GPL-2.0-only}"
COSIGN_CERTIFICATE_IDENTITY_REGEXP="${COSIGN_CERTIFICATE_IDENTITY_REGEXP:-^https://github.com/woosungchoi/fpm-alpine/.github/workflows/publish.yml@refs/heads/8\.5$}"
COSIGN_CERTIFICATE_OIDC_ISSUER="${COSIGN_CERTIFICATE_OIDC_ISSUER:-https://token.actions.githubusercontent.com}"
EXPECTED_PLATFORMS=(linux/amd64 linux/arm64)

if [ -z "$DOCKERHUB_REF" ] || [ -z "$GHCR_REF" ] || [ -z "$EXPECTED_VERSION" ]; then
  echo "usage: $0 <dockerhub-ref> <ghcr-ref> <40-char-source-sha> <php-patch> [report-dir]" >&2
  exit 64
fi
[[ "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo "expected revision must be an exact lowercase commit SHA" >&2; exit 64; }
[[ "$EXPECTED_VERSION" =~ ^8\.[2-5]\.[0-9]+$ ]] || { echo "expected version must be an active PHP patch" >&2; exit 64; }
for command in docker cosign python3; do
  command -v "$command" >/dev/null 2>&1 || { echo "$command is required" >&2; exit 69; }
done

mkdir -p "$REPORT_DIR/verification" "$REPORT_DIR/provenance" "$REPORT_DIR/sbom" "$REPORT_DIR/smoke"

resolve_digest() {
  "$(dirname "$0")/resolve-image-digest.sh" "$1"
}

repository_from_ref() {
  local ref="$1"
  if [[ "$ref" == *@* ]]; then
    printf '%s\n' "${ref%@*}"
  else
    printf '%s\n' "${ref%:*}"
  fi
}

inspect_subject() {
  local name="$1" ref="$2" digest="$3" repository
  local prefix="$REPORT_DIR/verification/$name"
  repository="$(repository_from_ref "$ref")"
  docker buildx imagetools inspect --raw "${repository}@${digest}" > "${prefix}.index.json"
  docker buildx imagetools inspect "${repository}@${digest}" --format '{{ json .Image }}' > "${prefix}.image.json"
  docker buildx imagetools inspect "${repository}@${digest}" --format '{{ json .Provenance }}' > "$REPORT_DIR/provenance/${name}.json"
  docker buildx imagetools inspect "${repository}@${digest}" --format '{{ json .SBOM }}' > "$REPORT_DIR/sbom/${name}.json"

  python3 - "$prefix" "$repository" "${EXPECTED_PLATFORMS[@]}" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

prefix, image_ref, *expected = sys.argv[1:]
index = json.loads(Path(prefix + ".index.json").read_text())
descriptors = {}
for item in index.get("manifests", []):
    platform = item.get("platform") or {}
    key = f"{platform.get('os', '')}/{platform.get('architecture', '')}"
    if key in expected:
        descriptors[key] = item["digest"]
missing = sorted(set(expected) - set(descriptors))
if missing:
    raise SystemExit(f"missing runtime platform descriptors for {image_ref}: {', '.join(missing)}")
for platform, digest in descriptors.items():
    output = subprocess.check_output(
        ["docker", "buildx", "imagetools", "inspect", "--raw", f"{image_ref}@{digest}"],
        text=True,
    )
    Path(prefix + "." + platform.replace("/", "_") + ".manifest.json").write_text(output)
PY
}

PUBLISHER_MODE=github-actions MANIFEST_REPORT_DIR="$REPORT_DIR/manifests" \
  ./scripts/report-manifest.sh "$DOCKERHUB_REF" "${EXPECTED_PLATFORMS[@]}"
PUBLISHER_MODE=github-actions MANIFEST_REPORT_DIR="$REPORT_DIR/manifests" \
  ./scripts/report-manifest.sh "$GHCR_REF" "${EXPECTED_PLATFORMS[@]}"

dockerhub_digest="$(resolve_digest "$DOCKERHUB_REF")"
ghcr_digest="$(resolve_digest "$GHCR_REF")"
[[ "$dockerhub_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "failed to resolve Docker Hub digest" >&2; exit 1; }
[[ "$ghcr_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "failed to resolve GHCR digest" >&2; exit 1; }

inspect_subject dockerhub "$DOCKERHUB_REF" "$dockerhub_digest"
inspect_subject ghcr "$GHCR_REF" "$ghcr_digest"

./scripts/verify-provenance.py "$REPORT_DIR/provenance/dockerhub.json" "$EXPECTED_REVISION"
./scripts/verify-provenance.py "$REPORT_DIR/provenance/ghcr.json" "$EXPECTED_REVISION"

python3 - "$REPORT_DIR" "$EXPECTED_SOURCE" "$EXPECTED_REVISION" "$EXPECTED_VERSION" "$EXPECTED_LICENSES" <<'PY'
import json
import re
import sys
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

def load(name: str, suffix: str):
    return json.loads((verification / f"{name}.{suffix}").read_text())

# Semantic parity intentionally compares each platform config.digest and ordered
# layer digests; registries may normalize the top-level index differently.
for platform in platforms:
    suffix = platform.replace("/", "_") + ".manifest.json"
    left = load("dockerhub", suffix)
    right = load("ghcr", suffix)
    if left.get("config", {}).get("digest") != right.get("config", {}).get("digest"):
        raise SystemExit(f"config.digest parity failed for {platform}")
    left_layers = [item.get("digest") for item in left.get("layers", [])]
    right_layers = [item.get("digest") for item in right.get("layers", [])]
    if left_layers != right_layers:
        raise SystemExit(f"ordered layer digests parity failed for {platform}")

for registry in ("dockerhub", "ghcr"):
    images = load(registry, "image.json")
    sbom = json.loads((report_dir / "sbom" / f"{registry}.json").read_text())
    for platform in platforms:
        image = images.get(platform)
        if not isinstance(image, dict):
            raise SystemExit(f"missing image metadata for {registry} {platform}")
        labels = (image.get("config") or {}).get("Labels") or (image.get("config") or {}).get("labels") or {}
        for key, expected in expected_labels.items():
            if labels.get(key) != expected:
                raise SystemExit(f"label mismatch for {registry} {platform}: {key}")
        created = labels.get("org.opencontainers.image.created", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created):
            raise SystemExit(f"invalid OCI creation label for {registry} {platform}: {created!r}")
        if not sbom.get(platform):
            raise SystemExit(f"missing SBOM attestation for {registry} {platform}")
print("cross-registry platform config/layer/label parity and SBOM verified")
PY

for entry in "$DOCKERHUB_REF|$dockerhub_digest" "$GHCR_REF|$ghcr_digest"; do
  IFS='|' read -r ref digest <<< "$entry"
  subject="$(repository_from_ref "$ref")@${digest}"
  cosign verify \
    --certificate-identity-regexp "$COSIGN_CERTIFICATE_IDENTITY_REGEXP" \
    --certificate-oidc-issuer "$COSIGN_CERTIFICATE_OIDC_ISSUER" \
    "$subject" >/dev/null
  echo "Cosign signature verified: $subject"
done

minor="${EXPECTED_VERSION%.*}"
mapfile -t runtime_values < <(python3 - "$minor" <<'PY'
import json
import sys

data = json.load(open("build/versions.json"))
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
)
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

for entry in "dockerhub|$DOCKERHUB_REF|$dockerhub_digest" "ghcr|$GHCR_REF|$ghcr_digest"; do
  IFS='|' read -r registry ref digest <<< "$entry"
  repository="$(repository_from_ref "$ref")"
  for platform in "${EXPECTED_PLATFORMS[@]}"; do
    EXPECTED_PLATFORM="$platform" \
    SMOKE_REPORT_MD="$REPORT_DIR/smoke/${registry}-${platform//\//-}.md" \
      ./scripts/smoke-test-image.sh "${repository}@${digest}"
  done
done

dockerhub_subject="$(repository_from_ref "$DOCKERHUB_REF")@${dockerhub_digest}"
ghcr_subject="$(repository_from_ref "$GHCR_REF")@${ghcr_digest}"
cat > "$REPORT_DIR/verification-summary.md" <<EOF
# Verified multi-registry image

- Docker Hub: \`${dockerhub_subject}\`
- GHCR: \`${ghcr_subject}\`
- Source revision: \`${EXPECTED_REVISION}\`
- PHP version: \`${EXPECTED_VERSION}\`
- Platforms: \`${EXPECTED_PLATFORMS[*]}\`
- Gates: manifest, provenance, SBOM, signature, runtime smoke, semantic cross-registry parity
EOF
cat "$REPORT_DIR/verification-summary.md"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$REPORT_DIR/verification-summary.md" >> "$GITHUB_STEP_SUMMARY"
fi
