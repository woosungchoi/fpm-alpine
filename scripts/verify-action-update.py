#!/usr/bin/env python3
"""Verify that a workflow diff changes only allowlisted full-SHA Action pins."""

from __future__ import annotations

import argparse
import re
import subprocess
from typing import Callable

ALLOWED_OWNERS = {"actions", "docker", "sigstore"}
USE = re.compile(
    r"^\s*uses:\s*([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)@([0-9a-f]{40})\s+#\s+(v[0-9][0-9A-Za-z_.-]*)\s*$"
)


def resolve_release(owner: str, repository: str, tag: str) -> str:
    remote = f"https://github.com/{owner}/{repository}.git"
    completed = subprocess.run(
        ["git", "ls-remote", remote, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
        check=True,
        text=True,
        capture_output=True,
    )
    direct = None
    peeled = None
    for line in completed.stdout.splitlines():
        try:
            sha, ref = line.split("\t", 1)
        except ValueError:
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            continue
        if ref == f"refs/tags/{tag}^{{}}":
            peeled = sha
        elif ref == f"refs/tags/{tag}":
            direct = sha
    resolved = peeled or direct
    if resolved is None:
        raise ValueError(f"release tag not found: {owner}/{repository}@{tag}")
    return resolved


def verify_diff(
    diff: str,
    resolver: Callable[[str, str, str], str] = resolve_release,
) -> list[str]:
    errors: list[str] = []
    files = re.findall(r"^diff --git a/(.+?) b/(.+?)$", diff, re.M)
    if not files:
        return ["empty or malformed Action update diff"]
    for old, new in files:
        if (
            old != new
            or not new.startswith(".github/workflows/")
            or not new.endswith((".yml", ".yaml"))
        ):
            errors.append(
                f"non-workflow or renamed file in Action update: {old} -> {new}"
            )
    changed = [
        line[1:]
        for line in diff.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    ]
    if not changed:
        errors.append("Action update diff has no changed lines")
        return errors
    parsed = []
    for line in changed:
        match = USE.fullmatch(line)
        if not match:
            errors.append(f"Action update changed a non-pin line: {line.strip()}")
            continue
        parsed.append(match.groups())
    additions = []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        match = USE.fullmatch(line[1:])
        if match:
            additions.append(match.groups())
    if not additions:
        errors.append("Action update has no added full-SHA pin")
    if len(parsed) != len(changed):
        return errors
    for owner, repository, sha, tag in additions:
        if owner not in ALLOWED_OWNERS:
            errors.append(f"Action owner is not allowlisted: {owner}")
            continue
        try:
            release_sha = resolver(owner, repository, tag)
        except Exception as exc:
            errors.append(
                f"Action release resolution failed for {owner}/{repository}@{tag}: {exc}"
            )
            continue
        if release_sha != sha:
            errors.append(
                f"Action pin does not match release tag: {owner}/{repository}@{tag} "
                f"expected {release_sha}, got {sha}"
            )
    return errors


def _run(command: list[str]) -> str:
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args()
    for value in (args.base_sha, args.head_sha):
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise SystemExit("base/head SHA must be 40 lowercase hex characters")
    names = [
        line
        for line in _run(
            ["git", "diff", "--name-only", args.base_sha, args.head_sha]
        ).splitlines()
        if line
    ]
    workflow_files = [name for name in names if name.startswith(".github/workflows/")]
    if not workflow_files:
        print("action_update=not-applicable")
        return 0
    if set(workflow_files) != set(names):
        print("Action update is mixed with non-workflow files", flush=True)
        return 1
    diff = _run(
        [
            "git",
            "diff",
            "--unified=0",
            args.base_sha,
            args.head_sha,
            "--",
            *workflow_files,
        ]
    )
    errors = verify_diff(diff)
    if errors:
        for error in errors:
            print(f"action update rejected: {error}")
        return 1
    print(f"action_update=verified files={len(workflow_files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
