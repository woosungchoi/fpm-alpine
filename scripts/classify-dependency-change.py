#!/usr/bin/env python3
"""Classify a versions manifest diff for fail-closed dependency automation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
ALLOWED_FILES = {"build/versions.json"}


def _load_validator():
    path = Path(__file__).with_name("validate-versions.py")
    spec = importlib.util.spec_from_file_location("fpm_validate_versions", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = SEMVER.fullmatch(value)
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _result(
    eligible: bool,
    kind: str,
    affected: list[str],
    changed: list[str],
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "eligible": eligible,
        "class": kind,
        "affectedMinors": affected,
        "changedKeys": changed,
        "blockedReasons": reasons,
    }


def classify(
    base: Any,
    head: Any,
    policy: Any,
    changed_files: list[str],
) -> dict[str, Any]:
    reasons: list[str] = []
    unknown_files = sorted(set(changed_files) - ALLOWED_FILES)
    if unknown_files:
        reasons.append(
            "changed file outside dependency automation allowlist: "
            + ", ".join(unknown_files)
        )
    validator = _load_validator()
    base_errors = validator.validate(base, policy)
    head_errors = validator.validate(head, policy)
    if base_errors:
        reasons.append("base manifest invalid: " + "; ".join(base_errors))
    if head_errors:
        reasons.append("manual-only head manifest invalid: " + "; ".join(head_errors))
    if reasons:
        return _result(False, "invalid", [], [], reasons)

    if base == head:
        return _result(False, "none", [], [], ["empty dependency change"])

    changed_roots = [
        key
        for key in ("schemaVersion", "dependencies", "runtimeContracts", "versions")
        if base[key] != head[key]
    ]
    if any(key in changed_roots for key in ("schemaVersion", "runtimeContracts")):
        return _result(
            False,
            "manual-only",
            [],
            changed_roots,
            ["manual-only schema or runtime contract change"],
        )
    if set(changed_roots) == {"dependencies", "versions"}:
        return _result(
            False,
            "mixed",
            [],
            changed_roots,
            ["mixed base image and PECL dependency change"],
        )

    if changed_roots == ["versions"]:
        changed_minors = [
            minor
            for minor in base["versions"]
            if base["versions"][minor] != head["versions"][minor]
        ]
        if len(changed_minors) != 1:
            return _result(
                False,
                "manual-only",
                [],
                [f"versions.{minor}" for minor in changed_minors],
                ["one base-image minor per automated PR is required"],
            )
        minor = changed_minors[0]
        old = base["versions"][minor]
        new = head["versions"][minor]
        fields = [key for key in old if old[key] != new[key]]
        blocked = sorted(set(fields) - {"patch", "base_image"})
        if blocked:
            return _result(
                False,
                "manual-only",
                [],
                [f"versions.{minor}.{field}" for field in fields],
                ["manual-only version fields changed: " + ", ".join(blocked)],
            )
        old_patch, new_patch = _version(old["patch"]), _version(new["patch"])
        if (
            old_patch is None
            or new_patch is None
            or old_patch[:2] != new_patch[:2]
            or new_patch < old_patch
        ):
            return _result(
                False,
                "manual-only",
                [],
                [f"versions.{minor}.{field}" for field in fields],
                ["base image update must stay in the same minor and never downgrade"],
            )
        return _result(
            True,
            "base-same-minor",
            [minor],
            [f"versions.{minor}.{field}" for field in fields],
            [],
        )

    if changed_roots == ["dependencies"]:
        changed_dependencies = [
            name
            for name in base["dependencies"]
            if base["dependencies"][name] != head["dependencies"][name]
        ]
        if len(changed_dependencies) != 1:
            return _result(
                False,
                "manual-only",
                [],
                [f"dependencies.{name}" for name in changed_dependencies],
                ["one PECL dependency per automated PR is required"],
            )
        name = changed_dependencies[0]
        old = base["dependencies"][name]
        new = head["dependencies"][name]
        old_version, new_version = _version(old["version"]), _version(new["version"])
        fields = [key for key in old if old[key] != new[key]]
        if (
            old_version is None
            or new_version is None
            or old_version[:2] != new_version[:2]
            or new_version <= old_version
        ):
            return _result(
                False,
                "manual-only",
                [],
                [f"dependencies.{name}.{field}" for field in fields],
                ["only a strictly newer PECL patch-level update is eligible"],
            )
        return _result(
            True,
            "pecl-patch",
            list(policy["lifecycle"]),
            [f"dependencies.{name}.{field}" for field in fields],
            [],
        )

    return _result(
        False, "manual-only", [], changed_roots, ["unsupported dependency change shape"]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-json", required=True)
    parser.add_argument("--head-json", required=True)
    parser.add_argument("--policy", default="build/automation-policy.json")
    parser.add_argument("--changed-files", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    base = json.loads(Path(args.base_json).read_text())
    head = json.loads(Path(args.head_json).read_text())
    policy = json.loads(Path(args.policy).read_text())
    files_payload = json.loads(Path(args.changed_files).read_text())
    if not isinstance(files_payload, list) or not all(
        isinstance(item, str) for item in files_payload
    ):
        raise SystemExit("changed-files must be a JSON string array")
    result = classify(base, head, policy, files_payload)
    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(payload)
    else:
        print(payload, end="")
    return 0 if result["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
