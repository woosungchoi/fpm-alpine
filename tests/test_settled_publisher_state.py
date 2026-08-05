#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)


def load_module():
    path = ROOT / "scripts/validate-settled-publisher-state.py"
    spec = importlib.util.spec_from_file_location("validate_settled_publisher_state", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evidence() -> dict:
    return {
        "schemaVersion": 1,
        "source_sha": "a" * 40,
        "captured_at": "2026-07-13T14:56:00Z",
        "dockerhub": {"build_rule_active": False, "in_flight_builds": 0},
        "github": {"legacy_webhook_present": False},
    }


def metadata() -> dict:
    return {
        "namespace": "woosungchoi",
        "name": "fpm-alpine",
        "is_automated": False,
        "status": 1,
    }


class SettledPublisherStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def validate(self, payload: dict | None = None, live: dict | None = None) -> dict:
        raw = json.dumps(payload or evidence(), separators=(",", ":"), sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        return self.module.validate_state(
            raw,
            digest,
            live or metadata(),
            "woosungchoi/fpm-alpine",
            NOW,
        )

    def test_historical_quiescence_plus_live_disabled_state_passes(self) -> None:
        result = self.validate()
        self.assertEqual(result["repository"], "woosungchoi/fpm-alpine")
        self.assertIs(result["isAutomated"], False)
        self.assertGreater(result["settledSeconds"], 86400)

    def test_live_automatic_builds_are_rejected(self) -> None:
        live = metadata()
        live["is_automated"] = True
        with self.assertRaisesRegex(SystemExit, "still automated"):
            self.validate(live=live)

    def test_historical_in_flight_boolean_is_rejected(self) -> None:
        payload = evidence()
        payload["dockerhub"]["in_flight_builds"] = False
        with self.assertRaisesRegex(SystemExit, "not quiescent"):
            self.validate(payload=payload)

    def test_historical_webhook_presence_is_rejected(self) -> None:
        payload = evidence()
        payload["github"]["legacy_webhook_present"] = True
        with self.assertRaisesRegex(SystemExit, "webhook"):
            self.validate(payload=payload)

    def test_recent_or_future_cutover_is_not_settled(self) -> None:
        for captured in ("2026-08-05T11:30:00Z", "2026-08-05T12:02:00Z"):
            with self.subTest(captured=captured):
                payload = evidence()
                payload["captured_at"] = captured
                with self.assertRaisesRegex(SystemExit, "not settled"):
                    self.validate(payload=payload)

    def test_wrong_repository_or_invalid_live_types_are_rejected(self) -> None:
        cases = []
        wrong = metadata()
        wrong["name"] = "other"
        cases.append(wrong)
        bool_status = metadata()
        bool_status["status"] = True
        cases.append(bool_status)
        for live in cases:
            with self.subTest(live=live):
                with self.assertRaisesRegex(SystemExit, "metadata"):
                    self.validate(live=live)

    def test_hash_mismatch_is_rejected(self) -> None:
        raw = json.dumps(evidence(), separators=(",", ":"), sort_keys=True).encode()
        with self.assertRaisesRegex(SystemExit, "hash mismatch"):
            self.module.validate_state(
                raw,
                "0" * 64,
                metadata(),
                "woosungchoi/fpm-alpine",
                NOW,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
