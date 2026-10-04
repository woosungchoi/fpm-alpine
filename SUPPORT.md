# Support policy

This file is the canonical lifecycle and support policy for the image tags in this repository. PHP lifecycle dates below follow the upstream PHP supported-versions schedule. Repository support cannot extend beyond upstream support.

## Version lifecycle

| Image tag / branch | Repository status | Upstream end of life |
| --- | --- | --- |
| PHP 8.0 (`8.0`) | EOL, frozen, unsupported | 2023-11-26 |
| PHP 8.1 (`8.1`) | EOL, frozen, unsupported | 2025-12-31 |
| PHP 8.2 (`8.2`) | security-only | 2026-12-31 |
| PHP 8.3 (`8.3`) | security-only | 2027-12-31 |
| PHP 8.4 (`8.4`) | active support, then security support | 2028-12-31 |
| PHP 8.5 (`8.5`) | active support, then security support | 2029-12-31 |

“Security-only” means changes are limited to security fixes and the minimum maintenance needed to deliver them. “Active support” includes compatible bug fixes and security fixes. Support ends when the listed upstream EOL date is reached.

For source-only CI, these four active matrix entries and their lifecycle fields are mirrored in `build/versions.json`. The validator requires the exact version set, order, status, and EOL values; this document remains the human-facing canonical lifecycle policy.

The daily, network-free `php-lifecycle` date check warns 90 and 30 days before EOL, fails when an active matrix entry reaches EOL, and updates its issue only when the attention state changes. Monthly and manual runs additionally report upstream release-source outages separately from lifecycle mismatches. EOL is inclusive: 2026-12-31 is the final supported day for 8.2; 2027-01-01 fails.

## Unsupported legacy images

PHP 8.0 and 8.1 remain in this lifecycle table as unsupported history, but their former Docker Hub tags are no longer published. Signed archival subjects and the source-to-archive digest map are retained in GHCR and protected workflow evidence for audit or emergency recovery. Existing deployments that referenced `8.0` or `8.1` must migrate to an active PHP line.

Former PHP 7.4 / `master` and version-branch source lines are archived, unsupported history. `main` is the only active source branch.

The former Docker Hub `this` tag was an unsupported accidental alias and is no longer published. It was never a supported version contract.

## Tag policy

Docker Hub exposes exactly the active moving tags `8.2`, `8.3`, `8.4`, and `8.5`. There is intentionally no `latest`, canary, immutable, source, frozen, or legacy tag on Docker Hub. Pin an active minor or its resolved digest and review this policy before selecting a line. GHCR is the canonical evidence registry for non-moving canary, immutable release/source, provenance, signature, archive, and rollback subjects.

## PHP 8.2 migration checklist

- Identify applications and jobs using the 8.2 image; source CI cannot discover deployments.
- Test a supported higher PHP minor with representative CMS requests and plugins.
- Restrict new 8.2 deployments as the 90-day and 30-day warning states arrive.
- Review archive/tag retention and remove 8.2 from the active matrix through a manual policy PR after its final supported day; the date check never removes it automatically.
