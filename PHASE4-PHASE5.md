# Phase 4 / Phase 5 progress notes

## Phase 4 — build verification / smoke coverage

Implemented:

- Added `scripts/smoke-test-image.sh` to validate a built image with runtime-oriented checks:
  - `php -v`
  - `php -m`
  - `php-fpm -t`
  - extension load checks for `imagick`, `redis`, `apcu`
  - `ffmpeg` presence/version
  - a minimal `iconv` runtime check
  - a minimal `Imagick` class instantiation check
- Added `.github/workflows/smoke-test.yml` so every push / PR can build the Docker image and run the smoke test script without changing Docker Hub hook behavior.
- Added `scripts/check-manifest.sh` plus an optional `workflow_dispatch` input for post-push manifest inspection of a published tag.

Notes:

- The workflow intentionally does a single-arch CI build (`load: true`) rather than a registry push. This keeps verification cheap and low-risk while leaving Docker Hub multi-arch publishing semantics unchanged.
- Multi-arch publish behavior remains in `hooks/build`.
- Manifest inspection is implemented as an explicit post-push check because PR builds do not publish registry tags to inspect.

## Phase 5 — conservative modernization / preparation

Implemented:

- Added explicit modernization notes in this file to separate low-risk improvements from deferred higher-risk work.
- Switched the `gnu-libiconv` edge-community repository URL to HTTPS.
- Normalized `LD_PRELOAD` to a standard Dockerfile `ENV key=value` form while preserving the existing iconv workaround behavior.
- The new smoke test now covers the historically fragile areas (`imagick`, `iconv`, `ffmpeg`, FPM config), reducing risk for future dependency updates.

Deferred on purpose:

- **Pinning `imagick` to a specific PECL release:** beneficial for reproducibility, but should be tested across the active 8.0–8.5 lines before forcing a version on the mainline.
- **Changing the `gnu-libiconv` install source / approach:** the current edge-community workaround is operationally important; replacing it should only happen after branch-by-branch validation.
- **Reworking multi-arch manifest validation in CI:** possible later, but not necessary for this first safe verification phase.

## Recommended next step

1. Keep the smoke workflow green on this maintained branch and on the primary `8.5` branch after future image changes.
2. Use the shared smoke/manifest helpers consistently across maintained version branches (`8.0`–`8.5`) when making low-risk updates.
3. In a dedicated follow-up branch, test a pinned `imagick` PECL release across all supported branches before adopting it repo-wide.
