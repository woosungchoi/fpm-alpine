#!/usr/bin/env python3
"""Verify that Docker Hub exposes exactly the four active moving tags."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dockerhub_tag_policy import KEEP_TAGS, fetch_inventory, inventory_sha256, verify_exact_surface


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    inventory = fetch_inventory()
    verify_exact_surface(inventory)
    report = {
        "schema_version": 1,
        "repository": "woosungchoi/fpm-alpine",
        "status": "PASS",
        "tag_count": len(inventory),
        "tags": [row["name"] for row in inventory],
        "expected_tags": list(KEEP_TAGS),
        "inventory_sha256": inventory_sha256(inventory),
    }
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        "dockerhub_tag_policy=PASS repository=woosungchoi/fpm-alpine "
        f"tags={','.join(report['tags'])} inventory_sha256={report['inventory_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
