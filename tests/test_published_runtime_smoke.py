"""Regression tests for Docker-Hub-only published runtime verification."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "published-runtime-smoke.yml"
DOCKERHUB_VERIFIER = ROOT / "scripts" / "verify-published-dockerhub-image.sh"
STRICT_VERIFIER = ROOT / "scripts" / "verify-published-image.sh"
OPERATION_RESOLVER = ROOT / "scripts" / "resolve-published-operation.sh"


class PublishedRuntimeSmokeTests(unittest.TestCase):
    def load_workflow(self) -> tuple[str, dict]:
        text = WORKFLOW.read_text()
        data = yaml.safe_load(text)
        return text, data

    @staticmethod
    def trigger(data: dict) -> dict:
        value = data.get("on", data.get(True))
        assert isinstance(value, dict)
        return value

    @staticmethod
    def write_executable(path: Path, content: str) -> None:
        path.write_text(content)
        path.chmod(0o755)

    def verifier_fixture(
        self, root: Path, *, resolver_succeeds: bool = True
    ) -> dict[str, Path | str]:
        scripts = root / "scripts"
        fake_bin = root / "bin"
        build = root / "build"
        scripts.mkdir()
        fake_bin.mkdir()
        build.mkdir()

        verifier = scripts / DOCKERHUB_VERIFIER.name
        shutil.copy2(DOCKERHUB_VERIFIER, verifier)

        digest = "sha256:" + "a" * 64
        platform_digests = {
            "linux/amd64": "sha256:" + "b" * 64,
            "linux/arm64": "sha256:" + "c" * 64,
        }
        logs = {
            name: root / f"{name}.log"
            for name in ("resolve", "report", "docker", "platform", "smoke")
        }

        resolver_result = f"printf '%s\\n' '{digest}'" if resolver_succeeds else "exit 1"
        self.write_executable(
            scripts / "resolve-image-digest.sh",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$1\" >> \"$RESOLVE_LOG\"\n"
            f"{resolver_result}\n",
        )
        self.write_executable(
            scripts / "report-manifest.sh",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$1\" >> \"$REPORT_LOG\"\n",
        )
        self.write_executable(
            scripts / "verify-provenance.py",
            "#!/usr/bin/env bash\nexit 0\n",
        )
        self.write_executable(
            scripts / "resolve-platform-image.py",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s|%s\\n' \"$1\" \"$2\" >> \"$PLATFORM_LOG\"\n"
            "case \"$2\" in\n"
            f"  linux/amd64) printf '%s\\n' 'docker.io/woosungchoi/fpm-alpine@{platform_digests['linux/amd64']}' ;;\n"
            f"  linux/arm64) printf '%s\\n' 'docker.io/woosungchoi/fpm-alpine@{platform_digests['linux/arm64']}' ;;\n"
            "  *) exit 64 ;;\n"
            "esac\n",
        )
        self.write_executable(
            scripts / "smoke-test-image.sh",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$1\" >> \"$SMOKE_LOG\"\n"
            "mkdir -p \"$(dirname \"$SMOKE_REPORT_MD\")\"\n"
            "printf '%s\\n' 'Smoke test passed' > \"$SMOKE_REPORT_MD\"\n",
        )

        labels = {
            "org.opencontainers.image.source": "https://github.com/woosungchoi/fpm-alpine",
            "org.opencontainers.image.revision": "d" * 40,
            "org.opencontainers.image.version": "8.5.8",
            "org.opencontainers.image.licenses": "GPL-2.0-only",
            "org.opencontainers.image.created": "2026-08-09T18:00:00Z",
        }
        index_path = root / "index.json"
        image_path = root / "image.json"
        provenance_path = root / "provenance.json"
        sbom_path = root / "sbom.json"
        index_path.write_text(
            json.dumps(
                {
                    "manifests": [
                        {
                            "digest": platform_digest,
                            "platform": {
                                "os": platform.split("/")[0],
                                "architecture": platform.split("/")[1],
                            },
                        }
                        for platform, platform_digest in platform_digests.items()
                    ]
                }
            )
        )
        image_path.write_text(
            json.dumps(
                {
                    platform: {"config": {"Labels": labels}}
                    for platform in platform_digests
                }
            )
        )
        provenance_path.write_text("{}\n")
        sbom_path.write_text(
            json.dumps({platform: {"SPDX": {}} for platform in platform_digests})
        )
        self.write_executable(
            fake_bin / "docker",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
            "case \"$*\" in\n"
            "  *'--raw'*) cat \"$INDEX_JSON\" ;;\n"
            "  *'json .Image'*) cat \"$IMAGE_JSON\" ;;\n"
            "  *'json .Provenance'*) cat \"$PROVENANCE_JSON\" ;;\n"
            "  *'json .SBOM'*) cat \"$SBOM_JSON\" ;;\n"
            "  *) exit 98 ;;\n"
            "esac\n",
        )
        (build / "versions.json").write_text(
            json.dumps(
                {
                    "dependencies": {
                        "imagick": {"version": "3.8.0"},
                        "redis": {"version": "6.2.0"},
                        "apcu": {"version": "5.1.27"},
                    },
                    "runtimeContracts": {
                        "libiconv": {
                            "implementation": "gnu-libiconv",
                            "version": "1.18",
                            "package": "gnu-libiconv-libs",
                            "packageVersion": "1.18-r0",
                            "ownerPath": "/usr/lib/libiconv.so.2",
                            "target": "/usr/local/lib/libiconv.so.2",
                        }
                    },
                }
            )
        )
        return {
            "verifier": verifier,
            "fake_bin": fake_bin,
            "digest": digest,
            "index": index_path,
            "image": image_path,
            "provenance": provenance_path,
            "sbom": sbom_path,
            **logs,
        }

    @staticmethod
    def verifier_environment(fixture: dict[str, Path | str]) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            PATH=f"{fixture['fake_bin']}:{env['PATH']}",
            RESOLVE_LOG=str(fixture["resolve"]),
            REPORT_LOG=str(fixture["report"]),
            DOCKER_LOG=str(fixture["docker"]),
            PLATFORM_LOG=str(fixture["platform"]),
            SMOKE_LOG=str(fixture["smoke"]),
            INDEX_JSON=str(fixture["index"]),
            IMAGE_JSON=str(fixture["image"]),
            PROVENANCE_JSON=str(fixture["provenance"]),
            SBOM_JSON=str(fixture["sbom"]),
            INSPECT_ATTEMPTS="1",
            INSPECT_RETRY_DELAY_SECONDS="0",
        )
        return env

    def test_verification_scope_matches_the_publisher_that_triggered_it(self) -> None:
        _, data = self.load_workflow()
        trigger = self.trigger(data)
        self.assertEqual(
            trigger["workflow_run"]["workflows"],
            ["publish", "dependency-auto-publish"],
        )

        prepare = data["jobs"]["prepare"]
        self.assertEqual(
            prepare["outputs"]["verification_mode"],
            "${{ steps.mode.outputs.verification_mode }}",
        )
        self.assertEqual(
            prepare["outputs"]["signing_workflow"],
            "${{ steps.mode.outputs.signing_workflow }}",
        )
        mode = next(step for step in prepare["steps"] if step.get("id") == "mode")
        self.assertEqual(mode["env"]["EVENT_NAME"], "${{ github.event_name }}")
        self.assertEqual(
            mode["env"]["UPSTREAM_WORKFLOW_PATH"],
            "${{ github.event.workflow_run.path }}",
        )

        known_cases = (
            ("schedule", "", "multi-registry", "any-authorized", "false"),
            ("workflow_dispatch", "", "multi-registry", "any-authorized", "false"),
            (
                "workflow_run",
                ".github/workflows/dependency-auto-publish.yml",
                "multi-registry",
                "dependency-auto-publish.yml",
                "true",
            ),
            (
                "workflow_run",
                ".github/workflows/publish.yml",
                "multi-registry",
                "publish.yml",
                "false",
            ),
        )
        for event_name, upstream_path, expected_mode, expected_signer, expected_binding in known_cases:
            with (
                self.subTest(event_name=event_name, upstream_path=upstream_path),
                tempfile.TemporaryDirectory() as temporary,
            ):
                    output_path = Path(temporary) / "output"
                    env = os.environ.copy()
                    env.update(
                        EVENT_NAME=event_name,
                        UPSTREAM_WORKFLOW_PATH=upstream_path,
                        UPSTREAM_HEAD_SHA="a" * 40 if event_name == "workflow_run" else "",
                        GITHUB_OUTPUT=str(output_path),
                    )
                    completed = subprocess.run(
                        ["bash", "-c", mode["run"]],
                        cwd=ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stdout)
                    self.assertEqual(
                        output_path.read_text(),
                        f"verification_mode={expected_mode}\n"
                        f"signing_workflow={expected_signer}\n"
                        f"bind_upstream_evidence={expected_binding}\n",
                    )

        for event_name, upstream_path in (
            ("workflow_run", ""),
            ("workflow_run", ".github/workflows/renamed-publisher.yml"),
            ("push", ""),
        ):
            with (
                self.subTest(rejected_event=event_name, upstream_path=upstream_path),
                tempfile.TemporaryDirectory() as temporary,
            ):
                    output_path = Path(temporary) / "output"
                    env = os.environ.copy()
                    env.update(
                        EVENT_NAME=event_name,
                        UPSTREAM_WORKFLOW_PATH=upstream_path,
                        UPSTREAM_HEAD_SHA="a" * 40 if event_name == "workflow_run" else "",
                        GITHUB_OUTPUT=str(output_path),
                    )
                    completed = subprocess.run(
                        ["bash", "-c", mode["run"]],
                        cwd=ROOT,
                        env=env,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                    self.assertNotEqual(completed.returncode, 0, completed.stdout)
                    self.assertIn("unsupported", completed.stdout)
                    self.assertFalse(output_path.exists())

        prepare_checkout = next(
            step for step in prepare["steps"] if step["name"] == "Checkout main"
        )
        expected_checkout_ref = (
            "${{ github.event_name == 'workflow_run' && "
            "github.event.workflow_run.head_sha || 'main' }}"
        )
        self.assertEqual(prepare_checkout["with"]["ref"], expected_checkout_ref)
        matrix = next(step for step in prepare["steps"] if step.get("id") == "matrix")
        self.assertIn('{"active", "security-only"}', matrix["run"])

        steps = data["jobs"]["verify"]["steps"]
        verify_checkout = next(
            step
            for step in steps
            if step["name"] == "Checkout main with protected history"
        )
        self.assertEqual(verify_checkout["with"]["ref"], expected_checkout_ref)
        source = next(
            step for step in steps if step["name"] == "Resolve published source revision"
        )
        self.assertIn("dockerhub_digest=", source["run"])
        self.assertIn("dockerhub_subject=", source["run"])
        self.assertNotIn("resolve-publisher-signing-ref.sh", source["run"])

        cosign = next(step for step in steps if step["name"] == "Install Cosign")
        prerequisites = next(
            step
            for step in steps
            if step["name"] == "Resolve strict multi-registry prerequisites"
        )
        multi = next(
            step
            for step in steps
            if step["name"] == "Verify exact multi-registry runtime and supply chain"
        )
        for step in (cosign, prerequisites, multi):
            self.assertEqual(
                step["if"],
                "needs.prepare.outputs.verification_mode == 'multi-registry'",
            )
        self.assertIn("resolve-publisher-signing-ref.sh", prerequisites["run"])
        self.assertIn("resolve-published-operation.sh", prerequisites["run"])
        self.assertEqual(
            prerequisites["env"]["EXPECTED_SIGNING_WORKFLOW"],
            "${{ needs.prepare.outputs.signing_workflow }}",
        )
        self.assertIn("signing_ref=main", prerequisites["run"])
        self.assertEqual(
            multi["env"]["DOCKERHUB_REF"],
            "${{ steps.source.outputs.dockerhub_subject }}",
        )
        self.assertEqual(
            multi["env"]["GHCR_REF"],
            "${{ steps.multi.outputs.ghcr_subject }}",
        )
        self.assertEqual(
            multi["env"]["SIGNING_REF"],
            "${{ steps.multi.outputs.signing_ref }}",
        )
        self.assertEqual(
            multi["env"]["EXPECTED_SIGNING_WORKFLOW"],
            "${{ steps.multi.outputs.signing_workflow }}",
        )
        self.assertEqual(
            multi["env"]["VERIFY_DOCKERHUB_SIGNATURE"],
            "${{ steps.multi.outputs.verify_dockerhub_signature }}",
        )
        self.assertIn("scripts/verify-published-image.sh", multi["run"])
        self.assertNotIn("${{", multi["run"])
        self.assertNotIn(
            "Verify exact Docker Hub runtime and supply chain",
            [step.get("name") for step in steps],
        )

        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "output"
            digest = "sha256:" + "f" * 64
            env = os.environ.copy()
            env.update(
                GHCR_REF="ghcr.io/woosungchoi/fpm-alpine:8.2",
                EXPECTED_REVISION="a" * 40,
                PINNED_GHCR_DIGEST=digest,
                EXPECTED_SIGNING_WORKFLOW="dependency-auto-publish.yml",
                UPSTREAM_OPERATION="backfill-ghcr",
                GITHUB_OUTPUT=str(output_path),
            )
            completed = subprocess.run(
                ["bash", "-c", prerequisites["run"]],
                cwd=temporary,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("signing_ref=main\n", output_path.read_text())
            self.assertIn("signing_workflow=dependency-auto-publish.yml\n", output_path.read_text())
            self.assertIn("verify_dockerhub_signature=0\n", output_path.read_text())

    def test_dependency_signatures_carry_a_signed_operation_marker(self) -> None:
        auto = (ROOT / ".github/workflows/dependency-auto-publish.yml").read_text()
        transaction = (ROOT / "scripts/promote-auto-canaries.sh").read_text()
        self.assertIn('"fpm.operation=$MODE"', auto)
        self.assertIn('"fpm.operation=$operation"', transaction)
        self.assertTrue(OPERATION_RESOLVER.is_file())
        resolver = OPERATION_RESOLVER.read_text()
        self.assertIn("cosign verify", resolver)
        self.assertIn("fpm.operation=backfill-ghcr", resolver)
        self.assertIn("fpm.operation=automatic", resolver)
        self.assertIn("exactly one signed publication operation", resolver)

    def test_source_inspection_is_bounded_and_retried(self) -> None:
        _, data = self.load_workflow()
        source = next(
            step
            for step in data["jobs"]["verify"]["steps"]
            if step["name"] == "Resolve published source revision"
        )
        self.assertIn("SOURCE_INSPECT_ATTEMPTS=5", source["run"])
        self.assertIn("source inspect attempt", source["run"])
        self.assertIn("source inspection failed after", source["run"])

    def test_dockerhub_verifier_captures_one_digest_before_all_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.verifier_fixture(root)
            completed = subprocess.run(
                [
                    str(fixture["verifier"]),
                    "docker.io/woosungchoi/fpm-alpine:8.5",
                    "d" * 40,
                    "8.5.8",
                    str(root / "reports"),
                ],
                cwd=root,
                env=self.verifier_environment(fixture),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)

            digest = str(fixture["digest"])
            subject = f"docker.io/woosungchoi/fpm-alpine@{digest}"
            self.assertEqual(
                Path(fixture["resolve"]).read_text().splitlines(),
                ["docker.io/woosungchoi/fpm-alpine:8.5"],
            )
            self.assertEqual(Path(fixture["report"]).read_text().splitlines(), [subject])
            docker_calls = Path(fixture["docker"]).read_text()
            self.assertNotIn("fpm-alpine:8.5", docker_calls)
            self.assertTrue(
                all(
                    line.startswith(subject + "|")
                    for line in Path(fixture["platform"]).read_text().splitlines()
                )
            )
            self.assertEqual(len(Path(fixture["smoke"]).read_text().splitlines()), 2)

    def test_digest_resolution_exhaustion_prevents_manifest_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.verifier_fixture(root, resolver_succeeds=False)
            env = self.verifier_environment(fixture)
            env["INSPECT_ATTEMPTS"] = "2"
            completed = subprocess.run(
                [
                    str(fixture["verifier"]),
                    "docker.io/woosungchoi/fpm-alpine:8.5",
                    "d" * 40,
                    "8.5.8",
                    str(root / "reports"),
                ],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertEqual(len(Path(fixture["resolve"]).read_text().splitlines()), 2)
            self.assertFalse(Path(fixture["report"]).exists())

    def test_scan_consumes_the_source_steps_captured_digest(self) -> None:
        _, data = self.load_workflow()
        scan = next(
            step
            for step in data["jobs"]["verify"]["steps"]
            if step["name"] == "Scan active exact subject for fixable vulnerabilities"
        )
        self.assertEqual(
            scan["env"]["DOCKERHUB_DIGEST"],
            "${{ steps.source.outputs.dockerhub_digest }}",
        )
        self.assertNotIn("resolve-image-digest.sh", scan["run"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            scripts.mkdir()
            scan_log = root / "scan.log"
            self.write_executable(
                scripts / "scan-image.sh",
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf '%s|%s|%s|%s\\n' \"$1\" \"$2\" \"$3\" \"$4\" >> \"$SCAN_LOG\"\n",
            )
            digest = "sha256:" + "e" * 64
            env = os.environ.copy()
            env.update(
                DOCKERHUB_DIGEST=digest,
                REPORT_DIR="reports/8.5",
                SCAN_LOG=str(scan_log),
            )
            completed = subprocess.run(
                ["bash", "-c", scan["run"]],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout)
            calls = [line.split("|") for line in scan_log.read_text().splitlines()]
            self.assertEqual(len(calls), 2)
            self.assertEqual({call[1] for call in calls}, {digest})
            self.assertEqual({call[3] for call in calls}, {"linux/amd64", "linux/arm64"})

    def test_focused_suite_is_part_of_the_required_ci_lane(self) -> None:
        smoke_workflow = yaml.safe_load(
            (ROOT / ".github/workflows/smoke-test.yml").read_text()
        )
        policy_step = next(
            step
            for step in smoke_workflow["jobs"]["dependency-safety"]["steps"]
            if step["name"] == "Run policy and mutation tests"
        )
        self.assertIn("python3 tests/test_published_runtime_smoke.py", policy_step["run"])

    def test_dockerhub_verifier_is_isolated_from_ghcr_and_cosign(self) -> None:
        self.assertTrue(DOCKERHUB_VERIFIER.is_file())
        self.assertTrue(os.access(DOCKERHUB_VERIFIER, os.X_OK))
        text = DOCKERHUB_VERIFIER.read_text()
        for required in (
            "resolve-image-digest.sh",
            "report-manifest.sh",
            "verify-provenance.py",
            "resolve-platform-image.py",
            "smoke-test-image.sh",
            "org.opencontainers.image.revision",
            "linux/amd64",
            "linux/arm64",
            'INSPECT_ATTEMPTS="${INSPECT_ATTEMPTS:-5}"',
            "exact Docker Hub subject",
        ):
            self.assertIn(required, text)
        self.assertNotIn("ghcr.io", text.lower())
        self.assertNotIn("cosign", text.lower())
        for verifier in (DOCKERHUB_VERIFIER, STRICT_VERIFIER):
            verifier_text = verifier.read_text()
            self.assertLess(
                verifier_text.index("dockerhub_digest="),
                verifier_text.index("report-manifest.sh"),
            )
            self.assertIn('report-manifest.sh "$dockerhub_subject"', verifier_text)
            self.assertNotIn("mapfile -t runtime_values < <(", verifier_text)
            self.assertIn("runtime_values_output", verifier_text)

    def test_dockerhub_verifier_rejects_an_invalid_ref_before_network_use(self) -> None:
        completed = subprocess.run(
            [
                str(DOCKERHUB_VERIFIER),
                "ghcr.io/woosungchoi/fpm-alpine:8.5",
                "a" * 40,
                "8.5.8",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(completed.returncode, 64, completed.stdout)
        self.assertIn("usage:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
