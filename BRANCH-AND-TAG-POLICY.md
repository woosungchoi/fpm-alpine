# Branch and tag policy

This document defines branch and tag behavior. [SUPPORT.md](./SUPPORT.md) is the canonical source for version lifecycle status and EOL dates.

## Branch roles

- `8.5` is the current primary and GitHub default branch.
- Version branches identify their matching PHP major/minor image line.
- A branch or tag existing in the repository or registry does not by itself mean that the line is supported.
- PHP 7.4 / former `master` is frozen, unsupported history; the remote `master` branch has been removed.

Support, security-only, frozen, and unsupported status is defined only in [SUPPORT.md](./SUPPORT.md). In particular, the retained `8.0` and `8.1` tags are unsupported and never rebuilt.

## Docker Hub tag policy

- Production users should pin an explicit tag such as `woosungchoi/fpm-alpine:8.5`.
- There is intentionally no `latest` tag. Consumers must select a PHP line explicitly.
- Docker Hub hooks remain the publish path for supported release targets.
- GitHub Actions verifies, observes, and reports; it does not replace Docker Hub publishing.
- Operational workflows may still inspect historical tags. Such inspection does not grant or imply support.

## Change policy

- New development starts from the primary branch or the supported target branch that needs the change.
- Security-only branches receive security fixes and only the minimum maintenance required to deliver them.
- Frozen branches receive no rebuilds, dependency refreshes, CVE remediation, or compatibility fixes.
- Publish-sensitive changes, Dockerfiles, and Docker Hub hooks require explicit branch-specific review and image validation.
- Safe workflow, script, test, and policy guardrails may be synchronized under the controls documented in the CI runbook.

## Imagick baseline

Supported branches use the following baseline unless a branch-specific, smoke-tested exception is documented:

- pinned release: `imagick-3.8.1`
- source: PECL release tarball copied to `/usr/src/php/ext/imagick`
- build: `docker-php-ext-install imagick`

Do not infer ongoing support for a frozen image merely because its historical Dockerfile used this baseline.

## CI and operations

See [docs/ci-operations.md](./docs/ci-operations.md) for branch protection, report-only workflow boundaries, triage, and rollback procedures.
