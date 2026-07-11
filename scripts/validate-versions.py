#!/usr/bin/env python3
"""Validate the reproducible PHP build matrix and emit selected validated data."""

from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

EXPECTED_VERSIONS = {
    "8.2": (
        "8.2.32",
        "php:8.2-fpm-alpine@sha256:41ddda74d95c43518c3e4414e6c1c99f9c062d397f0c7a2d8cadf8d1f035d196",
        "security-only",
        "2026-12-31",
    ),
    "8.3": (
        "8.3.32",
        "php:8.3-fpm-alpine@sha256:9fcec48321d890240d700ccdc2b475420c87d398826e68c3d8830b8fca663e5c",
        "security-only",
        "2027-12-31",
    ),
    "8.4": (
        "8.4.23",
        "php:8.4-fpm-alpine@sha256:913ddd6934a805429618a16aa36da47cd8a8aec8b2f111c294936ba4003fded6",
        "active",
        "2028-12-31",
    ),
    "8.5": (
        "8.5.8",
        "php:8.5-fpm-alpine@sha256:79def1d16ece3ab1a6656c46a23bfd80ad33887fbd33626e7bd743cef54ef9c6",
        "active",
        "2029-12-31",
    ),
}
EXPECTED_DEPENDENCIES = {
    "imagick": (
        "3.8.1",
        "https://pecl.php.net/get/imagick-3.8.1.tgz",
        "3a3587c0a524c17d0dad9673a160b90cd776e836838474e173b549ed864352ee",
    ),
    "redis": (
        "6.3.0",
        "https://pecl.php.net/get/redis-6.3.0.tgz",
        "0d5141f634bd1db6c1ddcda053d25ecf2c4fc1c395430d534fd3f8d51dd7f0b5",
    ),
    "apcu": (
        "5.1.28",
        "https://pecl.php.net/get/apcu-5.1.28.tgz",
        "ca9c1820810a168786f8048a4c3f8c9e3fd941407ad1553259fb2e30b5f057bf",
    ),
}
ICONV_FIELDS = (
    "implementation",
    "version",
    "package",
    "packageVersion",
    "ownerPath",
    "target",
)
EXPECTED_ICONV = (
    "libiconv",
    "1.18",
    "gnu-libiconv-libs",
    "1.18-r0",
    "/usr/lib/libiconv.so.2",
    "/usr/lib/libiconv.so.2.7.0",
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def require(ok: bool, message: str, errors: list[str]) -> None:
    if not ok:
        errors.append(message)


def validate(data: Any) -> list[str]:
    errors = []
    if not isinstance(data, dict):
        return ["root must be an object"]
    require(
        list(data) == ["schemaVersion", "dependencies", "runtimeContracts", "versions"],
        "root keys/order must be schemaVersion, dependencies, runtimeContracts, versions",
        errors,
    )
    require(
        type(data.get("schemaVersion")) is int and data.get("schemaVersion") == 2,
        "schemaVersion must be integer 2",
        errors,
    )
    deps, contracts, versions = (
        data.get("dependencies"),
        data.get("runtimeContracts"),
        data.get("versions"),
    )
    if not all(isinstance(x, dict) for x in (deps, contracts, versions)):
        return errors + ["dependencies, runtimeContracts, and versions must be objects"]
    require(
        list(deps) == list(EXPECTED_DEPENDENCIES),
        "dependency keys/order must be imagick, redis, apcu",
        errors,
    )
    require(
        list(contracts) == ["libiconv"],
        "runtime contract keys/order must be exactly libiconv",
        errors,
    )
    iconv = contracts.get("libiconv")
    if isinstance(iconv, dict):
        require(
            tuple(iconv) == ICONV_FIELDS,
            "libiconv runtime contract fields/order is invalid",
            errors,
        )
        require(
            tuple(iconv.get(k) for k in ICONV_FIELDS) == EXPECTED_ICONV,
            "libiconv runtime contract does not match approved official base contract",
            errors,
        )
    else:
        errors.append("libiconv runtime contract must be an object")
    require(
        list(versions) == list(EXPECTED_VERSIONS),
        "version keys/order must be exactly 8.2, 8.3, 8.4, 8.5",
        errors,
    )
    for name, expected in EXPECTED_DEPENDENCIES.items():
        item = deps.get(name)
        if not isinstance(item, dict):
            errors.append(f"dependency {name} must be an object")
            continue
        require(
            tuple(item) == ("version", "url", "sha256"),
            f"dependency {name} has missing/extra/reordered fields",
            errors,
        )
        require(
            tuple(item.get(k) for k in ("version", "url", "sha256")) == expected,
            f"dependency {name} pin does not match approved version/url/checksum",
            errors,
        )
        require(
            isinstance(item.get("sha256"), str)
            and bool(SHA256.fullmatch(item["sha256"])),
            f"dependency {name} sha256 must be 64 lowercase hex characters",
            errors,
        )
    refs = []
    for minor, expected in EXPECTED_VERSIONS.items():
        item = versions.get(minor)
        if not isinstance(item, dict):
            errors.append(f"version {minor} must be an object")
            continue
        require(
            set(item) == {"minor", "patch", "base_image", "support", "eol"},
            f"version {minor} has missing/extra fields",
            errors,
        )
        patch, ref = item.get("patch"), item.get("base_image")
        match = SEMVER.fullmatch(patch) if isinstance(patch, str) else None
        require(
            item.get("minor") == minor,
            f"version {minor} minor field must match its key",
            errors,
        )
        require(
            bool(match) and ".".join(match.groups()[:2]) == minor,
            f"version {minor} patch must be a corresponding semantic version",
            errors,
        )
        require(
            (patch, ref, item.get("support"), item.get("eol")) == expected,
            f"version {minor} patch/base image/support/eol does not match approved metadata",
            errors,
        )
        require(
            isinstance(ref, str)
            and bool(
                re.fullmatch(
                    rf"php:{re.escape(minor)}-fpm-alpine@sha256:[0-9a-f]{{64}}", ref
                )
            ),
            f"version {minor} base_image must be an exact minor digest ref",
            errors,
        )
        if isinstance(ref, str):
            refs.append(ref)
    require(len(refs) == len(set(refs)), "base image refs must be unique", errors)
    return errors


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("path", nargs="?", default="build/versions.json")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--matrix", action="store_true")
    out.add_argument("--get-base", metavar="MINOR")
    args = p.parse_args()
    try:
        data = json.loads(Path(args.path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"versions validation failed: {exc}", file=sys.stderr)
        return 1
    errors = validate(data)
    if errors:
        for e in errors:
            print(f"versions validation failed: {e}", file=sys.stderr)
        return 1
    if args.matrix:
        dep_args = {
            f"{n}_{k}": v for n, d in data["dependencies"].items() for k, v in d.items()
        }
        iconv = {
            f"iconv_{re.sub(r'(?<!^)(?=[A-Z])', '_', k).lower()}": v
            for k, v in data["runtimeContracts"]["libiconv"].items()
        }
        include = [
            {
                "php_minor": m,
                "php_patch": i["patch"],
                "php_base_image": i["base_image"],
                "platform": p,
                "arch": a,
                **dep_args,
                **iconv,
            }
            for m, i in data["versions"].items()
            for p, a in (("linux/amd64", "amd64"), ("linux/arm64", "arm64"))
        ]
        print(json.dumps({"include": include}, separators=(",", ":")))
    elif args.get_base:
        if args.get_base not in data["versions"]:
            print(
                f"versions validation failed: unknown PHP minor {args.get_base}",
                file=sys.stderr,
            )
            return 1
        print(data["versions"][args.get_base]["base_image"])
    else:
        print(
            f"validated {len(data['versions'])} PHP versions, {len(data['dependencies'])} source dependencies, and 1 runtime contract"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
