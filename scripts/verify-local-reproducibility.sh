#!/usr/bin/env bash
set -euo pipefail

first="${1:?first OCI archive required}"
second="${2:?second OCI archive required}"
report="${3:-reproducibility-report.json}"
platform="${4:?expected platform required}"
[[ "$platform" =~ ^linux/(amd64|arm64)$ ]] || { echo "invalid platform" >&2; exit 64; }

python3 - "$first" "$second" "$report" "$platform" <<'PY'
from __future__ import annotations

import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path
from typing import Any

first_path, second_path, report_path, expected_platform = sys.argv[1:]
DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_BLOB_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_LAYER_BYTES = 2 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 10 * 1024 * 1024
MAX_MEMBERS = 4096
MAX_LAYERS = 256
HASH_CHUNK_BYTES = 1024 * 1024


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def read_json(archive: tarfile.TarFile, name: str, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        member = archive.getmember(name)
    except KeyError as exc:
        raise ValueError(f"missing {label}: {name}") from exc
    if not member.isfile() or member.size > MAX_JSON_BYTES:
        raise ValueError(f"invalid {label}: {name}")
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"unreadable {label}: {name}")
    payload = stream.read()
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {label}") from exc
    return require_object(parsed, label), payload


def descriptor_digest(descriptor: dict[str, Any], label: str) -> tuple[str, str]:
    value = descriptor.get("digest")
    match = DIGEST.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError(f"invalid {label} digest")
    return value, match.group(1)


def verify_blob(
    archive: tarfile.TarFile,
    descriptor: dict[str, Any],
    label: str,
    *,
    capture: bool = False,
) -> bytes | None:
    digest_value, hex_digest = descriptor_digest(descriptor, label)
    size = descriptor.get("size")
    if type(size) is not int or size < 0 or size > MAX_BLOB_BYTES:
        raise ValueError(f"invalid {label} size")
    name = f"blobs/sha256/{hex_digest}"
    try:
        member = archive.getmember(name)
    except KeyError as exc:
        raise ValueError(f"missing {label} blob") from exc
    if not member.isfile() or member.size != size:
        raise ValueError(f"{label} blob size mismatch")
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"unreadable {label} blob")
    hasher = hashlib.sha256()
    captured = bytearray() if capture else None
    while chunk := stream.read(HASH_CHUNK_BYTES):
        hasher.update(chunk)
        if captured is not None:
            if len(captured) + len(chunk) > MAX_JSON_BYTES:
                raise ValueError(f"{label} JSON blob exceeds size limit")
            captured.extend(chunk)
    if "sha256:" + hasher.hexdigest() != digest_value:
        raise ValueError(f"{label} blob digest mismatch")
    return bytes(captured) if captured is not None else None


def inspect(path: str) -> dict[str, Any]:
    archive_path = Path(path)
    if not archive_path.is_file():
        raise ValueError(f"OCI archive does not exist: {path}")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError(f"OCI archive exceeds size limit: {path}")
    with tarfile.open(archive_path, mode="r:") as archive:
        members = archive.getmembers()
        if len(members) > MAX_MEMBERS:
            raise ValueError("OCI archive contains too many members")
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("OCI archive contains duplicate member names")
        index, index_payload = read_json(archive, "index.json", "OCI index")
        if type(index.get("schemaVersion")) is not int or index["schemaVersion"] != 2:
            raise ValueError("OCI index schemaVersion must be integer 2")
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError("OCI index must contain exactly one image manifest")
        manifest_descriptor = require_object(manifests[0], "manifest descriptor")
        platform = require_object(manifest_descriptor.get("platform"), "manifest platform")
        observed_platform = f"{platform.get('os')}/{platform.get('architecture')}"
        if observed_platform != expected_platform:
            raise ValueError(
                f"manifest platform mismatch: expected {expected_platform}, got {observed_platform}"
            )
        manifest_payload = verify_blob(archive, manifest_descriptor, "manifest", capture=True)
        if manifest_payload is None:
            raise ValueError("unreadable manifest payload")
        manifest = require_object(json.loads(manifest_payload), "manifest")
        if type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] != 2:
            raise ValueError("manifest schemaVersion must be integer 2")
        config = require_object(manifest.get("config"), "config descriptor")
        config_payload = verify_blob(archive, config, "config", capture=True)
        if config_payload is None:
            raise ValueError("unreadable config payload")
        config_data = require_object(json.loads(config_payload), "config")
        config_platform = f"{config_data.get('os')}/{config_data.get('architecture')}"
        if config_platform != expected_platform:
            raise ValueError(
                f"config platform mismatch: expected {expected_platform}, got {config_platform}"
            )
        layers = manifest.get("layers")
        if not isinstance(layers, list) or not layers or len(layers) > MAX_LAYERS:
            raise ValueError("manifest layers must be a non-empty array")
        layer_digests: list[str] = []
        verified_layers: dict[str, int] = {}
        total_layer_bytes = 0
        for index, raw_layer in enumerate(layers):
            layer = require_object(raw_layer, f"layer descriptor {index}")
            layer_digest = descriptor_digest(layer, f"layer {index}")[0]
            layer_size = layer.get("size")
            if type(layer_size) is not int or layer_size < 0 or layer_size > MAX_BLOB_BYTES:
                raise ValueError(f"invalid layer {index} size")
            if layer_digest in verified_layers:
                if verified_layers[layer_digest] != layer_size:
                    raise ValueError(f"duplicate layer {index} size mismatch")
            else:
                total_layer_bytes += layer_size
                if total_layer_bytes > MAX_TOTAL_LAYER_BYTES:
                    raise ValueError("manifest unique layers exceed total size limit")
                verify_blob(archive, layer, f"layer {index}")
                verified_layers[layer_digest] = layer_size
            layer_digests.append(layer_digest)
        return {
            "archive": str(archive_path),
            "indexDigest": "sha256:" + hashlib.sha256(index_payload).hexdigest(),
            "manifestDigest": descriptor_digest(manifest_descriptor, "manifest")[0],
            "configDigest": descriptor_digest(config, "config")[0],
            "layerDigests": layer_digests,
            "platform": observed_platform,
        }


try:
    first = inspect(first_path)
    second = inspect(second_path)
except (OSError, tarfile.TarError, ValueError, json.JSONDecodeError) as exc:
    print(f"reproducibility archive validation failed: {exc}", file=sys.stderr)
    raise SystemExit(65) from exc

status = (
    "success"
    if first["indexDigest"] == second["indexDigest"]
    and first["manifestDigest"] == second["manifestDigest"]
    and first["configDigest"] == second["configDigest"]
    and first["layerDigests"] == second["layerDigests"]
    else "failed"
)
result = {
    "schemaVersion": 1,
    "status": status,
    "expectedPlatform": expected_platform,
    "first": first,
    "second": second,
}
output = Path(report_path)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
if status != "success":
    print(
        "OCI reproducibility probe failed: index, manifest, config, or layer digests differ",
        file=sys.stderr,
    )
    raise SystemExit(1)
print(
    "oci_reproducibility=PASS "
    f"manifest={first['manifestDigest']} platform={expected_platform}"
)
PY
