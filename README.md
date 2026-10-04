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

GitHub Actions is the sole publisher for PHP 8.2–8.5. Docker Hub Automatic Builds and the legacy publication webhook have been removed. Source pull requests cannot access registry credentials or publish images.

The active `dependency-auto-publish.yml` publishes one eligible same-minor base update from protected `main` directly to both registry minor tags, using one multi-platform Buildx build with provenance and SBOM. Its read-back gate requires the published top-level digest and amd64/arm64 platform presence in both registries. Source, runtime-policy and workflow changes require manual review and do not automatically publish. The owner-only `fpm-manual-publish` dispatch in `publish.yml` separately creates an immutable GHCR canary and verifies its signature/runtime/scan gates; it does not update production aliases.

The repository also retains exact-subject Cosign, provenance, SBOM, semantic parity, promotion, transaction and rollback scripts, with policy/mutation tests. Those scripts' presence does not imply that every gate is executed by the active automatic publisher. This PR adds read-only periodic verification without changing publishing authority or those mutation safeguards.

PHP 8.0 and 8.1 are excluded from publication, and no `latest` tag is created.
See [docs/ci-operations.md](./docs/ci-operations.md) for the active publishing and verification paths.

### Published manifest verification reports

The daily manifest workflow observes active Docker Hub moving aliases by exact digest:

- `verify-published-manifest` runs after `main` pushes, on a schedule, and on manual dispatch.
- The workflow verifies the four active Docker Hub tags for `linux/amd64` and `linux/arm64`, and its exact-set guard rejects every additional public tag after enforcement is enabled.
- Each run writes a GitHub Actions step summary and uploads manifest report artifacts containing the observed tag digest, per-platform digests, and attestation/metadata manifest entries when present.
- It resolves each moving tag once, then retries raw reads against the frozen digest. This run verifies platform presence only; attestation entries are inventory and do not imply signature, provenance, SBOM, runtime or vulnerability validation.
- `published-runtime-smoke` separately runs weekly, on manual dispatch, and on PRs changing its verification path. It reuses `verify-published-dockerhub-image.sh`, `verify-image-parity.py` and `scan-image.sh` to reverify frozen subjects: protected-history source labels, provenance, SBOM presence, FastCGI/media runtime for amd64/arm64, APK version/architecture evidence, registry config/layer parity and fixable-CRITICAL vulnerabilities.
- The current `dependency-auto-publish.yml` builds both registries with provenance and SBOM and reads back digests/platforms; it does not sign its images. The weekly workflow therefore does not claim Cosign verification. Existing `verify-published-image.sh` and `verify-canary-image.sh` retain signature and operation gates for signed publication paths; no unsigned exception is added to those gates.
- A successful source CI run validates its locally built candidate. It is separate from a report about the already published digest; neither deploys to an application server.

### Dependency freshness and guarded update automation

`dependency-freshness` remains report-only and records:

- every exact matrix base-image digest and source dependency pin,
- the published Docker Hub tag digests covered by the workflow configuration,
- PECL latest-version observations for `imagick`, `redis`, and `apcu`, and
- the currently pinned PECL releases versus upstream observations.

The workflow runs weekly, on manual dispatch, and on PRs changing its verification path, writes a GitHub Actions step summary, and uploads `freshness-reports/` artifacts for review.

`dependency-update-pr` is controlled by `DEPENDENCY_AUTOMATION_ENABLED`. With the repository-scoped GitHub App and that variable enabled, it serializes official PHP same-minor patch/digest updates: one run opens the next eligible pull request, and a successful automatic publication starts the next discovery pass. `dependency-auto-merge` revalidates the dependency-only diff and all checks on the exact PR head before a normal squash merge. An eligible `build/versions.json`-only update on protected `main` triggers `dependency-auto-publish` for the affected minor. PECL, runtime policy, source and workflow changes require manual review.

The human-owned policy is `build/automation-policy.json`. PHP minor membership, support/EOL state, runtime contracts, workflow permissions, publisher behavior, and exception policy always require manual review.

This repository is maintained through one `main` source trunk and verification workflows:

- `smoke-test` builds the active PHP matrix from `main` and validates PHP/FPM runtime basics, required extensions, `ffmpeg`, `iconv`, and `Imagick` behavior.
- `verify-published-manifest` runs on a schedule and verifies the configured published Docker Hub tags.
- `dependency-freshness` produces report-only observations; the separate updater may open strictly classified dependency-only pull requests when explicitly enabled.
- `php-lifecycle` checks configured EOL dates daily without network access; the monthly and manual runs also check upstream release availability. Issues change only when the attention state changes.
- `published-runtime-smoke` performs the weekly/manual read-only verification described above. Each report distinguishes reverified gates from unverified signatures.
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

The supported local command uses the same validated matrix as CI, including all
base/PECL pins, PHP patch, OCI metadata and source epoch:

```bash
./scripts/build-local-image.py --minor 8.5 --platform linux/arm64 --tag fpm-alpine:8.5-local --smoke
# Use linux/amd64 on an amd64 Docker host. Inspect inputs without building:
./scripts/build-local-image.py --minor 8.5 --platform linux/arm64 --tag fpm-alpine:8.5-local --dry-run
```

Dockerfile source arguments deliberately have no duplicated defaults. An argument-free
`docker build .` fails on the missing base image; advanced callers must pass every
validated source URL/checksum and base ref. This prevents a manifest update from
silently leaving a stale local base. The smoke test compares the exact PHP patch in
both CLI and the real FPM response.

For local validation after a Docker build, run:

```bash
EXPECTED_PHP_PATCH="$(python3 -c 'import json; print(json.load(open("build/versions.json"))["versions"]["8.5"]["patch"])')" \
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

### Runtime settings and reproducibility scope

The smoke test sends FastCGI requests to the actual FPM worker in a container
with `--network none`, without publishing a host port. It checks FPM SAPI,
patch/extensions, effective ini values and OPcache availability, records observed
JIT status, and validates tiny JPEG/PNG/WebP decode/resize/encode and two-frame
GIF→H.264 MP4/VP9 WebM output (codec, dimensions, frame count). Rejected malformed
input is followed by a second successful FPM request. These fixtures do not prove
CMS compatibility, performance, or all production media formats.

The existing JIT defaults remain `opcache.jit=tracing` and
`opcache.jit_buffer_size=100M`. No CMS speedup is asserted and no default is changed
without representative latency/memory benchmarks. Service owners can mount a
late-loading `/usr/local/etc/php/conf.d/zz-service.ini`, for example:

```ini
; Select only after measuring the service workload.
opcache.jit=disable
opcache.jit_buffer_size=0
```

Use an actual FPM request to inspect effective values; CLI settings do not establish
FPM behavior. The strict smoke fixture verifies image defaults, so an image with
intentional service overrides needs expectations adapted to that service.
Removed PHP settings `opcache.fast_shutdown` and `log_errors_max_len` are omitted.

No-cache archive A/B comparison establishes reproducibility with the same available
inputs at the time of CI. Runtime contracts record exact installed APK name,
version and architecture and reject unreviewed package drift; a changed version is
not automatically called a security update. Review upstream package changelogs and
scan evidence before accepting changes. Alpine APK repositories are not snapshot
pinned, so rebuilding this source months later is not guaranteed to reproduce the
same packages/digest. Historical APK retention, hashes, snapshot policy and its
security-update process require a separate infrastructure decision; this change
creates no external preservation service.
