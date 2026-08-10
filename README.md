# fpm-alpine

Custom PHP-FPM Alpine images used for WordPress / Gnuboard / Rhymix deployments.

See also: [SUPPORT.md](./SUPPORT.md), [BRANCH-AND-TAG-POLICY.md](./BRANCH-AND-TAG-POLICY.md), and [docs/ci-operations.md](./docs/ci-operations.md).

> [!IMPORTANT]
> PHP `8.0` and `8.1` are frozen, unsupported history and their former Docker Hub tags are no longer published. See [SUPPORT.md](./SUPPORT.md) for the canonical lifecycle policy.
>
> The repository's primary/default branch is **`main`**, the only active source trunk.
>
> For production use, **pin an explicit image tag** such as `woosungchoi/fpm-alpine:8.5` instead of relying on `latest`.

## Source and image-tag map

The single `main` source trunk builds the active PHP matrix from `build/versions.json`.

| Image tag | Base image | Status |
| --- | --- | --- |
| `8.0` | historical `php:8.0-fpm-alpine` | EOL / frozen / not published on Docker Hub |
| `8.1` | historical `php:8.1-fpm-alpine` | EOL / frozen / not published on Docker Hub |
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

- Docker Hub exposes exactly `8.2`, `8.3`, `8.4`, and `8.5`
- Explicit active-minor tags or resolved digests remain the production contract
- No `latest`, canary, immutable, source, frozen, or legacy tag is published on Docker Hub
- GHCR retains non-moving canary, immutable, provenance, signature, archive, and rollback evidence

Safe rule for production use:

- **use an explicit major/minor image tag**
- **read the branch you plan to use**, not just a cached registry or UI default view

For the full policy and operational notes, see [BRANCH-AND-TAG-POLICY.md](./BRANCH-AND-TAG-POLICY.md).

## Maintenance and security status

### Protected GitHub Actions publisher

GitHub Actions is the sole publisher for PHP 8.2–8.5. Docker Hub Automatic Builds and the legacy publication webhook have been removed. Manual releases use the owner-only typed `fpm-manual-publish` `repository_dispatch`, so GitHub always loads `publish.yml` from protected default-branch `main`; eligible dependency-only changes on protected `main` use a separate unattended controller that cannot pass its mutation preflight until the current Docker Hub and GHCR subjects have semantic baseline parity. Automatic Docker Hub credentials and moving mutation additionally require a fresh owner-dispatched 15-minute lease bound to the exact current `main` SHA; live capture verifies disabled Docker Hub automation and the exact privacy-redacted nonpublisher hook allowlist. Source pull requests cannot access registry credentials or publish images.

- Canary tags are GHCR-only and non-moving per workflow attempt: `canary-<minor>-<run-id>-<run-attempt>`. Existing GHCR canary tags are rejected before push.
- Production promotion requires one explicit PHP minor in the strictly validated dispatch payload, downloaded and content-validated GHCR evidence from two consecutive canary runs, repository variable `LEGACY_PUBLISHER_DISABLED=true`, payload attestation `legacy_publisher_disabled=true`, and a matching fresh 15-minute cutover-evidence hash. It promotes the verified GHCR subject without rebuilding, writes GHCR moving and immutable evidence tags, and writes only the selected moving alias to Docker Hub.
- GHCR release and source tags include the full verified digest (`<patch>-<date>-<digest64>` and `sha-<minor>-<commit12>-<digest64>`) so different content cannot claim the same immutable tag name.
- Every publisher subject is checked by its registry-specific exact digest for amd64/arm64 manifests, runtime behavior, BuildKit SBOM/provenance, keyless Cosign signatures, Trivy fixable-CRITICAL findings, and cross-registry semantic parity. Top-level Docker Hub and GHCR index digests are never assumed to be interchangeable.
- GHCR backfill uses a typed default-branch `repository_dispatch`, requires the requested source commit's `build/versions.json` to equal the trusted release manifest, copies already-verified Docker Hub exact subjects without rebuilding, and performs no Docker Hub login, alias write, or signature write. Legacy Docker Hub source subjects may predate Cosign; their trust gate is exact provenance, SBOM, labels, platform/runtime contract, and immutable digest binding. The copied GHCR canary and promoted GHCR subject are signed with an authenticated `fpm.operation=backfill-ghcr` annotation and the pinned automatic-workflow identity. A frozen SHA-bound JSON plan and no-clobber GHCR rollback pins precede moving mutation; the Docker Hub disaster fallback pin must preserve the exact original Docker Hub top-level digest or automatic promotion stops before moving aliases.
- A separate default-branch recovery controller accepts only failed, cancelled, or timed-out publisher runs plus their exact attempt and plan SHA, and rejects recovery after any later successful trusted publisher run or rerun attempt. A successful transaction uploads a three-file immutable committed receipt immediately; if the wrapper run later fails, recovery validates that exact receipt and records a no-op instead of rolling back already-verified publication. Otherwise it classifies every alias before writing, refuses all recovery mutation if any alias is unknown, attempts registry-independent compensation for known targets, and requires a final full-set baseline read-back. Backfill recovery does not use Docker Hub credentials.
- PHP 8.0 and 8.1 are excluded from publication, and no `latest` tag is created.

See [docs/ci-operations.md](./docs/ci-operations.md) for dispatch, verification, promotion, and rollback gates.

### Published manifest verification reports

GitHub Actions verifies active Docker Hub moving aliases and their GHCR evidence subjects by exact digest:

- `verify-published-manifest` runs after `main` pushes, on a schedule, and on manual dispatch.
- The workflow verifies the four active Docker Hub tags for `linux/amd64` and `linux/arm64`, and its exact-set guard rejects every additional public tag after enforcement is enabled.
- Each run writes a GitHub Actions step summary and uploads manifest report artifacts containing the observed tag digest, per-platform digests, and attestation/metadata manifest entries when present.
- Scheduled/manual verification remains the source of truth for the final published state.

### Dependency freshness and guarded update automation

`dependency-freshness` remains report-only and records:

- every exact matrix base-image digest and source dependency pin,
- the published Docker Hub tag digests covered by the workflow configuration,
- PECL latest-version observations for `imagick`, `redis`, and `apcu`, and
- the currently pinned PECL releases versus upstream observations.

The workflow runs weekly and on manual dispatch, writes a GitHub Actions step summary, and uploads `freshness-reports/` artifacts for review.

`dependency-update-pr` is a disabled-by-default updater. With the repository-scoped GitHub App and `DEPENDENCY_AUTOMATION_ENABLED=true`, it opens isolated pull requests for official PHP same-minor patch/digest updates and PECL patch updates. After the required `docker-smoke` workflow succeeds, `dependency-auto-merge` revalidates the exact dependency-only diff and requests GitHub native auto-merge. When that PR changes `build/versions.json` on protected `main`, `dependency-auto-publish` builds PHP 8.2, 8.3, 8.4, and 8.5 for `linux/amd64` and `linux/arm64` as non-moving GHCR canaries. Every canary must pass provenance, SBOM, OCI-label, Cosign, anonymous amd64/arm64 runtime, full runtime-contract, and Trivy gates before the single protected-main controller updates the Docker Hub and GHCR minor aliases with aggregate rollback. Its JSON plan records registry-specific baselines and targets; the actual Docker Hub destination digest is captured only after the cross-registry copy.

The human-owned policy is `build/automation-policy.json`. PHP minor membership, support/EOL state, runtime contracts, workflow permissions, publisher behavior, and exception policy always require manual review.

This repository is maintained through one `main` source trunk and verification workflows:

- `smoke-test` builds the active PHP matrix from `main` and validates PHP/FPM runtime basics, required extensions, `ffmpeg`, `iconv`, and `Imagick` behavior.
- `verify-published-manifest` runs on a schedule and verifies the configured published Docker Hub tags.
- `dependency-freshness` produces report-only observations; the separate updater may open strictly classified dependency-only pull requests when explicitly enabled.
- `php-lifecycle` checks the active matrix monthly against configured EOL dates and upstream PHP release availability.
- `published-runtime-smoke` uses the exact registry-specific subjects from a successful dependency publisher's transaction artifact. Scheduled/manual checks resolve each moving tag once and verify the GHCR exact subject's signed operation annotation before selecting the signer identity or allowing the backfill-only unsigned Docker Hub exception. Manual publication performs anonymous exact-subject verification inside its own compensated mutation step rather than attributing the whole active matrix to one manual workflow run. All paths propagate immutable subjects through manifest, provenance, SBOM, Cosign, runtime, semantic-parity, and vulnerability verification and accept only the exact authorized `publish.yml` or `dependency-auto-publish.yml` signer identity.
- Dependabot proposes full-SHA GitHub Actions updates, and the repository-scoped updater may propose strictly classified PHP base or PECL patch updates.
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
`build/automation-policy.json`, `scripts/validate-versions.py`, and mutation tests enforce
the lifecycle, source-host, runtime-contract, and allowed-bump boundaries without duplicating
mutable patch pins in validator code. The `smoke-test` workflow validates those files, derives its
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

## License and attribution

Repository source is licensed under GPL-2.0-only. See the canonical [LICENSE](./LICENSE) text and [NOTICE.md](./NOTICE.md) for upstream attribution and retained third-party license information.

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
