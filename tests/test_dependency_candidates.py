#!/usr/bin/env python3
"""Tests for deterministic dependency candidate discovery and application."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import tarfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = json.loads((ROOT / "build/versions.json").read_text())
POLICY = json.loads((ROOT / "build/automation-policy.json").read_text())


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tgz_bytes(name: str = "package/README") -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        payload = b"fixture\n"
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return out.getvalue()


class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module(
            ROOT / "scripts/resolve-dependency-candidates.py", "candidate_resolver"
        )

    def discover(
        self,
        *,
        digests: dict[str, str] | None = None,
        patches: dict[str, str] | None = None,
        latest: dict[str, str] | None = None,
        archives: dict[str, bytes] | None = None,
    ) -> dict:
        digests = digests or {
            minor: row["base_image"].split("@", 1)[1]
            for minor, row in VERSIONS["versions"].items()
        }
        patches = patches or {
            minor: row["patch"] for minor, row in VERSIONS["versions"].items()
        }
        latest = latest or {
            name: row["version"] for name, row in VERSIONS["dependencies"].items()
        }
        archives = archives or {}

        def digest_resolver(ref: str) -> str:
            minor = ref.split(":", 1)[1].split("-", 1)[0]
            return digests[minor]

        def patch_resolver(subject: str) -> str:
            minor = subject.split(":", 1)[1].split("-", 1)[0]
            return patches[minor]

        def text_fetcher(url: str) -> str:
            name = url.rsplit("/", 2)[-2]
            return latest[name]

        def bytes_fetcher(url: str) -> bytes:
            name = url.rsplit("/", 1)[-1].split("-", 1)[0]
            return archives.get(name, tgz_bytes())

        return self.module.discover(
            copy.deepcopy(VERSIONS),
            copy.deepcopy(POLICY),
            digest_resolver=digest_resolver,
            patch_resolver=patch_resolver,
            text_fetcher=text_fetcher,
            bytes_fetcher=bytes_fetcher,
            generated_at="2026-07-12T00:00:00Z",
            source_commit="a" * 40,
        )

    def test_no_updates_is_empty(self) -> None:
        result = self.discover()
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["warnings"], [])

    def test_same_patch_new_base_digest_is_eligible(self) -> None:
        result = self.discover(
            digests={
                **{
                    minor: row["base_image"].split("@", 1)[1]
                    for minor, row in VERSIONS["versions"].items()
                },
                "8.5": "sha256:" + "b" * 64,
            }
        )
        candidate = result["candidates"][0]
        self.assertEqual(candidate["key"], "base-8.5")
        self.assertTrue(candidate["eligible"])
        self.assertEqual(candidate["affectedMinors"], ["8.5"])

    def test_new_same_minor_patch_is_eligible(self) -> None:
        result = self.discover(
            patches={
                **{m: r["patch"] for m, r in VERSIONS["versions"].items()},
                "8.5": "8.5.9",
            },
            digests={
                **{
                    m: r["base_image"].split("@", 1)[1]
                    for m, r in VERSIONS["versions"].items()
                },
                "8.5": "sha256:" + "c" * 64,
            },
        )
        self.assertTrue(result["candidates"][0]["eligible"])
        applied = self.module.apply_candidate(
            copy.deepcopy(VERSIONS), result["candidates"][0]
        )
        self.assertEqual(applied["versions"]["8.5"]["patch"], "8.5.9")
        self.assertTrue(applied["versions"]["8.5"]["base_image"].endswith("c" * 64))

    def test_wrong_minor_base_patch_is_warning_not_candidate(self) -> None:
        result = self.discover(
            patches={
                **{m: r["patch"] for m, r in VERSIONS["versions"].items()},
                "8.5": "8.6.0",
            },
            digests={
                **{
                    m: r["base_image"].split("@", 1)[1]
                    for m, r in VERSIONS["versions"].items()
                },
                "8.5": "sha256:" + "d" * 64,
            },
        )
        self.assertEqual(result["candidates"], [])
        self.assertIn("wrong minor", " ".join(result["warnings"]))

    def test_pecl_patch_candidate_records_real_archive_hash(self) -> None:
        archive = tgz_bytes()
        result = self.discover(
            latest={
                **{n: r["version"] for n, r in VERSIONS["dependencies"].items()},
                "imagick": "3.8.2",
            },
            archives={"imagick": archive},
        )
        candidate = result["candidates"][0]
        self.assertEqual(candidate["key"], "pecl-imagick")
        self.assertTrue(candidate["eligible"])
        self.assertEqual(len(candidate["new"]["sha256"]), 64)
        applied = self.module.apply_candidate(copy.deepcopy(VERSIONS), candidate)
        self.assertEqual(applied["dependencies"]["imagick"]["version"], "3.8.2")

    def test_pecl_minor_candidate_requires_manual_review(self) -> None:
        result = self.discover(
            latest={
                **{n: r["version"] for n, r in VERSIONS["dependencies"].items()},
                "redis": "6.4.0",
            }
        )
        candidate = result["candidates"][0]
        self.assertFalse(candidate["eligible"])
        self.assertEqual(candidate["class"], "pecl-manual-review")

    def test_invalid_archive_is_warning_not_candidate(self) -> None:
        result = self.discover(
            latest={
                **{n: r["version"] for n, r in VERSIONS["dependencies"].items()},
                "apcu": "5.1.29",
            },
            archives={"apcu": b"<html>upstream error</html>"},
        )
        self.assertEqual(result["candidates"], [])
        self.assertIn("archive", " ".join(result["warnings"]))

    def test_boolean_policy_schema_is_rejected(self) -> None:
        policy = copy.deepcopy(POLICY)
        policy["schemaVersion"] = True
        with self.assertRaises(ValueError):
            self.module.discover(
                copy.deepcopy(VERSIONS),
                policy,
                digest_resolver=lambda _: "sha256:" + "0" * 64,
                patch_resolver=lambda _: "8.5.8",
                text_fetcher=lambda _: "1.0.0",
                bytes_fetcher=lambda _: tgz_bytes(),
                generated_at="2026-07-12T00:00:00Z",
                source_commit="a" * 40,
            )

    def test_php_patch_is_read_from_official_image_aliases(self) -> None:
        metadata = """Tags: 8.5.8-fpm-alpine3.24, 8.5-fpm-alpine3.24, 8.5.8-fpm-alpine, 8.5-fpm-alpine
Architectures: amd64, arm64v8
GitCommit: a819a7d09d5597ce6ebfa58b32003e5e052bac37
Directory: 8.5/alpine3.24/fpm
"""
        self.assertEqual(
            self.module.patch_from_official_images(metadata, "8.5-fpm-alpine"),
            "8.5.8",
        )
        ambiguous = metadata.replace(
            "8.5.8-fpm-alpine,", "8.5.8-fpm-alpine, 8.5.9-fpm-alpine,"
        )
        with self.assertRaises(ValueError):
            self.module.patch_from_official_images(ambiguous, "8.5-fpm-alpine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
