#!/usr/bin/env python3
"""Select the newest exact-source immutable cutover lease run."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, cast

SOURCE = re.compile(r"^[0-9a-f]{40}$")
WORKFLOW_PATH = ".github/workflows/legacy-cutover-lease.yml"
TRUSTED_ACTOR_ID = 5674610
TRUSTED_ACTOR_LOGIN = "woosungchoi"
MAX_RUN_AGE_SECONDS = 900
MAX_FUTURE_SKEW_SECONDS = 60


def parse_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def exact_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def select_run(
    payload: object,
    source_sha: str,
    repository: str,
    now: dt.datetime,
) -> dict[str, int]:
    if not SOURCE.fullmatch(source_sha) or repository != "woosungchoi/fpm-alpine":
        raise SystemExit("invalid fresh cutover lease query identity")
    if now.tzinfo is None:
        raise SystemExit("fresh cutover lease clock must be timezone-aware")
    now = now.astimezone(dt.timezone.utc)
    if type(payload) is not dict or type(payload.get("workflow_runs")) is not list:
        raise SystemExit("invalid fresh cutover lease run listing")

    candidates: list[tuple[dt.datetime, int, int]] = []
    for row in payload["workflow_runs"]:
        if type(row) is not dict:
            continue
        actor = row.get("actor")
        repository_row = row.get("repository")
        head_repository = row.get("head_repository")
        created = parse_time(row.get("created_at"))
        run_id = row.get("id")
        attempt = row.get("run_attempt")
        if (
            not exact_positive_int(run_id)
            or not exact_positive_int(attempt)
            or row.get("path") != WORKFLOW_PATH
            or row.get("event") != "repository_dispatch"
            or row.get("head_branch") != "main"
            or row.get("head_sha") != source_sha
            or row.get("status") != "completed"
            or row.get("conclusion") != "success"
            or type(actor) is not dict
            or actor.get("id") != TRUSTED_ACTOR_ID
            or actor.get("login") != TRUSTED_ACTOR_LOGIN
            or type(repository_row) is not dict
            or repository_row.get("full_name") != repository
            or type(head_repository) is not dict
            or head_repository.get("full_name") != repository
            or created is None
        ):
            continue
        age = (now - created).total_seconds()
        if age < -MAX_FUTURE_SKEW_SECONDS or age > MAX_RUN_AGE_SECONDS:
            continue
        candidates.append((created, cast(int, run_id), cast(int, attempt)))

    if not candidates:
        raise SystemExit("no fresh trusted cutover lease run exists for the exact source")
    _, run_id, attempt = max(candidates, key=lambda row: (row[0], row[1]))
    return {"runId": run_id, "runAttempt": attempt}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload: Any = json.loads(args.runs.read_text())
    selected = select_run(
        payload,
        args.source_sha,
        args.repository,
        dt.datetime.now(dt.timezone.utc),
    )
    args.output.write_text(json.dumps(selected, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
