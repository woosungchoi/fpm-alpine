# CI operations runbook

This runbook explains how `woosungchoi/fpm-alpine` uses GitHub Actions around the existing Docker Hub publish flow.

## One-screen summary

- Default branch: `8.5`
- Maintained branches: `8.0`, `8.1`, `8.2`, `8.3`, `8.4`, `8.5`
- Legacy `master` / PHP 7.4: not active
- Docker Hub hooks remain the publish path. GitHub Actions verifies, observes, and reports; it does not replace Docker Hub publishing.
- Production users should pin explicit image tags such as `woosungchoi/fpm-alpine:8.5`.

## Required status check

The branch protection required check for maintained branches is the `smoke-test` workflow job named:

- `docker-smoke`

This is the only check that should block regular PR merges by default because it builds the branch Dockerfile and runs runtime checks in the built container.

Non-required / report-only workflows:

- `verify-published-manifest`
  - Observes already-published Docker Hub tags.
  - Can be affected by Docker Hub propagation or registry/network latency.
- `dependency-freshness`
  - Report-only dependency/source freshness observations.
  - Does not mutate Dockerfiles, open PRs, or publish images.
- `branch-drift`
  - Report-only maintained-branch drift detection.
  - Does not sync branches automatically.

## Workflow responsibilities

### `smoke-test`

Purpose: PR/push build validation.

Checks:

- `php -v`
- `php -m`
- `php-fpm -t`
- extension loading for `imagick`, `redis`, `apcu`
- `ffmpeg`
- `iconv` runtime behavior
- `Imagick` class instantiation

Triage:

1. Open the failed `docker-smoke` job.
2. Find the named smoke check that failed.
3. If `php-fpm -t` failed, inspect the `php-fpm` config error and the emitted `/tmp/php-fpm.log` section.
4. If an extension failed, first check Dockerfile extension install/build logs for that extension.
5. If `iconv` failed, reassess the `gnu-libiconv` / `LD_PRELOAD` workaround on that branch only.

Rollback:

- Revert only the Dockerfile/script/workflow change that caused the smoke failure.
- Do not change Docker Hub hooks to fix a GitHub Actions smoke failure.

### `verify-published-manifest`

Purpose: Observe Docker Hub-published multi-arch manifests.

Expected platforms:

- `linux/amd64`
- `linux/arm64`

Triage:

1. Check whether the failure happened immediately after a GitHub push.
2. If yes, suspect Docker Hub propagation lag first.
3. Re-run the workflow manually with the same image ref after Docker Hub finishes publishing.
4. If the tag still fails, inspect Docker Hub build/publish logs and the generated manifest report artifact.
5. Only change publish logic after repeated manual checks prove the manifest is genuinely missing or malformed.

Rollback:

- GitHub Actions manifest verification can be reverted independently.
- Docker Hub hooks remain the publish path and should not be replaced as a rollback shortcut.

### `dependency-freshness`

Purpose: Report dependency freshness without automatic mutation.

The report includes:

- Dockerfile base image digest
- maintained Docker Hub tag digests
- PECL latest observations for `imagick`, `redis`, and `apcu`
- installed package signals parsed from the Dockerfile
- `gnu-libiconv` workaround status
- manual follow-up guidance

Triage:

1. Treat image `inspect_failed` as an observation, not an immediate CI failure.
2. Treat PECL latest changes as manual review prompts.
3. Keep `imagick-3.8.1` unless branch-specific smoke validation proves a different version is safe.
4. Do not automatically update Dockerfiles from freshness output.

Rollback:

- Revert report formatting or parsing changes only.
- No published images or dependency pins are changed by this workflow.

### `branch-drift`

Purpose: Detect missing workflow/script/policy changes across maintained branches.

Triage:

1. Review `branch-drift-reports/branch-drift.md`.
2. Ignore `allowed-drift` only if the allowlist reason still matches current policy.
3. For unexpected `drift`, manually inspect the file difference before syncing.
4. Open an explicit PR for any branch sync; do not auto-merge branch-wide drift fixes.

Rollback:

- Disable or revert the `branch-drift` workflow if it is noisy.
- Keep the allowlist narrow and reasoned.

## Merge checklist

Before merging CI/workflow changes:

- `docker-smoke` passes on the PR branch.
- Workflow YAML has explicit minimal permissions.
- Report-only workflows are not marked as required checks.
- Docker Hub publish hooks are unchanged unless the task is explicitly a publish migration.
- README, branch policy, and this runbook agree on the maintained branch set.
- Rollback is limited to GitHub Actions/docs/scripts when publish behavior is untouched.

## Manual commands

```bash
HOME=/home/openclaw XDG_CONFIG_HOME= gh run list --repo woosungchoi/fpm-alpine --branch 8.5 --limit 10
HOME=/home/openclaw XDG_CONFIG_HOME= gh workflow run verify-published-manifest.yml --repo woosungchoi/fpm-alpine --ref 8.5 -f image_ref=woosungchoi/fpm-alpine:8.5
HOME=/home/openclaw XDG_CONFIG_HOME= gh workflow run dependency-freshness.yml --repo woosungchoi/fpm-alpine --ref 8.5
HOME=/home/openclaw XDG_CONFIG_HOME= gh workflow run branch-drift.yml --repo woosungchoi/fpm-alpine --ref 8.5
```

## Branch protection rollback

If a required check name is changed accidentally:

1. Restore the previous required status check name in GitHub branch protection: `docker-smoke`.
2. Re-run the PR workflow.
3. Confirm the check appears on the PR as a required check.
4. Only then merge or re-enable stricter settings.
