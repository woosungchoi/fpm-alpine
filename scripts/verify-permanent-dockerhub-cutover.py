#!/usr/bin/env python3
"""Verify the owner-attested permanent Docker Hub publisher cutover."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ENDPOINT = "https://hub.docker.com/v2/repositories/woosungchoi/fpm-alpine/"
REPOSITORY = "woosungchoi/fpm-alpine"
DEFAULT_ATTESTATION = Path(".github/dockerhub-cutover-attestation.json")
MAX_JSON_BYTES = 64 * 1024
OWNER = {"login": "woosungchoi", "id": 5674610}
OWNER_STATEMENT = (
    "I observed zero queued and running Docker Hub legacy builds, removed every "
    "source-capable publisher hook and external writer, and rotated a dedicated "
    "GitHub Actions Docker Hub write token."
)
EXPECTED_HOOK = {
    "id": 402842509,
    "name": "web",
    "active": True,
    "events": ["pull_request", "push"],
    "urlHost": "api.snyk.io",
    "urlKind": "github-webhook-uuid",
}
ATTESTATION_KEYS = {
    "schemaVersion",
    "status",
    "repository",
    "owner",
    "attestedAt",
    "queueObservation",
    "activeGitHubHooks",
    "dockerHubToken",
    "ownerStatement",
}
QUEUE_KEYS = {"source", "observedAt", "queued", "running"}
TOKEN_KEYS = {"dedicatedTo", "rotatedAt", "externalWritersRemoved"}
EVIDENCE_KEYS = {
    "schemaVersion",
    "checkedAt",
    "repository",
    "endpoint",
    "dockerHub",
    "attestation",
}
DOCKERHUB_EVIDENCE_KEYS = {
    "namespace",
    "name",
    "status",
    "isAutomated",
    "lastUpdated",
}
ATTESTATION_EVIDENCE_KEYS = {
    "sha256",
    "owner",
    "attestedAt",
    "queueObservedAt",
    "tokenRotatedAt",
    "activeGitHubHooks",
    "ownerStatement",
}


def require_exact_dict(value: object, keys: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{field} keys are invalid")
    return value


def parse_rfc3339(value: object, field: str) -> dt.datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be an RFC3339 string")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC3339 string") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def load_json_file(path: Path, field: str) -> tuple[bytes, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{field} must be a regular file")
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_JSON_BYTES:
        raise ValueError(f"{field} size is invalid")
    try:
        return raw, json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} must contain valid UTF-8 JSON") from exc


def fetch_metadata() -> Any:
    request = urllib.request.Request(
        ENDPOINT,
        headers={"User-Agent": "fpm-alpine-permanent-cutover/2"},
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


def validate_metadata(data: Any, now: dt.datetime) -> dict[str, Any]:
    if type(data) is not dict:
        raise ValueError("Docker Hub metadata root must be an object")
    if data.get("namespace") != "woosungchoi" or data.get("name") != "fpm-alpine":
        raise ValueError("Docker Hub repository identity mismatch")
    if type(data.get("status")) is not int or data["status"] != 1:
        raise ValueError("Docker Hub repository is not active")
    if data.get("is_automated") is not False:
        raise ValueError("Docker Hub legacy automated builds are not disabled")
    last_updated = parse_rfc3339(data.get("last_updated"), "last_updated")
    if last_updated > now + dt.timedelta(minutes=5):
        raise ValueError("Docker Hub last_updated is implausibly in the future")
    return {
        "namespace": "woosungchoi",
        "name": "fpm-alpine",
        "status": 1,
        "isAutomated": False,
        "lastUpdated": data["last_updated"],
    }


def validate_attestation(
    raw: bytes,
    data: Any,
    now: dt.datetime,
) -> dict[str, Any]:
    attestation = require_exact_dict(data, ATTESTATION_KEYS, "cutover attestation")
    if type(attestation.get("schemaVersion")) is not int or attestation["schemaVersion"] != 1:
        raise ValueError("cutover attestation schema is invalid")
    if attestation.get("repository") != REPOSITORY:
        raise ValueError("cutover attestation repository mismatch")
    if attestation.get("owner") != OWNER:
        raise ValueError("cutover attestation owner mismatch")
    if attestation.get("status") != "active":
        raise ValueError("cutover attestation is not active")
    if attestation.get("ownerStatement") != OWNER_STATEMENT:
        raise ValueError("cutover attestation owner statement mismatch")

    queue = require_exact_dict(
        attestation.get("queueObservation"),
        QUEUE_KEYS,
        "cutover queue observation",
    )
    if queue.get("source") != "dockerhub-builds-ui-owner-observation":
        raise ValueError("cutover queue observation is not an owner UI observation")
    for field in ("queued", "running"):
        if type(queue.get(field)) is not int or queue[field] != 0:
            raise ValueError(f"cutover queue {field} count must be exactly zero")

    hooks = attestation.get("activeGitHubHooks")
    if type(hooks) is not list or hooks != [EXPECTED_HOOK]:
        raise ValueError("cutover GitHub hook set is not the exact nonpublisher allowlist")

    token = require_exact_dict(
        attestation.get("dockerHubToken"),
        TOKEN_KEYS,
        "cutover Docker Hub token evidence",
    )
    if token.get("dedicatedTo") != "github-actions:woosungchoi/fpm-alpine":
        raise ValueError("cutover Docker Hub token is not dedicated to this repository")
    if token.get("externalWritersRemoved") is not True:
        raise ValueError("cutover external Docker Hub writers were not removed")

    observed_at = parse_rfc3339(queue.get("observedAt"), "queue observedAt")
    rotated_at = parse_rfc3339(token.get("rotatedAt"), "token rotatedAt")
    attested_at = parse_rfc3339(attestation.get("attestedAt"), "attestedAt")
    if not observed_at <= rotated_at <= attested_at:
        raise ValueError("cutover observation, token rotation, and attestation order is invalid")
    if attested_at > now + dt.timedelta(minutes=5):
        raise ValueError("cutover attestation is implausibly in the future")

    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "owner": OWNER,
        "attestedAt": attestation["attestedAt"],
        "queueObservedAt": queue["observedAt"],
        "tokenRotatedAt": token["rotatedAt"],
        "activeGitHubHooks": [EXPECTED_HOOK],
        "ownerStatement": OWNER_STATEMENT,
    }


def validate_evidence_shape(data: Any, field: str) -> dict[str, Any]:
    evidence = require_exact_dict(data, EVIDENCE_KEYS, field)
    if type(evidence.get("schemaVersion")) is not int or evidence["schemaVersion"] != 2:
        raise ValueError(f"{field} schema is invalid")
    if evidence.get("repository") != REPOSITORY or evidence.get("endpoint") != ENDPOINT:
        raise ValueError(f"{field} identity is invalid")
    parse_rfc3339(evidence.get("checkedAt"), f"{field} checkedAt")
    dockerhub = require_exact_dict(
        evidence.get("dockerHub"),
        DOCKERHUB_EVIDENCE_KEYS,
        f"{field} Docker Hub state",
    )
    if (
        dockerhub.get("namespace") != "woosungchoi"
        or dockerhub.get("name") != "fpm-alpine"
        or type(dockerhub.get("status")) is not int
        or dockerhub["status"] != 1
        or dockerhub.get("isAutomated") is not False
    ):
        raise ValueError(f"{field} Docker Hub identity is invalid")
    parse_rfc3339(dockerhub.get("lastUpdated"), f"{field} lastUpdated")
    attestation = require_exact_dict(
        evidence.get("attestation"),
        ATTESTATION_EVIDENCE_KEYS,
        f"{field} attestation",
    )
    if not isinstance(attestation.get("sha256"), str) or len(attestation["sha256"]) != 64:
        raise ValueError(f"{field} attestation digest is invalid")
    return evidence


def validate_expected_state(data: Any, observed: dict[str, Any]) -> None:
    expected = validate_evidence_shape(data, "expected permanent-cutover evidence")
    for key in ("repository", "endpoint", "attestation"):
        if expected[key] != observed[key]:
            raise ValueError(f"expected permanent-cutover {key} mismatch")
    for key in ("namespace", "name", "status", "isAutomated"):
        if expected["dockerHub"].get(key) != observed["dockerHub"].get(key):
            raise ValueError(f"expected permanent-cutover Docker Hub {key} mismatch")
    previous_update = parse_rfc3339(
        expected["dockerHub"]["lastUpdated"],
        "expected permanent-cutover lastUpdated",
    )
    current_update = parse_rfc3339(
        observed["dockerHub"]["lastUpdated"],
        "observed permanent-cutover lastUpdated",
    )
    if current_update < previous_update:
        raise ValueError("Docker Hub last_updated moved backwards between cutover checks")


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
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-state", type=Path)
    parser.add_argument("--attestation", type=Path, default=DEFAULT_ATTESTATION)
    parser.add_argument("--metadata-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    attestation_raw, attestation_json = load_json_file(
        args.attestation,
        "cutover attestation",
    )
    attestation = validate_attestation(attestation_raw, attestation_json, now)
    metadata = (
        load_json_file(args.metadata_file, "Docker Hub metadata fixture")[1]
        if args.metadata_file is not None
        else fetch_metadata()
    )
    dockerhub = validate_metadata(metadata, now)
    observed = {
        "schemaVersion": 2,
        "checkedAt": now.isoformat().replace("+00:00", "Z"),
        "repository": REPOSITORY,
        "endpoint": ENDPOINT,
        "dockerHub": dockerhub,
        "attestation": attestation,
    }
    validate_evidence_shape(observed, "observed permanent-cutover evidence")
    if args.expected_state is not None:
        _, expected = load_json_file(
            args.expected_state,
            "expected permanent-cutover evidence",
        )
        validate_expected_state(expected, observed)
    write_evidence(args.output, observed)
    print(
        "permanent Docker Hub cutover verified: "
        f"attestation={attestation['sha256']} is_automated=false "
        f"last_updated={dockerhub['lastUpdated']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
