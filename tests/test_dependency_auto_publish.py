from __future__ import annotations

import hashlib
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
        self.assertEqual(
            trigger["repository_dispatch"]["types"],
            ["fpm-ghcr-backfill", "fpm-dependency-publish-replay"],
        )
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertEqual(trigger["push"]["paths"], ["build/versions.json"])
        self.assertIn("repository_dispatch:fpm-ghcr-backfill", text)
        self.assertIn("repository_dispatch:fpm-dependency-publish-replay", text)
        self.assertIn("automatic-replay", text)
        self.assertIn('git rev-parse "${source_sha}:build/versions.json"', text)
        self.assertIn('git rev-parse "${WORKFLOW_SHA}:build/versions.json"', text)
        self.assertIn("git merge-base --is-ancestor", text)
        self.assertNotIn("github.event.inputs", text)

        prepare = data["jobs"]["prepare"]
        checkout = next(
            step for step in prepare["steps"]
            if step["name"] == "Checkout trusted default-branch revision"
        )
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
        parent = subprocess.check_output(
            ["git", "rev-parse", "HEAD^"], cwd=ROOT, text=True
        ).strip()
        for event, action, operation, ref, expected_ok in (
            ("push", "", "", "refs/heads/main", True),
            ("repository_dispatch", "fpm-ghcr-backfill", "backfill-ghcr", "refs/heads/main", True),
            (
                "repository_dispatch",
                "fpm-dependency-publish-replay",
                "automatic-replay",
                "refs/heads/main",
                True,
            ),
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
                    EVENT_BEFORE=parent,
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

        current_versions = subprocess.check_output(
            ["git", "rev-parse", "HEAD:build/versions.json"], cwd=ROOT, text=True
        ).strip()
        history = subprocess.check_output(
            ["git", "rev-list", "HEAD", "--", "build/versions.json"],
            cwd=ROOT,
            text=True,
        ).splitlines()
        stale_source = next(
            candidate
            for candidate in history
            if subprocess.check_output(
                ["git", "rev-parse", f"{candidate}:build/versions.json"],
                cwd=ROOT,
                text=True,
            ).strip()
            != current_versions
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            env = os.environ.copy()
            env.update(
                EVENT_NAME="repository_dispatch",
                EVENT_ACTION="fpm-dependency-publish-replay",
                EVENT_BEFORE="",
                EVENT_REF="refs/heads/main",
                WORKFLOW_SHA=head,
                REQUESTED_OPERATION="automatic-replay",
                REQUESTED_SOURCE_SHA=stale_source,
                GITHUB_OUTPUT=str(output),
            )
            result = subprocess.run(
                ["bash", "-c", mode["run"]],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

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
        source_checkout = next(
            step
            for step in canary["steps"]
            if step["name"] == "Checkout exact image source revision"
        )
        self.assertEqual(source_checkout["if"], "needs.prepare.outputs.mode == 'automatic'")
        self.assertEqual(source_checkout["with"]["ref"], "${{ needs.prepare.outputs.source_sha }}")
        self.assertEqual(source_checkout["with"]["path"], "image-source")
        self.assertEqual(build["with"]["context"], "image-source")
        self.assertEqual(build["with"]["file"], "image-source/Dockerfile")
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
        _text, data = self.load(WORKFLOW)
        self.assertEqual(data["concurrency"]["group"], "fpm-production-promotion")
        self.assertFalse(data["concurrency"]["cancel-in-progress"])
        controller = data["jobs"]["promote"]
        self.assertEqual(controller["environment"], "fpm-auto-production")
        self.assertEqual(controller["permissions"]["packages"], "write")
        self.assertEqual(controller["permissions"]["id-token"], "write")
        self.assertEqual(controller["permissions"]["contents"], "write")
        names = [step["name"] for step in controller["steps"]]
        expected_order = [
            "Validate exact artifact set and capture unauthenticated baselines",
            "Validate strict draft plan before credentials",
            "Freeze deterministic final plan before any registry write",
            "Upload frozen plan before the first registry write",
            "Verify permanent Docker Hub cutover before automatic credentials",
            "Acquire durable single-transaction writer fence",
            "Log in to Docker Hub mutation boundary",
            "Materialize frozen immutable subjects without moving aliases",
            "Revalidate permanent Docker Hub cutover at mutation boundary",
            "Promote exact subjects as one fail-closed transaction",
            "Commit exact verified transaction receipt",
            "Upload production transaction evidence",
            "Close durable writer fence after committed receipt and evidence",
        ]
        indices = [names.index(name) for name in expected_order]
        self.assertEqual(indices, sorted(indices))
        for cutover_name in (
            "Verify permanent Docker Hub cutover before automatic credentials",
            "Revalidate permanent Docker Hub cutover at mutation boundary",
        ):
            cutover = next(
                step for step in controller["steps"] if step["name"] == cutover_name
            )
            self.assertEqual(cutover["if"], "needs.prepare.outputs.mode == 'automatic'")
            self.assertIn("verify-permanent-dockerhub-cutover.py", cutover["run"])
            self.assertEqual(
                cutover["env"]["LEGACY_PUBLISHER_DISABLED"],
                "${{ vars.LEGACY_PUBLISHER_DISABLED }}",
            )
            self.assertEqual(
                cutover["env"]["DOCKERHUB_TAG_POLICY_ENFORCED"],
                "${{ vars.DOCKERHUB_TAG_POLICY_ENFORCED }}",
            )
            self.assertEqual(cutover["env"]["WORKFLOW_SHA"], "${{ github.sha }}")
            self.assertIn('test "$current_main" = "$WORKFLOW_SHA"', cutover["run"])
        dumped = yaml.safe_dump(controller, sort_keys=False)
        self.assertNotIn("require-fresh-cutover-lease.sh", dumped)
        for required in (
            "canary artifact set mismatch",
            "promotion-plan.json",
            "target_dockerhub_digest",
            "target_ghcr_digest",
            "rollback-auto-dockerhub-",
            "rollback-auto-ghcr-",
            "scripts/validate-auto-promotion-plan.py",
            "scripts/promote-auto-canaries.sh",
            "scripts/transaction-journal.py begin",
            "scripts/transaction-journal.py finish",
            "crane copy",
            "go-containerregistry_Linux_x86_64.tar.gz",
            "59b59f68ee37aba51f5523d69ec779ee925d9be4e279f9220eca357267f2ee67",
            "dockerhub-inventory-before-stage.json",
            "dockerhub-inventory-after-stage.json",
            "verify-permanent-dockerhub-cutover.py",
        ):
            self.assertIn(required, dumped)
        freeze = next(
            step for step in controller["steps"]
            if step["name"] == "Freeze deterministic final plan before any registry write"
        )["run"]
        self.assertIn('unit["target_dockerhub_digest"] = unit["target_ghcr_digest"]', freeze)
        materialize = next(
            step for step in controller["steps"]
            if step["name"] == "Materialize frozen immutable subjects without moving aliases"
        )["run"]
        self.assertNotIn('Path("publisher-reports/promotion-plan.json").write', materialize)
        self.assertIn("cmp --silent", materialize)
        self.assertIn("--expected-state", materialize)
        self.assertLess(
            materialize.index("verify-permanent-dockerhub-cutover.py"),
            materialize.index("crane copy"),
        )
        second_cutover = next(
            step for step in controller["steps"]
            if step["name"] == "Revalidate permanent Docker Hub cutover at mutation boundary"
        )
        self.assertIn("--expected-state", second_cutover["run"])
        self.assertIn("git ls-remote --exit-code origin refs/heads/main", second_cutover["run"])
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
        self.assertEqual(trigger["schedule"], [{"cron": "*/15 * * * *"}])
        self.assertNotIn("workflow_dispatch", trigger)
        self.assertEqual(data["concurrency"]["group"], "fpm-production-promotion")
        self.assertFalse(data["concurrency"]["cancel-in-progress"])
        recover = data["jobs"]["recover"]
        self.assertEqual(recover["environment"], "fpm-auto-production")
        self.assertEqual(recover["permissions"]["contents"], "write")
        self.assertIn("needs.prepare.outputs.has_pending == 'true'", recover["if"])
        for required in (
            "failure\", \"cancelled\", \"timed_out",
            "later successful publisher run blocks stale recovery",
            "publisher-auto-plan-${{ needs.prepare.outputs.original_run_id }}",
            "recovery artifact exact-set mismatch",
            "promotion-plan.sha256",
            "original-versions.json",
            "--versions-file recovery-plan/original-versions.json",
            "AUTO_PROMOTION_VERSIONS_FILE",
            "promote-auto-canaries.sh recover",
            "committed_artifact_id",
            "--expected-plan-sha256",
            "--exact-set",
            '"status": "already-verified"',
            "transaction-journal.py pending",
            "transaction-journal.py assert-owner",
            "transaction-journal.py finish",
            "has_pending=false",
            "event_name == \"schedule\"",
        ):
            self.assertIn(required, text)
        self.assertIn("publisher-auto-committed-${{ github.run_id }}", WORKFLOW.read_text())
        self.assertIn(
            "AUTO_PROMOTION_VERSIONS_FILE",
            (ROOT / "scripts/verify-published-dockerhub-image.sh").read_text(),
        )
        self.assertNotIn("mapfile -t", text)

        steps = recover["steps"]
        names = [step["name"] for step in steps]
        self.assertLess(
            names.index("Verify artifact bytes and strict plan binding before credentials"),
            names.index(
                "Verify permanent Docker Hub cutover before automatic recovery credentials"
            ),
        )
        self.assertLess(
            names.index(
                "Verify permanent Docker Hub cutover before automatic recovery credentials"
            ),
            names.index("Log in to Docker Hub recovery boundary"),
        )
        self.assertNotIn("require-fresh-cutover-lease.sh", text)
        self.assertIn("verify-permanent-dockerhub-cutover.py", text)
        self.assertLess(
            names.index("Classify every alias before restoring exact baselines"),
            names.index("Upload recovery evidence"),
        )
        self.assertLess(
            names.index("Upload recovery evidence"),
            names.index("Close durable writer fence after recovery evidence"),
        )
        plan = next(step for step in steps if step.get("id") == "plan")
        self.assertIn("operation=", plan["run"])
        self.assertIn("transaction-journal.py assert-owner", plan["run"])
        dockerhub_login = next(
            step for step in steps
            if step["name"] == "Log in to Docker Hub recovery boundary"
        )
        self.assertIn("steps.committed.outputs.recovery_required == 'true'", dockerhub_login["if"])
        self.assertIn("steps.plan.outputs.operation == 'automatic'", dockerhub_login["if"])
        ghcr_login = next(
            step for step in steps
            if step["name"] == "Log in to GHCR recovery boundary"
        )
        self.assertEqual(
            ghcr_login["if"], "steps.committed.outputs.recovery_required == 'true'"
        )

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
                "import json,os,sys\n"
                "if '--paginate' in sys.argv:\n"
                "    print(os.environ['COMPLETED_RUNS'], end='')\n"
                "elif any('/artifacts?' in arg for arg in sys.argv):\n"
                "    print(os.environ['ARTIFACTS_JSON'])\n"
                "else:\n"
                "    print(os.environ['ORIGINAL_RUN_JSON'])\n"
            )
            gh.chmod(0o755)
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip()
            digest = lambda value: "sha256:" + f"{value:064x}"
            units = []
            for index, minor in enumerate(("8.2", "8.3", "8.4", "8.5"), start=1):
                units.append({
                    "php_minor": minor,
                    "previous_dockerhub_digest": digest(100 + index),
                    "previous_ghcr_digest": digest(300 + index),
                    "target_dockerhub_digest": digest(200 + index),
                    "target_ghcr_digest": digest(200 + index),
                })
            plan = root / "promotion-plan.json"
            plan.write_text(json.dumps({
                "schema_version": 1,
                "operation": "automatic",
                "repository": "woosungchoi/fpm-alpine",
                "workflow_path": ".github/workflows/dependency-auto-publish.yml",
                "workflow_sha": head,
                "run_id": 123,
                "run_attempt": 1,
                "source_sha": head,
                "release_units": units,
            }, indent=2, sort_keys=True) + "\n")
            plan_sha256 = hashlib.sha256(plan.read_bytes()).hexdigest()
            journal_dir = root / "journal"
            journal_env = os.environ.copy()
            journal_env["TRANSACTION_JOURNAL_DIR"] = str(journal_dir)
            subprocess.run(
                [str(ROOT / "scripts/transaction-journal.py"), "begin", str(plan)],
                cwd=ROOT,
                env=journal_env,
                check=True,
            )
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
                ("123\t1\t.github/workflows/dependency-auto-publish.yml\tfailure\n", True),
                ("123\t2\t.github/workflows/dependency-auto-publish.yml\tsuccess\n", False),
                ("124\t1\t.github/workflows/dependency-auto-publish.yml\tsuccess\n", False),
            ):
                with self.subTest(later_success=not expected_ok):
                    output = root / f"output-{expected_ok}"
                    env = os.environ.copy()
                    env.update(
                        PATH=f"{fake_bin}:{env['PATH']}",
                        EVENT_NAME="repository_dispatch",
                        EVENT_ACTION="fpm-publish-recover",
                        EVENT_REF="refs/heads/main",
                        ORIGINAL_RUN_ID="123",
                        ORIGINAL_RUN_ATTEMPT="1",
                        PLAN_SHA256=plan_sha256,
                        GITHUB_REPOSITORY="woosungchoi/fpm-alpine",
                        GITHUB_OUTPUT=str(output),
                        GH_TOKEN="test-token",
                        TRANSACTION_JOURNAL_DIR=str(journal_dir),
                        ORIGINAL_RUN_JSON=original,
                        COMPLETED_RUNS=completed_runs,
                        ARTIFACTS_JSON=json.dumps({"total_count": 0, "artifacts": []}),
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

            committed_output = root / "output-committed"
            committed_name = f"publisher-auto-committed-123-1-{plan_sha256}"
            committed_env = os.environ.copy()
            committed_env.update(
                PATH=f"{fake_bin}:{committed_env['PATH']}",
                EVENT_NAME="repository_dispatch",
                EVENT_ACTION="fpm-publish-recover",
                EVENT_REF="refs/heads/main",
                ORIGINAL_RUN_ID="123",
                ORIGINAL_RUN_ATTEMPT="1",
                PLAN_SHA256=plan_sha256,
                GITHUB_REPOSITORY="woosungchoi/fpm-alpine",
                GITHUB_OUTPUT=str(committed_output),
                GH_TOKEN="test-token",
                TRANSACTION_JOURNAL_DIR=str(journal_dir),
                ORIGINAL_RUN_JSON=original,
                COMPLETED_RUNS=(
                    "123\t1\t.github/workflows/dependency-auto-publish.yml\tfailure\n"
                ),
                ARTIFACTS_JSON=json.dumps(
                    {
                        "total_count": 1,
                        "artifacts": [
                            {
                                "id": 456,
                                "name": committed_name,
                                "expired": False,
                                "workflow_run": {"id": 123},
                            }
                        ],
                    }
                ),
            )
            committed = subprocess.run(
                ["bash", "-c", request["run"]],
                cwd=ROOT,
                env=committed_env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(committed.returncode, 0, committed.stderr)
            self.assertIn("committed_artifact_id=456", committed_output.read_text())

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
            "test_permanent_dockerhub_cutover.py",
            "test_transaction_journal.py",
            "test_transaction_journal_github.py",
            "test_backfill_source_manifest.py",
            "test_fresh_cutover_lease.py",
            "test_require_fresh_cutover_lease.py",
            "test_refresh_cutover_lease.py",
            "test_published_operation.py",
        ):
            self.assertIn(f"python3 tests/{test}", step["run"])


if __name__ == "__main__":
    unittest.main()
