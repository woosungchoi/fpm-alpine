#!/usr/bin/env python3
"""Fail-closed metadata preselection for dependency auto-merge PRs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

AUTOMATION_BRANCH = re.compile(
    r"^automation/(?:base-8\.[2-5]|pecl-(?:imagick|redis|apcu))-[0-9a-f]{12}$"
)
DEPENDABOT_BRANCH = re.compile(
    r"^dependabot/github_actions/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$"
)
BOT_LOGIN = re.compile(r"^[A-Za-z0-9_.-]+\[bot\]$")


def select(rows: Any, repository: str) -> tuple[list[int], dict[Any, list[str]]]:
    selected: list[int] = []
    rejected: dict[Any, list[str]] = {}
    if not isinstance(rows, list) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        return selected, {"input": ["invalid rows or repository"]}
    for index, row in enumerate(rows):
        reasons: list[str] = []
        key: Any = f"index-{index}"
        if not isinstance(row, dict):
            rejected[key] = ["row must be an object"]
            continue
        number = row.get("number")
        if type(number) is not int or number < 1:
            reasons.append("number must be a positive integer")
        else:
            key = number
        author = row.get("author")
        login = author.get("login") if isinstance(author, dict) else None
        head_repository = row.get("headRepository")
        head_name = (
            head_repository.get("nameWithOwner")
            if isinstance(head_repository, dict)
            else None
        )
        if row.get("baseRefName") != "main":
            reasons.append("base branch is not main")
        if row.get("isCrossRepository") is not False or head_name != repository:
            reasons.append("PR head is not from the canonical repository")
        if row.get("isDraft") is not False:
            reasons.append("PR is draft or draft state is missing")
        if row.get("reviewDecision") == "CHANGES_REQUESTED":
            reasons.append("changes have been requested")
        branch = row.get("headRefName")
        if not isinstance(branch, str):
            reasons.append("head branch is missing")
        elif AUTOMATION_BRANCH.fullmatch(branch):
            if (
                not isinstance(login, str)
                or not BOT_LOGIN.fullmatch(login)
                or login == "dependabot[bot]"
            ):
                reasons.append("automation branch author is not an updater bot")
        elif DEPENDABOT_BRANCH.fullmatch(branch):
            if login != "dependabot[bot]":
                reasons.append("Dependabot branch author is not dependabot[bot]")
        else:
            reasons.append("head branch is outside the dependency automation allowlist")
        if reasons:
            rejected[key] = reasons
        elif type(number) is int:
            selected.append(number)
    return sorted(selected), rejected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--selected-output", required=True)
    parser.add_argument("--report-output", required=True)
    args = parser.parse_args()
    rows = json.loads(Path(args.input).read_text())
    selected, rejected = select(rows, args.repository)
    Path(args.selected_output).write_text("".join(f"{number}\n" for number in selected))
    Path(args.report_output).write_text(
        json.dumps(
            {"schemaVersion": 1, "selected": selected, "rejected": rejected},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"selected={len(selected)} rejected={len(rejected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
