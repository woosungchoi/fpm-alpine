#!/usr/bin/env python3
"""Validate and safely project a frozen automatic promotion plan."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

REPOSITORY = "woosungchoi/fpm-alpine"
WORKFLOW_PATH = ".github/workflows/dependency-auto-publish.yml"
DOCKERHUB_REPOSITORY = "docker.io/woosungchoi/fpm-alpine"
GHCR_REPOSITORY = "ghcr.io/woosungchoi/fpm-alpine"
PLATFORMS = ["linux/amd64", "linux/arm64"]
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
SHA = re.compile(r"[0-9a-f]{40}")
TOP_LEVEL_KEYS = {
    "schema_version",
    "operation",
    "repository",
    "workflow_path",
    "workflow_sha",
    "run_id",
    "run_attempt",
    "source_sha",
    "release_units",
}
UNIT_KEYS = {
    "php_minor",
    "php_patch",
    "canary_ref",
    "target_ghcr_digest",
    "target_dockerhub_digest",
    "dockerhub_source_digest",
    "previous_dockerhub_digest",
    "previous_ghcr_digest",
    "rollback_dockerhub_ref",
    "rollback_dockerhub_backup_digest",
    "rollback_ghcr_ref",
    "rollback_ghcr_digest",
    "platforms",
}


def exact_int(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise SystemExit(f"invalid {label}")
    return value


def exact_string(value: object, pattern: re.Pattern[str], label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise SystemExit(f"invalid {label}")
    return value


def nullable_digest(value: object, label: str) -> str | None:
    if value is None:
        return None
    return exact_string(value, DIGEST, label)


def active_versions(path: Path) -> list[tuple[str, str]]:
    payload = json.loads(path.read_text())
    versions = payload.get("versions")
    if type(versions) is not dict:
        raise SystemExit("versions manifest is invalid")
    return [
        (minor, row["patch"])
        for minor, row in versions.items()
        if type(row) is dict and row.get("support") in {"active", "security-only"}
    ]


def validate(args: argparse.Namespace) -> dict:
    payload = json.loads(args.plan.read_text())
    if type(payload) is not dict or set(payload) != TOP_LEVEL_KEYS:
        raise SystemExit("promotion plan top-level keys do not match schema v1")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise SystemExit("promotion plan schema_version must be integer 1")
    operation = payload["operation"]
    if type(operation) is not str or operation not in {"automatic", "backfill-ghcr"}:
        raise SystemExit("invalid promotion operation")
    if args.operation is not None and operation != args.operation:
        raise SystemExit("promotion operation mismatch")
    if payload["repository"] != REPOSITORY or type(payload["repository"]) is not str:
        raise SystemExit("promotion plan repository mismatch")
    if payload["workflow_path"] != WORKFLOW_PATH or type(payload["workflow_path"]) is not str:
        raise SystemExit("promotion plan workflow path mismatch")
    workflow_sha = exact_string(payload["workflow_sha"], SHA, "workflow SHA")
    source_sha = exact_string(payload["source_sha"], SHA, "source SHA")
    run_id = exact_int(payload["run_id"], "run ID")
    run_attempt = exact_int(payload["run_attempt"], "run attempt")
    if args.workflow_sha is not None and workflow_sha != args.workflow_sha:
        raise SystemExit("promotion plan workflow SHA mismatch")
    if args.source_sha is not None and source_sha != args.source_sha:
        raise SystemExit("promotion plan source SHA mismatch")
    if args.run_id is not None and run_id != args.run_id:
        raise SystemExit("promotion plan run ID mismatch")
    if args.run_attempt is not None and run_attempt != args.run_attempt:
        raise SystemExit("promotion plan run attempt mismatch")

    units = payload["release_units"]
    if type(units) is not list:
        raise SystemExit("promotion plan release_units must be a list")
    observed_versions: list[tuple[str, str]] = []
    for index, unit in enumerate(units):
        label = f"release unit {index + 1}"
        if type(unit) is not dict or set(unit) != UNIT_KEYS:
            raise SystemExit(f"{label} keys do not match schema v1")
        minor = exact_string(unit["php_minor"], re.compile(r"8\.[2-5]"), f"{label} minor")
        patch = exact_string(
            unit["php_patch"], re.compile(rf"{re.escape(minor)}\.[0-9]+"), f"{label} patch"
        )
        observed_versions.append((minor, patch))
        canary_ref = unit["canary_ref"]
        expected_canary = f"{GHCR_REPOSITORY}:canary-{minor}-{run_id}-{run_attempt}"
        if type(canary_ref) is not str or canary_ref != expected_canary:
            raise SystemExit(f"{label} canary ref mismatch")
        target_ghcr = exact_string(unit["target_ghcr_digest"], DIGEST, f"{label} GHCR target")
        target_dockerhub = nullable_digest(unit["target_dockerhub_digest"], f"{label} Docker Hub target")
        dockerhub_source = nullable_digest(unit["dockerhub_source_digest"], f"{label} Docker Hub source")
        previous_dockerhub = exact_string(
            unit["previous_dockerhub_digest"], DIGEST, f"{label} previous Docker Hub"
        )
        previous_ghcr = exact_string(
            unit["previous_ghcr_digest"], DIGEST, f"{label} previous GHCR"
        )
        rollback_ghcr_ref = unit["rollback_ghcr_ref"]
        expected_ghcr_ref = f"{GHCR_REPOSITORY}:rollback-auto-ghcr-{minor}-{run_id}-{run_attempt}"
        rollback_ghcr_digest = nullable_digest(
            unit["rollback_ghcr_digest"], f"{label} GHCR rollback digest"
        )
        if args.draft:
            if rollback_ghcr_ref is not None or rollback_ghcr_digest is not None:
                raise SystemExit(f"{label} draft must not carry a GHCR rollback pin")
        else:
            if type(rollback_ghcr_ref) is not str or rollback_ghcr_ref != expected_ghcr_ref:
                raise SystemExit(f"{label} GHCR rollback ref mismatch")
            if rollback_ghcr_digest != previous_ghcr:
                raise SystemExit(
                    f"{label} GHCR rollback pin does not preserve its exact baseline"
                )
        rollback_dockerhub_ref = unit["rollback_dockerhub_ref"]
        rollback_dockerhub_backup = nullable_digest(
            unit["rollback_dockerhub_backup_digest"], f"{label} Docker Hub rollback backup"
        )
        if unit["platforms"] != PLATFORMS or type(unit["platforms"]) is not list:
            raise SystemExit(f"{label} platforms mismatch")

        if operation == "automatic":
            expected_dockerhub_ref = (
                f"{GHCR_REPOSITORY}:rollback-auto-dockerhub-{minor}-{run_id}-{run_attempt}"
            )
            if args.draft:
                if rollback_dockerhub_ref is not None or rollback_dockerhub_backup is not None:
                    raise SystemExit(
                        f"{label} draft must not carry a Docker Hub rollback pin"
                    )
            else:
                if (
                    type(rollback_dockerhub_ref) is not str
                    or rollback_dockerhub_ref != expected_dockerhub_ref
                ):
                    raise SystemExit(f"{label} Docker Hub rollback ref mismatch")
                if rollback_dockerhub_backup != previous_dockerhub:
                    raise SystemExit(
                        f"{label} Docker Hub rollback backup does not preserve its exact baseline"
                    )
            if args.draft:
                if target_dockerhub is not None or dockerhub_source is not None:
                    raise SystemExit(f"{label} automatic draft must defer the Docker Hub target")
            elif target_dockerhub != target_ghcr or dockerhub_source is not None:
                raise SystemExit(
                    f"{label} automatic Docker Hub target must be the digest-preserved staged subject"
                )
        else:
            if rollback_dockerhub_ref is not None or rollback_dockerhub_backup is not None:
                raise SystemExit(f"{label} backfill must not carry a Docker Hub rollback pin")
            if dockerhub_source is None or target_dockerhub != previous_dockerhub:
                raise SystemExit(f"{label} backfill Docker Hub source/target is invalid")
            if dockerhub_source != previous_dockerhub:
                raise SystemExit(f"{label} backfill source no longer matches the Docker Hub baseline")
        if target_ghcr == previous_ghcr:
            raise SystemExit(f"{label} target must differ from the GHCR rollback baseline")

    expected_versions = active_versions(args.versions_file)
    if observed_versions != expected_versions:
        raise SystemExit("promotion plan does not match the exact active version matrix")
    return payload


def emit_tsv(payload: dict) -> None:
    for unit in payload["release_units"]:
        values = [
            unit["php_minor"],
            unit["php_patch"],
            payload["source_sha"],
            unit["canary_ref"],
            unit["target_ghcr_digest"],
            unit["target_dockerhub_digest"],
            unit["dockerhub_source_digest"],
            unit["previous_dockerhub_digest"],
            unit["previous_ghcr_digest"],
            unit["rollback_dockerhub_ref"],
            unit["rollback_dockerhub_backup_digest"],
            unit["rollback_ghcr_ref"],
            unit["rollback_ghcr_digest"],
        ]
        print("\t".join("-" if value is None else str(value) for value in values))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("--operation", choices=("automatic", "backfill-ghcr"))
    parser.add_argument("--workflow-sha")
    parser.add_argument("--source-sha")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--run-attempt", type=int)
    parser.add_argument(
        "--versions-file",
        type=Path,
        default=Path(os.environ.get("AUTO_PROMOTION_VERSIONS_FILE", "build/versions.json")),
    )
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--emit-tsv", action="store_true")
    args = parser.parse_args()
    if args.draft and args.emit_tsv:
        parser.error("--draft cannot be combined with --emit-tsv")
    payload = validate(args)
    if args.emit_tsv:
        emit_tsv(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
