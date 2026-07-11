# fpm-alpine

Custom PHP-FPM Alpine images used for WordPress / Gnuboard / Rhymix deployments.

See also: [SUPPORT.md](./SUPPORT.md), [BRANCH-AND-TAG-POLICY.md](./BRANCH-AND-TAG-POLICY.md), and [docs/ci-operations.md](./docs/ci-operations.md).

> [!IMPORTANT]
> PHP `8.0` and `8.1` images are frozen, unsupported legacy artifacts and are never rebuilt. See [SUPPORT.md](./SUPPORT.md) for the canonical lifecycle policy.
>
> The repository's primary/default branch is **`main`**, the only active source trunk.
>
> For production use, **pin an explicit image tag** such as `woosungchoi/fpm-alpine:8.5` instead of relying on `latest`.

## Source and image-tag map

The single `main` source trunk builds the active PHP matrix from `build/versions.json`.

| Image tag | Base image | Status |
| --- | --- | --- |
| `8.0` | `php:8.0-fpm-alpine` | EOL / frozen / unsupported |
| `8.1` | `php:8.1-fpm-alpine` | EOL / frozen / unsupported |
| `8.2` | `php:8.2-fpm-alpine` | security-only |
| `8.3` | `php:8.3-fpm-alpine` | security-only |
| `8.4` | `php:8.4-fpm-alpine` | active / security support |
| `8.5` | `php:8.5-fpm-alpine` | active / security support |

### Archived source history

Former version-branch tips are preserved by annotated `archive/php-<minor>-final-branch` tags. Legacy `master` / PHP 7.4 history is frozen and unsupported.

## Support and branch policy

The canonical support matrix and definitions are in [SUPPORT.md](./SUPPORT.md). `main` is the only active source branch. PHP 7.4 / legacy `master`, PHP 8.0, and PHP 8.1 are unsupported frozen history.

What that means in practice:

- start source changes from **`main`**
- use only a supported explicit image tag for new deployments
- do **not** start new work from the legacy PHP 7.4 branch history
- do **not** expect PHP 7.4 fixes or refreshes going forward

## Docker tags / Docker Hub notes

- PHP 8.0 and 8.1 tags are retained but never rebuilt
- Explicit version tags remain the safest production contract
- No `latest` tag is intentionally published

Safe rule for production use:

- **use an explicit major/minor image tag**
- **read the branch you plan to use**, not just a cached registry or UI default view

For the full policy and operational notes, see [BRANCH-AND-TAG-POLICY.md](./BRANCH-AND-TAG-POLICY.md).

## Maintenance and security status

### Manual-only GitHub Actions publisher

GitHub Actions is the sole publisher for PHP 8.2–8.5. Docker Hub Automatic Builds and the legacy publication webhook have been removed. The publisher remains manual-only so source merges cannot access registry credentials or publish images.

- Canary tags are non-moving per workflow attempt: `canary-<minor>-<run-id>-<run-attempt>` on Docker Hub and GHCR. Existing canary tags are rejected before push.
- Production promotion requires one explicit PHP minor per dispatch, downloaded and content-validated evidence from the immediately preceding PHP 8.5 canary and every current 8.2–8.5 canary artifact, repository variable `LEGACY_PUBLISHER_DISABLED=true`, explicit dispatch input `legacy_publisher_disabled=true`, and a matching fresh 15-minute cutover-evidence hash proving inactive build rule, strict integer zero in-flight builds, and absent webhook. The lease is revalidated immediately before bootstrap creation and production promotion. It re-tags the verified full-matrix canary digest without rebuilding, so a failed run cannot leave a partially updated multi-minor release or race an enabled legacy publisher.
- Release and source tags include the full verified digest (`<patch>-<date>-<digest64>` and `sha-<minor>-<commit12>-<digest64>`) so different content cannot claim the same immutable tag name.
- Every publisher subject is checked by exact digest for amd64/arm64 manifests, runtime behavior, BuildKit SBOM/provenance, keyless Cosign signatures, Trivy fixable-CRITICAL findings, and cross-registry semantic parity.
- PHP 8.0 and 8.1 are excluded from publication, and no `latest` tag is created.

See [docs/ci-operations.md](./docs/ci-operations.md) for dispatch, verification, promotion, and rollback gates.

### Published manifest verification reports

GitHub Actions publishes to Docker Hub and GHCR and verifies both registries by exact digest:

- `verify-published-manifest` runs after `main` pushes, on a schedule, and on manual dispatch.
- The workflow verifies each maintained Docker Hub tag for the expected `linux/amd64` and `linux/arm64` platforms.
- Each run writes a GitHub Actions step summary and uploads manifest report artifacts containing the observed tag digest, per-platform digests, and attestation/metadata manifest entries when present.
- Scheduled/manual verification remains the source of truth for the final published state.

### Dependency freshness reports

`dependency-freshness` validates and reads `build/versions.json`; it is a report-only workflow and does not publish images or update pins automatically. It records:

- every exact matrix base-image digest and source dependency pin,
- the published Docker Hub tag digests covered by the workflow configuration,
- PECL latest-version observations for `imagick`, `redis`, and `apcu`, and
- the currently pinned PECL releases versus upstream observations.

The workflow runs weekly and on manual dispatch, writes a GitHub Actions step summary, and uploads `freshness-reports/` artifacts for review.

This repository is maintained through one `main` source trunk and verification workflows:

- `smoke-test` builds the active PHP matrix from `main` and validates PHP/FPM runtime basics, required extensions, `ffmpeg`, `iconv`, and `Imagick` behavior.
- `verify-published-manifest` runs on a schedule and verifies the configured published Docker Hub tags.
- `dependency-freshness` produces report-only dependency/source freshness observations for maintainers.
- `php-lifecycle` checks the active matrix monthly against configured EOL dates and upstream PHP release availability.
- `published-runtime-smoke` verifies Docker Hub/GHCR manifests, provenance, SBOM, signatures, runtime behavior, and semantic parity weekly and after a successful production publisher run.
- Dependabot proposes reviewed full-SHA GitHub Actions updates while source dependencies remain report-only.
- Active matrix entries use the documented Imagick baseline in [BRANCH-AND-TAG-POLICY.md](./BRANCH-AND-TAG-POLICY.md).
- Security reporting and supported-version policy are documented in [SECURITY.md](./SECURITY.md).

GitHub Releases are intentionally optional for this Docker image repository. The operational release contract is the explicit Docker image tag for each supported PHP minor.

## What this image adds

Compared with the upstream PHP Alpine FPM image, this repository adds / configures:

- `ffmpeg`
- `redis` extension
- `apcu`
- `pdo`, `pdo_mysql`, `intl`
- official pinned-base `gnu-libiconv-libs=1.18-r0` runtime, with exact package ownership and `libiconv.so.2` target validation
- other PHP extensions needed by the maintained app stacks

You can convert animated `gif` images to `mp4` or `webm` with `ffmpeg`.

## Imagick policy

For supported branches, this repository standardizes on:

- pinned `imagick` release: **`3.8.1`**
- install method: **PECL release tarball + `docker-php-ext-install imagick`**

Treat that as the branch matrix unless a future branch-specific exception is documented explicitly.

## Verification

`build/versions.json` is the canonical machine-readable build and matrix input for
the supported PHP 8.2–8.5 patch versions, lifecycle metadata (`support`/`eol`),
digest-pinned base images, and verified source archives. Independently,
`scripts/validate-versions.py` and literal policy fixtures enforce the approved
pin and lifecycle baseline, so an intentional update requires coordinated JSON,
validator, and test approval changes. The `smoke-test` workflow validates that file, derives its
PHP/platform matrix from it, and only builds and runs local CI images; it does
not log in to a registry or publish images.

To select a version locally, pass its exact `base_image` value as
`PHP_BASE_IMAGE`. Validate all pins before building:

```bash
./scripts/validate-versions.py
docker build \
  --build-arg PHP_BASE_IMAGE="$(./scripts/validate-versions.py --get-base 8.5)" \
  --build-arg OCI_SOURCE="https://github.com/woosungchoi/fpm-alpine" \
  --build-arg OCI_REVISION="$(git rev-parse HEAD)" \
  --build-arg OCI_VERSION="8.5.8" \
  --build-arg OCI_CREATED="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
  -t fpm-alpine:8.5-local .
```

For local validation after a Docker build, run:

```bash
EXPECTED_IMAGICK_VERSION=3.8.1 EXPECTED_REDIS_VERSION=6.3.0 EXPECTED_APCU_VERSION=5.1.28 \
EXPECTED_ICONV_IMPLEMENTATION=libiconv EXPECTED_ICONV_VERSION=1.18 EXPECTED_ICONV_PACKAGE=gnu-libiconv-libs EXPECTED_ICONV_PACKAGE_VERSION=1.18-r0 EXPECTED_ICONV_OWNER_PATH=/usr/lib/libiconv.so.2 EXPECTED_ICONV_TARGET=/usr/lib/libiconv.so.2.7.0 \
  ./scripts/smoke-test-image.sh <built-image-tag> [expected-php-minor] [expected-platform]
```

This smoke test checks:

- `php -v`
- `php -m`
- `php-fpm -t`
- `imagick`, `redis`, `apcu` extension loading
- `ffmpeg` availability
- exact official-base iconv implementation/version/package/owner/target contract, transliteration, and `Imagick` runtime behavior

For a published multi-arch image, you can also inspect the manifest explicitly:

```bash
./scripts/check-manifest.sh woosungchoi/fpm-alpine:8.5
```

That manifest check verifies that both `linux/amd64` and `linux/arm64` entries are present.

A separate GitHub Actions workflow also performs scheduled/manual published-manifest checks for the maintained tags.

## Upstream base

Historically this image started from the WordPress PHP-FPM Alpine Dockerfile lineage.

The exact upstream base differs by active matrix entry. Check `build/versions.json` and the `main` Dockerfile.

## Repositories where this image is used

### docker-wordpress

Source: <https://github.com/woosungchoi/docker-wordpress>

Clean WordPress CMS + Docker (development & production)

### docker-gnuboard

Source: <https://github.com/woosungchoi/docker-gnuboard>

Clean Gnuboard CMS + Docker (development & production)

### docker-rhymix

Source: <https://github.com/woosungchoi/docker-rhymix>

Clean Rhymix CMS + Docker (development & production)

### docker-multi-site

Source: <https://github.com/woosungchoi/docker-multi-site>

Docker with WordPress, Gnuboard, Rhymix
