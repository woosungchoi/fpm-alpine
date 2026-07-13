#!/usr/bin/env python3
"""Create or apply an inventory-bound Docker Hub tag deletion plan."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from dockerhub_tag_policy import (
    CONFIRMATION,
    PruneError,
    apply_deletion_plan,
    authenticate,
    build_deletion_plan,
    deletion_plan_sha256,
    fetch_inventory,
)


def load_object(path: Path) -> dict:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def write_object(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def plan_command(output: Path) -> int:
    plan = build_deletion_plan(fetch_inventory())
    write_object(output, plan)
    print(
        f"dockerhub_prune_plan=PASS inventory_sha256={plan['inventory_sha256']} "
        f"deletion_plan_sha256={deletion_plan_sha256(plan)} delete_count={len(plan['delete'])}"
    )
    return 0


def apply_command(args: argparse.Namespace) -> int:
    if args.confirmation != CONFIRMATION:
        raise ValueError("exact destructive confirmation phrase mismatch")
    plan = load_object(args.plan)
    archive = load_object(args.archive_map)
    actual_plan_sha = deletion_plan_sha256(plan)
    if plan.get("inventory_sha256") != args.expected_inventory_sha256:
        raise ValueError("inventory SHA-256 does not match the bound plan")
    if actual_plan_sha != args.expected_deletion_plan_sha256:
        raise ValueError("deletion plan SHA-256 mismatch")
    username = os.environ.get("DOCKERHUB_USERNAME", "")
    secret = os.environ.get("DOCKERHUB_TOKEN", "")
    access_token = authenticate(username, secret)
    try:
        result = apply_deletion_plan(plan, archive, access_token=access_token)
    except PruneError as error:
        write_object(args.result_output, error.result)
        print(
            f"dockerhub_prune=PARTIAL_FAILURE deleted={len(error.result['deleted'])} "
            f"failed={len(error.result['failed'])}",
            file=sys.stderr,
        )
        return 1
    write_object(args.result_output, result)
    print(
        f"dockerhub_prune=PASS deleted={len(result['deleted'])} "
        f"already_absent={len(result['already_absent'])} "
        f"final_inventory_sha256={result['final_inventory_sha256']}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--output", type=Path, required=True)
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--archive-map", type=Path, required=True)
    apply_parser.add_argument("--expected-inventory-sha256", required=True)
    apply_parser.add_argument("--expected-deletion-plan-sha256", required=True)
    apply_parser.add_argument("--confirmation", required=True)
    apply_parser.add_argument("--result-output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "plan":
        return plan_command(args.output)
    return apply_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
