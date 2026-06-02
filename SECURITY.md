# Security Policy

## Supported versions

This repository publishes custom PHP-FPM Alpine images for the maintained PHP version branches `8.0` through `8.5`.

The current primary/default branch is `8.5`. The legacy PHP 7.4 / `master` line is no longer maintained by this repository.

| Branch | Base image | Status |
| --- | --- | --- |
| `8.0` | `php:8.0-fpm-alpine` | supported |
| `8.1` | `php:8.1-fpm-alpine` | supported |
| `8.2` | `php:8.2-fpm-alpine` | supported |
| `8.3` | `php:8.3-fpm-alpine` | supported |
| `8.4` | `php:8.4-fpm-alpine` | supported |
| `8.5` | `php:8.5-fpm-alpine` | supported / primary branch |
| legacy `master` | `php:7.4-fpm-alpine` | unsupported / frozen history |

Production users should pin an explicit version tag, for example `woosungchoi/fpm-alpine:8.5`, instead of relying on `latest`.

## Security maintenance

The maintained image lines are validated with repository smoke tests and published-manifest checks:

- `smoke-test` builds the branch Dockerfile and verifies PHP/FPM runtime basics, required extensions, `ffmpeg`, `iconv`, and `Imagick` behavior.
- `verify-published-manifest` checks the published Docker Hub tags for required multi-arch manifest entries.
- The repository policy standardizes maintained branches on the documented Imagick release baseline unless a branch-specific exception is documented.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub Security Advisories for this repository when available. If advisories are not available to you, contact the maintainer through the GitHub profile linked from this repository.

Please include:

- affected image tag or branch
- affected architecture, if architecture-specific
- reproduction steps or a minimal proof of concept
- expected impact
- relevant upstream CVE or advisory links, if known

Do not open a public issue for a vulnerability until a fix or mitigation is available.

## Maintainer response

The maintainer will triage reports against the relevant PHP branch, Dockerfile, bundled Alpine packages, PECL extensions, and Docker Hub published image tags. Confirmed vulnerabilities are fixed by updating the affected version branch, adjusting build configuration, or documenting mitigations as appropriate.
