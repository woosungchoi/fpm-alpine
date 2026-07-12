#!/usr/bin/env python3
"""Re-download every pinned PECL source and verify archive shape and SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path


def _resolver():
    path = Path(__file__).with_name("resolve-dependency-candidates.py")
    spec = importlib.util.spec_from_file_location("checksum_candidate_resolver", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--versions", default="build/versions.json")
    args = parser.parse_args()
    data = json.loads(Path(args.versions).read_text())
    resolver = _resolver()
    for name, row in data["dependencies"].items():
        archive = resolver.fetch_bytes(row["url"])
        if not resolver.valid_tgz(archive):
            print(f"source checksum rejected: {name} archive is not a safe tgz")
            return 1
        actual = hashlib.sha256(archive).hexdigest()
        if actual != row["sha256"]:
            print(
                f"source checksum rejected: {name} expected {row['sha256']}, got {actual}"
            )
            return 1
        print(f"source_checksum=PASS dependency={name} bytes={len(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
