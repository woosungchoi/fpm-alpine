#!/usr/bin/env python3
"""Verify that the legacy Docker Hub publisher remains permanently disabled."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ENDPOINT = "https://hub.docker.com/v2/repositories/woosungchoi/fpm-alpine/"
EXPECTED_EVIDENCE_KEYS = {
    "schemaVersion",
    "checkedAt",
    "endpoint",
    "namespace",
    "name",
    "status",
    "isAutomated",
    "lastUpdated",
}


def parse_rfc3339(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be an RFC3339 string")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC3339 string") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return value


def fetch_metadata() -> Any:
    request = urllib.request.Request(
        ENDPOINT,
        headers={"User-Agent": "fpm-alpine-permanent-cutover/1"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        final = urlsplit(response.geturl())
        if (
            final.scheme != "https"
            or final.hostname != "hub.docker.com"
            or final.username is not None
            or final.password is not None
            or final.port is not None
            or final.path != "/v2/repositories/woosungchoi/fpm-alpine/"
            or final.query
            or final.fragment
        ):
            raise ValueError("Docker Hub metadata redirected outside the trusted endpoint")
        return json.load(response)


def validate_metadata(data: Any) -> dict[str, Any]:
    if type(data) is not dict:
        raise ValueError("Docker Hub metadata root must be an object")
    if data.get("namespace") != "woosungchoi" or data.get("name") != "fpm-alpine":
        raise ValueError("Docker Hub repository identity mismatch")
    if type(data.get("status")) is not int or data["status"] != 1:
        raise ValueError("Docker Hub repository is not active")
    if data.get("is_automated") is not False:
        raise ValueError("Docker Hub legacy automated builds are not disabled")
    last_updated = parse_rfc3339(data.get("last_updated"), "last_updated")
    checked_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schemaVersion": 1,
        "checkedAt": checked_at,
        "endpoint": ENDPOINT,
        "namespace": "woosungchoi",
        "name": "fpm-alpine",
        "status": 1,
        "isAutomated": False,
        "lastUpdated": last_updated,
    }


def validate_expected_state(data: Any, observed: dict[str, Any]) -> None:
    if type(data) is not dict or set(data) != EXPECTED_EVIDENCE_KEYS:
        raise ValueError("expected permanent-cutover evidence keys are invalid")
    if type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
        raise ValueError("expected permanent-cutover schema is invalid")
    parse_rfc3339(data.get("checkedAt"), "expected checkedAt")
    parse_rfc3339(data.get("lastUpdated"), "expected lastUpdated")
    for key in ("endpoint", "namespace", "name", "status", "isAutomated"):
        if data.get(key) != observed[key] or type(data.get(key)) is not type(observed[key]):
            raise ValueError(f"expected permanent-cutover {key} mismatch")
    if data["lastUpdated"] != observed["lastUpdated"]:
        raise ValueError("Docker Hub repository changed between cutover checks")


def write_evidence(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise ValueError("permanent-cutover output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-state", type=Path)
    parser.add_argument("--metadata-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    metadata = (
        json.loads(args.metadata_file.read_text())
        if args.metadata_file is not None
        else fetch_metadata()
    )
    observed = validate_metadata(metadata)
    if args.expected_state is not None:
        validate_expected_state(json.loads(args.expected_state.read_text()), observed)
    write_evidence(args.output, observed)
    print(
        "permanent Docker Hub cutover verified: "
        f"is_automated=false last_updated={observed['lastUpdated']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
