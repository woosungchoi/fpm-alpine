#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dockerhub_tag_policy.py"
spec = importlib.util.spec_from_file_location("dockerhub_tag_policy", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load dockerhub tag policy module")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeResponse:
    def __init__(self, payload=None):
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
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return FakeResponse(response)


def tag(name: str, seed: str) -> dict:
    digest = "sha256:" + seed * 64
    return {
        "name": name,
        "digest": digest,
        "images": [
            {"os": "linux", "architecture": "amd64", "digest": "sha256:" + "a" * 64},
            {"os": "linux", "architecture": "arm64", "digest": "sha256:" + "b" * 64},
        ],
    }


def archive_map(plan: dict) -> dict:
    canonical_classes = {"canary", "immutable-release", "immutable-source"}
    entries = []
    for index, row in enumerate(plan["delete"]):
        classification = row["classification"]
        canonical = classification in canonical_classes
        entries.append(
            {
                "source_tag": row["name"],
                "source_digest": row["digest"],
                "classification": classification,
                "php_minor": "8.5" if classification != "frozen" else row["name"],
                "archive_ref": f"ghcr.io/woosungchoi/fpm-alpine:archive-dockerhub-{index}",
                "archive_digest": "sha256:" + "d" * 64,
                "parity": "verified",
                "signature": "verified",
                "anonymous_read": "verified",
                "runtime": "verified",
                "canonical_ref": f"ghcr.io/woosungchoi/fpm-alpine:{row['name']}" if canonical else None,
                "canonical_digest": "sha256:" + "c" * 64 if canonical else None,
                "canonical_parity": "verified" if canonical else "not_applicable",
                "canonical_signature": "verified" if canonical else "not_applicable",
                "canonical_anonymous_read": "verified" if canonical else "not_applicable",
            }
        )
    return {"schema_version": 1, "entries": entries}


class DockerHubTagPolicyTests(unittest.TestCase):
    def setUp(self):
        self.inventory = [
            tag("8.2", "2"),
            tag("8.3", "3"),
            tag("8.4", "4"),
            tag("8.5", "5"),
            tag("canary-8.5-100-1", "6"),
            tag("8.5.8-20260713-" + "7" * 64, "7"),
            tag("sha-8.5-0123456789ab-" + "8" * 64, "8"),
            tag("8.0", "9"),
            tag("8.1", "a"),
            tag("this", "b"),
        ]

    def test_fetch_inventory_follows_pagination_and_canonicalizes(self):
        opener = QueueOpener([
            {
                "results": self.inventory[:5],
                "next": "https://hub.docker.com/v2/repositories/woosungchoi/fpm-alpine/tags?page=2&page_size=100",
            },
            {"results": self.inventory[5:], "next": None},
        ])
        observed = module.fetch_inventory(opener=opener)
        self.assertEqual([row["name"] for row in observed], sorted(row["name"] for row in self.inventory))
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(opener.requests[0][0].get_method(), "GET")
        self.assertNotIn("Authorization", dict(opener.requests[0][0].header_items()))

    def test_exact_surface_requires_only_four_moving_tags(self):
        keep = [row for row in self.inventory if row["name"] in module.KEEP_TAGS]
        module.verify_exact_surface(keep)
        with self.assertRaisesRegex(module.PolicyError, "unexpected"):
            module.verify_exact_surface(keep + [tag("latest", "c")])
        with self.assertRaisesRegex(module.PolicyError, "missing"):
            module.verify_exact_surface(keep[:-1])

    def test_plan_classifies_known_tags_and_rejects_unknown(self):
        plan = module.build_deletion_plan(self.inventory)
        self.assertEqual(plan["keep_tags"], ["8.2", "8.3", "8.4", "8.5"])
        self.assertEqual(len(plan["delete"]), 6)
        self.assertEqual(plan["inventory_sha256"], module.inventory_sha256(self.inventory))
        self.assertEqual(len(module.deletion_plan_sha256(plan)), 64)
        with self.assertRaisesRegex(module.PolicyError, "unclassified"):
            module.build_deletion_plan(self.inventory + [tag("latest", "c")])

    def test_archive_map_must_cover_every_delete_digest(self):
        plan = module.build_deletion_plan(self.inventory)
        archive = archive_map(plan)
        module.validate_archive_map(plan, archive)
        canonical = next(entry for entry in archive["entries"] if entry["classification"] == "canary")
        original_ref = canonical["canonical_ref"]
        canonical["canonical_ref"] = "ghcr.io/woosungchoi/fpm-alpine:wrong"
        with self.assertRaisesRegex(module.PolicyError, "canonical reference"):
            module.validate_archive_map(plan, archive)
        canonical["canonical_ref"] = original_ref
        canonical["runtime"] = "missing"
        with self.assertRaisesRegex(module.PolicyError, "runtime"):
            module.validate_archive_map(plan, archive)
        canonical["runtime"] = "verified"
        archive["entries"].pop()
        with self.assertRaisesRegex(module.PolicyError, "archive coverage"):
            module.validate_archive_map(plan, archive)

    def test_apply_is_inventory_bound_and_idempotent_for_missing_candidates(self):
        plan = module.build_deletion_plan(self.inventory)
        archive = archive_map(plan)
        current = list(self.inventory)
        deleted = []

        def fetcher():
            return list(current)

        def deleter(name, digest, token):
            self.assertEqual(token, "session-jwt")
            matching = [row for row in current if row["name"] == name]
            self.assertEqual([row["digest"] for row in matching], [digest])
            current.remove(matching[0])
            deleted.append(name)

        result = module.apply_deletion_plan(
            plan,
            archive,
            access_token="session-jwt",
            inventory_fetcher=fetcher,
            delete_tag=deleter,
            sleeper=lambda _: None,
            attempts=2,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(set(deleted), {row["name"] for row in plan["delete"]})
        self.assertEqual({row["name"] for row in current}, set(module.KEEP_TAGS))
        result = module.apply_deletion_plan(
            plan,
            archive,
            access_token="session-jwt",
            inventory_fetcher=fetcher,
            delete_tag=deleter,
            sleeper=lambda _: None,
            attempts=2,
        )
        self.assertEqual(result["deleted"], [])
        self.assertEqual(len(result["already_absent"]), len(plan["delete"]))

    def test_apply_stops_on_partial_failure_and_preserves_result(self):
        plan = module.build_deletion_plan(self.inventory)
        archive = archive_map(plan)
        current = list(self.inventory)
        calls = 0

        def fetcher():
            return list(current)

        def deleter(name, digest, token):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("HTTP 503")
            current[:] = [row for row in current if row["name"] != name]

        with self.assertRaises(module.PruneError) as caught:
            module.apply_deletion_plan(
                plan,
                archive,
                access_token="session-jwt",
                inventory_fetcher=fetcher,
                delete_tag=deleter,
                sleeper=lambda _: None,
                attempts=2,
            )
        self.assertEqual(caught.exception.result["status"], "partial_failure")
        self.assertEqual(len(caught.exception.result["deleted"]), 1)
        self.assertEqual(len(caught.exception.result["failed"]), 1)

    def test_delete_url_quotes_tag_and_never_prints_token(self):
        opener = QueueOpener([{}])
        module.delete_tag("sha-8.5/a b", "sha256:" + "1" * 64, "session-jwt", opener=opener)
        request, _ = opener.requests[0]
        self.assertTrue(request.full_url.endswith("/tags/sha-8.5%2Fa%20b"), request.full_url)
        self.assertEqual(request.get_method(), "DELETE")
        self.assertEqual(request.get_header("Authorization"), "Bearer session-jwt")


if __name__ == "__main__":
    unittest.main()
