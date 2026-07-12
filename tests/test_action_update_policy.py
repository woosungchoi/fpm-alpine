#!/usr/bin/env python3
"""Tests for strict GitHub Action pin-only update verification."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts/verify-action-update.py"
    spec = importlib.util.spec_from_file_location("verify_action_update", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ActionUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def verify(self, diff: str, resolver=None) -> list[str]:
        resolver = resolver or (lambda owner, repo, tag: "b" * 40)
        return self.module.verify_diff(diff, resolver)

    def test_allowlisted_full_sha_release_update_passes(self) -> None:
        diff = """diff --git a/.github/workflows/x.yml b/.github/workflows/x.yml
--- a/.github/workflows/x.yml
+++ b/.github/workflows/x.yml
@@ -1 +1 @@
-      uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v7.0.0
+      uses: actions/checkout@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v7.1.0
"""
        self.assertEqual(self.verify(diff), [])

    def test_non_allowlisted_owner_fails(self) -> None:
        diff = """diff --git a/.github/workflows/x.yml b/.github/workflows/x.yml
--- a/.github/workflows/x.yml
+++ b/.github/workflows/x.yml
@@ -1 +1 @@
-      uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v7.0.0
+      uses: evil/example@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v1.0.0
"""
        self.assertTrue(self.verify(diff))

    def test_floating_ref_fails(self) -> None:
        diff = """diff --git a/.github/workflows/x.yml b/.github/workflows/x.yml
--- a/.github/workflows/x.yml
+++ b/.github/workflows/x.yml
@@ -1 +1 @@
-      uses: actions/checkout@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v7.0.0
+      uses: actions/checkout@v7 # v7
"""
        self.assertTrue(self.verify(diff))

    def test_trigger_or_permission_change_fails(self) -> None:
        for line in (
            "+  pull_request_target:",
            "+  contents: write",
            "+        run: curl example.invalid",
        ):
            with self.subTest(line=line):
                diff = f"""diff --git a/.github/workflows/x.yml b/.github/workflows/x.yml
--- a/.github/workflows/x.yml
+++ b/.github/workflows/x.yml
@@ -1 +1 @@
-{line[1:]} old
{line}
"""
                self.assertTrue(self.verify(diff))

    def test_release_tag_must_resolve_to_new_sha(self) -> None:
        diff = """diff --git a/.github/workflows/x.yml b/.github/workflows/x.yml
--- a/.github/workflows/x.yml
+++ b/.github/workflows/x.yml
@@ -1 +1 @@
-      uses: docker/login-action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v4.3.0
+      uses: docker/login-action@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v4.4.0
"""
        self.assertTrue(self.verify(diff, lambda owner, repo, tag: "c" * 40))

    def test_non_workflow_file_fails(self) -> None:
        diff = """diff --git a/Dockerfile b/Dockerfile
--- a/Dockerfile
+++ b/Dockerfile
@@ -1 +1 @@
-FROM old
+FROM new
"""
        self.assertTrue(self.verify(diff))

    def test_empty_diff_is_not_an_action_update(self) -> None:
        self.assertTrue(self.verify(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
