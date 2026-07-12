#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_dockerhub_metadata.py"
spec = importlib.util.spec_from_file_location("sync_dockerhub_metadata", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        if self.payload is None:
            return b""
        return json.dumps(self.payload).encode()


class QueueOpener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.payloads:
            raise AssertionError("unexpected HTTP request")
        return FakeResponse(self.payloads.pop(0))


class DockerHubMetadataTests(unittest.TestCase):
    def test_constants_are_scoped(self):
        self.assertEqual(module.REPOSITORY, "woosungchoi/fpm-alpine")
        self.assertLessEqual(len(module.SHORT_DESCRIPTION), 100)
        self.assertEqual(module.MAX_FULL_DESCRIPTION_BYTES, 25000)

    def test_load_description_rejects_oversized_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "description.md"
            path.write_text("x" * (module.MAX_FULL_DESCRIPTION_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "25000"):
                module.load_description(path)

    def test_authentication_uses_current_token_endpoint(self):
        opener = QueueOpener([{"access_token": "session-jwt"}])
        token = module.authenticate("woosungchoi", "repository-pat", opener=opener)
        self.assertEqual(token, "session-jwt")
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, "https://hub.docker.com/v2/auth/token")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(timeout, 30)
        self.assertEqual(json.loads(request.data), {"identifier": "woosungchoi", "secret": "repository-pat"})

    def test_patch_is_bearer_authenticated_and_exact(self):
        opener = QueueOpener([{}])
        module.patch_metadata("session-jwt", "full description", opener=opener)
        request, _ = opener.requests[0]
        self.assertEqual(request.full_url, "https://hub.docker.com/v2/repositories/woosungchoi/fpm-alpine")
        self.assertEqual(request.get_method(), "PATCH")
        self.assertEqual(request.get_header("Authorization"), "Bearer session-jwt")
        self.assertEqual(
            json.loads(request.data),
            {"description": module.SHORT_DESCRIPTION, "full_description": "full description"},
        )

    def test_sync_retries_public_readback_without_leaking_secrets(self):
        expected = {"description": module.SHORT_DESCRIPTION, "full_description": "full description"}
        opener = QueueOpener([
            {"access_token": "session-jwt"},
            {},
            {"description": "stale", "full_description": "old"},
            expected,
        ])
        sleeps = []
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            digest = module.synchronize(
                "woosungchoi",
                "repository-pat",
                "full description",
                opener=opener,
                sleeper=sleeps.append,
                attempts=3,
            )
        self.assertEqual(sleeps, [2])
        self.assertEqual(len(digest), 64)
        self.assertNotIn("repository-pat", stdout.getvalue())
        self.assertNotIn("session-jwt", stdout.getvalue())
        self.assertEqual(opener.requests[-1][0].get_method(), "GET")


if __name__ == "__main__":
    unittest.main()
