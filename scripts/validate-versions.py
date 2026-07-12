#!/usr/bin/env python3
"""Validate the reproducible PHP build matrix and emit selected validated data."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_POLICY_PATH = Path("build/automation-policy.json")
EXPECTED_MINORS = ("8.2", "8.3", "8.4", "8.5")
EXPECTED_DEPENDENCIES = ("imagick", "redis", "apcu")
EXPECTED_MANUAL_ONLY = (
    "lifecycle",
    "runtimeContracts",
    "minorSet",
    "dockerfileLogic",
    "workflowPermissions",
    "publisherPolicy",
)
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
DATE = re.compile(r"^20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")


def require(ok: bool, message: str, errors: list[str]) -> None:
    if not ok:
        errors.append(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def validate_policy(policy: Any, errors: list[str]) -> bool:
    if not isinstance(policy, dict):
        errors.append("automation policy root must be an object")
        return False
    require(
        list(policy)
        == [
            "schemaVersion",
            "lifecycle",
            "dependencies",
            "baseImages",
            "manualOnly",
        ],
        "automation policy root keys/order is invalid",
        errors,
    )
    require(
        type(policy.get("schemaVersion")) is int and policy.get("schemaVersion") == 1,
        "automation policy schemaVersion must be integer 1",
        errors,
    )
    lifecycle = policy.get("lifecycle")
    dependencies = policy.get("dependencies")
    base_images = policy.get("baseImages")
    manual_only = policy.get("manualOnly")
    if not isinstance(lifecycle, dict):
        errors.append("automation policy lifecycle must be an object")
    else:
        require(
            tuple(lifecycle) == EXPECTED_MINORS,
            "automation policy lifecycle keys/order must be 8.2, 8.3, 8.4, 8.5",
            errors,
        )
        for minor in EXPECTED_MINORS:
            row = lifecycle.get(minor)
            if not isinstance(row, dict):
                errors.append(f"automation policy lifecycle {minor} must be an object")
                continue
            require(
                tuple(row) == ("support", "eol"),
                f"automation policy lifecycle {minor} fields/order is invalid",
                errors,
            )
            require(
                row.get("support") in {"active", "security-only"},
                f"automation policy lifecycle {minor} support is invalid",
                errors,
            )
            require(
                isinstance(row.get("eol"), str) and bool(DATE.fullmatch(row["eol"])),
                f"automation policy lifecycle {minor} eol is invalid",
                errors,
            )
    if not isinstance(dependencies, dict):
        errors.append("automation policy dependencies must be an object")
    else:
        require(
            tuple(dependencies) == EXPECTED_DEPENDENCIES,
            "automation policy dependency keys/order is invalid",
            errors,
        )
        for name in EXPECTED_DEPENDENCIES:
            row = dependencies.get(name)
            if not isinstance(row, dict):
                errors.append(f"automation policy dependency {name} must be an object")
                continue
            require(
                tuple(row) == ("sourceHost", "autoBump"),
                f"automation policy dependency {name} fields/order is invalid",
                errors,
            )
            require(
                row.get("sourceHost") == "pecl.php.net"
                and row.get("autoBump") == "patch",
                f"automation policy dependency {name} policy is invalid",
                errors,
            )
    require(
        isinstance(base_images, dict)
        and base_images
        == {
            "repository": "php",
            "hubRepository": "library/php",
            "tagSuffix": "-fpm-alpine",
            "tagApiBase": "https://hub.docker.com/v2/repositories/library/php/tags",
            "officialImagesMetadata": "https://raw.githubusercontent.com/docker-library/official-images/master/library/php",
            "autoBump": "same-minor-patch-or-digest",
        },
        "automation policy baseImages is invalid",
        errors,
    )
    require(
        isinstance(manual_only, list) and tuple(manual_only) == EXPECTED_MANUAL_ONLY,
        "automation policy manualOnly is invalid",
        errors,
    )
    return not errors


def valid_dependency_url(name: str, version: str, url: Any, host: str) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == host
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and parsed.path == f"/get/{name}-{version}.tgz"
        and not parsed.query
        and not parsed.fragment
    )


def validate(data: Any, policy: Any | None = None) -> list[str]:
    errors: list[str] = []
    if policy is None:
        try:
            policy = load_json(DEFAULT_POLICY_PATH)
        except (OSError, json.JSONDecodeError) as exc:
            return [f"automation policy load failed: {exc}"]
    if not validate_policy(policy, errors):
        return errors
    assert isinstance(policy, dict)
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
    if (
        not isinstance(deps, dict)
        or not isinstance(contracts, dict)
        or not isinstance(versions, dict)
    ):
        return errors + ["dependencies, runtimeContracts, and versions must be objects"]
    require(
        tuple(deps) == EXPECTED_DEPENDENCIES,
        "dependency keys/order must be imagick, redis, apcu",
        errors,
    )
    require(
        tuple(contracts) == ("libiconv",),
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
            tuple(iconv.get(key) for key in ICONV_FIELDS) == EXPECTED_ICONV,
            "libiconv runtime contract does not match approved official base contract",
            errors,
        )
    else:
        errors.append("libiconv runtime contract must be an object")
    require(
        tuple(versions) == EXPECTED_MINORS,
        "version keys/order must be exactly 8.2, 8.3, 8.4, 8.5",
        errors,
    )
    for name in EXPECTED_DEPENDENCIES:
        item = deps.get(name)
        if not isinstance(item, dict):
            errors.append(f"dependency {name} must be an object")
            continue
        require(
            tuple(item) == ("version", "url", "sha256"),
            f"dependency {name} has missing/extra/reordered fields",
            errors,
        )
        version = item.get("version")
        require(
            isinstance(version, str) and bool(SEMVER.fullmatch(version)),
            f"dependency {name} version must be semantic x.y.z",
            errors,
        )
        host = policy["dependencies"][name]["sourceHost"]
        require(
            isinstance(version, str)
            and valid_dependency_url(name, version, item.get("url"), host),
            f"dependency {name} URL must be exact official package/version URL",
            errors,
        )
        require(
            isinstance(item.get("sha256"), str)
            and bool(SHA256.fullmatch(item["sha256"])),
            f"dependency {name} sha256 must be 64 lowercase hex characters",
            errors,
        )
    refs: list[str] = []
    for minor in EXPECTED_MINORS:
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
        lifecycle = policy["lifecycle"][minor]
        require(
            item.get("support") == lifecycle["support"]
            and item.get("eol") == lifecycle["eol"],
            f"version {minor} lifecycle does not match automation policy",
            errors,
        )
        require(
            isinstance(ref, str)
            and bool(
                re.fullmatch(
                    rf"php:{re.escape(minor)}-fpm-alpine@sha256:[0-9a-f]{{64}}", ref
                )
            ),
            f"version {minor} base_image must be an exact official minor digest ref",
            errors,
        )
        if isinstance(ref, str):
            refs.append(ref)
    require(len(refs) == len(set(refs)), "base image refs must be unique", errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="build/versions.json")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--matrix", action="store_true")
    output.add_argument("--get-base", metavar="MINOR")
    args = parser.parse_args()
    try:
        data = load_json(Path(args.path))
        policy = load_json(Path(args.policy))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"versions validation failed: {exc}", file=sys.stderr)
        return 1
    errors = validate(data, policy)
    if errors:
        for error in errors:
            print(f"versions validation failed: {error}", file=sys.stderr)
        return 1
    if args.matrix:
        dep_args = {
            f"{name}_{key}": value
            for name, dependency in data["dependencies"].items()
            for key, value in dependency.items()
        }
        iconv = {
            f"iconv_{re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower()}": value
            for key, value in data["runtimeContracts"]["libiconv"].items()
        }
        include = [
            {
                "php_minor": minor,
                "php_patch": item["patch"],
                "php_base_image": item["base_image"],
                "platform": platform,
                "arch": arch,
                **dep_args,
                **iconv,
            }
            for minor, item in data["versions"].items()
            for platform, arch in (("linux/amd64", "amd64"), ("linux/arm64", "arm64"))
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
            f"validated {len(data['versions'])} PHP versions, "
            f"{len(data['dependencies'])} source dependencies, and 1 runtime contract"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
