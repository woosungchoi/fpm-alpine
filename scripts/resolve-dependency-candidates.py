#!/usr/bin/env python3
"""Discover strict PHP base and PECL dependency update candidates."""

from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import tarfile
import urllib.request
from urllib.parse import quote, urlsplit
from pathlib import Path
from typing import Any, Callable

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


def _validator():
    path = Path(__file__).with_name("validate-versions.py")
    spec = importlib.util.spec_from_file_location("candidate_validate_versions", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _semver(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = SEMVER.fullmatch(value)
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _run(command: list[str]) -> str:
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    return completed.stdout.strip()


def _fetch_url(url: str, expected_host: str, limit: int) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "fpm-alpine-dependency-updater/1"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        final = urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname != expected_host:
            raise ValueError(f"request redirected outside {expected_host}")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"response from {expected_host} exceeds size limit")
    return data


def patch_from_official_images(metadata: str, floating_tag: str) -> str:
    matches: list[str] = []
    for block in metadata.split("\n\n"):
        tag_line = next(
            (line for line in block.splitlines() if line.startswith("Tags: ")), None
        )
        if tag_line is None:
            continue
        tags = [tag.strip() for tag in tag_line.removeprefix("Tags: ").split(",")]
        if floating_tag not in tags:
            continue
        versions = {
            match.group(1)
            for tag in tags
            if (match := re.fullmatch(r"(\d+\.\d+\.\d+)-fpm-alpine(?:\d+\.\d+)?", tag))
        }
        if len(versions) != 1:
            raise ValueError(f"ambiguous official PHP patch aliases for {floating_tag}")
        matches.extend(versions)
    if len(matches) != 1 or not SEMVER.fullmatch(matches[0]):
        raise ValueError(
            f"official image metadata does not uniquely define {floating_tag}"
        )
    expected_minor = floating_tag.split("-", 1)[0]
    if not matches[0].startswith(expected_minor + "."):
        raise ValueError(f"official image patch does not match {expected_minor}")
    return matches[0]


@lru_cache(maxsize=4)
def _official_images_metadata(url: str) -> str:
    return _fetch_url(url, "raw.githubusercontent.com", 5 * 1024 * 1024).decode("utf-8")


def _hub_tag(url: str, tag: str) -> dict[str, Any]:
    data = json.loads(
        _fetch_url(f"{url}/{quote(tag, safe='')}", "hub.docker.com", 2 * 1024 * 1024)
    )
    if (
        not isinstance(data, dict)
        or data.get("name") != tag
        or not DIGEST.fullmatch(data.get("digest", ""))
    ):
        raise ValueError(f"invalid Docker Hub tag metadata for {tag}")
    images = data.get("images")
    if not isinstance(images, list):
        raise ValueError(f"missing Docker Hub platform metadata for {tag}")
    for architecture in ("amd64", "arm64"):
        rows = [
            row
            for row in images
            if isinstance(row, dict)
            and row.get("os") == "linux"
            and row.get("architecture") == architecture
            and DIGEST.fullmatch(row.get("digest", ""))
        ]
        if len(rows) != 1:
            raise ValueError(
                f"Docker Hub tag {tag} lacks one verified linux/{architecture} image"
            )
    return data


def resolve_base(ref: str, policy: Any) -> tuple[str, str]:
    base_policy = policy.get("baseImages") if isinstance(policy, dict) else None
    if not isinstance(base_policy, dict) or ":" not in ref or "@" in ref:
        raise ValueError("invalid base resolver input")
    repository, tag = ref.split(":", 1)
    if repository != base_policy.get("repository") or not re.fullmatch(
        r"8\.[2-5]-fpm-alpine", tag
    ):
        raise ValueError("base reference is outside policy")
    first = _hub_tag(base_policy["tagApiBase"], tag)
    official = _official_images_metadata(base_policy["officialImagesMetadata"])
    patch = patch_from_official_images(official, tag)
    second = _hub_tag(base_policy["tagApiBase"], tag)
    if first["digest"] != second["digest"]:
        raise ValueError(f"Docker Hub tag moved during observation: {tag}")
    return first["digest"], patch


def fetch_bytes(url: str) -> bytes:
    return _fetch_url(url, "pecl.php.net", MAX_ARCHIVE_BYTES)


def fetch_text(url: str) -> str:
    data = fetch_bytes(url)
    if len(data) > 256:
        raise ValueError("PECL latest response exceeds size limit")
    return data.decode("utf-8", errors="strict").strip()


def valid_tgz(data: bytes) -> bool:
    if len(data) < 3 or data[:2] != b"\x1f\x8b":
        return False
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            if not members:
                return False
            for member in members:
                path = Path(member.name)
                if path.is_absolute() or ".." in path.parts:
                    return False
    except (tarfile.TarError, OSError):
        return False
    return True


def discover(
    versions: Any,
    policy: Any,
    *,
    base_resolver: Callable[[str, Any], tuple[str, str]] = resolve_base,
    digest_resolver: Callable[[str], str] | None = None,
    patch_resolver: Callable[[str], str] | None = None,
    text_fetcher: Callable[[str], str] = fetch_text,
    bytes_fetcher: Callable[[str], bytes] = fetch_bytes,
    generated_at: str,
    source_commit: str,
) -> dict[str, Any]:
    validator = _validator()
    errors = validator.validate(versions, policy)
    if errors:
        raise ValueError("invalid source manifest or policy: " + "; ".join(errors))
    if not COMMIT.fullmatch(source_commit):
        raise ValueError("source commit must be 40 lowercase hex characters")
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []

    for minor, current in versions["versions"].items():
        floating = f"php:{minor}-fpm-alpine"
        try:
            if (digest_resolver is None) != (patch_resolver is None):
                raise ValueError(
                    "digest and patch resolver fixtures must be provided together"
                )
            if digest_resolver is not None and patch_resolver is not None:
                digest = digest_resolver(floating)
                subject = f"{floating}@{digest}"
                patch = patch_resolver(subject)
            else:
                digest, patch = base_resolver(floating, policy)
            if not DIGEST.fullmatch(digest):
                raise ValueError("resolver returned malformed digest")
            subject = f"{floating}@{digest}"
        except (
            Exception
        ) as exc:  # remote observation failure, never an update authorization
            warnings.append(
                f"base {minor} observation failed: {type(exc).__name__}: {exc}"
            )
            continue
        old_version, new_version = _semver(current["patch"]), _semver(patch)
        if old_version is None or new_version is None:
            warnings.append(f"base {minor} returned invalid patch {patch!r}")
            continue
        if new_version[:2] != old_version[:2]:
            warnings.append(f"base {minor} returned wrong minor patch {patch}")
            continue
        if new_version < old_version:
            warnings.append(f"base {minor} returned downgrade patch {patch}")
            continue
        old_digest = current["base_image"].split("@", 1)[1]
        if patch == current["patch"] and digest == old_digest:
            continue
        candidates.append(
            {
                "key": f"base-{minor}",
                "class": "base-same-minor",
                "eligible": True,
                "affectedMinors": [minor],
                "old": {"patch": current["patch"], "base_image": current["base_image"]},
                "new": {"patch": patch, "base_image": subject},
                "evidence": {"floatingRef": floating, "resolvedDigest": digest},
            }
        )

    for name, current in versions["dependencies"].items():
        latest_url = f"https://pecl.php.net/rest/r/{name}/latest.txt"
        try:
            latest = text_fetcher(latest_url)
        except Exception as exc:
            warnings.append(
                f"PECL {name} latest observation failed: {type(exc).__name__}: {exc}"
            )
            continue
        old_version, new_version = _semver(current["version"]), _semver(latest)
        if old_version is None or new_version is None:
            warnings.append(f"PECL {name} returned invalid semantic version {latest!r}")
            continue
        if new_version <= old_version:
            continue
        url = f"https://pecl.php.net/get/{name}-{latest}.tgz"
        try:
            archive = bytes_fetcher(url)
        except Exception as exc:
            warnings.append(
                f"PECL {name} archive fetch failed: {type(exc).__name__}: {exc}"
            )
            continue
        if not valid_tgz(archive):
            warnings.append(f"PECL {name} archive validation failed")
            continue
        eligible = new_version[:2] == old_version[:2]
        candidates.append(
            {
                "key": f"pecl-{name}",
                "class": "pecl-patch" if eligible else "pecl-manual-review",
                "eligible": eligible,
                "affectedMinors": list(policy["lifecycle"]) if eligible else [],
                "old": dict(current),
                "new": {
                    "version": latest,
                    "url": url,
                    "sha256": hashlib.sha256(archive).hexdigest(),
                },
                "evidence": {"latestUrl": latest_url, "archiveBytes": len(archive)},
            }
        )

    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "sourceCommit": source_commit,
        "candidates": candidates,
        "warnings": warnings,
    }


def apply_candidate(versions: Any, candidate: Any) -> Any:
    if not isinstance(candidate, dict) or not isinstance(candidate.get("key"), str):
        raise ValueError("candidate must be an object with a key")
    key = candidate["key"]
    if key.startswith("base-"):
        minor = key.removeprefix("base-")
        if minor not in versions["versions"]:
            raise ValueError("unknown base candidate minor")
        versions["versions"][minor]["patch"] = candidate["new"]["patch"]
        versions["versions"][minor]["base_image"] = candidate["new"]["base_image"]
    elif key.startswith("pecl-"):
        name = key.removeprefix("pecl-")
        if name not in versions["dependencies"]:
            raise ValueError("unknown PECL candidate")
        versions["dependencies"][name] = dict(candidate["new"])
    else:
        raise ValueError("unsupported candidate key")
    return versions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--versions", default="build/versions.json")
    parser.add_argument("--policy", default="build/automation-policy.json")
    parser.add_argument("--output", default="dependency-candidates.json")
    parser.add_argument("--apply-from")
    parser.add_argument("--apply-key")
    parser.add_argument("--apply-output")
    args = parser.parse_args()
    versions_path = Path(args.versions)
    versions = json.loads(versions_path.read_text())
    policy = json.loads(Path(args.policy).read_text())
    if args.apply_from:
        if not args.apply_key:
            raise SystemExit("--apply-key is required with --apply-from")
        result = json.loads(Path(args.apply_from).read_text())
    else:
        source_commit = _run(["git", "rev-parse", "HEAD"])
        generated_at = _run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"])
        result = discover(
            versions,
            policy,
            generated_at=generated_at,
            source_commit=source_commit,
        )
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    if args.apply_key:
        matches = [
            item
            for item in result.get("candidates", [])
            if item.get("key") == args.apply_key
        ]
        if len(matches) != 1 or matches[0].get("eligible") is not True:
            raise SystemExit(
                f"eligible candidate key must match exactly once: {args.apply_key}"
            )
        output = Path(args.apply_output or args.versions)
        updated = apply_candidate(versions, matches[0])
        errors = _validator().validate(updated, policy)
        if errors:
            raise SystemExit("applied candidate is invalid: " + "; ".join(errors))
        output.write_text(json.dumps(updated, indent=2) + "\n")
    print(
        f"dependency_candidates={len(result.get('candidates', []))} "
        f"warnings={len(result.get('warnings', []))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
