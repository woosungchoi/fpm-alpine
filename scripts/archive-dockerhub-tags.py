#!/usr/bin/env python3
"""Archive every Docker Hub deletion candidate to signed, public GHCR evidence tags."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

DOCKERHUB_REPOSITORY = "docker.io/woosungchoi/fpm-alpine"
GHCR_REPOSITORY = "ghcr.io/woosungchoi/fpm-alpine"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IDENTITY = r"^https://github.com/woosungchoi/fpm-alpine/.github/workflows/prune-dockerhub-tags.yml@refs/heads/main$"
ISSUER = "https://token.actions.githubusercontent.com"
ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, env: dict[str, str] | None = None, output: bool = False) -> str:
    completed = subprocess.run(
        command,
        env=env,
        text=True,
        stdout=subprocess.PIPE if output else None,
        check=True,
    )
    return completed.stdout.strip() if output else ""


def resolve(reference: str, *, env: dict[str, str] | None = None) -> str:
    digest = run([str(ROOT / "scripts/resolve-image-digest.sh"), reference], env=env, output=True)
    if not DIGEST_RE.fullmatch(digest):
        raise RuntimeError(f"invalid resolved digest: {reference}")
    return digest


def archive_tag(source_tag: str, source_digest: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", source_tag):
        raise RuntimeError(f"source tag cannot be archived safely: {source_tag}")
    value = f"archive-dockerhub-{source_tag}-{source_digest[7:19]}"
    if len(value) > 128:
        raise RuntimeError(f"archive tag is too long: {source_tag}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()

    for command in ("docker", "cosign"):
        if shutil.which(command) is None:
            raise SystemExit(f"required command is missing: {command}")
    plan = json.loads(args.plan.read_text())
    delete = plan.get("delete")
    if plan.get("repository") != "woosungchoi/fpm-alpine" or not isinstance(delete, list):
        raise SystemExit("invalid deletion plan")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, candidate in enumerate(delete):
        source_tag = candidate.get("name")
        source_digest = candidate.get("digest")
        if not isinstance(source_tag, str) or not isinstance(source_digest, str) or not DIGEST_RE.fullmatch(source_digest):
            raise SystemExit("invalid deletion candidate")
        source_subject = f"{DOCKERHUB_REPOSITORY}@{source_digest}"
        destination_tag = archive_tag(source_tag, source_digest)
        destination_ref = f"{GHCR_REPOSITORY}:{destination_tag}"
        try:
            destination_digest = resolve(destination_ref)
        except subprocess.CalledProcessError:
            run(["docker", "buildx", "imagetools", "create", "--tag", destination_ref, source_subject])
            destination_digest = resolve(destination_ref)
        destination_subject = f"{GHCR_REPOSITORY}@{destination_digest}"
        parity_path = args.report_dir / f"{index:03d}-{destination_tag}-parity.json"
        run(
            [
                str(ROOT / "scripts/verify-image-parity.py"),
                source_subject,
                destination_subject,
                "--output",
                str(parity_path),
            ]
        )
        run(["cosign", "sign", "--yes", destination_subject])
        run(
            [
                "cosign",
                "verify",
                "--certificate-identity-regexp",
                IDENTITY,
                "--certificate-oidc-issuer",
                ISSUER,
                destination_subject,
            ]
        )
        with tempfile.TemporaryDirectory() as docker_config:
            anonymous_env = os.environ.copy()
            anonymous_env["DOCKER_CONFIG"] = docker_config
            run(["docker", "buildx", "imagetools", "inspect", destination_subject], env=anonymous_env)
        entries.append(
            {
                "source_tag": source_tag,
                "source_digest": source_digest,
                "archive_ref": destination_ref,
                "archive_digest": destination_digest,
                "parity": "verified",
                "signature": "verified",
                "anonymous_read": "verified",
            }
        )
    archive_map = {
        "schema_version": 1,
        "repository": "woosungchoi/fpm-alpine",
        "inventory_sha256": plan.get("inventory_sha256"),
        "entries": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(archive_map, indent=2, sort_keys=True) + "\n")
    print(f"dockerhub_archive=PASS count={len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
