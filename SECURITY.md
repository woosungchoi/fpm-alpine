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

Dependency automation is fail-closed and disabled by default. The updater may propose only official PHP same-minor patch/digest changes and PECL patch changes allowed by `build/automation-policy.json`. Every generated pull request is reclassified from its exact diff; native auto-merge additionally requires the protected exact-head `docker-smoke` check. Pull-request workflows have no registry credentials and cannot publish.

After an eligible dependency-only change reaches protected `main`, the trusted `dependency-auto-publish` workflow builds all maintained PHP minors as non-moving GHCR canaries. It verifies exact provenance, SBOM, labels, Cosign identity, anonymous amd64/arm64 runtime, the full runtime contract, and Trivy results before a single unattended `fpm-auto-production` controller may promote them. That environment is a protected-main authority boundary, not a claim that reviewers or environment-scoped secrets are configured. Before automatic Docker Hub credential use, before each immutable Docker Hub stage, and again immediately before moving aliases, the controller requires the exact source to remain current `main`, validates the SHA-bound active owner cutover attestation, and anonymously verifies the live Docker Hub repository identity, active status, and `is_automated=false`. The attestation is accepted only after an actual zero-queue/zero-running Builds UI observation, exact nonpublisher hook inventory, post-observation rotation to a repository-dedicated GitHub Actions token, and removal of external writers; public metadata or an inferred queue count alone is insufficient. The controller freezes and uploads the final plan before its first write, acquires a protected commit-backed single-writer lock, and durably records every immutable stage, moving-alias attempt/result, referrer write, rollback, and recovery transition. Unknown state leaves the lock blocked rather than releasing it. Backfill is a default-branch `repository_dispatch` whose requested commit must contain the exact trusted release manifest and that performs no Docker Hub login, alias write, or signature write: a legacy unsigned Docker Hub source must instead pass exact provenance/SBOM/label/runtime validation, while the copied GHCR subject receives a signed operation annotation and the automatic-workflow identity. A separate plan-SHA-bound recovery controller refuses all writes if any alias cannot be classified as the recorded prior or known target state. PHP minor-set changes, support/EOL changes, runtime-contract changes, workflow permission changes, publisher changes, and vulnerability exceptions remain manual-review operations.

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
