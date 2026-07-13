#!/usr/bin/env bash
set -euo pipefail

GHCR_SUBJECT="${1:-}"
EXPECTED_REVISION="${2:-}"
EXPECTED_VERSION="${3:-}"
REPORT_DIR="${4:-publisher-reports}"
EXPECTED_SIGNING_REF="${5:-main}"
EXPECTED_SOURCE="${EXPECTED_SOURCE:-https://github.com/woosungchoi/fpm-alpine}"
EXPECTED_LICENSES="${EXPECTED_LICENSES:-GPL-2.0-only}"
OIDC_ISSUER="${COSIGN_CERTIFICATE_OIDC_ISSUER:-https://token.actions.githubusercontent.com}"
PLATFORMS=(linux/amd64 linux/arm64)

[[ "$GHCR_SUBJECT" =~ ^ghcr\.io/woosungchoi/fpm-alpine@sha256:[0-9a-f]{64}$ ]] || {
  echo "usage: $0 <ghcr-subject@sha256:digest> <40-char-source-sha> <php-patch> [report-dir] [main|8.5]" >&2
  exit 64
}
[[ "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo "expected revision must be exact" >&2; exit 64; }
[[ "$EXPECTED_VERSION" =~ ^8\.[2-5]\.[0-9]+$ ]] || { echo "expected version must be active" >&2; exit 64; }
case "$EXPECTED_SIGNING_REF" in main|8.5) ;; *) echo "invalid signing ref" >&2; exit 64 ;; esac
identity="^https://github.com/woosungchoi/fpm-alpine/.github/workflows/publish.yml@refs/heads/${EXPECTED_SIGNING_REF}$"

mkdir -p "$REPORT_DIR/manifests" "$REPORT_DIR/verification" "$REPORT_DIR/provenance" "$REPORT_DIR/sbom" "$REPORT_DIR/smoke"
PUBLISHER_MODE=github-actions MANIFEST_REPORT_DIR="$REPORT_DIR/manifests" \
  ./scripts/report-manifest.sh "$GHCR_SUBJECT" "${PLATFORMS[@]}"
docker buildx imagetools inspect "$GHCR_SUBJECT" --format '{{ json .Image }}' > "$REPORT_DIR/verification/ghcr.image.json"
docker buildx imagetools inspect "$GHCR_SUBJECT" --format '{{ json .Provenance }}' > "$REPORT_DIR/provenance/ghcr.json"
docker buildx imagetools inspect "$GHCR_SUBJECT" --format '{{ json .SBOM }}' > "$REPORT_DIR/sbom/ghcr.json"
./scripts/verify-provenance.py "$REPORT_DIR/provenance/ghcr.json" "$EXPECTED_REVISION"

python3 - "$REPORT_DIR" "$EXPECTED_SOURCE" "$EXPECTED_REVISION" "$EXPECTED_VERSION" "$EXPECTED_LICENSES" <<'PY'
import json
import re
import sys
from pathlib import Path
root = Path(sys.argv[1])
expected = {
    "org.opencontainers.image.source": sys.argv[2],
    "org.opencontainers.image.revision": sys.argv[3],
    "org.opencontainers.image.version": sys.argv[4],
    "org.opencontainers.image.licenses": sys.argv[5],
}
images = json.loads((root / "verification/ghcr.image.json").read_text())
sbom = json.loads((root / "sbom/ghcr.json").read_text())
for platform in ("linux/amd64", "linux/arm64"):
    image = images.get(platform)
    if not isinstance(image, dict):
        raise SystemExit(f"missing image metadata: {platform}")
    labels = (image.get("config") or {}).get("Labels") or (image.get("config") or {}).get("labels") or {}
    for key, value in expected.items():
        if labels.get(key) != value:
            raise SystemExit(f"label mismatch for {platform}: {key}")
    created = labels.get("org.opencontainers.image.created", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", created):
        raise SystemExit(f"invalid OCI creation label: {platform}: {created!r}")
    if not sbom.get(platform):
        raise SystemExit(f"missing SBOM attestation: {platform}")
PY

cosign verify \
  --certificate-identity-regexp "$identity" \
  --certificate-oidc-issuer "$OIDC_ISSUER" \
  "$GHCR_SUBJECT" >/dev/null

minor="${EXPECTED_VERSION%.*}"
mapfile -t runtime_values < <(python3 - "$minor" <<'PY'
import json
import sys
data = json.load(open("build/versions.json"))
deps = data["dependencies"]
iconv = data["runtimeContracts"]["libiconv"]
for value in (
    deps["imagick"]["version"], deps["redis"]["version"], deps["apcu"]["version"],
    iconv["implementation"], iconv["version"], iconv["package"], iconv["packageVersion"],
    iconv["ownerPath"], iconv["target"],
): print(value)
PY
)
[ "${#runtime_values[@]}" -eq 9 ] || exit 1
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
for platform in "${PLATFORMS[@]}"; do
  platform_subject="$(./scripts/resolve-platform-image.py "$GHCR_SUBJECT" "$platform")"
  EXPECTED_PLATFORM="$platform" SMOKE_REPORT_MD="$REPORT_DIR/smoke/ghcr-${platform//\//-}.md" \
    ./scripts/smoke-test-image.sh "$platform_subject"
done
cat > "$REPORT_DIR/verification-summary.md" <<EOF
# Verified GHCR canary subject

- GHCR: \`${GHCR_SUBJECT}\`
- Source revision: \`${EXPECTED_REVISION}\`
- PHP version: \`${EXPECTED_VERSION}\`
- Platforms: \`${PLATFORMS[*]}\`
- Gates: manifest, provenance, SBOM, signature, runtime smoke
EOF
cat "$REPORT_DIR/verification-summary.md"
