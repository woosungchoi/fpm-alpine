# CI operations runbook

This runbook explains the single-trunk GitHub Actions build, separated public/evidence registry publish, verification, cleanup, and rollback flow for `woosungchoi/fpm-alpine`.

## One-screen summary

- Default and only active source branch: `main`
- Lifecycle policy: [SUPPORT.md](../SUPPORT.md) is canonical; workflow coverage does not imply support
- Frozen legacy lines are not release targets
- GitHub Actions is the sole publisher for Docker Hub and GHCR; legacy Docker Hub publication hooks are removed.
- Production users should pin explicit image tags such as `woosungchoi/fpm-alpine:8.5`.
- Docker Hub exposes exactly moving tags `8.2`–`8.5`; GHCR retains canary, immutable, provenance, signature, archive, and rollback subjects.

## Required status check

The required branch-protection context is exactly `docker-smoke`.

- `docker-smoke`

This lightweight aggregate gate is the only check that should block regular PR
merges by default. It succeeds only when `dependency-safety` and all eight
`docker-smoke-matrix` jobs (PHP 8.2–8.5 × amd64/arm64) succeed. The safety job
enforces policy/mutation tests and source checksum replay; matrix jobs build the
Dockerfile three times (one runtime candidate and two independent no-cache
reproducibility probes), require reproducibility, compare package/module contracts,
scan for fixable CRITICAL vulnerabilities, and run target-platform runtime checks.

## Manual-only publisher

`.github/workflows/publish.yml` is manual-only through `workflow_dispatch`. Pull requests never receive registry credentials and cannot run login, signing, or push steps.

The `canary` channel publishes non-moving `canary-<minor>-<run-id>-<run-attempt>` tags only to GHCR. It rejects existing GHCR canary tags before push, builds one multi-platform subject for each selected PHP minor, and verifies exact-digest manifests, runtime behavior, OCI labels, BuildKit SBOM/provenance, keyless Cosign signatures, and per-platform Trivy fixable-CRITICAL findings. Canary metadata schema v2 has one canonical GHCR subject and intentionally contains no Docker Hub digest. Runtime startup runs PHP-FPM as container PID 1 and uses a bounded readiness poll. Failure reporting does not create Docker Hub canary issues because no Docker Hub canary subject exists.

The `production` channel requires one explicit PHP minor, the protected dispatch SHA, two distinct successful and consecutive schema-v2 canary run IDs/attempts, explicit `legacy_publisher_disabled=true`, repository variable `LEGACY_PUBLISHER_DISABLED=true`, and the SHA-256 of fresh cutover evidence. Aggregate and mutation-time preflight validate the exact GHCR subject, source SHA, minor/patch, run ID/attempt, and strict JSON types. Production never rebuilds: it promotes GHCR moving and digest-derived immutable release/source tags under the `evidence` policy, then copies only the selected moving alias to Docker Hub under the `moving-only` policy. The Docker Hub destination is resolved, keylessly signed, and checked against GHCR for amd64/arm64 manifest, runtime, provenance, SBOM, and config/layer parity. Any failure after mutation restores both moving aliases from the durable prior GHCR subject. Moving aliases are `8.2`–`8.5`; PHP 8.0/8.1, `latest`, and non-moving evidence tags are never Docker Hub publication targets.

Legacy publisher cutover is complete: Docker Hub Automatic Builds are disabled and the verified publication webhook is absent. Before production dispatch, live read-back must still prove zero in-flight legacy builds and refresh the 15-minute cutover lease. The protected `fpm-production` environment supplies the approval gate. Before mutation the bootstrap job proves a durable prior GHCR rollback subject for the selected minor and records machine-readable source/destination digest and runtime/parity evidence. Rollback attempts both moving aliases independently, uses GHCR rather than Docker Hub tag retention as the durable source, re-signs the restored Docker Hub destination, and repeats manifest, cross-registry parity, and compatibility runtime verification. Canary success also requires anonymous GHCR manifest and runtime access; a private package fails closed.

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
- `php-lifecycle`
  - Monthly and manual lifecycle/EOL validation with upstream-source failure separated from policy mismatch.
  - Opens or updates one deduplicated `php-lifecycle` issue when attention is required.
- `published-runtime-smoke`
  - Weekly, manual, and post-publish exact-digest runtime/supply-chain verification for active PHP 8.2–8.5 tags.
  - Weekly and manual runs resolve each Docker Hub and GHCR moving tag once. A run triggered by `dependency-auto-publish` instead downloads that exact upstream run's aggregate transaction artifact and never re-resolves its moving tags. The workflow propagates the resulting immutable subjects through manifest reporting, platform descriptors, provenance, SBOM, OCI labels, Cosign, amd64/arm64 runtime behavior, semantic parity, and vulnerability scanning. Bounded retries absorb transient registry-inspection failures without weakening an artifact check.
  - `publish.yml` and `dependency-auto-publish.yml` are the only authorized signing workflow identities. Unknown workflow paths and signer identities fail closed.
  - The multi-registry path resolves the exact Cosign branch identity from the annotated `archive/php-8.5-final-branch` boundary pinned to commit `f941dde2ff8864e1b056c051d330eb4321afb916`: source revisions at or before the boundary must be signed by `refs/heads/8.5`, while descendants must be signed by `refs/heads/main`. A moved tag or unrelated history is rejected.
- `sync-dockerhub-metadata`
  - Manual-only and gated by the protected `fpm-production` environment.
  - Uses the existing Docker Hub repository secrets to synchronize the short description and the reviewed `docs/dockerhub-description.md`, then requires exact public API read-back.
  - Never runs for pull requests or pushes, and never prints the repository PAT or temporary Docker Hub access token.
- `prune-dockerhub-tags`
  - `plan` is read-only and uploads a canonical inventory-bound deletion plan.
  - `apply` is gated by `fpm-production`, a successful plan run ID, inventory and plan SHA-256 values, and the exact phrase `DELETE NON-ACTIVE DOCKER HUB TAGS`.
  - Every candidate is copied to a signed, anonymously readable GHCR archive subject and verified for amd64/arm64 config/layer parity before the first Docker Hub DELETE.
  - A partial failure stops immediately and can resume idempotently from the same plan; keep-tag drift or any unclassified tag fails closed.

All third-party Actions are pinned to full commit SHAs with release-tag comments. Dependabot is limited to the `github-actions` ecosystem. The separate repository-scoped updater may propose only strictly classified official PHP same-minor patch/digest changes and PECL patch changes; checksum, lifecycle, runtime-contract, workflow-policy, and publisher changes remain manual-review operations.

## Workflow responsibilities

### `smoke-test`

Purpose: build validation once per pull request and again after integration into
the protected `main` branch. Feature-branch pushes do not start a duplicate
eight-job matrix; `workflow_dispatch` remains available for explicit reruns.

The prepare job validates `build/versions.json`, the canonical build and matrix
input for PHP and source-archive pins, then derives the PHP 8.2–8.5 ×
amd64/arm64 matrix. `build/automation-policy.json`, `scripts/validate-versions.py`,
and mutation tests independently enforce lifecycle, source-host, runtime-contract,
and allowed-bump boundaries without duplicating mutable patch pins. Matrix
jobs build with `push: false` on architecture-matched GitHub-hosted runners:
amd64 uses `ubuntu-24.04` and arm64 uses `ubuntu-24.04-arm`. The matrix fails
closed if runner, host, Docker daemon, and target architectures do not match;
it does not use QEMU fallback. QEMU remains limited to workflows that genuinely
exercise both architectures from one job. No registry login, secrets, or image
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
- Do not weaken publisher or registry gates to fix a source smoke failure.

### `verify-published-manifest`

Purpose: Observe Docker Hub and GHCR published multi-arch manifests.

Expected platforms:

- `linux/amd64`
- `linux/arm64`

Triage:

1. Check whether the failure happened immediately after a production promotion.
2. If yes, compare each registry's recorded exact subject and then compare platform config/layer semantics. Do not require the two registries' top-level index digests to be equal.
3. Re-run the workflow manually with the same image ref after propagation.
4. If a `manifest-failure` issue was opened, treat it as a triage queue item and close it only after the manifest is verified or the tag is intentionally unsupported.
5. If the tag still fails, inspect the GitHub Actions publisher logs and generated manifest report artifact.
6. Only change publish logic after repeated manual checks prove the manifest is genuinely missing or malformed.

Rollback:

- GitHub Actions manifest verification can be reverted independently.
- Restore moving aliases only through the recorded exact-digest rollback workflow.

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
4. Keep `imagick-3.8.1` unless matrix smoke validation proves a different version is safe.
5. Do not automatically update Dockerfiles from freshness output.

Rollback:

- Revert report formatting or parsing changes only.
- No published images or dependency pins are changed by this workflow.

### Dependency PR, auto-merge, and Docker Hub publish

The automated path has three stages:

- `dependency-update-pr`
  - Discovery is report-only by default and uses public Docker Hub tag metadata, Docker Official Images metadata, and PECL archives.
  - PR creation additionally requires a pre-created `dependency-updater` environment, `DEPENDENCY_UPDATE_APP_ID`, `DEPENDENCY_UPDATE_APP_PRIVATE_KEY`, and `DEPENDENCY_AUTOMATION_ENABLED=true`.
  - The GitHub App must be repository-scoped with only Contents and Pull requests read/write permissions. It cannot merge or publish.
- `dependency-auto-merge`
  - It runs only after the trusted `smoke-test` workflow completes successfully for a pull request.
  - It revalidates the exact workflow run, same-repository PR metadata, bot branch, and dependency-only diff before requesting GitHub native auto-merge.
  - `DEPENDENCY_AUTO_MERGE_ENABLED=true` is required. Protected `main` and the strict required `docker-smoke` check remain authoritative; direct/admin merge is not used.
- `dependency-auto-publish`
  - It runs automatically only when protected `main` changes `build/versions.json` and the merged diff is accepted by the dependency classifier. Backfill is not a branch-selectable `workflow_dispatch`; it is the typed `fpm-ghcr-backfill` `repository_dispatch`, so GitHub loads the workflow definition from the default branch. Its payload must name `backfill-ghcr` and an exact ancestor source SHA, and that commit's `build/versions.json` must exactly equal the trusted default-branch release manifest before the active matrix is accepted.
  - Automatic runs build all maintained PHP minors for `linux/amd64` and `linux/arm64` as unique non-moving GHCR canaries. Backfill runs copy the already-published Docker Hub exact subjects into equivalent GHCR canaries without rebuilding.
  - Every canary must pass exact provenance, SBOM, OCI labels, Cosign, anonymous amd64/arm64 runtime, full runtime-contract, and Trivy checks. Cosign is pinned to `v3.1.2`; its OCI 1.1 referrer signatures carry a signed `fpm.operation` annotation and do not add public moving aliases to the Docker Hub four-tag surface. Schema-v3 metadata binds mode, minor/patch, source SHA, run ID/attempt, canonical GHCR ref/digest, and the exact Docker Hub source digest for backfill.
  - A single `fpm-auto-production` controller downloads and revalidates the exact active artifact set before using registry credentials. Automatic mode additionally selects a successful immutable `legacy-cutover-lease` artifact for the exact owner, repository, workflow path, current-main source SHA, run ID, and attempt. The 15-minute evidence is revalidated both before Docker Hub credentials and immediately before moving mutation. `scripts/refresh-cutover-lease.py --dispatch --if-publisher-active` is the owner-authenticated recurring capture entry point; it publishes nothing when no exact-main automatic publisher is queued or running and never records the opaque webhook URL. This environment is a protected-main unattended authority boundary, not a reviewer-approval or environment-secret isolation claim.
  - The controller records Docker Hub and GHCR baselines independently, verifies semantic parity rather than top-level digest equality, creates no-clobber GHCR rollback pins for both registry baselines, and uploads a strict JSON plan whose SHA is part of the artifact name before moving mutation. The GHCR-held Docker Hub fallback is accepted only when the copy preserves the exact Docker Hub prior top-level digest.
  - Docker Hub's destination digest is unknown until the cross-registry copy completes and is recorded separately in the aggregate result. Backfill performs no Docker Hub login, alias write, or signature write; historical Docker Hub subjects may predate Cosign, so the source is trusted through exact manifest/provenance/SBOM/labels/platform-runtime binding and the copied GHCR subject is the one signed by the pinned automatic-workflow identity with `fpm.operation=backfill-ghcr`. Scheduled/manual observers verify that signed exact-subject operation before allowing the unsigned Docker Hub exception. Automatic and manual production share the non-cancelling repository-wide `fpm-production-promotion` concurrency group.
  - Partial promotion or post-promotion verification failure first classifies and rechecks every attempted alias. Known states trigger aggregate rollback for every attempted release unit; any read error or unknown external drift removes the verified result and prevents all trap-driven alias overwrites. The final strict gate verifies registry-specific exact subjects, operation-appropriate signatures, provenance, SBOM, labels, platform runtime, alias read-back, and semantic config/layer parity.
- `dependency-publish-recovery`
  - It is an independent default-branch `fpm-publish-recover` repository-dispatch controller in the same production concurrency group.
  - It accepts only a failed, cancelled, or timed-out `dependency-auto-publish` run, its exact attempt, and the SHA-256 of that run's frozen plan artifact. A later successful `dependency-auto-publish` or `publish` run blocks the stale recovery request.
  - It classifies the complete active alias set as `prior`, `known-target`, or `unknown` before any write. One unknown state aborts the entire recovery without overwriting an alias.
  - When every state is known, it restores only changed aliases from the registry-specific rollback subjects and requires exact read-back plus semantic parity. Recovery evidence records `restored`, `unknown`, or `failed` atomically; GHCR-only backfill recovery never logs in to Docker Hub.

Activation order:

1. Create the scoped App/environment and run `dependency-update-pr` manually with `dry_run=true`.
2. Keep `DEPENDENCY_AUTO_MERGE_ENABLED=true` and verify that protected `main` requires the strict `docker-smoke` check.
3. Require the protected-branch-only `fpm-auto-production` unattended environment and valid repository-scoped Docker Hub credentials before enabling publication. Install the owner-authenticated recurring cutover-lease capture controller and prove that one capture-only run reports disabled Docker Hub automation, zero in-flight legacy builds, the exact redacted nonpublisher hook allowlist, and no raw hook URL. Do not describe the environment as a reviewer gate unless reviewers are actually configured.
4. Complete the typed GHCR backfill and verify live semantic parity for all active minors. Automatic promotion independently enforces this parity and remains fail-closed before that state exists.
5. Set `DEPENDENCY_AUTOMATION_ENABLED=true` after the updater dry run is clean. Each generated PR then follows the same check, native auto-merge, GHCR canary, and dual-registry promotion path.

Immediately set `DEPENDENCY_AUTOMATION_ENABLED=false` when discovery is ambiguous or generated PRs are incorrect. Set `DEPENDENCY_AUTO_MERGE_ENABLED=false` to stop automatic merges. To stop new publisher runs, disable `dependency-auto-publish` on the default branch and keep `main` unchanged; disabling does not cancel an already queued or running run. Inspect queued/running `dependency-auto-publish`, `dependency-publish-recovery`, and `publish` runs before taking further action. Before moving mutation begins, cancel the unwanted run. Once mutation may have begun, preserve Docker Hub/GHCR credentials, allow the controller's aggregate rollback to finish when possible, and use the plan-SHA-bound recovery dispatch after a cancellation or runner loss. Revoke or rotate publisher credentials only after registry-specific alias read-back and recovery evidence are complete.

### External Snyk webhook

The active Snyk webhook receives `push` and `pull_request` events as a non-required external advisory signal. The repository maintainer owns the integration and its Snyk project configuration.

- Snyk does not satisfy the required `docker-smoke` context.
- Snyk does not replace the exact-subject Trivy fixable-CRITICAL gate or the scheduled runtime/supply-chain verification.
- On delivery failures, inspect recent webhook deliveries and the linked Snyk project before changing repository policy.
- Remove the webhook only after a 90-day audit finds no accepted deliveries, checks, or statuses and the Trivy replacement remains verified.

## Merge checklist

Before merging CI/workflow changes:

- `docker-smoke` passes on the PR branch.
- Workflow YAML has explicit minimal permissions.
- Report-only workflows are not marked as required checks.
- Registry credentials and publish steps remain unreachable from pull requests.
- README, branch policy, and this runbook defer lifecycle status to `SUPPORT.md`.
- Rollback uses recorded exact registry digests when publish behavior changes.

## Manual commands

```bash
HOME=/home/openclaw XDG_CONFIG_HOME= gh run list --repo woosungchoi/fpm-alpine --branch main --limit 10
HOME=/home/openclaw XDG_CONFIG_HOME= gh workflow run verify-published-manifest.yml --repo woosungchoi/fpm-alpine --ref main -f image_ref=woosungchoi/fpm-alpine:8.5
HOME=/home/openclaw XDG_CONFIG_HOME= gh workflow run dependency-freshness.yml --repo woosungchoi/fpm-alpine --ref main

# Default-branch-only GHCR backfill; substitute the verified Docker Hub source revision.
HOME=/home/openclaw XDG_CONFIG_HOME= gh api --method POST repos/woosungchoi/fpm-alpine/dispatches \
  -f event_type=fpm-ghcr-backfill \
  -F 'client_payload[operation]=backfill-ghcr' \
  -F 'client_payload[source_sha]=<40-hex-source-sha>'

# Recovery; use only after independently verifying the failed run and frozen plan SHA.
HOME=/home/openclaw XDG_CONFIG_HOME= gh api --method POST repos/woosungchoi/fpm-alpine/dispatches \
  -f event_type=fpm-publish-recover \
  -F 'client_payload[original_run_id]=<run-id>' \
  -F 'client_payload[original_run_attempt]=<attempt>' \
  -f 'client_payload[plan_sha256]=<64-hex-plan-sha256>'
```

## Branch protection rollback

If a required check name is changed accidentally:

1. Restore the previous required status check name in GitHub branch protection: `docker-smoke`.
2. Re-run the PR workflow.
3. Confirm the check appears on the PR as a required check.
4. Only then merge or re-enable stricter settings.
