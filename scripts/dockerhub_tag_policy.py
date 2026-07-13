#!/usr/bin/env python3
"""Fail-closed Docker Hub tag inventory and deletion policy for fpm-alpine."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = "woosungchoi/fpm-alpine"
API_BASE = "https://hub.docker.com/v2"
AUTH_URL = f"{API_BASE}/auth/token"
TAGS_URL = f"{API_BASE}/repositories/{REPOSITORY}/tags?page_size=100"
KEEP_TAGS = ("8.2", "8.3", "8.4", "8.5")
HTTP_TIMEOUT_SECONDS = 30
CONFIRMATION = "DELETE NON-ACTIVE DOCKER HUB TAGS"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CLASSIFIERS = (
    ("keep", re.compile(r"^8\.[2-5]$")),
    ("canary", re.compile(r"^canary-8\.[2-5]-[1-9][0-9]*-[1-9][0-9]*$")),
    ("immutable-release", re.compile(r"^8\.[2-5]\.[0-9]+-[0-9]{8}-[0-9a-f]{64}$")),
    ("immutable-source", re.compile(r"^sha-8\.[2-5]-[0-9a-f]{12}-[0-9a-f]{64}$")),
    ("legacy", re.compile(r"^this$")),
    ("frozen", re.compile(r"^8\.[01]$")),
)
DELETE_ORDER = {"canary": 0, "immutable-release": 1, "immutable-source": 2, "legacy": 3, "frozen": 4}
Opener = Callable[..., Any]
InventoryFetcher = Callable[[], list[dict[str, Any]]]
DeleteTag = Callable[[str, str, str], None]
Sleeper = Callable[[float], None]


class PolicyError(RuntimeError):
    """The observed registry state does not satisfy the policy contract."""


class PruneError(RuntimeError):
    """Deletion stopped after a partial failure, with a resumable result."""

    def __init__(self, message: str, result: dict[str, Any]):
        super().__init__(message)
        self.result = result


def _request_json(
    url: str,
    *,
    method: str,
    payload: dict[str, str] | None = None,
    access_token: str | None = None,
    opener: Opener | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json", "User-Agent": "fpm-alpine-tag-policy"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    open_request = opener or urllib.request.urlopen
    try:
        with open_request(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Docker Hub API {method} failed with HTTP {error.code} {error.reason}") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"Docker Hub API {method} transport failure: {error.reason}") from None
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Docker Hub API {method} returned invalid JSON") from error
    if not isinstance(decoded, dict):
        raise RuntimeError(f"Docker Hub API {method} returned a non-object payload")
    return decoded


def authenticate(username: str, secret: str, *, opener: Opener | None = None) -> str:
    if username != REPOSITORY.split("/", 1)[0] or not secret:
        raise ValueError("configured Docker Hub owner and token are required")
    response = _request_json(
        AUTH_URL,
        method="POST",
        payload={"identifier": username, "secret": secret},
        opener=opener,
    )
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Docker Hub authentication returned no access token")
    return token


def _normalize_tag(row: dict[str, Any]) -> dict[str, Any]:
    name = row.get("name")
    digest = row.get("digest")
    if not isinstance(name, str) or not name:
        raise PolicyError("Docker Hub inventory contains an invalid tag name")
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        raise PolicyError(f"Docker Hub tag has an invalid digest: {name}")
    platforms = []
    images = row.get("images") if "images" in row else row.get("platforms")
    images = images or []
    if not isinstance(images, list):
        raise PolicyError(f"Docker Hub tag has an invalid images list: {name}")
    for image in images:
        if not isinstance(image, dict):
            raise PolicyError(f"Docker Hub tag has an invalid image descriptor: {name}")
        platform_digest = image.get("digest")
        if not isinstance(platform_digest, str) or not DIGEST_RE.fullmatch(platform_digest):
            raise PolicyError(f"Docker Hub tag has an invalid platform digest: {name}")
        platforms.append(
            {
                "os": str(image.get("os") or ""),
                "architecture": str(image.get("architecture") or ""),
                "digest": platform_digest,
            }
        )
    platforms.sort(key=lambda item: (item["os"], item["architecture"], item["digest"]))
    return {"name": name, "digest": digest, "platforms": platforms}


def canonical_inventory(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_normalize_tag(row) for row in inventory]
    names = [row["name"] for row in normalized]
    if len(names) != len(set(names)):
        raise PolicyError("Docker Hub inventory contains duplicate tag names")
    return sorted(normalized, key=lambda row: row["name"])


def fetch_inventory(*, opener: Opener | None = None) -> list[dict[str, Any]]:
    url: str | None = TAGS_URL
    rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    while url:
        if url in seen_urls or not url.startswith(f"{API_BASE}/repositories/{REPOSITORY}/tags"):
            raise PolicyError("Docker Hub pagination returned an unsafe or repeated next URL")
        seen_urls.add(url)
        payload = _request_json(url, method="GET", opener=opener)
        page = payload.get("results")
        if not isinstance(page, list):
            raise PolicyError("Docker Hub tag page has no results list")
        rows.extend(page)
        next_url = payload.get("next")
        if next_url is not None and not isinstance(next_url, str):
            raise PolicyError("Docker Hub pagination returned an invalid next URL")
        url = next_url
    return canonical_inventory(rows)


def inventory_sha256(inventory: list[dict[str, Any]]) -> str:
    payload = json.dumps(canonical_inventory(inventory), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def classify_tag(name: str) -> str:
    matches = [label for label, pattern in CLASSIFIERS if pattern.fullmatch(name)]
    if len(matches) != 1:
        raise PolicyError(f"unclassified or ambiguous Docker Hub tag: {name}")
    return matches[0]


def verify_exact_surface(inventory: list[dict[str, Any]]) -> None:
    names = {row["name"] for row in canonical_inventory(inventory)}
    expected = set(KEEP_TAGS)
    missing = sorted(expected - names)
    unexpected = sorted(names - expected)
    if missing:
        raise PolicyError("missing required Docker Hub tags: " + ", ".join(missing))
    if unexpected:
        raise PolicyError("unexpected Docker Hub tags: " + ", ".join(unexpected))


def build_deletion_plan(inventory: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = canonical_inventory(inventory)
    classified = [(classify_tag(row["name"]), row) for row in canonical]
    keep = [row for label, row in classified if label == "keep"]
    keep_names = sorted(row["name"] for row in keep)
    if keep_names != list(KEEP_TAGS):
        missing = sorted(set(KEEP_TAGS) - set(keep_names))
        raise PolicyError("missing required Docker Hub tags: " + ", ".join(missing))
    delete = [dict(row, classification=label) for label, row in classified if label != "keep"]
    delete.sort(key=lambda row: (DELETE_ORDER[row["classification"]], row["name"]))
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "inventory_sha256": inventory_sha256(canonical),
        "keep_tags": list(KEEP_TAGS),
        "keep": keep,
        "delete": delete,
    }


def deletion_plan_sha256(plan: dict[str, Any]) -> str:
    payload = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def validate_archive_map(plan: dict[str, Any], archive: dict[str, Any]) -> None:
    if archive.get("schema_version") != 1:
        raise PolicyError("archive map schema version mismatch")
    expected = {row["name"]: row["digest"] for row in plan.get("delete", [])}
    entries = archive.get("entries")
    if not isinstance(entries, list):
        raise PolicyError("archive map entries must be a list")
    observed: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PolicyError("archive map entry must be an object")
        name = entry.get("source_tag")
        digest = entry.get("source_digest")
        archive_ref = entry.get("archive_ref")
        archive_digest = entry.get("archive_digest")
        if not isinstance(name, str) or name in observed:
            raise PolicyError("archive map contains a duplicate or invalid source tag")
        if not isinstance(digest, str) or digest != expected.get(name):
            raise PolicyError(f"archive source digest mismatch: {name}")
        if not isinstance(archive_ref, str) or not archive_ref.startswith("ghcr.io/woosungchoi/fpm-alpine:archive-dockerhub-"):
            raise PolicyError(f"archive reference is outside the canonical GHCR repository: {name}")
        if not isinstance(archive_digest, str) or not DIGEST_RE.fullmatch(archive_digest):
            raise PolicyError(f"archive digest is invalid: {name}")
        for field in ("parity", "signature", "anonymous_read"):
            if entry.get(field) != "verified":
                raise PolicyError(f"archive {field} is not verified: {name}")
        observed[name] = digest
    if observed != expected:
        missing = sorted(set(expected) - set(observed))
        extra = sorted(set(observed) - set(expected))
        raise PolicyError(f"archive coverage mismatch; missing={missing}, extra={extra}")


def _validate_live_for_apply(live: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    canonical = canonical_inventory(live)
    current = {row["name"]: row for row in canonical}
    keep = {row["name"]: row["digest"] for row in plan.get("keep", [])}
    delete = {row["name"]: row["digest"] for row in plan.get("delete", [])}
    allowed = set(keep) | set(delete)
    unexpected = sorted(set(current) - allowed)
    missing_keep = sorted(set(keep) - set(current))
    if unexpected:
        raise PolicyError("live inventory has unexpected tags: " + ", ".join(unexpected))
    if missing_keep:
        raise PolicyError("live inventory is missing keep tags: " + ", ".join(missing_keep))
    for name, digest in keep.items():
        if current[name]["digest"] != digest:
            raise PolicyError(f"keep tag digest drifted: {name}")
    for name, digest in delete.items():
        if name in current and current[name]["digest"] != digest:
            raise PolicyError(f"delete candidate digest drifted: {name}")
    return current


def delete_tag(
    name: str,
    expected_digest: str,
    access_token: str,
    *,
    opener: Opener | None = None,
) -> None:
    if not name or not DIGEST_RE.fullmatch(expected_digest) or not access_token:
        raise ValueError("tag name, exact digest, and access token are required")
    encoded = urllib.parse.quote(name, safe="")
    _request_json(
        f"{API_BASE}/repositories/{REPOSITORY}/tags/{encoded}",
        method="DELETE",
        access_token=access_token,
        opener=opener,
    )


def apply_deletion_plan(
    plan: dict[str, Any],
    archive: dict[str, Any],
    *,
    access_token: str,
    inventory_fetcher: InventoryFetcher | None = None,
    delete_tag: DeleteTag | None = None,
    sleeper: Sleeper = time.sleep,
    attempts: int = 10,
) -> dict[str, Any]:
    if attempts < 1:
        raise ValueError("read-back attempts must be positive")
    if not access_token:
        raise ValueError("Docker Hub access token is required")
    validate_archive_map(plan, archive)
    fetcher = inventory_fetcher or fetch_inventory
    delete_operation = delete_tag or globals()["delete_tag"]
    current = _validate_live_for_apply(fetcher(), plan)
    result: dict[str, Any] = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "inventory_sha256": plan.get("inventory_sha256"),
        "deletion_plan_sha256": deletion_plan_sha256(plan),
        "status": "in_progress",
        "deleted": [],
        "already_absent": [],
        "failed": [],
    }
    for candidate in plan.get("delete", []):
        name = candidate["name"]
        digest = candidate["digest"]
        if name not in current:
            result["already_absent"].append(name)
            continue
        try:
            delete_operation(name, digest, access_token)
            for attempt in range(attempts):
                current = _validate_live_for_apply(fetcher(), plan)
                if name not in current:
                    result["deleted"].append(name)
                    break
                if attempt + 1 < attempts:
                    sleeper(2)
            else:
                raise RuntimeError("tag remained visible after DELETE")
        except Exception as error:
            result["status"] = "partial_failure"
            result["failed"].append({"name": name, "error": type(error).__name__})
            raise PruneError(f"Docker Hub deletion stopped at {name}: {error}", result) from None
    final_inventory = fetcher()
    verify_exact_surface(final_inventory)
    result["status"] = "success"
    result["final_inventory_sha256"] = inventory_sha256(final_inventory)
    return result
