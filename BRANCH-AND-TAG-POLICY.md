# Branch and tag policy

This document defines the repository policy after retiring PHP 7.4 / `master` from active support.

## Current policy

This repository uses **version branches as the active supported lines**.

### Active supported branches

- `8.0`
- `8.1`
- `8.2`
- `8.3`
- `8.4`
- `8.5`

### Primary branch

- **current stable/mainline branch:** `8.5`
- **GitHub default branch:** `8.5`

### Legacy branch history

- `master` was the old **PHP 7.4** branch
- PHP 7.4 / `master` is **no longer maintained or updated**
- the remote `master` branch has already been removed
- legacy `master` history may still be preserved locally under an archive ref for historical reference

## What each branch should mean

### `8.5`

`8.5` is the branch that should currently be treated as the repository's primary line.

That means:

- users should land on `8.5` by default
- new documentation should describe `8.5` as the mainline
- if Docker Hub keeps a `latest` tag, it should follow `8.5`
- when the main supported PHP line advances in the future, this role should move forward to the next stable branch

### `8.0` through `8.4`

These are still supported version branches for users who intentionally pin those lines.

They remain active build targets, but they are not the conceptual default branch.

### legacy `master`

The old PHP 7.4 line should no longer be used for current development, release signaling, or Docker Hub active branch builds.

Recommended treatment:

- preserve it only as archived history if needed
- do not advertise it as supported
- do not route `latest` through it
- do not revive it for new work

## Docker Hub branch/tag policy

The repository-side policy is now:

- active automated branch builds target **`8.0` through `8.5`**
- the legacy PHP 7.4 `master` line is **not part of the active branch set**
- explicit version tags remain the production-safe recommendation
- if Docker Hub publishes a `latest` tag, `latest` should point to the same image line as the current primary/default branch: **`8.5`**

## Current state

### Already true

- supported branches are **`8.0` through `8.5`**
- docs treat **`8.5`** as the current mainline
- PHP 7.4 / `master` is no longer an actively maintained line
- GitHub default branch is **`8.5`**
- remote `master` has been deleted

### Still worth verifying operationally

- Docker Hub automated build rules are limited to **`8.0` through `8.5`**
- Docker Hub `latest` behavior follows **`8.5`** if `latest` is still published

## Operational recommendation

- tell users to pin explicit tags such as `woosungchoi/fpm-alpine:8.5`
- treat `8.5` as the mainline in docs and internal references
- avoid new work on legacy PHP 7.4 history
- treat PHP 7.4 as frozen / unsupported here
- keep Docker Hub hooks as the publish path; use GitHub Actions for verification, observation, and report-only guardrails
- keep `docker-smoke` as the required branch-protection check, while manifest/freshness/drift workflows remain non-required reports

## CI / operations runbook

See [docs/ci-operations.md](./docs/ci-operations.md) for the maintained branch protection model, workflow triage steps, report-only workflow policy, and rollback procedure.


## Maintained Imagick baseline

For the actively supported PHP branches **`8.0` through `8.5`**, the repository policy is now:

- use **`imagick-3.8.1`** as the pinned release
- install from the **PECL release tarball** into `/usr/src/php/ext/imagick`
- compile with **`docker-php-ext-install imagick`**

If a future branch needs a different Imagick version, document that as an explicit exception instead of expanding a version matrix in multiple files.
