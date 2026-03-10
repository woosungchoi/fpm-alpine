# fpm-alpine refactoring roadmap

This document records the cleanup direction used to reduce branch/version drift without mixing legacy PHP 7.4 implementation history into the active lines.

## Goals

- make the repository safe to read and use
- reduce confusion between the intended mainline, supported versions, and Docker tags
- reflect the support policy clearly: **supported branches are `8.0` through `8.5`**
- treat **PHP 7.4 / legacy `master` as history only**
- avoid breaking production users who already pin explicit tags like `8.3`

## Phase 1 — documentation and guardrails

Status: **implemented**

- [x] Rewrite `README.md` so it no longer reads like `master` is the current PHP line
- [x] Document the current branch-to-version mapping
- [x] Mark PHP 7.4 / `master` as legacy and no longer updated
- [x] Add a clear warning to pin explicit tags instead of assuming `latest`
- [x] Document the supported branch set as `8.0` through `8.5`
- [x] Add policy / roadmap docs for follow-up work

## Phase 2 — align branch policy with current operations

Status: **mostly implemented; remaining checks are external/platform verification**

Chosen direction:

- [x] Treat **version branches** as the supported release lines
- [x] Treat **`8.5`** as the current conceptual mainline / default branch
- [x] Treat **legacy `master` / PHP 7.4** as frozen historical state
- [x] Document that Docker Hub active branch builds should target **`8.0` through `8.5`**
- [x] State that `latest`, if it exists, should follow **`8.5`**
- [x] Switch GitHub default branch to `8.5`
- [x] Delete remote `master`
- [ ] Verify Docker Hub `latest` mapping follows `8.5` if `latest` is still published

Notes:

- Documentation/policy state has been ported onto `8.5` without bringing over legacy PHP 7.4 Dockerfile logic.
- Legacy `master` history can be kept under an archive ref locally if historical recovery is ever needed.
