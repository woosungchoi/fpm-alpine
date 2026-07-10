# Pull request

## Summary

Describe the change and why it is needed.

## Scope

- [ ] The target PHP branch is identified.
- [ ] Image build or runtime behavior changes are called out explicitly.
- [ ] Docker Hub hooks and publishing behavior are unchanged, or the change is clearly justified.

## Verification

- [ ] `./tests/test_policy_scripts.sh`
- [ ] `bash -n scripts/*.sh tests/*.sh`
- [ ] Relevant image smoke test, when build behavior changes

## Policy

- [ ] Documentation agrees with [SUPPORT.md](https://github.com/woosungchoi/fpm-alpine/blob/HEAD/SUPPORT.md).
- [ ] No vulnerability details or secrets are included in this PR.
- [ ] Suspected vulnerabilities are reported only through [GitHub private vulnerability reporting](https://github.com/woosungchoi/fpm-alpine/security/advisories/new), never in a public issue or PR.
