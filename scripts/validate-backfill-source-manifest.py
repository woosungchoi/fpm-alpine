#!/usr/bin/env python3
"""Bind a requested backfill commit to the exact trusted release manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_ROOT_KEYS = ("schemaVersion", "dependencies", "runtimeContracts", "versions")


def load_manifest(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid {label} release manifest: {exc}") from exc
    if type(value) is not dict or tuple(value) != REQUIRED_ROOT_KEYS:
        raise SystemExit(f"invalid {label} release manifest root")
    if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 2:
        raise SystemExit(f"invalid {label} release manifest schemaVersion")
    for key in REQUIRED_ROOT_KEYS[1:]:
        if type(value.get(key)) is not dict:
            raise SystemExit(f"invalid {label} release manifest section: {key}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("current", type=Path)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()

    current = load_manifest(args.current, "trusted current")
    source = load_manifest(args.source, "requested source")
    if source != current:
        changed = [key for key in REQUIRED_ROOT_KEYS if source.get(key) != current.get(key)]
        detail = ", ".join(changed) or "unknown"
        raise SystemExit(
            "requested source release manifest does not exactly match "
            f"trusted current manifest (changed: {detail})"
        )
    print("requested source release manifest exactly matches trusted current manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
