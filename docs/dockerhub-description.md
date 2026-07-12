# fpm-alpine

Multi-architecture PHP-FPM Alpine images for WordPress, Gnuboard, Rhymix, and related deployments.

## Supported tags

- Active and maintained tags: `8.2`, `8.3`, `8.4`, and `8.5`.
- Frozen, unsupported compatibility tags: `8.0` and `8.1`; these are retained but never rebuilt.
- The Docker Hub `this` tag is unsupported legacy and must not be used as a version contract.
- There is intentionally no `latest` tag. Pin an explicit PHP minor or immutable digest.

Each active tag is published for `linux/amd64` and `linux/arm64`. Docker Hub and GHCR active tags are promoted from the same verified manifest digest.

## Source, support, and security

- Source: <https://github.com/woosungchoi/fpm-alpine>
- Support policy: <https://github.com/woosungchoi/fpm-alpine/blob/main/SUPPORT.md>
- Branch and tag policy: <https://github.com/woosungchoi/fpm-alpine/blob/main/BRANCH-AND-TAG-POLICY.md>
- CI and publisher operations: <https://github.com/woosungchoi/fpm-alpine/blob/main/docs/ci-operations.md>
- Security policy: <https://github.com/woosungchoi/fpm-alpine/security/policy>
- Private vulnerability report: <https://github.com/woosungchoi/fpm-alpine/security/advisories/new>

GitHub Actions on protected `main` is the sole publisher. Docker Hub Automatic Builds are disabled. Pull requests cannot access registry credentials or publish images.

## Included runtime components

The image extends upstream PHP-FPM Alpine with ffmpeg and the PHP extensions required by the maintained application stacks, including Imagick, Redis, APCu, PDO MySQL, and Intl. Exact PHP/source/PECL pins are recorded in the repository's `build/versions.json`.

## Usage

```console
docker pull woosungchoi/fpm-alpine:8.5
```

For reproducible deployment, resolve and pin the selected tag's digest rather than relying on a moving tag.

## License and attribution

Repository source is licensed under GPL-2.0-only. See:

- <https://github.com/woosungchoi/fpm-alpine/blob/main/LICENSE>
- <https://github.com/woosungchoi/fpm-alpine/blob/main/NOTICE.md>
