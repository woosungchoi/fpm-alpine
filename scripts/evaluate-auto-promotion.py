#!/usr/bin/env python3
"""Evaluate whether a trusted main commit is eligible for image auto-canary."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
from pathlib import Path
from typing import Any

COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _classifier():
    path = Path(__file__).with_name("classify-dependency-change.py")
    spec = importlib.util.spec_from_file_location(
        "promotion_dependency_classifier", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(
    source_sha: str,
    eligible: bool,
    kind: str,
    affected: list[str],
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "sourceCommit": source_sha,
        "eligible": eligible,
        "class": kind,
        "affectedMinors": affected,
        "blockedReasons": reasons,
    }


def evaluate(
    base: Any,
    head: Any,
    policy: Any,
    changed_files: list[str],
    source_sha: str,
) -> dict[str, Any]:
    if not COMMIT.fullmatch(source_sha):
        return _result(source_sha, False, "invalid", [], ["source commit is malformed"])
    if not changed_files:
        return _result(source_sha, False, "none", [], ["empty merged-main diff"])
    if changed_files == ["build/versions.json"]:
        classified = _classifier().classify(base, head, policy, changed_files)
        return _result(
            source_sha,
            classified.get("eligible") is True,
            str(classified.get("class", "invalid")),
            list(classified.get("affectedMinors", [])),
            list(classified.get("blockedReasons", [])),
        )
    if all(
        isinstance(path, str)
        and path.startswith(".github/workflows/")
        and path.endswith((".yml", ".yaml"))
        for path in changed_files
    ):
        return _result(
            source_sha,
            False,
            "actions-no-image-change",
            [],
            ["Action-only update does not change image content"],
        )
    return _result(
        source_sha,
        False,
        "manual-only",
        [],
        ["merged-main diff is outside dependency auto-promotion allowlist"],
    )


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, text=True, capture_output=True
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--policy", default="build/automation-policy.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not COMMIT.fullmatch(args.base_sha) or not COMMIT.fullmatch(args.head_sha):
        raise SystemExit("base/head SHA must be 40 lowercase hex characters")
    changed = [
        line
        for line in _git(
            "diff", "--name-only", args.base_sha, args.head_sha
        ).splitlines()
        if line
    ]
    base = json.loads(_git("show", f"{args.base_sha}:build/versions.json"))
    head = json.loads(_git("show", f"{args.head_sha}:build/versions.json"))
    policy = json.loads(Path(args.policy).read_text())
    result = evaluate(base, head, policy, changed, args.head_sha)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"auto_promotion_eligible={str(result['eligible']).lower()} "
        f"class={result['class']} affected={','.join(result['affectedMinors'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
