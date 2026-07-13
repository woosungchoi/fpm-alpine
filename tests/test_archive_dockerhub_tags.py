#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "archive-dockerhub-tags.py"
spec = importlib.util.spec_from_file_location("archive_dockerhub_tags", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load archive controller")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ArchiveDockerHubTagsTests(unittest.TestCase):
    def setUp(self):
        self.original_run = getattr(module, "run")

    def tearDown(self):
        setattr(module, "run", self.original_run)

    def test_detect_php_minor_uses_exact_amd64_platform_subject(self):
        subject = "docker.io/woosungchoi/fpm-alpine@sha256:" + "1" * 64
        platform_subject = "docker.io/woosungchoi/fpm-alpine@sha256:" + "2" * 64
        calls = []

        def fake_run(command, *, env=None, output=False):
            calls.append((command, env, output))
            if command[0].endswith("resolve-platform-image.py"):
                self.assertEqual(command[1:], [subject, "linux/amd64"])
                return platform_subject
            self.assertEqual(
                command[:8],
                ["docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "php", platform_subject],
            )
            return "8.0"

        setattr(module, "run", fake_run)
        self.assertEqual(module.detect_php_minor(subject), "8.0")
        self.assertEqual(len(calls), 2)

    def test_detect_php_minor_rejects_unsupported_version(self):
        responses = iter(["docker.io/example/image@sha256:" + "2" * 64, "8.6"])
        setattr(module, "run", lambda *args, **kwargs: next(responses))
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            module.detect_php_minor("docker.io/example/image@sha256:" + "1" * 64)

    def test_signature_verification_pins_identity_and_issuer(self):
        calls = []
        setattr(module, "run", lambda command, **kwargs: calls.append(command) or "")
        subject = "ghcr.io/woosungchoi/fpm-alpine@sha256:" + "3" * 64
        module.verify_signature(subject, module.PUBLISHER_IDENTITY)
        self.assertEqual(calls[0][0:2], ["cosign", "verify"])
        self.assertIn(module.PUBLISHER_IDENTITY, calls[0])
        self.assertIn(module.ISSUER, calls[0])
        self.assertEqual(calls[0][-1], subject)

    def test_anonymous_inspect_uses_temporary_empty_docker_config(self):
        observed = {}

        def fake_run(command, *, env=None, output=False):
            self.assertEqual(command[:4], ["docker", "buildx", "imagetools", "inspect"])
            self.assertIsNotNone(env)
            assert env is not None
            observed["config"] = env["DOCKER_CONFIG"]
            self.assertTrue(Path(observed["config"]).is_dir())
            self.assertEqual(list(Path(observed["config"]).iterdir()), [])
            return ""

        setattr(module, "run", fake_run)
        module.anonymous_inspect("ghcr.io/woosungchoi/fpm-alpine@sha256:" + "4" * 64)
        self.assertFalse(Path(observed["config"]).exists())

    def test_archive_tag_is_collision_resistant_and_bounded(self):
        digest = "sha256:" + "a" * 64
        self.assertEqual(
            module.archive_tag("8.0", digest),
            "archive-dockerhub-8.0-aaaaaaaaaaaa",
        )
        with self.assertRaisesRegex(RuntimeError, "safely"):
            module.archive_tag("bad/tag", digest)

    def test_qemu_sigsegv_is_retried_with_a_bound(self):
        calls = 0
        sleeps = []

        def flaky_run(command, **kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise subprocess.CalledProcessError(139, command)
            return ""

        setattr(module, "run", flaky_run)
        module.run_with_qemu_retry(["verify-runtime"], attempts=3, sleeper=sleeps.append)
        self.assertEqual(calls, 3)
        self.assertEqual(sleeps, [2, 4])

    def test_non_qemu_failure_is_not_retried(self):
        calls = 0

        def failed_run(command, **kwargs):
            nonlocal calls
            calls += 1
            raise subprocess.CalledProcessError(1, command)

        setattr(module, "run", failed_run)
        with self.assertRaises(subprocess.CalledProcessError):
            module.run_with_qemu_retry(["verify-runtime"], attempts=3, sleeper=lambda _: None)
        self.assertEqual(calls, 1)

    def test_archive_map_is_checkpointed_after_each_candidate(self):
        entry = {"source_tag": "8.0", "source_digest": "sha256:" + "1" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "archive-map.json"
            module.write_archive_checkpoint(output, "a" * 64, [entry])
            payload = json.loads(output.read_text())
            self.assertEqual(payload["inventory_sha256"], "a" * 64)
            self.assertEqual(payload["entries"], [entry])
            self.assertFalse(output.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
