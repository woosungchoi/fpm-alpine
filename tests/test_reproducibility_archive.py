#!/usr/bin/env python3
"""Tests for cryptographic comparison of independently exported OCI archives."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify-local-reproducibility.sh"


def canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def write_archive(
    path: Path,
    layer_payload: bytes,
    *,
    tar_mtime: int,
    manifest_count: int = 1,
    index_annotation: str | None = None,
    config_architecture: str = "amd64",
    layer_count: int = 1,
) -> None:
    config = canonical(
        {
            "architecture": config_architecture,
            "os": "linux",
            "rootfs": {
                "type": "layers",
                "diff_ids": [digest(layer_payload)] * layer_count,
            },
        }
    )
    manifest = canonical(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": digest(config),
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": digest(layer_payload),
                    "size": len(layer_payload),
                }
            ]
            * layer_count,
        }
    )
    descriptor = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "digest": digest(manifest),
        "size": len(manifest),
        "platform": {"architecture": "amd64", "os": "linux"},
    }
    index_value: dict[str, object] = {
        "schemaVersion": 2,
        "manifests": [descriptor] * manifest_count,
    }
    if index_annotation is not None:
        index_value["annotations"] = {"example.invalid/test": index_annotation}
    index = canonical(index_value)
    blobs = {
        digest(config): config,
        digest(layer_payload): layer_payload,
        digest(manifest): manifest,
    }
    with tarfile.open(path, "w") as archive:
        for name, payload in {
            "oci-layout": canonical({"imageLayoutVersion": "1.0.0"}),
            "index.json": index,
            **{
                f"blobs/sha256/{key.removeprefix('sha256:')}": value
                for key, value in blobs.items()
            },
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mtime = tar_mtime
            archive.addfile(info, io.BytesIO(payload))


class ReproducibilityArchiveTests(unittest.TestCase):
    def run_verify(
        self, first: Path, second: Path, report: Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), str(first), str(second), str(report), "linux/amd64"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

    def test_identical_manifest_passes_despite_outer_tar_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, report = (
                root / "first.tar",
                root / "second.tar",
                root / "report.json",
            )
            write_archive(first, b"same-layer", tar_mtime=1)
            write_archive(second, b"same-layer", tar_mtime=999)
            result = self.run_verify(first, second, report)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text())
            self.assertEqual(payload["status"], "success")
            self.assertEqual(
                payload["first"]["indexDigest"], payload["second"]["indexDigest"]
            )
            self.assertEqual(
                payload["first"]["manifestDigest"], payload["second"]["manifestDigest"]
            )

    def test_changed_layer_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, report = (
                root / "first.tar",
                root / "second.tar",
                root / "report.json",
            )
            write_archive(first, b"layer-a", tar_mtime=1)
            write_archive(second, b"layer-b", tar_mtime=1)
            result = self.run_verify(first, second, report)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(report.read_text())["status"], "failed")

    def test_changed_index_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, report = (
                root / "first.tar",
                root / "second.tar",
                root / "report.json",
            )
            write_archive(first, b"same", tar_mtime=1, index_annotation="first")
            write_archive(second, b"same", tar_mtime=1, index_annotation="second")
            result = self.run_verify(first, second, report)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(report.read_text())["status"], "failed")

    def test_config_platform_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, report = (
                root / "first.tar",
                root / "second.tar",
                root / "report.json",
            )
            write_archive(first, b"same", tar_mtime=1, config_architecture="arm64")
            write_archive(second, b"same", tar_mtime=1, config_architecture="arm64")
            result = self.run_verify(first, second, report)
            self.assertEqual(result.returncode, 65)
            self.assertIn("config platform mismatch", result.stderr)

    def test_multiple_manifests_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, report = (
                root / "first.tar",
                root / "second.tar",
                root / "report.json",
            )
            write_archive(first, b"same", tar_mtime=1, manifest_count=2)
            write_archive(second, b"same", tar_mtime=1)
            result = self.run_verify(first, second, report)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly one image manifest", result.stderr)

    def test_excessive_layer_count_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, report = (
                root / "first.tar",
                root / "second.tar",
                root / "report.json",
            )
            write_archive(first, b"same", tar_mtime=1, layer_count=257)
            write_archive(second, b"same", tar_mtime=1)
            result = self.run_verify(first, second, report)
            self.assertEqual(result.returncode, 65)
            self.assertIn("manifest layers", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
