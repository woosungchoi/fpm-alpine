#!/usr/bin/env python3
"""Tests for package/runtime contract drift comparison."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts/compare-image-contract.py"
    spec = importlib.util.spec_from_file_location("compare_image_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = {
    "schemaVersion": 2,
    "platform": "linux/amd64",
    "phpVersion": "8.5.8",
    "packages": ["a", "b"],
    "packageEvidence": [{"name": "a", "version": "1-r0", "architecture": "x86_64"}, {"name": "b", "version": "2-r0", "architecture": "noarch"}],
    "modules": ["Core", "imagick", "redis"],
    "iconv": {"implementation": "libiconv", "version": "1.18"},
    "fpmConfigValid": True,
}


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_patch_change_with_same_contract_passes(self) -> None:
        candidate = {**BASE, "phpVersion": "8.5.9"}
        self.assertEqual(self.module.compare(BASE, candidate, "8.5"), [])

    def test_package_add_remove_fails(self) -> None:
        candidate = {**BASE, "packages": ["a", "c"], "packageEvidence": [BASE["packageEvidence"][0], {"name": "c", "version": "2-r0", "architecture": "noarch"}]}
        errors = self.module.compare(BASE, candidate, "8.5")
        self.assertIn("package set drift", " ".join(errors))

    def test_same_name_version_drift_fails(self):
        candidate = {**BASE, "packageEvidence": [dict(row) for row in BASE["packageEvidence"]]}
        candidate["packageEvidence"][0]["version"] = "1-r1"
        self.assertIn("package version/architecture drift", " ".join(self.module.compare(BASE, candidate, "8.5")))

    def test_reviewed_transition_is_exact_and_architecture_bound(self):
        candidate = {**BASE, "packageEvidence": [dict(row) for row in BASE["packageEvidence"]]}
        candidate["packageEvidence"][0]["version"] = "1-r1"
        approved = (("a", "1-r0", "1-r1", "x86_64"),)
        self.assertEqual(self.module.compare(BASE, candidate, "8.5", approved), [])
        self.assertTrue(self.module.compare(BASE, candidate, "8.5", (("a", "1-r0", "1-r1", "aarch64"),)))
        candidate["packageEvidence"][0]["version"] = "1-r2"
        self.assertTrue(self.module.compare(BASE, candidate, "8.5", approved))
        candidate["packageEvidence"][0]["version"] = "0-r0"
        self.assertTrue(self.module.compare(BASE, candidate, "8.5", approved))

    def test_reviewed_policy_rejects_wildcards_and_duplicates(self):
        import json, tempfile
        path = ROOT / "build/approved-apk-transitions.json"
        policy = json.loads(path.read_text())
        expected_count = sum(len(row["architectures"]) for row in policy["transitions"])
        self.assertGreater(expected_count, 0)
        self.assertEqual(len(self.module.load_approved_transitions(path)), expected_count)
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "policy.json"
            policy["transitions"][0]["to"] = "*"
            target.write_text(json.dumps(policy))
            with self.assertRaises(ValueError): self.module.load_approved_transitions(target)
            policy = json.loads(path.read_text())
            policy["transitions"].append(policy["transitions"][0])
            target.write_text(json.dumps(policy))
            with self.assertRaises(ValueError): self.module.load_approved_transitions(target)

    def alpine_pair(self):
        import copy
        base = copy.deepcopy(BASE)
        base['packages'] = ['alpine-release', 'zlib']
        base['packageEvidence'] = [
            {'name': 'alpine-release', 'version': '3.24.3-r0', 'architecture': 'x86_64'},
            {'name': 'zlib', 'version': '1.3.2-r1', 'architecture': 'x86_64'}]
        candidate = copy.deepcopy(base)
        candidate['packageEvidence'][0]['version'] = '3.24.4-r0'
        candidate['packageEvidence'][1]['version'] = '1.4-r0'
        return base, candidate

    def test_same_alpine_branch_forward_updates_pass(self):
        base, candidate = self.alpine_pair()
        self.assertEqual(self.module.compare(base, candidate, '8.5', version_order=lambda a,b: '<'), [])

    def test_branch_change_downgrade_and_unknown_release_fail(self):
        base, candidate = self.alpine_pair()
        self.assertTrue(self.module.compare(base, candidate, '8.5', version_order=lambda a,b: '>'))
        candidate['packageEvidence'][0]['version'] = '3.25.0-r0'
        self.assertIn('same stable Alpine branch', ' '.join(self.module.compare(base,candidate,'8.5',version_order=lambda a,b:'<')))
        self.assertTrue(self.module.compare(BASE, BASE, '8.5',version_order=lambda a,b:'<'))

    def test_forward_policy_preserves_runtime_and_package_set_guards(self):
        base, candidate = self.alpine_pair()
        for key, value in [('modules', ['Core']), ('fpmConfigValid', False), ('phpVersion','8.5.7'), ('iconv',{'implementation':'musl','version':'1.2.5'})]:
            self.assertTrue(self.module.compare(base, candidate|{key:value},'8.5',version_order=lambda a,b:'<'))
        candidate['packageEvidence'][1]['architecture'] = 'noarch'
        self.assertTrue(self.module.compare(base,candidate,'8.5',version_order=lambda a,b:'<'))

    def test_native_apk_comparison_uses_immutable_image_offline(self):
        from unittest.mock import patch
        with patch.object(self.module.subprocess,'check_output',side_effect=['sha256:'+'a'*64, '<']) as command:
            order = self.module.apk_version_order('candidate:local','linux/amd64')
            self.assertEqual(order('1.9-r0','1.10-r0'),'<')
            argv = command.call_args.args[0]
            self.assertIn('--network=none',argv)
            self.assertIn('sha256:'+'a'*64,argv)
            self.assertEqual(argv[-4:],['version','-t','1.9-r0','1.10-r0'])
        with patch.object(self.module.subprocess,'check_output',side_effect=['sha256:'+'a'*64, 'maybe']):
            with self.assertRaises(ValueError):self.module.apk_version_order('candidate:local','linux/amd64')('1-r0','2-r0')

    def test_wrong_apk_architecture_fails(self):
        candidate = {**BASE, "packageEvidence": [dict(row) for row in BASE["packageEvidence"]]}
        candidate["packageEvidence"][0]["architecture"] = "aarch64"
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))

    def test_module_drift_fails(self) -> None:
        candidate = {**BASE, "modules": ["Core", "imagick"]}
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))

    def test_wrong_minor_fails(self) -> None:
        candidate = {**BASE, "phpVersion": "8.6.0"}
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))

    def test_platform_mismatch_fails(self) -> None:
        candidate = {**BASE, "platform": "linux/arm64"}
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))

    def test_boolean_schema_is_rejected(self) -> None:
        candidate = {**BASE, "schemaVersion": True}
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))

    def test_invalid_fpm_contract_fails(self) -> None:
        candidate = {**BASE, "fpmConfigValid": False}
        self.assertTrue(self.module.compare(BASE, candidate, "8.5"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
