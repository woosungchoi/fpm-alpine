from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "refresh-cutover-lease.py"
SOURCE = "a" * 40


def load_module():
    spec = importlib.util.spec_from_file_location("refresh_cutover_lease", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RefreshCutoverLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.hook = dict(self.module.ALLOWED_HOOK)
        self.hook["events"] = ["push", "pull_request"]
        self.hook["config"] = {
            "url": "https://api.snyk.io/webhook/github/00000000-0000-4000-8000-000000000000",
            "content_type": "json",
            "insecure_ssl": "0",
        }

    def gh_payload(self, endpoint: str):
        if endpoint == "user":
            return {"login": "woosungchoi", "id": 5674610}
        if endpoint.endswith("git/ref/heads/main"):
            return {"object": {"sha": SOURCE}}
        if "/hooks?" in endpoint:
            return [self.hook]
        raise AssertionError(endpoint)

    @staticmethod
    def dockerhub_payload(_url: str):
        return {
            "namespace": "woosungchoi",
            "name": "fpm-alpine",
            "status": 1,
            "is_automated": False,
            "last_updated": "2026-08-10T00:00:00Z",
        }

    def test_capture_binds_owner_main_live_disabled_metadata_and_exact_hooks(self) -> None:
        with mock.patch.object(self.module, "_gh_json", side_effect=self.gh_payload), mock.patch.object(
            self.module, "_url_json", side_effect=self.dockerhub_payload
        ):
            raw = self.module.capture(SOURCE)
        text = raw.decode()
        self.assertIn('"schemaVersion":1', text)
        self.assertIn(f'"source_sha":"{SOURCE}"', text)
        self.assertIn('"public_is_automated":false', text)
        self.assertIn('"in_flight_builds":0', text)
        self.assertIn('"id":402842509', text)
        self.assertNotIn("00000000-0000-4000-8000-000000000000", text)

    def test_wrong_owner_main_automation_or_hook_set_fails_closed(self) -> None:
        cases = []
        cases.append((lambda endpoint: {"login": "other", "id": 1} if endpoint == "user" else self.gh_payload(endpoint), self.dockerhub_payload))
        cases.append((lambda endpoint: {"object": {"sha": "b" * 40}} if endpoint.endswith("git/ref/heads/main") else self.gh_payload(endpoint), self.dockerhub_payload))
        cases.append((self.gh_payload, lambda _url: {**self.dockerhub_payload(_url), "is_automated": True}))
        cases.append((lambda endpoint: [] if "/hooks?" in endpoint else self.gh_payload(endpoint), self.dockerhub_payload))
        for index, (gh_side_effect, hub_side_effect) in enumerate(cases):
            with (
                self.subTest(case=index),
                mock.patch.object(
                    self.module, "_gh_json", side_effect=gh_side_effect
                ),
                mock.patch.object(
                    self.module, "_url_json", side_effect=hub_side_effect
                ),
                self.assertRaises(SystemExit),
            ):
                self.module.capture(SOURCE)

    def test_active_publisher_requires_exact_source_path_event_and_status(self) -> None:
        valid = {
            "head_sha": SOURCE,
            "status": "in_progress",
            "event": "push",
            "path": ".github/workflows/dependency-auto-publish.yml",
        }
        with mock.patch.object(
            self.module, "_gh_json", return_value={"workflow_runs": [valid]}
        ):
            self.assertTrue(self.module._active_publisher_for(SOURCE))
        for key, value in (
            ("head_sha", "b" * 40),
            ("status", "completed"),
            ("event", "repository_dispatch"),
            ("path", ".github/workflows/publish.yml"),
        ):
            with mock.patch.object(
                self.module, "_gh_json", return_value={"workflow_runs": [{**valid, key: value}]}
            ):
                self.assertFalse(self.module._active_publisher_for(SOURCE))


if __name__ == "__main__":
    unittest.main()
