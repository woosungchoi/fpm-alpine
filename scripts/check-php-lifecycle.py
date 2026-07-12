#!/usr/bin/env python3
"""Validate active PHP lifecycle dates and upstream release availability."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.error
import urllib.request
from pathlib import Path


def strict_date(value: object, field: str) -> dt.date:
    if not isinstance(value, str):
        raise SystemExit(f"{field} must be an ISO date string")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise SystemExit(f"{field} is not a valid ISO date: {value!r}") from error


def fetch_upstream(minor: str, base_url: str) -> str:
    url = base_url + minor
    try:
        with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - fixed HTTPS default
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError(f"upstream fetch failed for PHP {minor}: {error}") from error
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or not version.startswith(minor + "."):
        raise ValueError(f"upstream release mismatch for PHP {minor}: {version!r}")
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--versions", type=Path, default=Path("build/versions.json"))
    parser.add_argument("--today", help="ISO date override for deterministic tests")
    parser.add_argument("--output-json", type=Path, default=Path("lifecycle-reports/php-lifecycle.json"))
    parser.add_argument("--output-md", type=Path, default=Path("lifecycle-reports/php-lifecycle.md"))
    parser.add_argument("--skip-upstream", action="store_true")
    parser.add_argument(
        "--upstream-base-url",
        default="https://www.php.net/releases/index.php?json&version=",
    )
    args = parser.parse_args()

    today = strict_date(args.today, "today") if args.today else dt.datetime.now(dt.timezone.utc).date()
    try:
        payload = json.loads(args.versions.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"failed to read versions metadata: {error}") from error
    versions = payload.get("versions") if isinstance(payload, dict) else None
    if not isinstance(versions, dict) or list(versions) != ["8.2", "8.3", "8.4", "8.5"]:
        raise SystemExit("active lifecycle matrix must be exactly 8.2, 8.3, 8.4, 8.5")

    records: list[dict[str, object]] = []
    upstream_unavailable: list[str] = []
    mismatches: list[str] = []
    eol_reached: list[str] = []
    for minor, item in versions.items():
        if not isinstance(item, dict) or item.get("minor") != minor:
            raise SystemExit(f"invalid lifecycle entry for PHP {minor}")
        eol = strict_date(item.get("eol"), f"PHP {minor} eol")
        days = (eol - today).days
        state = "eol" if days < 0 else "warning-30" if days <= 30 else "warning-90" if days <= 90 else "ok"
        if state == "eol":
            eol_reached.append(minor)
        upstream_version = None
        if not args.skip_upstream:
            try:
                upstream_version = fetch_upstream(minor, args.upstream_base_url)
            except RuntimeError as error:
                upstream_unavailable.append(str(error))
            except ValueError as error:
                mismatches.append(str(error))
        records.append({
            "minor": minor,
            "configuredPatch": item.get("patch"),
            "support": item.get("support"),
            "eol": eol.isoformat(),
            "daysUntilEol": days,
            "state": state,
            "upstreamVersion": upstream_version,
        })

    report = {
        "schemaVersion": 1,
        "checkedAt": today.isoformat(),
        "records": records,
        "upstreamUnavailable": upstream_unavailable,
        "mismatches": mismatches,
        "eolReached": eol_reached,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = ["# PHP lifecycle report", "", f"- Checked: `{today.isoformat()}`", ""]
    for item in records:
        lines.append(
            f"- PHP `{item['minor']}`: `{item['state']}`; EOL `{item['eol']}` "
            f"(`{item['daysUntilEol']}` days); upstream `{item['upstreamVersion'] or 'unavailable'}`"
        )
    if upstream_unavailable:
        lines += ["", "## Upstream source unavailable", ""] + [f"- {item}" for item in upstream_unavailable]
    if mismatches:
        lines += ["", "## Lifecycle/source mismatch", ""] + [f"- {item}" for item in mismatches]
    args.output_md.write_text("\n".join(lines) + "\n")
    print(args.output_md.read_text(), end="")

    if upstream_unavailable:
        return 3
    if mismatches or eol_reached:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
