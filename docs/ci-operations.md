# CI operations runbook

This runbook explains the single-trunk GitHub Actions build, multi-registry publish, verification, and rollback flow for `woosungchoi/fpm-alpine`.

## One-screen summary

- Default and only active source branch: `main`
- Lifecycle policy: [SUPPORT.md](../SUPPORT.md) is canonical; workflow coverage does not imply support
- Frozen legacy lines are not release targets
- GitHub Actions is the sole publisher for Docker Hub and GHCR; legacy Docker Hub publication hooks are removed.
- Production users should pin explicit image tags such as `woosungchoi/fpm-alpine:8.5`.

## Required status check

The required branch-protection context is exactly `docker-smoke`.

- `docker-smoke`

This lightweight aggregate gate is the only check that should block regular PR
merges by default. It succeeds only when all eight `docker-smoke-matrix` jobs
(PHP 8.2–8.5 × amd64/arm64) succeed; the matrix jobs build the `main` Dockerfile
and run runtime checks in the built containers.

## Manual-only publisher

`.github/workflows/publish.yml` is manual-only through `workflow_dispatch`. Pull requests never receive registry credentials and cannot run login, signing, or push steps.

The `canary` channel publishes only non-moving `canary-<minor>-<run-id>-<run-attempt>` tags to Docker Hub and GHCR. It rejects existing canary tags before push, builds one multi-platform subject for each selected PHP minor, and verifies exact-digest manifests, runtime behavior, OCI labels, BuildKit SBOM/provenance, keyless Cosign signatures, per-platform Trivy fixable-CRITICAL findings, and cross-registry platform config/layer parity. Runtime startup runs PHP-FPM as container PID 1 and uses a bounded 10-second poll requiring both Docker's running state and the PHP-FPM readiness log, avoiding QEMU-dependent in-container process-name checks. Failure reporting inspects exact failed matrix jobs and does not open registry issues for successful minors.

The `production` channel requires one explicit PHP minor, the protected dispatch SHA, two distinct successful and consecutive canary run IDs/attempts, explicit `legacy_publisher_disabled=true`, repository variable `LEGACY_PUBLISHER_DISABLED=true`, and the SHA-256 of fresh cutover evidence. An aggregate preflight downloads the immediately preceding PHP 8.5 artifact and all current 8.2–8.5 artifacts, then validates each artifact's actual metadata content: channel, source SHA, minor/patch, run ID/attempt, and both registry digests. Names alone are insufficient. Mutation-time loading invokes the same strict shared metadata validator before exposing digest outputs, so JSON booleans cannot impersonate integer run fields. The base64-encoded evidence variables must hash to the dispatch input, bind the source SHA, be at most 15 minutes old, and use strict JSON types: integer schema version, inactive boolean build rule, integer in-flight count exactly zero, and absent boolean webhook. The lease is revalidated immediately before any GHCR bootstrap creation and again immediately before production promotion; approval or bootstrap delay cannot reuse stale evidence. Single-minor dispatch prevents a failed workflow from leaving a partially updated multi-minor release. The selected verified canary digest is bound from a validated artifact, and both live canary tags are matched to its exact recorded subjects before promotion. Full runtime, provenance, SBOM, signature, vulnerability, and cross-registry checks already passed in the successful canary run whose exact artifact set is validated by aggregate preflight; repeating those expensive checks after environment approval would consume the 15-minute cutover lease before mutation. The workflow instead performs immutable/check-only collision checks, revalidates the lease immediately before mutation, promotes without rebuilding, and repeats exact runtime and semantic parity after promotion. Before mutation the workflow rejects immutable or source tags that already point to another digest. Immutable names include the full verified digest (`<patch>-<date>-<digest64>` and `sha-<minor>-<commit12>-<digest64>`), so different content cannot race for the same name. Moving aliases are `8.2`–`8.5`; PHP 8.0/8.1 and `latest` are never publication targets.

Legacy publisher cutover is complete: Docker Hub Automatic Builds are disabled and the verified publication webhook is absent. Before production dispatch, live read-back must still prove zero in-flight legacy builds before setting `LEGACY_PUBLISHER_DISABLED=true`, `LEGACY_CUTOVER_EVIDENCE_B64`, and `LEGACY_CUTOVER_EVIDENCE_SHA256`. Refresh this 15-minute cutover lease from live read-back before every sequential production dispatch. Production remains fail-closed when the explicit input, repository state, evidence hash, evidence content, or freshness check disagrees; this prevents a delayed legacy build from racing a GitHub Actions promotion. The protected `fpm-production` environment supplies the approval gate. Before first promotion an idempotent job establishes the selected minor's GHCR rollback alias from the current Docker Hub moving alias and verifies runtime/parity. It creates dedicated machine-readable evidence before even resolving the Docker Hub baseline, then records source SHA, run ID/attempt, minor, Docker Hub/GHCR refs, baseline resolution/inspect/parse state and raw exits, cutover validation, create state/exit, post-create read-back raw inspect exit plus digest-parse exit/digest, verifier state/exit, timestamps, and final status. Early transport failures and post-create inspect exits such as 2 or 9 therefore remain durable instead of being normalized. Rollback attempts both registries independently, verifies the moving aliases themselves, and repeats manifest, cross-registry parity, and compatibility runtime verification. Canary success also requires anonymous GHCR manifest and runtime access; a private package fails closed.

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
  - Verifies Docker Hub/GHCR platform semantics, provenance, SBOM, Cosign identity, and amd64/arm64 runtime behavior.
  - Resolves the exact Cosign branch identity from the annotated `archive/php-8.5-final-branch` boundary pinned to commit `f941dde2ff8864e1b056c051d330eb4321afb916`: source revisions at or before the boundary must be signed by `refs/heads/8.5`, while descendants must be signed by `refs/heads/main`. A moved tag or unrelated history is rejected.

All third-party Actions are pinned to full commit SHAs with release-tag comments. Dependabot is limited to the `github-actions` ecosystem; source image, PECL, and checksum changes remain reviewed freshness findings rather than automatic mutations.

## Workflow responsibilities

### `smoke-test`

Purpose: build validation once per pull request and again after integration into
the protected `main` branch. Feature-branch pushes do not start a duplicate
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
- Do not weaken publisher or registry gates to fix a source smoke failure.

### `verify-published-manifest`

Purpose: Observe Docker Hub and GHCR published multi-arch manifests.

Expected platforms:

- `linux/amd64`
- `linux/arm64`

Triage:

1. Check whether the failure happened immediately after a production promotion.
2. If yes, compare both registries by exact digest and allow only bounded registry propagation delay.
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
```

## Branch protection rollback

If a required check name is changed accidentally:

1. Restore the previous required status check name in GitHub branch protection: `docker-smoke`.
2. Re-run the PR workflow.
3. Confirm the check appears on the PR as a required check.
4. Only then merge or re-enable stricter settings.
