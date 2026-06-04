# Branch sync auto-merge policy draft

This document intentionally describes a future policy only. The current `branch-sync-pr` workflow creates PRs and does **not** auto-merge them.

## Current policy

- `branch-sync-pr` may create PRs for safe workflow/script/docs/test guardrail syncs.
- Generated PRs require human review.
- Required check remains `docker-smoke` on every maintained branch.
- Docker Hub hooks remain the publish path.

## Future auto-merge allow conditions

A future workflow may consider auto-merge only when all conditions are true:

- PR author is `github-actions[bot]`.
- PR has `safe-sync` and `branch-sync` labels.
- Changed files are all listed in `docs/branch-sync-safe-files.txt`.
- No `Dockerfile`, `hooks/*`, publish-sensitive file, branch protection setting, PHP base image, `IMAGICK_VERSION`, `gnu-libiconv`, or `LD_PRELOAD` change is present.
- `docker-smoke` passes on the target branch PR.
- Branch protection still requires only the expected required checks.
- Generated PR body contains synced files, blocked/manual files, and the Docker Hub safety note.

## Auto-merge deny conditions

Never auto-merge when any of these are true:

- `Dockerfile` changed.
- `hooks/*` changed.
- Docker Hub publish path changed.
- `ARG IMAGICK_VERSION` changed.
- `gnu-libiconv` or `LD_PRELOAD` changed.
- PHP base image line changed.
- Required status checks changed.
- `docker-smoke` is missing, pending, cancelled, skipped, or failed.
- The generated plan has any blocked/manual files that require implementation in the same PR.

## Suggested future workflow name

- `.github/workflows/branch-sync-auto-merge.yml`

Keep this as a separate PR from PR generation so failures in merge policy cannot block safe PR creation.
