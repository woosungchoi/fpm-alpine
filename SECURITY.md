# Security Policy

## Supported versions

See [SUPPORT.md](./SUPPORT.md) for the canonical supported-version matrix, lifecycle definitions, and EOL policy. Reports for frozen or EOL tags may be closed without a repository fix.

Production users should pin an explicit version tag, for example `woosungchoi/fpm-alpine:8.5`, instead of relying on `latest`.

## Security maintenance

The active image lines are validated from the single `main` source trunk with repository smoke tests and published-manifest checks:

- `smoke-test` builds the `main` active matrix and verifies PHP/FPM runtime basics, required extensions, `ffmpeg`, `iconv`, and `Imagick` behavior.
- The same required `docker-smoke` aggregate also enforces source checksum replay, reproducibility, package/module contract drift, and fixable-CRITICAL vulnerability checks across PHP 8.2–8.5 on amd64/arm64.
- `verify-published-manifest` checks the published Docker Hub tags for required multi-arch manifest entries.
- The repository policy standardizes active matrix entries on the documented Imagick release baseline unless an explicit exception is documented.

Dependency automation is fail-closed and disabled by default. The updater may propose only official PHP same-minor patch/digest changes and PECL patch changes allowed by `build/automation-policy.json`. Every generated pull request is reclassified from its exact diff; native auto-merge additionally requires the exact-head `docker-smoke` check from GitHub Actions App ID `15368`. Pull-request workflows have no registry credentials and cannot publish.

Trusted-main auto-canary, when explicitly enabled, dispatches two immutable full-matrix canary runs and validates their exact source SHA, run attempts, consecutive run numbers, and evidence artifacts. It does not authorize production. PHP minor changes, support/EOL changes, runtime-contract changes, workflow permission changes, publisher changes, vulnerability exceptions, and production automation remain manual-review operations.

## Reporting a vulnerability

Report suspected vulnerabilities through [GitHub private vulnerability reporting](https://github.com/woosungchoi/fpm-alpine/security/advisories/new). Do not include vulnerability details, credentials, tokens, or other secrets in a public issue.

Please include:

- affected image tag and source revision
- affected architecture, if architecture-specific
- reproduction steps or a minimal proof of concept
- expected impact
- relevant upstream CVE or advisory links, if known

If private vulnerability reporting is unavailable, use the maintainer contact information on the repository owner's GitHub profile without disclosing details publicly. Do not open a public issue for a vulnerability until a fix or mitigation is available.

## Maintainer response

The maintainer will triage reports against the relevant `main` matrix entry, Dockerfile, bundled Alpine packages, PECL extensions, and published image tags. Confirmed vulnerabilities are fixed on `main` by updating the affected matrix entry, adjusting build configuration, or documenting mitigations as appropriate.
