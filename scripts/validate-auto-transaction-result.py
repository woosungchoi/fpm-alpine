#!/usr/bin/env python3
"""Validate a dependency publisher transaction result and optionally emit one unit."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SHA = re.compile(r"[0-9a-f]{40}")
TOP_KEYS = {
    "schema_version",
    "status",
    "operation",
    "repository",
    "workflow_path",
    "workflow_sha",
    "run_id",
    "run_attempt",
    "source_sha",
    "plan_sha256",
    "release_units",
}
UNIT_KEYS = {"php_minor", "php_patch", "source_sha", "dockerhub_digest", "ghcr_digest"}
PLAN_TOP_KEYS = {
    "schema_version", "operation", "repository", "workflow_path", "workflow_sha",
    "run_id", "run_attempt", "source_sha", "release_units",
}
PLAN_UNIT_KEYS = {
    "php_minor", "php_patch", "canary_ref", "target_ghcr_digest",
    "target_dockerhub_digest", "dockerhub_source_digest",
    "previous_dockerhub_digest", "previous_ghcr_digest",
    "rollback_dockerhub_ref", "rollback_dockerhub_backup_digest",
    "rollback_ghcr_ref", "rollback_ghcr_digest", "platforms",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_dir", type=Path)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--minor")
    parser.add_argument("--patch")
    parser.add_argument("--versions-file", type=Path, default=Path("build/versions.json"))
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--exact-set", action="store_true")
    args = parser.parse_args()
    if (args.minor is None) != (args.patch is None):
        parser.error("--minor and --patch must be supplied together")
    if args.expected_plan_sha256 is not None and re.fullmatch(
        r"[0-9a-f]{64}", args.expected_plan_sha256
    ) is None:
        parser.error("--expected-plan-sha256 must be a lowercase SHA-256")

    if args.exact_set:
        observed_files = sorted(
            path.relative_to(args.evidence_dir).as_posix()
            for path in args.evidence_dir.rglob("*")
            if path.is_file()
        )
        expected_files = [
            "promotion-plan.json",
            "promotion-plan.sha256",
            "transaction-result.json",
        ]
        if observed_files != expected_files:
            raise SystemExit(
                f"committed transaction artifact set mismatch: {observed_files}"
            )
        if any(path.is_symlink() for path in args.evidence_dir.rglob("*")):
            raise SystemExit("committed transaction artifact must not contain symlinks")

    files = list(args.evidence_dir.glob("**/transaction-result.json"))
    if len(files) != 1:
        raise SystemExit(f"expected one transaction result, found {len(files)}")
    plan_files = list(args.evidence_dir.glob("**/promotion-plan.json"))
    checksum_files = list(args.evidence_dir.glob("**/promotion-plan.sha256"))
    if len(plan_files) != 1 or len(checksum_files) != 1:
        raise SystemExit(
            "expected exactly one promotion plan and one checksum companion"
        )
    plan_bytes = plan_files[0].read_bytes()
    observed_plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()
    expected_checksum = f"{observed_plan_sha256}  promotion-plan.json\n"
    if checksum_files[0].read_text() != expected_checksum:
        raise SystemExit("promotion plan checksum companion mismatch")
    payload = json.loads(files[0].read_text())
    if type(payload) is not dict or set(payload) != TOP_KEYS:
        raise SystemExit("transaction result top-level keys do not match schema v2")
    expected = {
        "schema_version": 2,
        "status": "verified",
        "repository": "woosungchoi/fpm-alpine",
        "workflow_path": ".github/workflows/dependency-auto-publish.yml",
        "workflow_sha": args.workflow_sha,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
    }
    for key, value in expected.items():
        observed = payload.get(key)
        if type(observed) is not type(value) or observed != value:
            raise SystemExit(f"transaction result mismatch for {key}")
    if payload.get("operation") not in {"automatic", "backfill-ghcr"}:
        raise SystemExit("transaction result operation is invalid")
    source_sha = payload.get("source_sha")
    if type(source_sha) is not str or SHA.fullmatch(source_sha) is None:
        raise SystemExit("transaction result source SHA is invalid")
    plan_sha256 = payload.get("plan_sha256")
    if type(plan_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", plan_sha256) is None:
        raise SystemExit("transaction result plan SHA-256 is invalid")
    if plan_sha256 != observed_plan_sha256:
        raise SystemExit("transaction result does not bind the exact promotion plan bytes")
    if (
        args.expected_plan_sha256 is not None
        and plan_sha256 != args.expected_plan_sha256
    ):
        raise SystemExit("transaction result does not match the requested plan SHA-256")

    plan = json.loads(plan_bytes)
    if type(plan) is not dict or set(plan) != PLAN_TOP_KEYS:
        raise SystemExit("promotion plan top-level keys do not match schema v1")
    plan_expected = {
        "schema_version": 1,
        "operation": payload["operation"],
        "repository": payload["repository"],
        "workflow_path": payload["workflow_path"],
        "workflow_sha": payload["workflow_sha"],
        "run_id": payload["run_id"],
        "run_attempt": payload["run_attempt"],
        "source_sha": source_sha,
    }
    for key, value in plan_expected.items():
        observed = plan.get(key)
        if type(observed) is not type(value) or observed != value:
            raise SystemExit(f"promotion plan/result mismatch for {key}")
    plan_validator = Path(__file__).with_name("validate-auto-promotion-plan.py")
    try:
        subprocess.run(
            [
                str(plan_validator),
                str(plan_files[0]),
                "--operation",
                payload["operation"],
                "--workflow-sha",
                payload["workflow_sha"],
                "--source-sha",
                source_sha,
                "--run-id",
                str(payload["run_id"]),
                "--run-attempt",
                str(payload["run_attempt"]),
                "--versions-file",
                str(args.versions_file),
            ],
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise SystemExit("promotion plan failed canonical validation") from error

    versions = json.loads(args.versions_file.read_text()).get("versions")
    active = [
        (minor, row["patch"])
        for minor, row in versions.items()
        if row["support"] in {"active", "security-only"}
    ]
    units = payload.get("release_units")
    if type(units) is not list or len(units) != len(active):
        raise SystemExit("transaction result does not contain the exact active matrix")
    observed_versions = []
    selected = None
    for index, unit in enumerate(units):
        if type(unit) is not dict or set(unit) != UNIT_KEYS:
            raise SystemExit(f"transaction result release unit {index + 1} keys are invalid")
        minor = unit.get("php_minor")
        patch = unit.get("php_patch")
        if type(minor) is not str or type(patch) is not str:
            raise SystemExit("transaction result version fields are invalid")
        if unit.get("source_sha") != source_sha or type(unit.get("source_sha")) is not str:
            raise SystemExit("transaction result release unit source mismatch")
        for key in ("dockerhub_digest", "ghcr_digest"):
            value = unit.get(key)
            if type(value) is not str or DIGEST.fullmatch(value) is None:
                raise SystemExit(f"transaction result {key} is invalid")
        observed_versions.append((minor, patch))
        if args.minor is not None and minor == args.minor:
            selected = unit
    if observed_versions != active:
        raise SystemExit("transaction result release units do not match active versions in order")
    plan_units = plan.get("release_units")
    if type(plan_units) is not list or len(plan_units) != len(units):
        raise SystemExit("promotion plan/result release unit count mismatch")
    for index, (unit, plan_unit) in enumerate(zip(units, plan_units, strict=True)):
        if type(plan_unit) is not dict or set(plan_unit) != PLAN_UNIT_KEYS:
            raise SystemExit(f"promotion plan release unit {index + 1} keys are invalid")
        if (
            plan_unit.get("php_minor") != unit["php_minor"]
            or plan_unit.get("php_patch") != unit["php_patch"]
            or plan_unit.get("target_ghcr_digest") != unit["ghcr_digest"]
        ):
            raise SystemExit(f"promotion plan/result target mismatch for release unit {index + 1}")
        if payload["operation"] == "backfill-ghcr":
            if (
                plan_unit.get("target_dockerhub_digest") != unit["dockerhub_digest"]
                or plan_unit.get("dockerhub_source_digest") != unit["dockerhub_digest"]
                or plan_unit.get("previous_dockerhub_digest") != unit["dockerhub_digest"]
            ):
                raise SystemExit(
                    f"backfill Docker Hub plan/result mismatch for release unit {index + 1}"
                )
        elif plan_unit.get("target_dockerhub_digest") != unit["dockerhub_digest"]:
            raise SystemExit(
                f"automatic Docker Hub plan/result mismatch for release unit {index + 1}"
            )
    if args.minor is not None:
        if selected is None or selected["php_patch"] != args.patch:
            raise SystemExit("requested release unit is absent from transaction result")
        print("\t".join((
            source_sha,
            selected["dockerhub_digest"],
            selected["ghcr_digest"],
            payload["operation"],
            plan_sha256,
        )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
