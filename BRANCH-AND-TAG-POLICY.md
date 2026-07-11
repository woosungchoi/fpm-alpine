# Branch and tag policy

This document defines branch and tag behavior. [SUPPORT.md](./SUPPORT.md) is the canonical source for version lifecycle status and EOL dates.

## Branch roles

- `main` is the only active source branch and the GitHub default branch.
- Annotated `archive/php-<minor>-final-branch` tags preserve former version-branch tips.
- A branch or tag existing in the repository or registry does not by itself mean that the line is supported.
- PHP 7.4 / former `master` is frozen, unsupported history; the remote `master` branch has been removed.

Support, security-only, frozen, and unsupported status is defined only in [SUPPORT.md](./SUPPORT.md). In particular, the retained `8.0` and `8.1` tags are unsupported and never rebuilt.

## Docker Hub tag policy

- Production users should pin an explicit tag such as `woosungchoi/fpm-alpine:8.5`.
- There is intentionally no `latest` tag. Consumers must select a PHP line explicitly.
- GitHub Actions is the sole publisher for supported release targets on Docker Hub and GHCR.
- Docker Hub Automatic Builds and legacy publication hooks are removed.
- Operational workflows may still inspect historical tags. Such inspection does not grant or imply support.

## Change policy

- New development starts from `main`; `build/versions.json` selects the active matrix entry.
- Security-only image lines receive security fixes and only the minimum maintenance required to deliver them.
- Frozen branches receive no rebuilds, dependency refreshes, CVE remediation, or compatibility fixes.
- Publish-sensitive changes and Dockerfiles require explicit review and image validation.
- Frozen archive tags and EOL image tags are never used as source publication targets.

## Imagick baseline

Active matrix entries use the following baseline unless a smoke-tested exception is documented:

- pinned release: `imagick-3.8.1`
- source: PECL release tarball copied to `/usr/src/php/ext/imagick`
- build: `docker-php-ext-install imagick`

Do not infer ongoing support for a frozen image merely because its historical Dockerfile used this baseline.

## CI and operations

See [docs/ci-operations.md](./docs/ci-operations.md) for branch protection, report-only workflow boundaries, triage, and rollback procedures.
