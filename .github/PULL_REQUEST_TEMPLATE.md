# Pull request

## Summary

Describe the change and why it is needed.

## Scope

- [ ] The affected `main` matrix entry or image tag is identified.
- [ ] Image build or runtime behavior changes are called out explicitly.
- [ ] Registry credentials remain unreachable from pull requests and publishing changes are clearly justified.

## Verification

- [ ] `./tests/test_policy_scripts.sh`
- [ ] `bash -n scripts/*.sh tests/*.sh`
- [ ] Relevant image smoke test, when build behavior changes

## Policy

- [ ] Documentation agrees with [SUPPORT.md](https://github.com/woosungchoi/fpm-alpine/blob/HEAD/SUPPORT.md).
- [ ] No vulnerability details or secrets are included in this PR.
- [ ] Suspected vulnerabilities are reported only through [GitHub private vulnerability reporting](https://github.com/woosungchoi/fpm-alpine/security/advisories/new), never in a public issue or PR.
