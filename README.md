# fpm-alpine

Custom PHP-FPM Alpine images used for WordPress / Gnuboard / Rhymix deployments.

See also: [SUPPORT.md](./SUPPORT.md), [BRANCH-AND-TAG-POLICY.md](./BRANCH-AND-TAG-POLICY.md), and [docs/ci-operations.md](./docs/ci-operations.md).

> [!IMPORTANT]
> PHP `8.0` and `8.1` images are frozen, unsupported legacy artifacts and are never rebuilt. See [SUPPORT.md](./SUPPORT.md) for the canonical lifecycle policy.
>
> The repository's current primary/default branch is **`8.5`**.
>
> For production use, **pin an explicit image tag / version branch** such as `woosungchoi/fpm-alpine:8.5` instead of relying on `latest`.

## Current branch / version map

### Version branches

| Branch | Base image | Status |
| --- | --- | --- |
| `8.0` | `php:8.0-fpm-alpine` | EOL / frozen / unsupported |
| `8.1` | `php:8.1-fpm-alpine` | EOL / frozen / unsupported |
| `8.2` | `php:8.2-fpm-alpine` | security-only |
| `8.3` | `php:8.3-fpm-alpine` | security-only |
| `8.4` | `php:8.4-fpm-alpine` | active / security support |
| `8.5` | `php:8.5-fpm-alpine` | active / security support; primary branch |

### Legacy branch

| Branch | Base image | Status |
| --- | --- | --- |
| legacy `master` | `php:7.4-fpm-alpine` | legacy / frozen / archived locally only |

## Support and branch policy

The canonical support matrix and definitions are in [SUPPORT.md](./SUPPORT.md). `8.5` is the primary/default branch. PHP 7.4 / legacy `master`, PHP 8.0, and PHP 8.1 are unsupported frozen history.

What that means in practice:

- browse and document the repo as if **`8.5` is the mainline**
- use only a supported version branch for new deployments
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

### Published manifest verification reports

Docker Hub hooks remain the publishing path for this repository. GitHub Actions now provides the visibility layer around that publish path:

- `verify-published-manifest` runs after maintained-branch pushes, on a daily schedule, and on manual dispatch.
- The workflow verifies each maintained Docker Hub tag for the expected `linux/amd64` and `linux/arm64` platforms.
- Each run writes a GitHub Actions step summary and uploads manifest report artifacts containing the observed tag digest, per-platform digests, and attestation/metadata manifest entries when present.
- Branch-push runs make the current published Docker Hub state visible from GitHub Actions. Docker Hub autobuild can still lag behind the GitHub push, so the daily/manual verification remains the source of truth for the final published state.

This keeps Docker Hub autobuild hooks in place while making the final published image state easier to inspect from GitHub Actions.

### Dependency freshness reports

`dependency-freshness` is a report-only workflow. It does not publish images or update pins automatically. It records:

- the current Dockerfile base image digest,
- the published Docker Hub tag digests covered by the workflow configuration,
- PECL latest-version observations for `imagick`, `redis`, and `apcu`, and
- whether the Alpine `gnu-libiconv` / `LD_PRELOAD` workaround is still present and should be periodically reassessed.

The workflow runs weekly and on manual dispatch, writes a GitHub Actions step summary, and uploads `freshness-reports/` artifacts for review.

This repository is maintained through version branches and lightweight verification workflows:

- `smoke-test` builds the branch Dockerfile and validates PHP/FPM runtime basics, required extensions, `ffmpeg`, `iconv`, and `Imagick` behavior.
- `verify-published-manifest` runs on a schedule and verifies the configured published Docker Hub tags.
- `dependency-freshness` produces report-only dependency/source freshness observations for maintainers.
- `branch-drift` produces report-only workflow/script/policy drift reports across configured branches.
- `branch-sync-pr` can create safe-file sync PRs from `8.5` for workflow/script/docs/test guardrails only; operational coverage does not imply lifecycle support.
- Supported branches use the documented Imagick baseline in [BRANCH-AND-TAG-POLICY.md](./BRANCH-AND-TAG-POLICY.md).
- Security reporting and supported-version policy are documented in [SECURITY.md](./SECURITY.md).

GitHub Releases are intentionally optional for this Docker image repository. The operational release contract is the explicit Docker image tag for each supported PHP version branch.

## What this image adds

Compared with the upstream PHP Alpine FPM image, this repository adds / configures:

- `ffmpeg`
- `redis` extension
- `apcu`
- `pdo`, `pdo_mysql`, `intl`
- iconv compatibility fix for Alpine
- other PHP extensions needed by the maintained app stacks

You can convert animated `gif` images to `mp4` or `webm` with `ffmpeg`.

## Imagick policy

For supported branches, this repository standardizes on:

- pinned `imagick` release: **`3.8.1`**
- install method: **PECL release tarball + `docker-php-ext-install imagick`**

Treat that as the branch matrix unless a future branch-specific exception is documented explicitly.

## Verification

For local validation after a Docker build, run:

```bash
./scripts/smoke-test-image.sh <built-image-tag>
```

This smoke test checks:

- `php -v`
- `php -m`
- `php-fpm -t`
- `imagick`, `redis`, `apcu` extension loading
- `ffmpeg` availability
- minimal `iconv` / `Imagick` runtime behavior

For a published multi-arch image, you can also inspect the manifest explicitly:

```bash
./scripts/check-manifest.sh woosungchoi/fpm-alpine:8.5
```

That manifest check verifies that both `linux/amd64` and `linux/arm64` entries are present.

A separate GitHub Actions workflow also performs scheduled/manual published-manifest checks for the maintained tags.

## Upstream base

Historically this image started from the WordPress PHP-FPM Alpine Dockerfile lineage.

Because this repository now has multiple version branches, the exact upstream base differs by branch. Check the `Dockerfile` in the branch you are using.

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
