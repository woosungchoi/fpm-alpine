# CI operations runbook

This runbook explains how `woosungchoi/fpm-alpine` uses GitHub Actions around the existing Docker Hub publish flow.

## One-screen summary

- Default branch: `8.5`
- Lifecycle policy: [SUPPORT.md](../SUPPORT.md) is canonical; workflow coverage does not imply support
- Frozen legacy lines are not release targets
- Docker Hub hooks remain the publish path. GitHub Actions verifies, observes, and reports; it does not replace Docker Hub publishing.
- Production users should pin explicit image tags such as `woosungchoi/fpm-alpine:8.5`.

## Required status check

The required branch-protection context is exactly `docker-smoke`.

- `docker-smoke`

This lightweight aggregate gate is the only check that should block regular PR
merges by default. It succeeds only when all eight `docker-smoke-matrix` jobs
(PHP 8.2–8.5 × amd64/arm64) succeed; the matrix jobs build the branch Dockerfile
and run runtime checks in the built containers.

## Manual-only publisher migration

`.github/workflows/publish.yml` is initially manual-only through `workflow_dispatch`. Pull requests never receive registry credentials and cannot run login, signing, or push steps.

The `canary` channel publishes only non-moving `canary-<minor>-<run-id>-<run-attempt>` tags to Docker Hub and GHCR. It rejects existing canary tags before push, builds one multi-platform subject for each selected PHP minor, and verifies exact-digest manifests, runtime behavior, OCI labels, BuildKit SBOM/provenance, keyless Cosign signatures, per-platform Trivy fixable-CRITICAL findings, and cross-registry platform config/layer parity. Runtime startup uses a bounded 10-second readiness poll so QEMU scheduling latency cannot turn an already-ready PHP-FPM process into a false failure. Failure reporting inspects exact failed matrix jobs and does not open registry issues for successful minors.

The `production` channel requires one explicit PHP minor, the protected dispatch SHA, two distinct successful and consecutive canary run IDs/attempts, explicit `legacy_publisher_disabled=true`, repository variable `LEGACY_PUBLISHER_DISABLED=true`, and the SHA-256 of fresh cutover evidence. An aggregate preflight downloads the immediately preceding PHP 8.5 artifact and all current 8.2–8.5 artifacts, then validates each artifact's actual metadata content: channel, source SHA, minor/patch, run ID/attempt, and both registry digests. Names alone are insufficient. Mutation-time loading invokes the same strict shared metadata validator before exposing digest outputs, so JSON booleans cannot impersonate integer run fields. The base64-encoded evidence variables must hash to the dispatch input, bind the source SHA, be at most 15 minutes old, and use strict JSON types: integer schema version, inactive boolean build rule, integer in-flight count exactly zero, and absent boolean webhook. The lease is revalidated immediately before any GHCR bootstrap creation and again immediately before production promotion; approval or bootstrap delay cannot reuse stale evidence. Single-minor dispatch prevents a failed workflow from leaving a partially updated multi-minor release. The selected verified canary digest is bound from a validated artifact, and both live canary tags are matched to its exact recorded subjects before promotion. Full runtime, provenance, SBOM, signature, vulnerability, and cross-registry checks already passed in the successful canary run whose exact artifact set is validated by aggregate preflight; repeating those expensive checks after environment approval would consume the 15-minute cutover lease before mutation. The workflow instead performs immutable/check-only collision checks, revalidates the lease immediately before mutation, promotes without rebuilding, and repeats exact runtime and semantic parity after promotion. Before mutation the workflow rejects immutable or source tags that already point to another digest. Immutable names include the full verified digest (`<patch>-<date>-<digest64>` and `sha-<minor>-<commit12>-<digest64>`), so different content cannot race for the same name. Moving aliases are `8.2`–`8.5`; PHP 8.0/8.1 and `latest` are never publication targets.

Docker Hub hooks remain active only during the canary shadow period. After at least two consecutive replacement canary successes and before the first production dispatch, capture final rollback baselines, disable the Docker Hub build rule, remove the verified legacy webhook, read both states back, verify zero in-flight legacy builds, and only then set `LEGACY_PUBLISHER_DISABLED=true`, `LEGACY_CUTOVER_EVIDENCE_B64`, and `LEGACY_CUTOVER_EVIDENCE_SHA256`. Refresh this 15-minute cutover lease from live read-back before every sequential production dispatch. Production remains fail-closed when the explicit input, repository state, evidence hash, evidence content, or freshness check disagrees; this prevents a delayed legacy build from racing a GitHub Actions promotion. The protected `fpm-production` environment supplies the approval gate. Before first promotion an idempotent job establishes the selected minor's GHCR rollback alias from the current Docker Hub moving alias and verifies runtime/parity. It creates dedicated machine-readable evidence before even resolving the Docker Hub baseline, then records source SHA, run ID/attempt, minor, Docker Hub/GHCR refs, baseline resolution/inspect/parse state and raw exits, cutover validation, create state/exit, post-create read-back raw inspect exit plus digest-parse exit/digest, verifier state/exit, timestamps, and final status. Early transport failures and post-create inspect exits such as 2 or 9 therefore remain durable instead of being normalized. Rollback attempts both registries independently, verifies the moving aliases themselves, and repeats manifest, cross-registry parity, and compatibility runtime verification. Canary success also requires anonymous GHCR manifest and runtime access; a private package fails closed.

GHCR canary success requires anonymous exact-digest manifest and runtime access. Runtime checks resolve the multi-platform index to one exact platform-manifest digest per target before invoking Docker; reusing one index digest for sequential amd64 and arm64 runs can fail with Docker's `cannot overwrite digest` even when both anonymous pulls are valid. A private package or authenticated-only verification never counts as a replacement success.

Non-required / report-only workflows:

- `verify-published-manifest`
  - Observes already-published Docker Hub tags.
  - On failure, opens or updates a `manifest-failure` issue with the failed image ref and report.
  - Can be affected by Docker Hub propagation or registry/network latency.
- `dependency-freshness`
  - Report-only dependency/source freshness observations.
  - Does not mutate Dockerfiles, open PRs, or publish images.
  - When freshness signals require review, opens or updates a `dependency-freshness` issue.
- `branch-drift`
  - Report-only maintained-branch drift detection.
  - Does not sync branches automatically.
- `branch-sync-pr`
  - Creates safe-file sync PRs from `8.5` to maintained version branches.
  - Only copies allowlisted workflow/script/docs/test guardrails.
  - Never copies `Dockerfile`, Docker Hub hooks, or publish-sensitive files.
  - Uses a repository-scoped GitHub App installation token and enables PR auto-merge only after validating the generated PR shape and changed files.

## Workflow responsibilities

### `smoke-test`

Purpose: build validation once per pull request and again after integration into
the protected `8.5` branch. Feature-branch pushes do not start a duplicate
eight-job matrix; `workflow_dispatch` remains available for explicit reruns.

The prepare job validates `build/versions.json`, the canonical build and matrix
input for PHP and source-archive pins, then derives the PHP 8.2–8.5 ×
amd64/arm64 matrix. `scripts/validate-versions.py` and literal policy fixtures
independently enforce the approved pin and lifecycle baseline; intentional pin
or lifecycle changes therefore require coordinated JSON, validator, and test
approval updates. Matrix
jobs build with `push: false` and run the resulting target-platform image under
the GitHub-hosted runner's QEMU support. No registry login, secrets, or image
publishing are involved.

Each build explicitly passes OCI source, commit revision, PHP patch version,
and an RFC3339 creation timestamp. `OCI_CREATED` is intentionally an input: a
different creation value changes image identity even when source and dependency
pins are unchanged. These labels are provenance for source-only CI, not a
publishing step.

Checks:

- `php -v`
- `php -m`
- `php-fpm -t`
- extension loading for `imagick`, `redis`, `apcu`
- `ffmpeg`
- official `gnu-libiconv-libs=1.18-r0` ownership and link target, exact `ICONV_IMPL=libiconv` / `ICONV_VERSION=1.18`, clean APK audit, direct linkage, and transliteration
- `Imagick` class instantiation

Triage:

1. Open the failed `docker-smoke` aggregate gate.
2. Find the failed `docker-smoke-matrix` leg and its named smoke check.
3. If `php-fpm -t` failed, inspect the `php-fpm` config error and the emitted `/tmp/php-fpm.log` section.
4. If an extension failed, first check Dockerfile extension install/build logs for that extension.
5. If `iconv` failed, verify the pinned base still owns `/usr/lib/libiconv.so.2` through `gnu-libiconv-libs=1.18-r0`, resolves it to `/usr/lib/libiconv.so.2.7.0`, has a clean APK audit, and that `ldd /usr/local/bin/php` has no unresolved library.

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
4. If a `manifest-failure` issue was opened, treat it as a triage queue item and close it only after the manifest is verified or the tag is intentionally unsupported.
5. If the tag still fails, inspect Docker Hub build/publish logs and the generated manifest report artifact.
6. Only change publish logic after repeated manual checks prove the manifest is genuinely missing or malformed.

Rollback:

- GitHub Actions manifest verification can be reverted independently.
- Docker Hub hooks remain the publish path and should not be replaced as a rollback shortcut.

### `dependency-freshness`

Purpose: Report dependency freshness without automatic mutation.

The workflow first validates and then reads `build/versions.json`. Invalid JSON
or schema metadata fails clearly before any remote freshness query. The report includes:

- every exact matrix base image and digest
- configured Docker Hub tag digests
- PECL latest observations for `imagick`, `redis`, and `apcu`
- exact pinned versions, URLs, and checksums for source dependencies
- manual follow-up guidance

Triage:

1. Treat image `inspect_failed` as an observation, not an immediate CI failure.
2. Treat PECL latest changes as manual review prompts.
3. If a `dependency-freshness` issue was opened, treat it as a manual review queue item and close it only after the candidate update is accepted, rejected, or no longer reported.
4. Keep `imagick-3.8.1` unless branch-specific smoke validation proves a different version is safe.
5. Do not automatically update Dockerfiles from freshness output.

Rollback:

- Revert report formatting or parsing changes only.
- No published images or dependency pins are changed by this workflow.

### `branch-drift`

Purpose: Detect missing workflow/script/policy changes across configured branches. This operational coverage does not imply lifecycle support.

Triage:

1. Review `branch-drift-reports/branch-drift.md`.
2. Ignore `allowed-drift` only if the allowlist reason still matches current policy.
3. For unexpected `drift`, manually inspect the file difference before syncing.
4. Use `branch-sync-pr` only for allowlisted safe workflow/script/docs/test guardrails.
5. Open an explicit PR for any non-safe branch sync; do not auto-merge branch-wide drift fixes.

Rollback:

- Disable or revert the `branch-drift` workflow if it is noisy.
- Keep the allowlist narrow and reasoned.

### `branch-sync-pr`

Purpose: Create safe-file sync PRs from `8.5` to configured version branches when report-only branch drift identifies missing guardrails. This operational coverage does not imply lifecycle support.

Automation boundary:

- Allowed: files listed in `docs/branch-sync-safe-files.txt`.
- Blocked/manual: `Dockerfile`, Docker Hub hooks, publish-sensitive files, PHP base image lines, `IMAGICK_VERSION`, `gnu-libiconv`, and branch protection settings.
- Merge policy: generated PRs are auto-merge candidates only when the workflow validates branch name, base branch, labels, and changed files against the safe-sync plan; branch protection still requires `docker-smoke` before GitHub performs the merge.
- Token policy: `branch-sync-pr` must use a GitHub App installation token, not the default `GITHUB_TOKEN`, so PR creation/update events can trigger required checks normally.
- Check policy: generated PR branch pushes trigger `docker-smoke` normally; `branch-sync-pr` should not manually dispatch `smoke-test`, because duplicate check runs can leave a cancelled `docker-smoke` in the PR rollup and block native auto-merge.
- Branch policy: `sync/branch-drift-*` branches are generated output and may be updated with `--force-with-lease`; protected maintained branches are updated only through PR merge.

GitHub App setup:

1. Create a GitHub App installed only on `woosungchoi/fpm-alpine`.
2. Grant repository permissions: Contents read/write, Pull requests read/write, Issues read/write, and Actions read/write.
3. Generate a private key for the app.
4. Add repository variable `BRANCH_SYNC_APP_ID` with the numeric App ID.
5. Add repository secret `BRANCH_SYNC_APP_PRIVATE_KEY` with the full PEM private key.
6. Enable repository setting **Allow auto-merge** so `gh pr merge --auto --squash` can arm auto-merge while `docker-smoke` is pending.

Triage:

1. Run `branch-sync-pr` manually with a single `target_branch` first when testing changes.
2. Review the generated PR body for synced files and blocked/manual files.
3. Confirm the PR only touches workflow/script/docs/test guardrails.
4. Wait for target-branch `docker-smoke` to pass.
5. Close the PR if it includes an unexpected safe-file copy or if blocked/manual drift needs a separate human-authored PR.

Rollback:

- Close generated sync PRs without merging.
- Delete `sync/branch-drift-*` branches if the generated diff is noisy.
- Revert `.github/workflows/branch-sync-pr.yml` and the two branch-sync scripts if generation itself is faulty.

## Merge checklist

Before merging CI/workflow changes:

- `docker-smoke` passes on the PR branch.
- Workflow YAML has explicit minimal permissions.
- Report-only workflows are not marked as required checks.
- Docker Hub publish hooks are unchanged unless the task is explicitly a publish migration.
- README, branch policy, and this runbook defer lifecycle status to `SUPPORT.md`.
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
