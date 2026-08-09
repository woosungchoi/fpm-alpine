from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "dependency-auto-publish.yml"
RECOVERY = ROOT / ".github" / "workflows" / "dependency-publish-recovery.yml"
RUNTIME = ROOT / ".github" / "workflows" / "published-runtime-smoke.yml"
CUTOVER_WORKFLOW = ROOT / ".github" / "workflows" / "legacy-cutover-lease.yml"
DOCKERFILE = ROOT / "Dockerfile"
TRANSACTION = ROOT / "scripts" / "promote-auto-canaries.sh"
PLAN_VALIDATOR = ROOT / "scripts" / "validate-auto-promotion-plan.py"
RESULT_VALIDATOR = ROOT / "scripts" / "validate-auto-transaction-result.py"


class DependencyAutoPublishTests(unittest.TestCase):
    @staticmethod
    def load(path: Path) -> tuple[str, dict]:
        text = path.read_text()
        return text, yaml.safe_load(text)

    @staticmethod
    def trigger(data: dict) -> dict:
        value = data.get("on", data.get(True))
        assert isinstance(value, dict)
        return value

    def test_only_protected_push_and_default_branch_repository_dispatch_enter(self) -> None:
        text, data = self.load(WORKFLOW)
        trigger = self.trigger(data)
        self.assertNotIn("workflow_dispatch", trigger)
        self.assertEqual(trigger["repository_dispatch"]["types"], ["fpm-ghcr-backfill"])
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertEqual(trigger["push"]["paths"], ["build/versions.json"])
        self.assertIn("repository_dispatch:fpm-ghcr-backfill", text)
        self.assertIn("git merge-base --is-ancestor", text)
        self.assertNotIn("github.event.inputs", text)

        prepare = data["jobs"]["prepare"]
        checkout = prepare["steps"][0]
        self.assertEqual(checkout["with"]["ref"], "${{ github.sha }}")
        self.assertIn("scripts/evaluate-auto-promotion.py", text)
        eligibility = next(
            step for step in prepare["steps"]
            if step["name"] == "Require an eligible dependency-only main merge"
        )
        self.assertEqual(eligibility["if"], "steps.mode.outputs.mode == 'automatic'")
        cutover = next(
            step for step in prepare["steps"]
            if step["name"] == "Require permanent legacy publisher cutover for automatic mutation"
        )
        self.assertEqual(cutover["if"], "steps.mode.outputs.mode == 'automatic'")
        self.assertEqual(
            cutover["env"]["LEGACY_PUBLISHER_DISABLED"],
            "${{ vars.LEGACY_PUBLISHER_DISABLED }}",
        )
        self.assertEqual(
            cutover["env"]["DOCKERHUB_TAG_POLICY_ENFORCED"],
            "${{ vars.DOCKERHUB_TAG_POLICY_ENFORCED }}",
        )
        source_manifest = next(
            step
            for step in prepare["steps"]
            if step["name"] == "Bind backfill source SHA to its exact release manifest"
        )
        self.assertEqual(
            source_manifest["if"], "steps.mode.outputs.mode == 'backfill-ghcr'"
        )
        self.assertIn("git show", source_manifest["run"])
        self.assertIn("validate-backfill-source-manifest.py", source_manifest["run"])
        mode = next(step for step in prepare["steps"] if step.get("id") == "mode")
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        for event, action, operation, ref, expected_ok in (
            ("push", "", "", "refs/heads/main", True),
            ("repository_dispatch", "fpm-ghcr-backfill", "backfill-ghcr", "refs/heads/main", True),
            ("workflow_dispatch", "", "backfill-ghcr", "refs/heads/main", False),
            ("repository_dispatch", "fpm-ghcr-backfill", "backfill-ghcr", "refs/heads/topic", False),
            ("repository_dispatch", "wrong", "backfill-ghcr", "refs/heads/main", False),
        ):
            with self.subTest(event=event, action=action, ref=ref), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "output"
                env = os.environ.copy()
                env.update(
                    EVENT_NAME=event,
                    EVENT_ACTION=action,
                    EVENT_REF=ref,
                    WORKFLOW_SHA=head,
                    REQUESTED_OPERATION=operation,
                    REQUESTED_SOURCE_SHA=head,
                    GITHUB_OUTPUT=str(output),
                )
                result = subprocess.run(
                    ["bash", "-c", mode["run"]], cwd=ROOT, env=env,
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(result.returncode == 0, expected_ok, result.stdout + result.stderr)

    def test_canary_is_ghcr_only_and_backfill_copies_one_exact_source(self) -> None:
        text, data = self.load(WORKFLOW)
        canary = data["jobs"]["canary"]
        dumped = yaml.safe_dump(canary, sort_keys=False)
        self.assertEqual(canary["permissions"]["packages"], "write")
        self.assertEqual(canary["permissions"]["id-token"], "write")
        self.assertNotIn("DOCKERHUB_USERNAME", dumped)
        self.assertNotIn("DOCKERHUB_TOKEN", dumped)
        self.assertIn("canary-${{ matrix.php_minor }}-${{ github.run_id }}-${{ github.run_attempt }}", text)
        self.assertIn("provenance: mode=max", dumped)
        self.assertIn("sbom: true", dumped)
        self.assertIn("verify-published-dockerhub-image.sh", dumped)
        self.assertIn("${DOCKERHUB_REPOSITORY}@${source_digest}", dumped)
        self.assertIn("dockerhub_source_digest", dumped)
        parity = next(
            step for step in canary["steps"]
            if step["name"] == "Verify backfill source and GHCR canary semantic parity"
        )
        self.assertEqual(parity["if"], "needs.prepare.outputs.mode == 'backfill-ghcr'")
        self.assertIn("verify-image-parity.py", parity["run"])
        self.assertNotIn("tags: docker.io/woosungchoi/fpm-alpine", dumped)
        self.assertNotIn(":latest", dumped)

    def test_automatic_canary_reuses_validated_build_matrix_and_exact_platform_subjects(self) -> None:
        _, data = self.load(WORKFLOW)
        canary = data["jobs"]["canary"]
        prepare = data["jobs"]["prepare"]
        matrix = next(step for step in prepare["steps"] if step.get("id") == "matrix")
        self.assertIn("validate-versions.py --matrix", matrix["run"])
        self.assertIn('row["arch"] == "amd64"', matrix["run"])

        build = next(step for step in canary["steps"] if step.get("id") == "build")
        metadata = next(step for step in canary["steps"] if step.get("id") == "metadata")
        self.assertEqual(metadata["if"], "needs.prepare.outputs.mode == 'automatic'")
        build_args = build["with"]["build-args"]
        for name in (
            "PHP_BASE_IMAGE", "IMAGICK_URL", "IMAGICK_SHA256", "REDIS_URL",
            "REDIS_SHA256", "APCU_URL", "APCU_SHA256", "OCI_SOURCE",
            "OCI_REVISION", "OCI_VERSION", "OCI_CREATED", "SOURCE_DATE_EPOCH",
        ):
            self.assertIn(f"{name}=", build_args)
        for unused in (
            "ICONV_IMPLEMENTATION", "ICONV_VERSION", "ICONV_PACKAGE",
            "ICONV_PACKAGE_VERSION", "ICONV_OWNER_PATH", "ICONV_TARGET",
        ):
            self.assertNotIn(f"{unused}=", build_args)
        self.assertIn(
            'org.opencontainers.image.licenses="GPL-2.0-only"',
            DOCKERFILE.read_text(),
        )

        verify = next(
            step for step in canary["steps"]
            if step["name"] == "Verify exact GHCR canary supply chain and runtime"
        )
        self.assertEqual(
            verify["env"]["EXPECTED_PUBLISHER_WORKFLOW"],
            "dependency-auto-publish.yml",
        )

        runtime = next(
            step for step in canary["steps"]
            if step["name"] == "Require anonymous GHCR manifest and runtime access"
        )["run"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            bin_dir = root / "bin"
            scripts.mkdir()
            bin_dir.mkdir()
            resolver = scripts / "resolve-platform-image.py"
            resolver.write_text(
                "#!/usr/bin/env python3\n"
                "import hashlib,sys\n"
                "print(sys.argv[1].rsplit('@',1)[0] + '@sha256:' + "
                "hashlib.sha256(sys.argv[2].encode()).hexdigest())\n"
            )
            resolver.chmod(0o755)
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/usr/bin/env python3\n"
                "import os,sys\n"
                "with open(os.environ['DOCKER_CALLS'],'a') as f: "
                "f.write('\\t'.join(sys.argv[1:])+'\\n')\n"
            )
            docker.chmod(0o755)
            calls = root / "docker-calls"
            env = os.environ.copy()
            env.update(
                GHCR_SUBJECT="ghcr.io/woosungchoi/fpm-alpine@sha256:" + "a" * 64,
                DOCKER_CALLS=str(calls),
                PATH=f"{bin_dir}:{env['PATH']}",
            )
            result = subprocess.run(
                ["bash", "-c", runtime], cwd=root, env=env,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            lines = calls.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(all("--entrypoint\tphp" in line for line in lines))
            self.assertTrue(all("@sha256:" in line for line in lines))
            self.assertTrue(all(("a" * 64) not in line for line in lines))

    def test_single_unattended_controller_validates_before_credentials_and_mutation(self) -> None:
        text, data = self.load(WORKFLOW)
        self.assertEqual(data["concurrency"]["group"], "fpm-production-promotion")
        self.assertFalse(data["concurrency"]["cancel-in-progress"])
        controller = data["jobs"]["promote"]
        self.assertEqual(controller["environment"], "fpm-auto-production")
        self.assertEqual(controller["permissions"]["packages"], "write")
        self.assertEqual(controller["permissions"]["id-token"], "write")
        names = [step["name"] for step in controller["steps"]]
        validation = names.index("Validate exact artifact set and capture unauthenticated baselines")
        draft_validation = names.index("Validate strict draft plan before credentials")
        first_lease = names.index("Require fresh immutable cutover lease before automatic credentials")
        dockerhub_login = names.index("Log in to Docker Hub mutation boundary")
        pin = names.index("Create no-clobber rollback pins and freeze final JSON plan")
        upload_plan = names.index("Upload frozen pre-mutation plan")
        second_lease = names.index("Revalidate fresh immutable cutover lease at mutation boundary")
        promotion = names.index("Promote exact subjects as one fail-closed transaction")
        self.assertLess(validation, draft_validation)
        self.assertLess(draft_validation, first_lease)
        self.assertLess(first_lease, dockerhub_login)
        self.assertLess(dockerhub_login, pin)
        self.assertLess(pin, upload_plan)
        self.assertLess(upload_plan, second_lease)
        self.assertLess(second_lease, promotion)
        live_cutover = names.index("Recheck legacy publisher cutover at mutation boundary")
        self.assertLess(live_cutover, first_lease)
        for lease_name in (
            "Require fresh immutable cutover lease before automatic credentials",
            "Revalidate fresh immutable cutover lease at mutation boundary",
        ):
            lease = next(step for step in controller["steps"] if step["name"] == lease_name)
            self.assertEqual(lease["if"], "needs.prepare.outputs.mode == 'automatic'")
            self.assertIn("require-fresh-cutover-lease.sh", lease["run"])
        dumped = yaml.safe_dump(controller, sort_keys=False)
        self.assertIn("canary artifact set mismatch", dumped)
        self.assertIn("promotion-plan.json", dumped)
        self.assertIn("target_dockerhub_digest", dumped)
        self.assertIn("target_ghcr_digest", dumped)
        self.assertIn("rollback-auto-dockerhub-", dumped)
        self.assertIn('test "$rollback_dockerhub_backup" = "$previous_dockerhub"', text)
        self.assertIn("rollback-auto-ghcr-", dumped)
        self.assertIn("scripts/validate-auto-promotion-plan.py", dumped)
        self.assertIn("scripts/promote-auto-canaries.sh", dumped)
        dockerhub_step = next(
            step for step in controller["steps"]
            if step["name"] == "Log in to Docker Hub mutation boundary"
        )
        self.assertEqual(dockerhub_step["if"], "needs.prepare.outputs.mode == 'automatic'")
        self.assertIn("VERIFY_DOCKERHUB_SIGNATURE", TRANSACTION.read_text())

    def test_cutover_lease_is_owner_dispatched_exact_main_and_immutable(self) -> None:
        text, data = self.load(CUTOVER_WORKFLOW)
        trigger = self.trigger(data)
        self.assertEqual(
            trigger, {"repository_dispatch": {"types": ["legacy-cutover-lease"]}}
        )
        self.assertEqual(data["permissions"], {})
        capture = data["jobs"]["capture"]
        self.assertIn("github.event.sender.id == 5674610", capture["if"])
        self.assertIn("github.event.sender.login == 'woosungchoi'", capture["if"])
        self.assertIn("github.ref == 'refs/heads/main'", capture["if"])
        rendered = yaml.safe_dump(capture, sort_keys=False)
        self.assertIn("test \"$GITHUB_SHA\" = \"$EXPECTED_SOURCE_SHA\"", text)
        self.assertIn("validate-legacy-cutover-evidence.py", rendered)
        self.assertIn("legacy-cutover-lease-${{ github.run_id }}-${{ github.run_attempt }}", rendered)
        self.assertIn("overwrite: false", rendered)

    def test_recovery_is_independent_default_branch_sha_bound_and_no_clobber(self) -> None:
        text, data = self.load(RECOVERY)
        trigger = self.trigger(data)
        self.assertEqual(trigger["repository_dispatch"]["types"], ["fpm-publish-recover"])
        self.assertNotIn("workflow_dispatch", trigger)
        self.assertEqual(data["concurrency"]["group"], "fpm-production-promotion")
        self.assertFalse(data["concurrency"]["cancel-in-progress"])
        self.assertEqual(data["jobs"]["recover"]["environment"], "fpm-auto-production")
        self.assertIn("failure\", \"cancelled\", \"timed_out", text)
        self.assertIn("later successful publisher run blocks stale recovery", text)
        self.assertIn("publisher-auto-plan-${{ needs.prepare.outputs.original_run_id }}", text)
        self.assertIn("actual_sha256", text)
        self.assertIn("--workflow-sha", text)
        self.assertIn("recovery artifact exact-set mismatch", text)
        self.assertIn("promotion-plan.sha256", text)
        self.assertIn("original-versions.json", text)
        self.assertIn("--versions-file recovery-plan/original-versions.json", text)
        self.assertIn("AUTO_PROMOTION_VERSIONS_FILE", text)
        self.assertIn(
            "AUTO_PROMOTION_VERSIONS_FILE",
            (ROOT / "scripts/verify-published-dockerhub-image.sh").read_text(),
        )
        self.assertIn("promote-auto-canaries.sh recover", text)
        self.assertNotIn("mapfile -t", text)

        steps = data["jobs"]["recover"]["steps"]
        names = [step["name"] for step in steps]
        self.assertLess(
            names.index("Verify artifact bytes and strict plan binding before credentials"),
            names.index("Log in to Docker Hub recovery boundary"),
        )
        plan = next(step for step in steps if step.get("id") == "plan")
        self.assertIn("operation=", plan["run"])
        dockerhub_login = next(
            step for step in steps
            if step["name"] == "Log in to Docker Hub recovery boundary"
        )
        self.assertEqual(dockerhub_login["if"], "steps.plan.outputs.operation == 'automatic'")

    def test_recovery_request_rejects_a_later_successful_publisher_run(self) -> None:
        _, data = self.load(RECOVERY)
        request = next(
            step for step in data["jobs"]["prepare"]["steps"]
            if step.get("id") == "request"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            gh = fake_bin / "gh"
            gh.write_text(
                "#!/usr/bin/env python3\n"
                "import os,sys\n"
                "if '--paginate' in sys.argv:\n"
                "    print(os.environ['COMPLETED_RUNS'], end='')\n"
                "else:\n"
                "    print(os.environ['ORIGINAL_RUN_JSON'])\n"
            )
            gh.chmod(0o755)
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip()
            original = json.dumps({
                "path": ".github/workflows/dependency-auto-publish.yml",
                "head_branch": "main",
                "status": "completed",
                "event": "push",
                "conclusion": "failure",
                "run_attempt": 1,
                "head_sha": head,
            })
            for completed_runs, expected_ok in (
                ("123\t.github/workflows/dependency-auto-publish.yml\tfailure\n", True),
                ("124\t.github/workflows/dependency-auto-publish.yml\tsuccess\n", False),
            ):
                with self.subTest(later_success=not expected_ok):
                    output = root / f"output-{expected_ok}"
                    env = os.environ.copy()
                    env.update(
                        PATH=f"{fake_bin}:{env['PATH']}",
                        EVENT_ACTION="fpm-publish-recover",
                        EVENT_REF="refs/heads/main",
                        ORIGINAL_RUN_ID="123",
                        ORIGINAL_RUN_ATTEMPT="1",
                        PLAN_SHA256="a" * 64,
                        GITHUB_REPOSITORY="woosungchoi/fpm-alpine",
                        GITHUB_OUTPUT=str(output),
                        GH_TOKEN="test-token",
                        ORIGINAL_RUN_JSON=original,
                        COMPLETED_RUNS=completed_runs,
                    )
                    result = subprocess.run(
                        ["bash", "-c", request["run"]],
                        cwd=ROOT,
                        env=env,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode == 0, expected_ok, result.stderr)
                    if expected_ok:
                        self.assertIn(f"original_workflow_sha={head}", output.read_text())
                    else:
                        self.assertIn("later successful publisher run", result.stderr)

    def test_transaction_and_validators_enforce_registry_specific_recovery_contract(self) -> None:
        for path in (TRANSACTION, PLAN_VALIDATOR, RESULT_VALIDATOR):
            self.assertTrue(path.is_file())
            self.assertTrue(os.access(path, os.X_OK))
        transaction = TRANSACTION.read_text()
        plan = PLAN_VALIDATOR.read_text()
        for required in (
            "recover_transaction",
            "unknown GHCR alias state",
            "no moving aliases were modified",
            "rollback_dockerhub_backup",
            "rollback_ghcr_digest",
            "verify-rollback-image.sh",
            "target_dockerhub_digest",
            "target_ghcr_digest",
        ):
            self.assertIn(required, transaction + plan)
        self.assertNotIn('previous_dockerhub" = "$previous_ghcr', transaction)
        self.assertNotIn('dockerhub_actual" = "$target_ghcr', transaction)

    def test_signer_identities_are_exact_workflow_and_main_pairs(self) -> None:
        for workflow in (WORKFLOW, RECOVERY, ROOT / ".github/workflows/publish.yml", RUNTIME):
            self.assertIn("cosign-release: v3.1.2", workflow.read_text())
        verifier = (ROOT / "scripts" / "verify-published-image.sh").read_text()
        canary = (ROOT / "scripts" / "verify-canary-image.sh").read_text()
        rollback = (ROOT / "scripts" / "rollback-moving-aliases.sh").read_text()
        for workflow in ("publish\\.yml", "dependency-auto-publish\\.yml"):
            self.assertIn(workflow, verifier)
        self.assertIn("dependency-auto-publish\\.yml", canary)
        self.assertIn("dependency-auto-publish\\.yml", rollback)
        self.assertIn("@refs/heads/${EXPECTED_SIGNING_REF}$", canary)
        self.assertIn("@refs/heads/main$", rollback)

    def test_runtime_observer_binds_dependency_results_not_moving_tags(self) -> None:
        text, data = self.load(RUNTIME)
        self.assertEqual(data["permissions"]["actions"], "read")
        verify = data["jobs"]["verify"]
        dumped = yaml.safe_dump(verify, sort_keys=False)
        self.assertIn("publisher-auto-production-${{ github.event.workflow_run.id }}", dumped)
        self.assertIn("validate-auto-transaction-result.py", dumped)
        self.assertIn("promotion-plan.json", RESULT_VALIDATOR.read_text())
        self.assertIn("promotion-plan.sha256", RESULT_VALIDATOR.read_text())
        self.assertIn("PINNED_DOCKERHUB_DIGEST", dumped)
        self.assertIn("PINNED_GHCR_DIGEST", dumped)
        self.assertIn("UPSTREAM_OPERATION", dumped)
        self.assertIn("VERIFY_DOCKERHUB_SIGNATURE", dumped)
        self.assertIn("bind_upstream_evidence=true", text)
        self.assertIn("scan-image.sh", dumped)

    def test_required_ci_runs_new_transaction_and_workflow_contracts(self) -> None:
        _, data = self.load(ROOT / ".github" / "workflows" / "smoke-test.yml")
        step = next(
            step for step in data["jobs"]["dependency-safety"]["steps"]
            if step["name"] == "Run policy and mutation tests"
        )
        self.assertIn("python3 tests/test_dependency_auto_publish.py", step["run"])
        self.assertIn("python3 tests/test_auto_promotion_transaction.py", step["run"])
        for test in (
            "test_backfill_source_manifest.py",
            "test_fresh_cutover_lease.py",
            "test_require_fresh_cutover_lease.py",
            "test_refresh_cutover_lease.py",
            "test_published_operation.py",
        ):
            self.assertIn(f"python3 tests/{test}", step["run"])


if __name__ == "__main__":
    unittest.main()
