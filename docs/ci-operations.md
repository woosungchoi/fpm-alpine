# CI and publication operations

Lifecycle policy: [SUPPORT.md](../SUPPORT.md) is canonical. `build/versions.json`
and `build/automation-policy.json` define the canonical build and matrix inputs
and the allowed-bump boundaries. GitHub Actions is the sole publisher.

## Required status check

`smoke-test` builds PHP 8.2–8.5 on native amd64 and arm64 runners. The aggregate required check is `docker-smoke`; it passes only when the `dependency-safety` job and all `docker-smoke-matrix` jobs pass.

Protected `main` must require `docker-smoke`.

## Automatic PHP patch flow

The active automation has one path:

1. `dependency-update-pr` checks the existing PHP 8.2, 8.3, 8.4, and 8.5 lines.
2. A newer same-minor PHP patch or refreshed digest creates one scoped PR.
3. `smoke-test` runs the required CI matrix.
4. `dependency-auto-merge` requests native squash auto-merge for the validated dependency PR.
5. The merged `build/versions.json` change triggers `dependency-auto-publish` on protected `main`.
6. One Buildx invocation per affected minor pushes the same multi-platform image to:
   - `docker.io/woosungchoi/fpm-alpine:<minor>`
   - `ghcr.io/woosungchoi/fpm-alpine:<minor>`
7. The publisher reads both registries back, requires the same top-level digest, and requires linux/amd64 and linux/arm64 manifests.
8. A successful publisher run triggers the next updater discovery pass.

There is no automatic new-minor onboarding. PHP 8.6 or any later minor requires a reviewed policy change. PECL updates are discovered as manual-review candidates and are not auto-merged. There is intentionally no `latest` tag.

## Configuration

Repository variables:

- `DEPENDENCY_AUTOMATION_ENABLED=true` enables automatic PR creation.
- `DEPENDENCY_AUTO_MERGE_ENABLED=true` enables native auto-merge after required CI succeeds.
- `DEPENDENCY_UPDATE_APP_ID` identifies the repository-scoped updater GitHub App.

Repository secrets:

- `DEPENDENCY_UPDATE_APP_PRIVATE_KEY`
- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

The updater App needs only repository Contents and Pull requests read/write permissions. Registry secrets are used only by the protected `fpm-auto-production` environment after a trusted `main` merge.

## Manual synchronization

Use the same publisher workflow to republish one minor or all active minors. This is a retry/synchronization entry point, not a separate publication implementation.

```bash
HOME=/home/openclaw XDG_CONFIG_HOME= gh workflow run dependency-auto-publish.yml \
  --repo woosungchoi/fpm-alpine --ref main -f version=8.5

HOME=/home/openclaw XDG_CONFIG_HOME= gh workflow run dependency-auto-publish.yml \
  --repo woosungchoi/fpm-alpine --ref main -f version=all
```

## Pause and resume

To stop new update PRs:

```bash
HOME=/home/openclaw XDG_CONFIG_HOME= gh variable set DEPENDENCY_AUTOMATION_ENABLED \
  --repo woosungchoi/fpm-alpine --body false
```

To stop automatic merges:

```bash
HOME=/home/openclaw XDG_CONFIG_HOME= gh variable set DEPENDENCY_AUTO_MERGE_ENABLED \
  --repo woosungchoi/fpm-alpine --body false
```

Disabling these variables does not cancel an already running publisher. Check active runs before operational changes:

```bash
HOME=/home/openclaw XDG_CONFIG_HOME= gh run list \
  --repo woosungchoi/fpm-alpine --workflow dependency-auto-publish.yml --limit 10
```

Rollback is a normal retry of the same `dependency-auto-publish` workflow for
the selected minor after correcting `main`; there is no separate recovery or
registry transaction workflow.

## Other workflows

- `publish.yml`: owner-only immutable GHCR canary publisher; it does not move production tags.
- `verify-published-manifest.yml`: manual/scheduled public manifest verification.
- `sync-dockerhub-metadata.yml`: owner-only Docker Hub description synchronization.
- `prune-dockerhub-tags.yml`: owner-only read-only pruning plan; it does not delete tags.
- `php-lifecycle.yml`: lifecycle/EOL reporting.

`dependency-freshness.yml` opens or updates a `dependency-freshness` issue when
the checked source state needs attention. `verify-published-manifest.yml` opens or updates a `manifest-failure` issue after a failed public manifest check.

## External Snyk webhook

The external Snyk webhook is a non-required external advisory signal. The
repository maintainer owns the integration. It is not a publisher or required
check and does not replace the exact-subject Trivy fixable-CRITICAL gate.
