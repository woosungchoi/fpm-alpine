#!/usr/bin/env python3
"""Synchronize the public Docker Hub metadata for the canonical repository."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable
import urllib.error
import urllib.request

REPOSITORY = "woosungchoi/fpm-alpine"
SHORT_DESCRIPTION = (
    "Multi-arch PHP-FPM Alpine images: active Docker Hub tags 8.2, 8.3, 8.4, and 8.5."
)
API_BASE = "https://hub.docker.com/v2"
AUTH_URL = f"{API_BASE}/auth/token"
REPOSITORY_URL = f"{API_BASE}/repositories/{REPOSITORY}"
MAX_FULL_DESCRIPTION_BYTES = 25_000
HTTP_TIMEOUT_SECONDS = 30

Opener = Callable[..., Any]
Sleeper = Callable[[float], None]


def _request_json(
    url: str,
    *,
    method: str,
    payload: dict[str, str] | None = None,
    access_token: str | None = None,
    opener: Opener | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json", "User-Agent": "fpm-alpine-metadata-sync"}
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
        raise RuntimeError(
            f"Docker Hub API {method} failed with HTTP {error.code} {error.reason}"
        ) from None
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


def load_description(path: Path) -> str:
    description = path.read_text(encoding="utf-8")
    size = len(description.encode())
    if not description.strip():
        raise ValueError("Docker Hub full description must not be empty")
    if size > MAX_FULL_DESCRIPTION_BYTES:
        raise ValueError(
            f"Docker Hub full description exceeds the {MAX_FULL_DESCRIPTION_BYTES}-byte limit"
        )
    return description


def authenticate(username: str, secret: str, *, opener: Opener | None = None) -> str:
    if not username or not secret:
        raise ValueError("Docker Hub username and token are required")
    response = _request_json(
        AUTH_URL,
        method="POST",
        payload={"identifier": username, "secret": secret},
        opener=opener,
    )
    access_token = response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Docker Hub authentication returned no access token")
    return access_token


def patch_metadata(
    access_token: str,
    full_description: str,
    *,
    opener: Opener | None = None,
) -> None:
    if not access_token:
        raise ValueError("Docker Hub access token is required")
    _request_json(
        REPOSITORY_URL,
        method="PATCH",
        payload={
            "description": SHORT_DESCRIPTION,
            "full_description": full_description,
        },
        access_token=access_token,
        opener=opener,
    )


def fetch_metadata(*, opener: Opener | None = None) -> dict[str, Any]:
    return _request_json(REPOSITORY_URL, method="GET", opener=opener)


def synchronize(
    username: str,
    secret: str,
    full_description: str,
    *,
    opener: Opener | None = None,
    sleeper: Sleeper = time.sleep,
    attempts: int = 10,
) -> str:
    if username.lower() != REPOSITORY.split("/", 1)[0]:
        raise ValueError("Docker Hub username does not own the configured repository")
    if attempts < 1:
        raise ValueError("read-back attempts must be positive")
    if len(full_description.encode()) > MAX_FULL_DESCRIPTION_BYTES:
        raise ValueError(
            f"Docker Hub full description exceeds the {MAX_FULL_DESCRIPTION_BYTES}-byte limit"
        )

    access_token = authenticate(username, secret, opener=opener)
    patch_metadata(access_token, full_description, opener=opener)

    for attempt in range(attempts):
        current = fetch_metadata(opener=opener)
        if (
            current.get("description") == SHORT_DESCRIPTION
            and str(current.get("full_description") or "").rstrip()
            == full_description.rstrip()
        ):
            return hashlib.sha256(full_description.encode()).hexdigest()
        if attempt + 1 < attempts:
            sleeper(2)
    raise RuntimeError("Docker Hub metadata did not match the requested values after PATCH")


def main() -> int:
    username = os.environ.get("DOCKERHUB_USERNAME", "")
    secret = os.environ.get("DOCKERHUB_TOKEN", "")
    description_path = Path(__file__).resolve().parents[1] / "docs" / "dockerhub-description.md"
    full_description = load_description(description_path)
    digest = synchronize(username, secret, full_description)
    print(
        f"dockerhub_metadata_sync=PASS repository={REPOSITORY} "
        f"full_description_sha256={digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
