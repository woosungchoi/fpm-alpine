# fpm-alpine

Custom PHP-FPM Alpine images used for WordPress / Gnuboard / Rhymix deployments.

See also: [BRANCH-AND-TAG-POLICY.md](./BRANCH-AND-TAG-POLICY.md)

> [!IMPORTANT]
> This repository's **actively supported image lines** are the version branches **`8.0` through `8.5`**.
>
> **PHP 7.4 / legacy `master` is no longer actively maintained or updated.**
>
> The repository's current primary/default branch is **`8.5`**.
>
> For production use, **pin an explicit image tag / version branch** such as `woosungchoi/fpm-alpine:8.5` instead of relying on `latest`.

## Current branch / version map

### Active supported branches

| Branch | Base image | Status |
| --- | --- | --- |
| `8.0` | `php:8.0-fpm-alpine` | supported |
| `8.1` | `php:8.1-fpm-alpine` | supported |
| `8.2` | `php:8.2-fpm-alpine` | supported |
| `8.3` | `php:8.3-fpm-alpine` | supported |
| `8.4` | `php:8.4-fpm-alpine` | supported |
| `8.5` | `php:8.5-fpm-alpine` | supported / primary branch |

### Legacy branch

| Branch | Base image | Status |
| --- | --- | --- |
| legacy `master` | `php:7.4-fpm-alpine` | legacy / frozen / archived locally only |

## Support and branch policy

This repository now follows a simple policy:

- actively maintained PHP lines live on version branches **`8.0` through `8.5`**
- **`8.5` is the current primary/default branch** for the repository
- legacy `master` is a **PHP 7.4 historical line** and is no longer part of the active remote branch set
- **PHP 7.4 is no longer updated in this repository**
- Docker Hub automated builds are intended for the supported branches **`8.0` through `8.5`**

What that means in practice:

- browse and document the repo as if **`8.5` is the mainline**
- use older version branches only when you intentionally need that exact PHP line
- do **not** start new work from the legacy PHP 7.4 branch history
- do **not** expect PHP 7.4 fixes or refreshes going forward

## Docker tags / Docker Hub notes

- Supported automated branch builds should target **`8.0`, `8.1`, `8.2`, `8.3`, `8.4`, `8.5`**
- The legacy PHP 7.4 `master` line is no longer part of the active remote branch set
- Explicit version tags remain the safest production contract
- If Docker Hub publishes `latest`, it should follow the same image line as the primary/default branch: **`8.5`**

Safe rule for production use:

- **use an explicit major/minor image tag**
- **read the branch you plan to use**, not just a cached registry or UI default view

For the full policy and operational notes, see [BRANCH-AND-TAG-POLICY.md](./BRANCH-AND-TAG-POLICY.md).

## What this image adds

Compared with the upstream PHP Alpine FPM image, this repository adds / configures:

- `ffmpeg`
- `redis` extension
- `apcu`
- `pdo`, `pdo_mysql`, `intl`
- iconv compatibility fix for Alpine
- other PHP extensions needed by the maintained app stacks

You can convert animated `gif` images to `mp4` or `webm` with `ffmpeg`.

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
