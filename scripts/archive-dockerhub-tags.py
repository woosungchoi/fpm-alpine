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
PUBLISHER_IDENTITY = r"^https://github.com/woosungchoi/fpm-alpine/.github/workflows/publish.yml@refs/heads/(main|8\.5)$"
ISSUER = "https://token.actions.githubusercontent.com"
CANONICAL_CLASSES = frozenset({"canary", "immutable-release", "immutable-source"})
KNOWN_CLASSES = CANONICAL_CLASSES | {"legacy", "frozen"}
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


def anonymous_inspect(subject: str) -> None:
    with tempfile.TemporaryDirectory() as docker_config:
        anonymous_env = os.environ.copy()
        anonymous_env["DOCKER_CONFIG"] = docker_config
        run(["docker", "buildx", "imagetools", "inspect", subject], env=anonymous_env)


def verify_signature(subject: str, identity: str) -> None:
    run(
        [
            "cosign",
            "verify",
            "--certificate-identity-regexp",
            identity,
            "--certificate-oidc-issuer",
            ISSUER,
            subject,
        ]
    )


def detect_php_minor(subject: str) -> str:
    platform_subject = run(
        [str(ROOT / "scripts/resolve-platform-image.py"), subject, "linux/amd64"],
        output=True,
    )
    minor = run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--entrypoint",
            "php",
            platform_subject,
            "-r",
            'echo PHP_MAJOR_VERSION, ".", PHP_MINOR_VERSION;',
        ],
        output=True,
    )
    if not re.fullmatch(r"8\.[0-5]", minor):
        raise RuntimeError(f"unsupported archived PHP minor: {minor!r}")
    return minor


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
        classification = candidate.get("classification")
        if (
            not isinstance(source_tag, str)
            or not isinstance(source_digest, str)
            or not DIGEST_RE.fullmatch(source_digest)
            or classification not in KNOWN_CLASSES
        ):
            raise SystemExit("invalid deletion candidate")
        source_subject = f"{DOCKERHUB_REPOSITORY}@{source_digest}"
        php_minor = detect_php_minor(source_subject)

        canonical_ref = None
        canonical_digest = None
        canonical_parity = "not_applicable"
        canonical_signature = "not_applicable"
        canonical_anonymous_read = "not_applicable"
        if classification in CANONICAL_CLASSES:
            canonical_ref = f"{GHCR_REPOSITORY}:{source_tag}"
            canonical_digest = resolve(canonical_ref)
            canonical_subject = f"{GHCR_REPOSITORY}@{canonical_digest}"
            canonical_parity_path = args.report_dir / f"{index:03d}-{source_tag}-canonical-parity.json"
            run(
                [
                    str(ROOT / "scripts/verify-image-parity.py"),
                    source_subject,
                    canonical_subject,
                    "--output",
                    str(canonical_parity_path),
                ]
            )
            verify_signature(canonical_subject, PUBLISHER_IDENTITY)
            anonymous_inspect(canonical_subject)
            canonical_parity = "verified"
            canonical_signature = "verified"
            canonical_anonymous_read = "verified"

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
        runtime_path = args.report_dir / f"{index:03d}-{destination_tag}-runtime"
        run(
            [
                str(ROOT / "scripts/verify-rollback-image.sh"),
                source_subject,
                destination_subject,
                php_minor,
                str(runtime_path),
            ]
        )
        run(["cosign", "sign", "--yes", destination_subject])
        verify_signature(destination_subject, IDENTITY)
        anonymous_inspect(destination_subject)
        entries.append(
            {
                "source_tag": source_tag,
                "source_digest": source_digest,
                "classification": classification,
                "php_minor": php_minor,
                "archive_ref": destination_ref,
                "archive_digest": destination_digest,
                "parity": "verified",
                "signature": "verified",
                "anonymous_read": "verified",
                "runtime": "verified",
                "canonical_ref": canonical_ref,
                "canonical_digest": canonical_digest,
                "canonical_parity": canonical_parity,
                "canonical_signature": canonical_signature,
                "canonical_anonymous_read": canonical_anonymous_read,
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
