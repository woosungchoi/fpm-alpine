# Security Policy

## Supported versions

See [SUPPORT.md](./SUPPORT.md) for the canonical supported-version matrix, lifecycle definitions, and EOL policy. Reports for frozen or EOL tags may be closed without a repository fix.

Production users should pin an explicit version tag, for example `woosungchoi/fpm-alpine:8.5`, instead of relying on `latest`.

## Security maintenance

The maintained image lines are validated with repository smoke tests and published-manifest checks:

- `smoke-test` builds the branch Dockerfile and verifies PHP/FPM runtime basics, required extensions, `ffmpeg`, `iconv`, and `Imagick` behavior.
- `verify-published-manifest` checks the published Docker Hub tags for required multi-arch manifest entries.
- The repository policy standardizes supported branches on the documented Imagick release baseline unless a branch-specific exception is documented.

## Reporting a vulnerability

Report suspected vulnerabilities through [GitHub private vulnerability reporting](https://github.com/woosungchoi/fpm-alpine/security/advisories/new). Do not include vulnerability details, credentials, tokens, or other secrets in a public issue.

Please include:

- affected image tag or branch
- affected architecture, if architecture-specific
- reproduction steps or a minimal proof of concept
- expected impact
- relevant upstream CVE or advisory links, if known

If private vulnerability reporting is unavailable, use the maintainer contact information on the repository owner's GitHub profile without disclosing details publicly. Do not open a public issue for a vulnerability until a fix or mitigation is available.

## Maintainer response

The maintainer will triage reports against the relevant PHP branch, Dockerfile, bundled Alpine packages, PECL extensions, and Docker Hub published image tags. Confirmed vulnerabilities are fixed by updating the affected version branch, adjusting build configuration, or documenting mitigations as appropriate.
