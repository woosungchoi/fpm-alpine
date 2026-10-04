#!/usr/bin/env python3
"""Compare a built image contract with the current published minor contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

PLATFORM = re.compile(r"^linux/(amd64|arm64)$")
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _validate(name: str, data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{name} contract root must be an object"]
    if type(data.get("schemaVersion")) is not int or data.get("schemaVersion") != 2:
        errors.append(f"{name} schemaVersion must be integer 2")
    if not isinstance(data.get("platform"), str) or not PLATFORM.fullmatch(
        data["platform"]
    ):
        errors.append(f"{name} platform is invalid")
    if not isinstance(data.get("phpVersion"), str) or not SEMVER.fullmatch(
        data["phpVersion"]
    ):
        errors.append(f"{name} phpVersion is invalid")
    for field in ("packages", "modules"):
        value = data.get(field)
        if (
            not isinstance(value, list)
            or not all(isinstance(item, str) and item for item in value)
            or value != sorted(set(value))
        ):
            errors.append(f"{name} {field} must be a sorted unique string list")
    evidence = data.get("packageEvidence")
    expected_arch = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}.get(data.get("platform"))
    if (not isinstance(evidence, list) or not evidence or
        any(not isinstance(row, dict) or set(row) != {"name", "version", "architecture"} or
            any(not isinstance(row.get(k), str) or not row[k] for k in ("name", "version", "architecture")) or
            row.get("architecture") not in {expected_arch, "noarch"} for row in evidence)):
        errors.append(f"{name} package evidence is invalid")
    elif [row["name"] for row in evidence] != data.get("packages"):
        errors.append(f"{name} package evidence names do not match packages")
    iconv = data.get("iconv")
    if not isinstance(iconv, dict) or tuple(iconv) != ("implementation", "version"):
        errors.append(f"{name} iconv contract is invalid")
    elif not all(isinstance(iconv.get(field), str) and iconv[field] for field in iconv):
        errors.append(f"{name} iconv values are invalid")
    if type(data.get("fpmConfigValid")) is not bool:
        errors.append(f"{name} fpmConfigValid must be boolean")
    return errors


def compare(baseline: Any, candidate: Any, expected_minor: str, approved: tuple = ()) -> list[str]:
    errors = _validate("baseline", baseline) + _validate("candidate", candidate)
    if errors:
        return errors
    if not re.fullmatch(r"8\.[2-5]", expected_minor):
        return errors + ["expected minor is invalid"]
    if baseline["platform"] != candidate["platform"]:
        errors.append("platform mismatch")
    for name, row in (("baseline", baseline), ("candidate", candidate)):
        if not row["phpVersion"].startswith(expected_minor + "."):
            errors.append(f"{name} PHP minor mismatch")
    if baseline["packages"] != candidate["packages"]:
        removed = sorted(set(baseline["packages"]) - set(candidate["packages"]))
        added = sorted(set(candidate["packages"]) - set(baseline["packages"]))
        errors.append(f"package set drift: removed={removed}, added={added}")
    if baseline["packageEvidence"] != candidate["packageEvidence"]:
        old = {row["name"]: row for row in baseline["packageEvidence"]}
        changed = []
        for row in candidate["packageEvidence"]:
            prior = old.get(row["name"])
            if prior == row:
                continue
            transition = (row["name"], (prior or {}).get("version"), row["version"], row["architecture"])
            if not prior or prior["architecture"] != row["architecture"] or transition not in approved:
                changed.append({"name": row["name"], "before": prior, "after": row})
        if changed:
            errors.append(f"package version/architecture drift (requires review): {changed}")
    if baseline["modules"] != candidate["modules"]:
        removed = sorted(set(baseline["modules"]) - set(candidate["modules"]))
        added = sorted(set(candidate["modules"]) - set(baseline["modules"]))
        errors.append(f"PHP module set drift: removed={removed}, added={added}")
    if baseline["iconv"] != candidate["iconv"]:
        errors.append("iconv runtime contract drift")
    if candidate["fpmConfigValid"] is not True:
        errors.append("candidate FPM configuration is invalid")
    return errors


def load_approved_transitions(path: Path) -> tuple:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) != {"schemaVersion", "transitions"} or type(data["schemaVersion"]) is not int or data["schemaVersion"] != 1 or not isinstance(data["transitions"], list):
        raise ValueError("invalid APK transition policy")
    result = []
    for row in data["transitions"]:
        if not isinstance(row, dict) or set(row) != {"name", "from", "to", "architectures", "evidence", "reason"}:
            raise ValueError("invalid APK transition record")
        if not all(isinstance(row[k], str) and row[k] for k in ("name", "from", "to", "evidence", "reason")):
            raise ValueError("empty APK transition field")
        if not re.fullmatch(r"[a-z0-9][a-z0-9+_.-]*", row["name"]) or not all(re.fullmatch(r"[0-9][A-Za-z0-9._+~-]*-r[0-9]+", row[k]) for k in ("from", "to")) or row["from"] == row["to"]:
            raise ValueError("invalid exact APK package/version transition")
        if not isinstance(row["architectures"], list) or not row["architectures"] or any(arch not in ("x86_64", "aarch64", "noarch") for arch in row["architectures"]):
            raise ValueError("invalid APK transition architectures")
        if not re.fullmatch(r"https://gitlab\.alpinelinux\.org/alpine/aports/-/commit/[0-9a-f]{40}", row["evidence"]):
            raise ValueError("APK transition needs exact official source commit evidence")
        result.extend((row["name"], row["from"], row["to"], arch) for arch in row["architectures"])
    if len(result) != len(set(result)):
        raise ValueError("duplicate APK transition")
    return tuple(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("expected_minor")
    parser.add_argument("--approved-apk-transitions", type=Path, help="Explicit reviewed exact version transitions; default rejects all drift")
    args = parser.parse_args()
    baseline = json.loads(Path(args.baseline).read_text())
    candidate = json.loads(Path(args.candidate).read_text())
    approved = load_approved_transitions(args.approved_apk_transitions) if args.approved_apk_transitions else ()
    errors = compare(baseline, candidate, args.expected_minor, approved)
    if errors:
        for error in errors:
            print(f"image contract rejected: {error}")
        return 1
    before = {row["name"]: row for row in baseline["packageEvidence"]}
    for row in candidate["packageEvidence"]:
        if before[row["name"]] != row:
            print(f"reviewed_apk_transition={row['name']} {before[row['name']]['version']} -> {row['version']} ({row['architecture']})")
    print(
        f"image_contract=PASS minor={args.expected_minor} platform={candidate['platform']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
