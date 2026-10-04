#!/usr/bin/env bash
set -euo pipefail
minor="${1:?active PHP minor required}"
[[ "$minor" =~ ^8\.[2-5]$ ]] || exit 64
report="${2:-published-runtime-reports/$minor}"
mkdir -p "$report"
dockerhub_repository=docker.io/woosungchoi/fpm-alpine
ghcr_repository=ghcr.io/woosungchoi/fpm-alpine
# Freeze each moving tag exactly once. All subsequent reads use these subjects.
dockerhub_digest="$(./scripts/resolve-image-digest.sh "$dockerhub_repository:$minor")"
ghcr_digest="$(./scripts/resolve-image-digest.sh "$ghcr_repository:$minor")"
dockerhub_subject="$dockerhub_repository@$dockerhub_digest"
ghcr_subject="$ghcr_repository@$ghcr_digest"
# Labels are untrusted discovery inputs. Provenance is validated against the
# exact source in protected main history before runtime verification succeeds.
docker buildx imagetools inspect "$dockerhub_subject" --format '{{ json .Image }}' |
  python3 -c 'import json,sys; data=json.load(sys.stdin); print(json.dumps({p:(v.get("config",{}).get("Labels") or v.get("config",{}).get("labels") or {}) for p,v in data.items()}))' \
  > "$report/labels.json"
metadata="$(python3 - "$report/labels.json" "$minor" <<'PY'
import json,re,sys
labels=json.load(open(sys.argv[1])); minor=sys.argv[2]
rows=[labels.get(p,{}) for p in ('linux/amd64','linux/arm64')]
revision=rows[0].get('org.opencontainers.image.revision','')
patch=rows[0].get('org.opencontainers.image.version','')
if not re.fullmatch('[0-9a-f]{40}',revision) or not re.fullmatch(re.escape(minor)+r'\.[0-9]+',patch):
    raise SystemExit('invalid published source/version labels')
for row in rows:
    if row.get('org.opencontainers.image.revision') != revision or row.get('org.opencontainers.image.version') != patch:
        raise SystemExit('platform source/version labels disagree')
print(revision); print(patch)
PY
)"
mapfile -t values <<< "$metadata"
revision="${values[0]}"; patch="${values[1]}"
git cat-file -e "${revision}^{commit}"
git merge-base --is-ancestor "$revision" HEAD
git show "$revision:build/versions.json" > "$report/source-versions.json"
python3 - "$report/source-versions.json" "$minor" "$patch" <<'PY'
import json,sys
source=json.load(open(sys.argv[1]))
if source['versions'][sys.argv[2]]['patch'] != sys.argv[3]:
    raise SystemExit('published patch does not match its source manifest')
PY
AUTO_PROMOTION_VERSIONS_FILE="$report/source-versions.json" \
  ./scripts/verify-published-dockerhub-image.sh "$dockerhub_subject" "$revision" "$patch" "$report/dockerhub"
./scripts/verify-image-parity.py "$dockerhub_subject" "$ghcr_subject" --output "$report/parity.json"
for platform in linux/amd64 linux/arm64; do
  ./scripts/scan-image.sh "$dockerhub_repository" "$dockerhub_digest" "$report/scans" "$platform"
  ./scripts/scan-image.sh "$ghcr_repository" "$ghcr_digest" "$report/scans" "$platform"
  platform_subject="$(./scripts/resolve-platform-image.py "$dockerhub_subject" "$platform")"
  ./scripts/capture-image-contract.sh "$platform_subject" "$platform" "$report/packages-${platform#linux/}.json"
done
cat > "$report/scope.md" <<EOF
# Published runtime verification scope

- Docker Hub: \`$dockerhub_subject\`
- GHCR: \`$ghcr_subject\`
- Published source: \`$revision\`; patch: \`$patch\`
- Reverified in this run: exact-subject manifest, source-bound provenance, SBOM presence, OCI labels, FastCGI/media runtime (amd64/arm64), APK versions/architectures, semantic registry parity and fixable-CRITICAL scan.
- Cosign signature verification is not part of this workflow. Current dependency-auto-publish does not create signatures; existing signed-subject verification scripts remain available for signed publication paths.
- No registry mutation or operational deployment occurs.
EOF
cat "$report/scope.md"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat "$report/scope.md" >> "$GITHUB_STEP_SUMMARY"; fi
