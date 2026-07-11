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

## Unsupported legacy images

The `8.0` and `8.1` tags are retained so existing references and historical artifacts remain identifiable, but they are frozen and **never rebuilt**. They receive no package refreshes, CVE remediation, compatibility fixes, or support. Running these tags is unsupported legacy use and should be migrated to a supported PHP line.

Former PHP 7.4 / `master` and version-branch source lines are archived, unsupported history. `main` is the only active source branch.

The Docker Hub `this` tag is an unsupported legacy/accidental tag; it is not a supported version contract, receives no rebuilds, updates, or support, and must not be used.

## Tag policy

There is intentionally no `latest` tag. Pin an explicit major/minor image tag, such as `woosungchoi/fpm-alpine:8.5`, and review this policy before selecting a line. Retaining a tag does not imply that it is supported or rebuilt.
