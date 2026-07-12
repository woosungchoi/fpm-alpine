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
    if type(data.get("schemaVersion")) is not int or data.get("schemaVersion") != 1:
        errors.append(f"{name} schemaVersion must be integer 1")
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
    iconv = data.get("iconv")
    if not isinstance(iconv, dict) or tuple(iconv) != ("implementation", "version"):
        errors.append(f"{name} iconv contract is invalid")
    elif not all(isinstance(iconv.get(field), str) and iconv[field] for field in iconv):
        errors.append(f"{name} iconv values are invalid")
    if type(data.get("fpmConfigValid")) is not bool:
        errors.append(f"{name} fpmConfigValid must be boolean")
    return errors


def compare(baseline: Any, candidate: Any, expected_minor: str) -> list[str]:
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
    if baseline["modules"] != candidate["modules"]:
        removed = sorted(set(baseline["modules"]) - set(candidate["modules"]))
        added = sorted(set(candidate["modules"]) - set(baseline["modules"]))
        errors.append(f"PHP module set drift: removed={removed}, added={added}")
    if baseline["iconv"] != candidate["iconv"]:
        errors.append("iconv runtime contract drift")
    if candidate["fpmConfigValid"] is not True:
        errors.append("candidate FPM configuration is invalid")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("expected_minor")
    args = parser.parse_args()
    baseline = json.loads(Path(args.baseline).read_text())
    candidate = json.loads(Path(args.candidate).read_text())
    errors = compare(baseline, candidate, args.expected_minor)
    if errors:
        for error in errors:
            print(f"image contract rejected: {error}")
        return 1
    print(
        f"image_contract=PASS minor={args.expected_minor} platform={candidate['platform']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
