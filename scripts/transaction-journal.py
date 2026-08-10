#!/usr/bin/env python3
"""Durable publication journal with immutable records and a CAS lock chain.

Production stores immutable JSON records in annotated tags under
``refs/fpm-transactions/...``. Reusable ownership is a permanent commit-backed
branch advanced only by fast-forward updates. Tests may set
``TRANSACTION_JOURNAL_DIR`` for an fsynced local implementation of the same
state machine.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
NONCE_RE = re.compile(r"[0-9a-f]{32}")
MINOR_RE = re.compile(r"8\.[2-5]")
PLAN_SHA_RE = re.compile(r"[0-9a-f]{64}")
LOCK_REF = "refs/heads/fpm-transaction-lock"
LOCK_FILE = "transaction-lock.json"
REPOSITORY = "woosungchoi/fpm-alpine"
WORKFLOW_PATH = ".github/workflows/dependency-auto-publish.yml"
LOCK_STATES = {
    "FREE",
    "PREPARED",
    "ACTIVE",
    "RECOVERY_REQUIRED",
    "RECOVERING",
    "BLOCKED",
    "COMMITTED",
    "RECOVERED",
}
OWNED_STATES = LOCK_STATES - {"FREE"}
MUTABLE_OWNER_STATES = {"PREPARED", "ACTIVE", "RECOVERY_REQUIRED", "RECOVERING"}
ALLOWED_FINISH = {"committed", "recovered", "already-verified"}
PREPARE_KINDS = {"pin-ghcr", "pin-dockerhub-backup", "stage-dockerhub"}
FAILURE_REASONS = {
    "classification-init",
    "classification-append",
    "classification-unknown",
    "signal-int",
    "signal-term",
    "unexpected-exit",
    "restore-failed",
    "final-readback",
}
BLOCKING_FAILURES = {
    "classification-init",
    "classification-append",
    "classification-unknown",
    "restore-failed",
    "final-readback",
}


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()


def require_str(
    payload: dict[str, Any], key: str, pattern: re.Pattern[str] | None = None
) -> str:
    value = payload.get(key)
    if type(value) is not str or (
        pattern is not None and pattern.fullmatch(value) is None
    ):
        fail(f"invalid {key}")
    return cast(str, value)


def require_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value <= 0:
        fail(f"invalid {key}")
    return cast(int, value)


def load_plan(path_text: str) -> tuple[dict[str, Any], str]:
    path = Path(path_text)
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot load transaction plan: {error}")
    if type(payload) is not dict:
        fail("transaction plan must be an object")
    if (
        payload.get("schema_version") != 1
        or type(payload.get("schema_version")) is not int
    ):
        fail("invalid transaction plan schema")
    operation = require_str(payload, "operation")
    if operation not in {"automatic", "backfill-ghcr"}:
        fail("invalid transaction operation")
    if require_str(payload, "repository") != REPOSITORY:
        fail("invalid transaction repository")
    if require_str(payload, "workflow_path") != WORKFLOW_PATH:
        fail("invalid transaction workflow path")
    require_str(payload, "workflow_sha", SHA_RE)
    require_str(payload, "source_sha", SHA_RE)
    require_positive_int(payload, "run_id")
    require_positive_int(payload, "run_attempt")
    units = payload.get("release_units")
    if type(units) is not list or len(units) != 4:
        fail("transaction plan must contain four release units")
    observed: set[str] = set()
    for unit in units:
        if type(unit) is not dict:
            fail("invalid transaction release unit")
        minor = require_str(unit, "php_minor", MINOR_RE)
        if minor in observed:
            fail("duplicate transaction release unit")
        observed.add(minor)
        previous_dockerhub = require_str(
            unit, "previous_dockerhub_digest", DIGEST_RE
        )
        require_str(unit, "previous_ghcr_digest", DIGEST_RE)
        require_str(unit, "target_ghcr_digest", DIGEST_RE)
        target_dockerhub = require_str(unit, "target_dockerhub_digest", DIGEST_RE)
        if operation == "backfill-ghcr" and target_dockerhub != previous_dockerhub:
            fail("backfill Docker Hub target must remain the prior digest")
    if observed != {"8.2", "8.3", "8.4", "8.5"}:
        fail("transaction release-unit set mismatch")
    return payload, hashlib.sha256(raw).hexdigest()


def common_record(
    plan: dict[str, Any], plan_sha256: str, kind: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": kind,
        "operation": plan["operation"],
        "repository": plan["repository"],
        "workflow_path": plan["workflow_path"],
        "workflow_sha": plan["workflow_sha"],
        "run_id": plan["run_id"],
        "run_attempt": plan["run_attempt"],
        "source_sha": plan["source_sha"],
        "plan_sha256": plan_sha256,
    }


def transaction_prefix(plan: dict[str, Any], plan_sha256: str) -> str:
    return (
        f"refs/fpm-transactions/audit/{plan['run_id']}/{plan['run_attempt']}/"
        f"{plan_sha256}"
    )


def plan_ref(plan: dict[str, Any], plan_sha256: str) -> str:
    return f"{transaction_prefix(plan, plan_sha256)}/plan"


def record_ref(
    plan: dict[str, Any],
    plan_sha256: str,
    kind: str,
    minor: str,
    registry: str,
) -> str:
    return f"{transaction_prefix(plan, plan_sha256)}/{kind}/{registry}/{minor}"


def prepare_ref(
    plan: dict[str, Any], plan_sha256: str, kind: str, minor: str, result: bool
) -> str:
    phase = "prepare-result" if result else "prepare-attempt"
    return f"{transaction_prefix(plan, plan_sha256)}/{phase}/{kind}/{minor}"


def referrer_ref(
    plan: dict[str, Any], plan_sha256: str, minor: str, result: bool
) -> str:
    phase = "referrer-result" if result else "referrer-attempt"
    return f"{transaction_prefix(plan, plan_sha256)}/{phase}/dockerhub/{minor}"


def recovery_referrer_ref(
    plan: dict[str, Any], plan_sha256: str, minor: str, result: bool
) -> str:
    phase = "recovery-referrer-result" if result else "recovery-referrer-attempt"
    return f"{transaction_prefix(plan, plan_sha256)}/{phase}/dockerhub/{minor}"


def receipt_ref(plan: dict[str, Any], plan_sha256: str) -> str:
    return f"{transaction_prefix(plan, plan_sha256)}/receipt"


def unit_for(plan: dict[str, Any], minor: str) -> dict[str, Any]:
    if MINOR_RE.fullmatch(minor) is None:
        fail("invalid PHP minor")
    matches = [unit for unit in plan["release_units"] if unit["php_minor"] == minor]
    if len(matches) != 1:
        fail("PHP minor is not in the transaction plan")
    return matches[0]


@dataclass(frozen=True)
class LockSnapshot:
    sha: str
    payload: dict[str, Any]


class Backend:
    def read_record(self, ref: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def create_record(
        self, ref: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    def read_lock(self) -> LockSnapshot | None:
        raise NotImplementedError

    def ensure_lock(self, workflow_sha: str) -> LockSnapshot:
        raise NotImplementedError

    def advance_lock(
        self, expected_sha: str, payload: dict[str, Any]
    ) -> LockSnapshot:
        raise NotImplementedError


class LocalBackend(Backend):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.guard_path = root / ".lock-cas"

    @staticmethod
    def fsync_dir(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def record_path(self, ref: str) -> Path:
        if not ref.startswith("refs/fpm-transactions/"):
            fail("invalid local journal ref")
        relative = ref.removeprefix("refs/fpm-transactions/")
        return self.root / f"{relative}.json"

    def read_record(self, ref: str) -> dict[str, Any] | None:
        path = self.record_path(ref)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            fail(f"cannot read local transaction record: {error}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            fail(f"invalid local transaction record JSON: {error}")
        if type(payload) is not dict or canonical_bytes(payload) != raw:
            fail("local transaction record is not canonical")
        return payload

    def create_record(
        self, ref: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        path = self.record_path(ref)
        raw = canonical_bytes(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            observed = self.read_record(ref)
            assert observed is not None
            return observed
        except OSError as error:
            fail(f"cannot create local transaction record: {error}")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            self.fsync_dir(path.parent)
        except BaseException:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        return payload

    def lock_head_path(self) -> Path:
        return self.root / "lock" / "head.json"

    def lock_commit_path(self, sha: str) -> Path:
        return self.root / "lock" / "commits" / f"{sha}.json"

    def _read_lock_unlocked(self) -> LockSnapshot | None:
        head_path = self.lock_head_path()
        try:
            head_raw = head_path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            head = json.loads(head_raw)
        except json.JSONDecodeError as error:
            fail(f"invalid local lock head JSON: {error}")
        if (
            type(head) is not dict
            or set(head) != {"sha", "state"}
            or canonical_bytes(head) != head_raw
        ):
            fail("local lock head is not canonical")
        sha = require_str(head, "sha", SHA_RE)
        commit_path = self.lock_commit_path(sha)
        try:
            commit_raw = commit_path.read_bytes()
            commit = json.loads(commit_raw)
        except (OSError, json.JSONDecodeError) as error:
            fail(f"cannot read local lock commit: {error}")
        if type(commit) is not dict or canonical_bytes(commit) != commit_raw:
            fail("local lock commit is not canonical")
        if set(commit) != {"parent_sha", "payload", "sha"} or commit.get("sha") != sha:
            fail("local lock commit envelope is invalid")
        payload = commit.get("payload")
        if type(payload) is not dict:
            fail("local lock commit payload is invalid")
        validate_lock_payload(payload)
        if head["state"] != payload["state"]:
            fail("local lock head state mismatch")
        parent = commit.get("parent_sha")
        expected_parent = payload["parent_lock_sha"] or payload["initializer_sha"]
        if parent != expected_parent:
            fail("local lock commit parent mismatch")
        return LockSnapshot(sha, payload)

    def read_lock(self) -> LockSnapshot | None:
        return self._read_lock_unlocked()

    def _write_exclusive_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = canonical_bytes(payload)
        try:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            if path.read_bytes() != raw:
                fail("local lock commit hash collision")
            return
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        self.fsync_dir(path.parent)

    def _replace_head(self, sha: str, state: str) -> None:
        path = self.lock_head_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        raw = canonical_bytes({"sha": sha, "state": state})
        with temporary.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        self.fsync_dir(path.parent)

    def _new_commit(
        self, parent_sha: str, payload: dict[str, Any]
    ) -> LockSnapshot:
        envelope_without_sha = {"parent_sha": parent_sha, "payload": payload}
        sha = hashlib.sha1(canonical_bytes(envelope_without_sha)).hexdigest()
        envelope = {**envelope_without_sha, "sha": sha}
        self._write_exclusive_json(self.lock_commit_path(sha), envelope)
        return LockSnapshot(sha, payload)

    def ensure_lock(self, workflow_sha: str) -> LockSnapshot:
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard_path.touch(exist_ok=True)
        with self.guard_path.open("rb") as guard:
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
            observed = self._read_lock_unlocked()
            if observed is not None:
                return observed
            payload = lock_payload(
                state="FREE",
                generation=0,
                parent_lock_sha=None,
                initializer_sha=workflow_sha,
                transaction=None,
            )
            created = self._new_commit(workflow_sha, payload)
            self._replace_head(created.sha, "FREE")
            return created

    def advance_lock(
        self, expected_sha: str, payload: dict[str, Any]
    ) -> LockSnapshot:
        validate_lock_payload(payload)
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard_path.touch(exist_ok=True)
        with self.guard_path.open("rb") as guard:
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
            observed = self._read_lock_unlocked()
            if observed is None or observed.sha != expected_sha:
                fail("transaction lock CAS conflict")
            if payload["parent_lock_sha"] != expected_sha:
                fail("new lock state does not name the exact parent")
            created = self._new_commit(expected_sha, payload)
            self._replace_head(created.sha, payload["state"])
            return created


class GitHubBackend(Backend):
    def __init__(self, repository: str) -> None:
        if repository != REPOSITORY:
            fail("GitHub transaction journal repository mismatch")
        if not os.environ.get("GH_TOKEN"):
            fail("GH_TOKEN is required for the GitHub transaction journal")
        self.repository = repository

    def api(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> dict[str, Any] | None:
        command = ["gh", "api", "--method", method, endpoint]
        raw_input = None
        if payload is not None:
            command.extend(("--input", "-"))
            raw_input = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        result = subprocess.run(
            command,
            input=raw_input,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            if allow_404 and (
                "HTTP 404" in result.stderr or "Not Found" in result.stderr
            ):
                return None
            fail(
                f"GitHub journal API failed for {method} {endpoint}: "
                f"{result.stderr.strip()}"
            )
        if not result.stdout.strip():
            return {}
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError:
            fail("GitHub journal API returned invalid JSON")
        if type(response) is not dict:
            fail("GitHub journal API returned a non-object")
        return response

    def _read_ref(self, ref: str) -> dict[str, Any] | None:
        short_ref = ref.removeprefix("refs/")
        return self.api(
            "GET",
            f"repos/{self.repository}/git/ref/{short_ref}",
            allow_404=True,
        )

    def read_record(self, ref: str) -> dict[str, Any] | None:
        ref_payload = self._read_ref(ref)
        if ref_payload is None:
            return None
        obj = ref_payload.get("object")
        if type(obj) is not dict or obj.get("type") != "tag":
            fail(f"transaction record ref is not an annotated tag: {ref}")
        tag_sha = obj.get("sha")
        if type(tag_sha) is not str or SHA_RE.fullmatch(tag_sha) is None:
            fail("invalid transaction record tag object SHA")
        tag = self.api("GET", f"repos/{self.repository}/git/tags/{tag_sha}")
        assert tag is not None
        target = tag.get("object")
        if type(target) is not dict or target.get("type") != "commit":
            fail("transaction record tag does not target a commit")
        message = tag.get("message")
        if type(message) is not str:
            fail("transaction record tag message is missing")
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            fail("transaction record tag message is invalid JSON")
        if type(payload) is not dict or canonical_bytes(payload).decode() != message:
            fail("transaction record tag message is not canonical")
        if target.get("sha") != payload.get("workflow_sha"):
            fail("transaction record tag target does not match workflow SHA")
        return payload

    @staticmethod
    def tag_name(ref: str, payload: dict[str, Any]) -> str:
        suffix = (
            ref.removeprefix("refs/fpm-transactions/")
            .replace("/", "-")
            .replace(".", "-")
        )
        return f"fpm-{suffix}-{payload['plan_sha256'][:12]}"[:250]

    def create_record(
        self, ref: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        observed = self.read_record(ref)
        if observed is not None:
            return observed
        tag = self.api(
            "POST",
            f"repos/{self.repository}/git/tags",
            {
                "tag": self.tag_name(ref, payload),
                "message": canonical_bytes(payload).decode(),
                "object": payload["workflow_sha"],
                "type": "commit",
            },
        )
        assert tag is not None
        tag_sha = tag.get("sha")
        if type(tag_sha) is not str or SHA_RE.fullmatch(tag_sha) is None:
            fail("GitHub did not return an annotated-tag SHA")
        command = [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{self.repository}/git/refs",
            "--input",
            "-",
        ]
        result = subprocess.run(
            command,
            input=json.dumps({"ref": ref, "sha": tag_sha}, separators=(",", ":")),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raced = self.read_record(ref)
            if raced is None:
                fail(
                    f"GitHub transaction record creation failed for {ref}: "
                    f"{result.stderr.strip()}"
                )
            return raced
        confirmed = self.read_record(ref)
        if confirmed is None:
            fail("GitHub transaction record was not readable after creation")
        return confirmed

    def verify_lock_protection(self) -> None:
        if os.environ.get("TRANSACTION_JOURNAL_SKIP_PROTECTION") == "1":
            return
        protection = self.api(
            "GET",
            f"repos/{self.repository}/branches/fpm-transaction-lock/protection",
        )
        assert protection is not None
        for key in ("allow_force_pushes", "allow_deletions"):
            value = protection.get(key)
            if type(value) is not dict or value.get("enabled") is not False:
                fail(f"transaction lock branch protection does not forbid {key}")
        linear = protection.get("required_linear_history")
        if type(linear) is not dict or linear.get("enabled") is not True:
            fail("transaction lock branch does not require linear history")

    def _read_lock_commit(self, sha: str) -> LockSnapshot:
        commit = self.api("GET", f"repos/{self.repository}/git/commits/{sha}")
        assert commit is not None
        tree = commit.get("tree")
        if type(tree) is not dict:
            fail("transaction lock commit tree is missing")
        tree_sha = tree.get("sha")
        if type(tree_sha) is not str or SHA_RE.fullmatch(tree_sha) is None:
            fail("transaction lock tree SHA is invalid")
        tree_payload = self.api("GET", f"repos/{self.repository}/git/trees/{tree_sha}")
        assert tree_payload is not None
        entries = tree_payload.get("tree")
        if type(entries) is not list or len(entries) != 1:
            fail("transaction lock tree must contain exactly one state blob")
        entry = entries[0]
        if (
            type(entry) is not dict
            or entry.get("path") != LOCK_FILE
            or entry.get("mode") != "100644"
            or entry.get("type") != "blob"
        ):
            fail("transaction lock tree entry is invalid")
        blob_sha = entry.get("sha")
        if type(blob_sha) is not str or SHA_RE.fullmatch(blob_sha) is None:
            fail("transaction lock blob SHA is invalid")
        blob = self.api("GET", f"repos/{self.repository}/git/blobs/{blob_sha}")
        assert blob is not None
        if blob.get("encoding") != "base64" or type(blob.get("content")) is not str:
            fail("transaction lock blob encoding is invalid")
        try:
            raw = base64.b64decode(blob["content"], validate=False)
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            fail("transaction lock blob content is invalid")
        if type(payload) is not dict or canonical_bytes(payload) != raw:
            fail("transaction lock state is not canonical")
        validate_lock_payload(payload)
        parents = commit.get("parents")
        if type(parents) is not list or len(parents) != 1 or type(parents[0]) is not dict:
            fail("transaction lock commit must have exactly one parent")
        expected_parent = payload["parent_lock_sha"] or payload["initializer_sha"]
        if parents[0].get("sha") != expected_parent:
            fail("transaction lock commit parent mismatch")
        return LockSnapshot(sha, payload)

    def read_lock(self) -> LockSnapshot | None:
        ref_payload = self._read_ref(LOCK_REF)
        if ref_payload is None:
            return None
        obj = ref_payload.get("object")
        if type(obj) is not dict or obj.get("type") != "commit":
            fail("transaction lock head is not commit-backed")
        sha = obj.get("sha")
        if type(sha) is not str or SHA_RE.fullmatch(sha) is None:
            fail("transaction lock head SHA is invalid")
        return self._read_lock_commit(sha)

    def _new_lock_commit(
        self, parent_sha: str, payload: dict[str, Any]
    ) -> LockSnapshot:
        raw = canonical_bytes(payload)
        blob = self.api(
            "POST",
            f"repos/{self.repository}/git/blobs",
            {"content": raw.decode(), "encoding": "utf-8"},
        )
        assert blob is not None
        blob_sha = blob.get("sha")
        if type(blob_sha) is not str or SHA_RE.fullmatch(blob_sha) is None:
            fail("GitHub did not return a transaction lock blob SHA")
        tree = self.api(
            "POST",
            f"repos/{self.repository}/git/trees",
            {
                "tree": [
                    {
                        "path": LOCK_FILE,
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_sha,
                    }
                ]
            },
        )
        assert tree is not None
        tree_sha = tree.get("sha")
        if type(tree_sha) is not str or SHA_RE.fullmatch(tree_sha) is None:
            fail("GitHub did not return a transaction lock tree SHA")
        commit = self.api(
            "POST",
            f"repos/{self.repository}/git/commits",
            {
                "message": f"fpm transaction lock: {payload['state']}",
                "tree": tree_sha,
                "parents": [parent_sha],
            },
        )
        assert commit is not None
        commit_sha = commit.get("sha")
        if type(commit_sha) is not str or SHA_RE.fullmatch(commit_sha) is None:
            fail("GitHub did not return a transaction lock commit SHA")
        confirmed = self._read_lock_commit(commit_sha)
        if confirmed.payload != payload:
            fail("transaction lock commit read-back mismatch")
        return confirmed

    def ensure_lock(self, workflow_sha: str) -> LockSnapshot:
        observed = self.read_lock()
        if observed is None:
            if os.environ.get("TRANSACTION_JOURNAL_ALLOW_BOOTSTRAP") != "1":
                fail(
                    "transaction lock branch is absent; bootstrap and protect it before publication"
                )
            payload = lock_payload(
                state="FREE",
                generation=0,
                parent_lock_sha=None,
                initializer_sha=workflow_sha,
                transaction=None,
            )
            created = self._new_lock_commit(workflow_sha, payload)
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    f"repos/{self.repository}/git/refs",
                    "--input",
                    "-",
                ],
                input=json.dumps(
                    {"ref": LOCK_REF, "sha": created.sha},
                    separators=(",", ":"),
                ),
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                observed = self.read_lock()
                if observed is None:
                    fail(
                        "transaction lock branch creation failed: "
                        f"{result.stderr.strip()}"
                    )
            else:
                observed = self.read_lock()
        assert observed is not None
        self.verify_lock_protection()
        return observed

    def advance_lock(
        self, expected_sha: str, payload: dict[str, Any]
    ) -> LockSnapshot:
        validate_lock_payload(payload)
        if payload["parent_lock_sha"] != expected_sha:
            fail("new lock state does not name the exact parent")
        created = self._new_lock_commit(expected_sha, payload)
        result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "PATCH",
                f"repos/{self.repository}/git/refs/heads/fpm-transaction-lock",
                "--input",
                "-",
            ],
            input=json.dumps(
                {"sha": created.sha, "force": False},
                separators=(",", ":"),
            ),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            observed = self.read_lock()
            if observed is not None and observed.sha == created.sha:
                return observed
            fail(
                "transaction lock CAS conflict or ambiguous update: "
                f"{result.stderr.strip()}"
            )
        observed = self.read_lock()
        if observed is None or observed.sha != created.sha or observed.payload != payload:
            fail("transaction lock update read-back mismatch")
        return observed


def backend(repository: str | None = None) -> Backend:
    local = os.environ.get("TRANSACTION_JOURNAL_DIR")
    if local:
        return LocalBackend(Path(local))
    repo = repository or os.environ.get("GITHUB_REPOSITORY", "")
    return GitHubBackend(repo)


def lock_payload(
    *,
    state: str,
    generation: int,
    parent_lock_sha: str | None,
    initializer_sha: str,
    transaction: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "kind": "lock-state",
        "repository": REPOSITORY,
        "state": state,
        "generation": generation,
        "parent_lock_sha": parent_lock_sha,
        "initializer_sha": initializer_sha,
        "transaction": transaction,
    }


def validate_owner(owner: dict[str, Any]) -> None:
    required = {
        "operation",
        "repository",
        "workflow_path",
        "workflow_sha",
        "run_id",
        "run_attempt",
        "source_sha",
        "plan_sha256",
        "transaction_nonce",
        "transaction_id",
        "plan_ref",
        "fence",
    }
    if set(owner) != required:
        fail("transaction lock owner fields are invalid")
    if owner["operation"] not in {"automatic", "backfill-ghcr"}:
        fail("transaction lock operation is invalid")
    if owner["repository"] != REPOSITORY or owner["workflow_path"] != WORKFLOW_PATH:
        fail("transaction lock owner boundary mismatch")
    require_str(owner, "workflow_sha", SHA_RE)
    require_str(owner, "source_sha", SHA_RE)
    require_str(owner, "plan_sha256", PLAN_SHA_RE)
    require_str(owner, "transaction_nonce", NONCE_RE)
    require_str(owner, "fence", NONCE_RE)
    require_positive_int(owner, "run_id")
    require_positive_int(owner, "run_attempt")
    transaction_id = require_str(owner, "transaction_id")
    expected_id = (
        f"{owner['run_id']}.{owner['run_attempt']}.{owner['transaction_nonce']}"
    )
    if transaction_id != expected_id:
        fail("transaction lock transaction ID mismatch")
    plan_reference = require_str(owner, "plan_ref")
    if not plan_reference.startswith("refs/fpm-transactions/audit/") or not plan_reference.endswith("/plan"):
        fail("transaction lock plan ref is invalid")


def validate_lock_payload(payload: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "kind",
        "repository",
        "state",
        "generation",
        "parent_lock_sha",
        "initializer_sha",
        "transaction",
    }
    if set(payload) != required:
        fail("transaction lock state fields are invalid")
    if payload["schema_version"] != 2 or type(payload["schema_version"]) is not int:
        fail("transaction lock schema is invalid")
    if payload["kind"] != "lock-state" or payload["repository"] != REPOSITORY:
        fail("transaction lock identity is invalid")
    state = payload["state"]
    if type(state) is not str or state not in LOCK_STATES:
        fail("transaction lock state is invalid")
    generation = payload["generation"]
    if type(generation) is not int or generation < 0:
        fail("transaction lock generation is invalid")
    parent = payload["parent_lock_sha"]
    if parent is not None and (type(parent) is not str or SHA_RE.fullmatch(parent) is None):
        fail("transaction lock parent is invalid")
    require_str(payload, "initializer_sha", SHA_RE)
    transaction = payload["transaction"]
    if state == "FREE":
        if transaction is not None:
            fail("FREE transaction lock must not have an owner")
    elif type(transaction) is not dict:
        fail("owned transaction lock is missing its owner")
    else:
        validate_owner(transaction)


def ensure_exact_record(
    store: Backend, ref: str, payload: dict[str, Any]
) -> dict[str, Any]:
    observed = store.create_record(ref, payload)
    if observed != payload:
        fail(f"transaction record collision: {ref}")
    return observed


def ensure_plan_record(
    store: Backend, plan: dict[str, Any], plan_sha256: str
) -> dict[str, Any]:
    reference = plan_ref(plan, plan_sha256)
    observed = store.read_record(reference)
    expected_common = common_record(plan, plan_sha256, "plan")
    if observed is None:
        nonce = secrets.token_hex(16)
        candidate = {
            **expected_common,
            "transaction_nonce": nonce,
            "transaction_id": f"{plan['run_id']}.{plan['run_attempt']}.{nonce}",
        }
        observed = store.create_record(reference, candidate)
    if type(observed) is not dict:
        fail("transaction plan record is invalid")
    nonce = observed.get("transaction_nonce")
    transaction_id = observed.get("transaction_id")
    if type(nonce) is not str or NONCE_RE.fullmatch(nonce) is None:
        fail("transaction plan nonce is invalid")
    if transaction_id != f"{plan['run_id']}.{plan['run_attempt']}.{nonce}":
        fail("transaction plan ID is invalid")
    expected = {
        **expected_common,
        "transaction_nonce": nonce,
        "transaction_id": transaction_id,
    }
    if observed != expected:
        fail("transaction plan record collision")
    return observed


def owner_from_plan_record(
    record: dict[str, Any], plan_reference: str
) -> dict[str, Any]:
    return {
        "operation": record["operation"],
        "repository": record["repository"],
        "workflow_path": record["workflow_path"],
        "workflow_sha": record["workflow_sha"],
        "run_id": record["run_id"],
        "run_attempt": record["run_attempt"],
        "source_sha": record["source_sha"],
        "plan_sha256": record["plan_sha256"],
        "transaction_nonce": record["transaction_nonce"],
        "transaction_id": record["transaction_id"],
        "plan_ref": plan_reference,
        "fence": record["transaction_nonce"],
    }


def read_plan_record(
    store: Backend, plan: dict[str, Any], plan_sha256: str
) -> dict[str, Any]:
    reference = plan_ref(plan, plan_sha256)
    observed = store.read_record(reference)
    if observed is None:
        fail("transaction plan record is absent")
    expected_common = common_record(plan, plan_sha256, "plan")
    nonce = observed.get("transaction_nonce")
    if type(nonce) is not str or NONCE_RE.fullmatch(nonce) is None:
        fail("transaction plan record nonce is invalid")
    expected = {
        **expected_common,
        "transaction_nonce": nonce,
        "transaction_id": f"{plan['run_id']}.{plan['run_attempt']}.{nonce}",
    }
    if observed != expected:
        fail("transaction plan record mismatch")
    return observed


def require_owner(
    store: Backend,
    plan: dict[str, Any],
    plan_sha256: str,
    allowed_states: set[str] | None = None,
) -> tuple[LockSnapshot, dict[str, Any]]:
    plan_record = read_plan_record(store, plan, plan_sha256)
    expected_owner = owner_from_plan_record(plan_record, plan_ref(plan, plan_sha256))
    observed = store.read_lock()
    if observed is None or observed.payload["state"] == "FREE":
        fail("no pending transaction journal owner")
    if observed.payload["transaction"] != expected_owner:
        fail("transaction lock is owned by another transaction")
    allowed = allowed_states or MUTABLE_OWNER_STATES
    if observed.payload["state"] not in allowed:
        fail(
            "transaction owner is not in an allowed lock state: "
            f"{observed.payload['state']}"
        )
    return observed, plan_record


def transition(
    store: Backend, snapshot: LockSnapshot, state: str
) -> LockSnapshot:
    payload = lock_payload(
        state=state,
        generation=snapshot.payload["generation"],
        parent_lock_sha=snapshot.sha,
        initializer_sha=snapshot.payload["initializer_sha"],
        transaction=snapshot.payload["transaction"],
    )
    return store.advance_lock(snapshot.sha, payload)


def begin_transaction(
    store: Backend, plan: dict[str, Any], plan_sha256: str
) -> None:
    record = ensure_plan_record(store, plan, plan_sha256)
    owner = owner_from_plan_record(record, plan_ref(plan, plan_sha256))
    lock = store.ensure_lock(plan["workflow_sha"])
    receipt = store.read_record(receipt_ref(plan, plan_sha256))
    if receipt is not None:
        fail("finished transaction cannot reacquire the publication lock")
    if lock.payload["state"] == "FREE":
        payload = lock_payload(
            state="PREPARED",
            generation=lock.payload["generation"] + 1,
            parent_lock_sha=lock.sha,
            initializer_sha=lock.payload["initializer_sha"],
            transaction=owner,
        )
        store.advance_lock(lock.sha, payload)
        return
    if lock.payload["transaction"] == owner and lock.payload["state"] in MUTABLE_OWNER_STATES:
        return
    fail("transaction lock is owned by another transaction")


def owned_record_base(
    plan: dict[str, Any],
    plan_sha256: str,
    kind: str,
    plan_record: dict[str, Any],
    snapshot: LockSnapshot,
) -> dict[str, Any]:
    return {
        **common_record(plan, plan_sha256, kind),
        "transaction_id": plan_record["transaction_id"],
        "fence": plan_record["transaction_nonce"],
        "lock_commit_sha": snapshot.sha,
    }


def validate_record_owner(
    payload: dict[str, Any],
    plan: dict[str, Any],
    plan_sha256: str,
    plan_record: dict[str, Any],
) -> None:
    expected = common_record(plan, plan_sha256, payload.get("kind", ""))
    for key, value in expected.items():
        if payload.get(key) != value:
            fail(f"transaction record owner mismatch for {key}")
    if payload.get("transaction_id") != plan_record["transaction_id"]:
        fail("transaction record ID mismatch")
    if payload.get("fence") != plan_record["transaction_nonce"]:
        fail("transaction record fence mismatch")
    lock_sha = payload.get("lock_commit_sha")
    if type(lock_sha) is not str or SHA_RE.fullmatch(lock_sha) is None:
        fail("transaction record lock commit is invalid")


def prepare_spec(
    plan: dict[str, Any], minor: str, kind: str
) -> dict[str, Any]:
    if kind not in PREPARE_KINDS:
        fail("invalid preparation journal kind")
    unit = unit_for(plan, minor)
    if kind == "pin-ghcr":
        return {
            "object_kind": kind,
            "registry": "ghcr",
            "subject": unit["rollback_ghcr_ref"],
            "source_digest": unit["previous_ghcr_digest"],
            "expected_digest": unit["rollback_ghcr_digest"],
        }
    if plan["operation"] != "automatic":
        fail("backfill transactions cannot prepare Docker Hub state")
    if kind == "pin-dockerhub-backup":
        return {
            "object_kind": kind,
            "registry": "ghcr",
            "subject": unit["rollback_dockerhub_ref"],
            "source_digest": unit["previous_dockerhub_digest"],
            "expected_digest": unit["rollback_dockerhub_backup_digest"],
        }
    return {
        "object_kind": kind,
        "registry": "dockerhub",
        "subject": (
            "docker.io/woosungchoi/fpm-alpine@"
            f"{unit['target_dockerhub_digest']}"
        ),
        "source_digest": unit["target_ghcr_digest"],
        "expected_digest": unit["target_dockerhub_digest"],
    }


def preparation_state(
    store: Backend,
    plan: dict[str, Any],
    plan_sha256: str,
    minor: str,
    kind: str,
    plan_record: dict[str, Any],
) -> dict[str, Any]:
    attempt_reference = prepare_ref(plan, plan_sha256, kind, minor, False)
    result_reference = prepare_ref(plan, plan_sha256, kind, minor, True)
    attempt = store.read_record(attempt_reference)
    result = store.read_record(result_reference)
    if attempt is None:
        if result is not None:
            fail("preparation result exists without an attempt")
        return {"attempted": False, "completed": False, "observed_digest": None}
    validate_record_owner(attempt, plan, plan_sha256, plan_record)
    expected_fields = {"php_minor": minor, **prepare_spec(plan, minor, kind)}
    for key, value in expected_fields.items():
        if attempt.get(key) != value:
            fail(f"preparation attempt mismatch for {key}")
    if result is None:
        return {"attempted": True, "completed": False, "observed_digest": None}
    expected_result = {
        **attempt,
        "kind": "prepare-result",
        "observed_digest": attempt["expected_digest"],
        "attempt_sha256": hashlib.sha256(canonical_bytes(attempt)).hexdigest(),
    }
    if result != expected_result:
        fail("preparation result journal mismatch")
    return {
        "attempted": True,
        "completed": True,
        "observed_digest": result["observed_digest"],
    }


def required_preparations(plan: dict[str, Any]) -> tuple[str, ...]:
    if plan["operation"] == "automatic":
        return ("pin-ghcr", "pin-dockerhub-backup", "stage-dockerhub")
    return ("pin-ghcr",)


def require_preparations_complete(
    store: Backend,
    plan: dict[str, Any],
    plan_sha256: str,
    plan_record: dict[str, Any],
) -> None:
    for unit in plan["release_units"]:
        minor = unit["php_minor"]
        for kind in required_preparations(plan):
            state = preparation_state(
                store, plan, plan_sha256, minor, kind, plan_record
            )
            if not state["completed"]:
                fail(f"preparation is incomplete: {minor} {kind}")


def canonical_alias_subjects(
    plan: dict[str, Any], minor: str, registry: str, supplied: list[str] | None
) -> list[str]:
    if registry not in {"dockerhub", "ghcr"}:
        fail("invalid transaction registry")
    if registry == "dockerhub" and plan["operation"] == "backfill-ghcr":
        fail("backfill transactions cannot journal Docker Hub mutation")
    repository = (
        "docker.io/woosungchoi/fpm-alpine"
        if registry == "dockerhub"
        else "ghcr.io/woosungchoi/fpm-alpine"
    )
    subjects = supplied or [f"{repository}:{minor}"]
    if len(subjects) != len(set(subjects)) or not subjects:
        fail("alias mutation subjects must be nonempty and unique")
    if subjects[0] != f"{repository}:{minor}":
        fail("alias mutation must name the moving minor first")
    if registry == "dockerhub" and subjects != [f"{repository}:{minor}"]:
        fail("Docker Hub promotion may mutate only the moving minor alias")
    if registry == "ghcr":
        if len(subjects) not in {1, 3}:
            fail("GHCR promotion must journal the complete one- or three-tag batch")
        for subject in subjects:
            if not subject.startswith(f"{repository}:") or "@" in subject:
                fail("invalid GHCR alias batch subject")
    return subjects


def alias_attempt_payload(
    plan: dict[str, Any],
    plan_sha256: str,
    minor: str,
    registry: str,
    subjects: list[str] | None,
    plan_record: dict[str, Any],
    snapshot: LockSnapshot,
    kind: str = "attempt",
) -> dict[str, Any]:
    unit = unit_for(plan, minor)
    return {
        **owned_record_base(
            plan, plan_sha256, kind, plan_record, snapshot
        ),
        "php_minor": minor,
        "registry": registry,
        "object_kind": "alias-batch",
        "subjects": canonical_alias_subjects(plan, minor, registry, subjects),
        "previous_digest": unit[f"previous_{registry}_digest"],
        "target_digest": unit[f"target_{registry}_digest"],
    }


def validate_alias_attempt(
    attempt: dict[str, Any],
    plan: dict[str, Any],
    plan_sha256: str,
    minor: str,
    registry: str,
    plan_record: dict[str, Any],
    kind: str = "attempt",
) -> None:
    if attempt.get("kind") != kind:
        fail("transaction attempt kind mismatch")
    validate_record_owner(attempt, plan, plan_sha256, plan_record)
    unit = unit_for(plan, minor)
    expected = {
        "php_minor": minor,
        "registry": registry,
        "object_kind": "alias-batch",
        "previous_digest": unit[f"previous_{registry}_digest"],
        "target_digest": unit[f"target_{registry}_digest"],
    }
    for key, value in expected.items():
        if attempt.get(key) != value:
            fail(f"transaction attempt mismatch for {key}")
    subjects = attempt.get("subjects")
    if type(subjects) is not list or any(type(value) is not str for value in subjects):
        fail("transaction alias subjects are invalid")
    canonical_alias_subjects(plan, minor, registry, cast(list[str], subjects))


def publication_state(
    store: Backend,
    plan: dict[str, Any],
    plan_sha256: str,
    minor: str,
    registry: str,
    plan_record: dict[str, Any],
) -> dict[str, Any]:
    attempt_reference = record_ref(
        plan, plan_sha256, "attempt", minor, registry
    )
    result_reference = record_ref(plan, plan_sha256, "result", minor, registry)
    attempt = store.read_record(attempt_reference)
    result = store.read_record(result_reference)
    if attempt is None:
        if result is not None:
            fail("transaction result exists without an attempt journal")
        return {"attempted": False, "completed": False, "target_digest": None}
    validate_alias_attempt(
        attempt, plan, plan_sha256, minor, registry, plan_record
    )
    if result is None:
        return {
            "attempted": True,
            "completed": False,
            "target_digest": attempt["target_digest"],
        }
    expected_result = {
        **attempt,
        "kind": "result",
        "observed_digest": attempt["target_digest"],
        "observed_subjects": {
            subject: attempt["target_digest"] for subject in attempt["subjects"]
        },
        "attempt_sha256": hashlib.sha256(canonical_bytes(attempt)).hexdigest(),
    }
    if result != expected_result:
        fail("transaction result journal mismatch")
    return {
        "attempted": True,
        "completed": True,
        "target_digest": attempt["target_digest"],
    }


def referrer_attempt_payload(
    plan: dict[str, Any],
    plan_sha256: str,
    minor: str,
    plan_record: dict[str, Any],
    snapshot: LockSnapshot,
    kind: str = "referrer-attempt",
) -> dict[str, Any]:
    if plan["operation"] != "automatic":
        fail("backfill cannot journal Docker Hub referrer mutation")
    unit = unit_for(plan, minor)
    digest = unit["target_dockerhub_digest"]
    return {
        **owned_record_base(plan, plan_sha256, kind, plan_record, snapshot),
        "php_minor": minor,
        "registry": "dockerhub",
        "object_kind": "cosign-referrer",
        "subject": f"docker.io/woosungchoi/fpm-alpine@{digest}",
        "target_digest": digest,
        "operation_annotation": "automatic",
    }


def validate_referrer_attempt(
    attempt: dict[str, Any],
    plan: dict[str, Any],
    plan_sha256: str,
    minor: str,
    plan_record: dict[str, Any],
    kind: str = "referrer-attempt",
) -> None:
    if attempt.get("kind") != kind:
        fail("referrer attempt kind mismatch")
    validate_record_owner(attempt, plan, plan_sha256, plan_record)
    unit = unit_for(plan, minor)
    expected = {
        "php_minor": minor,
        "registry": "dockerhub",
        "object_kind": "cosign-referrer",
        "subject": (
            "docker.io/woosungchoi/fpm-alpine@"
            f"{unit['target_dockerhub_digest']}"
        ),
        "target_digest": unit["target_dockerhub_digest"],
        "operation_annotation": "automatic",
    }
    for key, value in expected.items():
        if attempt.get(key) != value:
            fail(f"referrer attempt mismatch for {key}")


def referrer_state(
    store: Backend,
    plan: dict[str, Any],
    plan_sha256: str,
    minor: str,
    plan_record: dict[str, Any],
) -> dict[str, Any]:
    attempt = store.read_record(referrer_ref(plan, plan_sha256, minor, False))
    result = store.read_record(referrer_ref(plan, plan_sha256, minor, True))
    if attempt is None:
        if result is not None:
            fail("referrer result exists without an attempt")
        return {"attempted": False, "completed": False}
    validate_referrer_attempt(
        attempt, plan, plan_sha256, minor, plan_record
    )
    if result is None:
        return {"attempted": True, "completed": False}
    expected = {
        **attempt,
        "kind": "referrer-result",
        "observed_digest": attempt["target_digest"],
        "attempt_sha256": hashlib.sha256(canonical_bytes(attempt)).hexdigest(),
    }
    if result != expected:
        fail("referrer result journal mismatch")
    return {"attempted": True, "completed": True}


def recovery_referrer_attempt_payload(
    plan: dict[str, Any],
    plan_sha256: str,
    minor: str,
    plan_record: dict[str, Any],
    snapshot: LockSnapshot,
) -> dict[str, Any]:
    if plan["operation"] != "automatic":
        fail("backfill cannot journal a Docker Hub recovery referrer")
    unit = unit_for(plan, minor)
    digest = unit["previous_dockerhub_digest"]
    return {
        **owned_record_base(
            plan,
            plan_sha256,
            "recovery-referrer-attempt",
            plan_record,
            snapshot,
        ),
        "php_minor": minor,
        "registry": "dockerhub",
        "object_kind": "cosign-referrer",
        "subject": f"docker.io/woosungchoi/fpm-alpine@{digest}",
        "target_digest": digest,
        "operation_annotation": "recovery",
    }


def validate_recovery_referrer_attempt(
    attempt: dict[str, Any],
    plan: dict[str, Any],
    plan_sha256: str,
    minor: str,
    plan_record: dict[str, Any],
) -> None:
    if attempt.get("kind") != "recovery-referrer-attempt":
        fail("recovery referrer attempt kind mismatch")
    validate_record_owner(attempt, plan, plan_sha256, plan_record)
    unit = unit_for(plan, minor)
    expected = {
        "php_minor": minor,
        "registry": "dockerhub",
        "object_kind": "cosign-referrer",
        "subject": (
            "docker.io/woosungchoi/fpm-alpine@"
            f"{unit['previous_dockerhub_digest']}"
        ),
        "target_digest": unit["previous_dockerhub_digest"],
        "operation_annotation": "recovery",
    }
    for key, value in expected.items():
        if attempt.get(key) != value:
            fail(f"recovery referrer attempt mismatch for {key}")


def required_committed_records_complete(
    store: Backend,
    plan: dict[str, Any],
    plan_sha256: str,
    plan_record: dict[str, Any],
) -> None:
    require_preparations_complete(store, plan, plan_sha256, plan_record)
    for unit in plan["release_units"]:
        minor = unit["php_minor"]
        ghcr = publication_state(
            store, plan, plan_sha256, minor, "ghcr", plan_record
        )
        if not ghcr["completed"]:
            fail(f"GHCR alias result is incomplete: {minor}")
        if plan["operation"] == "automatic":
            dockerhub = publication_state(
                store, plan, plan_sha256, minor, "dockerhub", plan_record
            )
            if not dockerhub["completed"]:
                fail(f"Docker Hub alias result is incomplete: {minor}")
            if not referrer_state(
                store, plan, plan_sha256, minor, plan_record
            )["completed"]:
                fail(f"Docker Hub referrer result is incomplete: {minor}")


def result_sha256(path_text: str | None) -> str:
    if not path_text:
        fail("terminal receipt requires a result file")
    path = Path(path_text)
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read terminal result: {error}")
    if type(payload) is not dict:
        fail("terminal result must be a JSON object")
    return hashlib.sha256(raw).hexdigest()


def finish_transaction(
    store: Backend,
    plan: dict[str, Any],
    plan_sha256: str,
    status: str,
    result_path: str | None,
) -> None:
    plan_record = read_plan_record(store, plan, plan_sha256)
    expected_owner = owner_from_plan_record(
        plan_record, plan_ref(plan, plan_sha256)
    )
    result_hash = result_sha256(result_path)
    receipt_payload = {
        **common_record(plan, plan_sha256, "receipt"),
        "transaction_id": plan_record["transaction_id"],
        "fence": plan_record["transaction_nonce"],
        "status": status,
        "result_sha256": result_hash,
    }
    receipt_reference = receipt_ref(plan, plan_sha256)
    existing_receipt = store.read_record(receipt_reference)
    lock = store.read_lock()
    if lock is None:
        fail("transaction lock is absent")
    if lock.payload["state"] == "FREE":
        if existing_receipt == receipt_payload:
            return
        fail("FREE transaction lock has no matching terminal receipt")
    if lock.payload["transaction"] != expected_owner:
        fail("transaction lock is owned by another transaction")
    if lock.payload["state"] == "BLOCKED":
        fail("BLOCKED transaction cannot be released automatically")
    if status == "committed":
        if lock.payload["state"] not in {"ACTIVE", "COMMITTED"}:
            fail("committed receipt requires ACTIVE transaction ownership")
        required_committed_records_complete(
            store, plan, plan_sha256, plan_record
        )
        terminal_state = "COMMITTED"
    else:
        if lock.payload["state"] not in {
            "PREPARED",
            "ACTIVE",
            "RECOVERY_REQUIRED",
            "RECOVERING",
            "RECOVERED",
        }:
            fail("recovery receipt is not allowed from the current lock state")
        terminal_state = "RECOVERED"
    ensure_exact_record(store, receipt_reference, receipt_payload)
    lock = store.read_lock()
    assert lock is not None
    if lock.payload["transaction"] != expected_owner:
        fail("transaction owner changed before terminal lock update")
    if lock.payload["state"] != terminal_state:
        lock = transition(store, lock, terminal_state)
    free_payload = lock_payload(
        state="FREE",
        generation=lock.payload["generation"],
        parent_lock_sha=lock.sha,
        initializer_sha=lock.payload["initializer_sha"],
        transaction=None,
    )
    store.advance_lock(lock.sha, free_payload)


def pending_payload(lock: LockSnapshot) -> dict[str, Any]:
    if lock.payload["state"] == "FREE":
        fail("no pending transaction")
    owner = lock.payload["transaction"]
    assert type(owner) is dict
    return {
        "schema_version": 1,
        "kind": "pending",
        "operation": owner["operation"],
        "repository": owner["repository"],
        "workflow_path": owner["workflow_path"],
        "workflow_sha": owner["workflow_sha"],
        "run_id": owner["run_id"],
        "run_attempt": owner["run_attempt"],
        "source_sha": owner["source_sha"],
        "plan_sha256": owner["plan_sha256"],
        "transaction_nonce": owner["transaction_nonce"],
        "transaction_id": owner["transaction_id"],
        "fence": owner["fence"],
        "lock_state": lock.payload["state"],
        "lock_generation": lock.payload["generation"],
        "lock_commit_sha": lock.sha,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "begin",
        "activate",
        "recover-begin",
        "assert-owner",
        "note-failure",
    ):
        command = sub.add_parser(name)
        command.add_argument("plan")
        if name == "note-failure":
            command.add_argument("reason", choices=sorted(FAILURE_REASONS))
    finish = sub.add_parser("finish")
    finish.add_argument("plan")
    finish.add_argument("status", choices=sorted(ALLOWED_FINISH))
    finish.add_argument("--result", required=True)
    sub.add_parser("pending")

    for name in ("prepare-attempt", "prepare-state"):
        command = sub.add_parser(name)
        command.add_argument("plan")
        command.add_argument("minor")
        command.add_argument("kind", choices=sorted(PREPARE_KINDS))
    prepare_complete = sub.add_parser("prepare-complete")
    prepare_complete.add_argument("plan")
    prepare_complete.add_argument("minor")
    prepare_complete.add_argument("kind", choices=sorted(PREPARE_KINDS))
    prepare_complete.add_argument("observed_digest")

    for name in ("attempt", "state", "recovery-attempt", "recovery-state"):
        command = sub.add_parser(name)
        command.add_argument("plan")
        command.add_argument("minor")
        command.add_argument("registry", choices=("dockerhub", "ghcr"))
        if name == "attempt":
            command.add_argument("--subject", action="append")
    for name in ("complete", "recovery-complete"):
        command = sub.add_parser(name)
        command.add_argument("plan")
        command.add_argument("minor")
        command.add_argument("registry", choices=("dockerhub", "ghcr"))
        command.add_argument("observed_digest")
        if name == "complete":
            command.add_argument("--observed-subject", action="append")

    for name in ("referrer-attempt", "referrer-state"):
        command = sub.add_parser(name)
        command.add_argument("plan")
        command.add_argument("minor")
    referrer_complete = sub.add_parser("referrer-complete")
    referrer_complete.add_argument("plan")
    referrer_complete.add_argument("minor")
    referrer_complete.add_argument("observed_digest")

    recovery_referrer_attempt = sub.add_parser("recovery-referrer-attempt")
    recovery_referrer_attempt.add_argument("plan")
    recovery_referrer_attempt.add_argument("minor")
    recovery_referrer_complete = sub.add_parser("recovery-referrer-complete")
    recovery_referrer_complete.add_argument("plan")
    recovery_referrer_complete.add_argument("minor")
    recovery_referrer_complete.add_argument("observed_digest")

    args = parser.parse_args()

    if args.command == "pending":
        store = backend()
        lock = store.read_lock()
        if lock is None or lock.payload["state"] == "FREE":
            raise SystemExit(3)
        print(
            json.dumps(
                pending_payload(lock), separators=(",", ":"), sort_keys=True
            )
        )
        return

    plan, plan_sha256 = load_plan(args.plan)
    store = backend(plan["repository"])

    if args.command == "begin":
        begin_transaction(store, plan, plan_sha256)
        return
    if args.command == "finish":
        finish_transaction(
            store, plan, plan_sha256, args.status, args.result
        )
        return

    if args.command == "activate":
        snapshot, plan_record = require_owner(
            store, plan, plan_sha256, {"PREPARED", "ACTIVE"}
        )
        if snapshot.payload["state"] == "PREPARED":
            require_preparations_complete(
                store, plan, plan_sha256, plan_record
            )
            transition(store, snapshot, "ACTIVE")
        return

    if args.command == "recover-begin":
        snapshot, _ = require_owner(
            store,
            plan,
            plan_sha256,
            {"PREPARED", "ACTIVE", "RECOVERY_REQUIRED", "RECOVERING"},
        )
        if snapshot.payload["state"] != "RECOVERING":
            transition(store, snapshot, "RECOVERING")
        return

    if args.command == "assert-owner":
        require_owner(store, plan, plan_sha256)
        return

    if args.command == "note-failure":
        snapshot, plan_record = require_owner(
            store,
            plan,
            plan_sha256,
            {"PREPARED", "ACTIVE", "RECOVERY_REQUIRED", "RECOVERING", "BLOCKED"},
        )
        payload = {
            **owned_record_base(
                plan, plan_sha256, "failure", plan_record, snapshot
            ),
            "reason": args.reason,
        }
        ensure_exact_record(
            store,
            f"{transaction_prefix(plan, plan_sha256)}/failure/{args.reason}",
            payload,
        )
        desired = (
            "BLOCKED" if args.reason in BLOCKING_FAILURES else "RECOVERY_REQUIRED"
        )
        if snapshot.payload["state"] != desired:
            transition(store, snapshot, desired)
        return

    if args.command.startswith("prepare-"):
        snapshot, plan_record = require_owner(
            store, plan, plan_sha256, {"PREPARED"}
        )
        if args.command == "prepare-state":
            print(
                json.dumps(
                    preparation_state(
                        store,
                        plan,
                        plan_sha256,
                        args.minor,
                        args.kind,
                        plan_record,
                    ),
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return
        spec = prepare_spec(plan, args.minor, args.kind)
        attempt_payload = {
            **owned_record_base(
                plan,
                plan_sha256,
                "prepare-attempt",
                plan_record,
                snapshot,
            ),
            "php_minor": args.minor,
            **spec,
        }
        attempt_reference = prepare_ref(
            plan, plan_sha256, args.kind, args.minor, False
        )
        if args.command == "prepare-attempt":
            ensure_exact_record(store, attempt_reference, attempt_payload)
            return
        state = preparation_state(
            store,
            plan,
            plan_sha256,
            args.minor,
            args.kind,
            plan_record,
        )
        if not state["attempted"]:
            fail("cannot complete preparation without an attempt")
        if DIGEST_RE.fullmatch(args.observed_digest) is None:
            fail("invalid preparation read-back digest")
        if args.observed_digest != spec["expected_digest"]:
            fail("preparation read-back digest mismatch")
        observed_attempt = store.read_record(attempt_reference)
        assert observed_attempt is not None
        result_payload = {
            **observed_attempt,
            "kind": "prepare-result",
            "observed_digest": args.observed_digest,
            "attempt_sha256": hashlib.sha256(
                canonical_bytes(observed_attempt)
            ).hexdigest(),
        }
        ensure_exact_record(
            store,
            prepare_ref(plan, plan_sha256, args.kind, args.minor, True),
            result_payload,
        )
        return

    if args.command in {"attempt", "state", "complete"}:
        publication_states = (
            {"ACTIVE", "RECOVERY_REQUIRED", "RECOVERING"}
            if args.command == "state"
            else {"ACTIVE"}
        )
        snapshot, plan_record = require_owner(
            store, plan, plan_sha256, publication_states
        )
        if args.command == "state":
            print(
                json.dumps(
                    publication_state(
                        store,
                        plan,
                        plan_sha256,
                        args.minor,
                        args.registry,
                        plan_record,
                    ),
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return
        if args.command == "attempt":
            payload = alias_attempt_payload(
                plan,
                plan_sha256,
                args.minor,
                args.registry,
                args.subject,
                plan_record,
                snapshot,
            )
            ensure_exact_record(
                store,
                record_ref(
                    plan, plan_sha256, "attempt", args.minor, args.registry
                ),
                payload,
            )
            return
        state = publication_state(
            store,
            plan,
            plan_sha256,
            args.minor,
            args.registry,
            plan_record,
        )
        if not state["attempted"]:
            fail("cannot complete a transaction unit without an attempt journal")
        if DIGEST_RE.fullmatch(args.observed_digest) is None:
            fail("invalid observed transaction digest")
        if args.observed_digest != state["target_digest"]:
            fail("observed transaction digest does not match the authorized target")
        attempt_reference = record_ref(
            plan, plan_sha256, "attempt", args.minor, args.registry
        )
        attempt = store.read_record(attempt_reference)
        assert attempt is not None
        observed_subjects = args.observed_subject or []
        if observed_subjects:
            parsed: dict[str, str] = {}
            for value in observed_subjects:
                subject, separator, digest = value.rpartition("=")
                if not separator or DIGEST_RE.fullmatch(digest) is None:
                    fail("invalid observed alias subject mapping")
                if subject in parsed:
                    fail("duplicate observed alias subject")
                parsed[subject] = digest
            if set(parsed) != set(attempt["subjects"]):
                fail("observed alias subject set mismatch")
            if set(parsed.values()) != {args.observed_digest}:
                fail("observed alias subject digest mismatch")
        elif len(attempt["subjects"]) == 1:
            parsed = {attempt["subjects"][0]: args.observed_digest}
        else:
            fail("complete GHCR alias batch read-back is required")
        result_payload = {
            **attempt,
            "kind": "result",
            "observed_digest": args.observed_digest,
            "observed_subjects": parsed,
            "attempt_sha256": hashlib.sha256(
                canonical_bytes(attempt)
            ).hexdigest(),
        }
        ensure_exact_record(
            store,
            record_ref(
                plan, plan_sha256, "result", args.minor, args.registry
            ),
            result_payload,
        )
        return

    if args.command in {
        "recovery-attempt",
        "recovery-complete",
        "recovery-state",
    }:
        snapshot, plan_record = require_owner(
            store, plan, plan_sha256, {"ACTIVE", "RECOVERING"}
        )
        publication = publication_state(
            store,
            plan,
            plan_sha256,
            args.minor,
            args.registry,
            plan_record,
        )
        if not publication["attempted"]:
            fail("cannot recover a unit with no durable publication attempt")
        attempt_reference = record_ref(
            plan,
            plan_sha256,
            "recovery-attempt",
            args.minor,
            args.registry,
        )
        result_reference = record_ref(
            plan,
            plan_sha256,
            "recovery-result",
            args.minor,
            args.registry,
        )
        publication_attempt = store.read_record(
            record_ref(
                plan, plan_sha256, "attempt", args.minor, args.registry
            )
        )
        assert publication_attempt is not None
        recovery_attempt = {
            **owned_record_base(
                plan,
                plan_sha256,
                "recovery-attempt",
                plan_record,
                snapshot,
            ),
            "php_minor": args.minor,
            "registry": args.registry,
            "object_kind": "alias-restore",
            "subject": publication_attempt["subjects"][0],
            "previous_digest": publication_attempt["previous_digest"],
            "publication_attempt_sha256": hashlib.sha256(
                canonical_bytes(publication_attempt)
            ).hexdigest(),
        }
        if args.command == "recovery-attempt":
            ensure_exact_record(store, attempt_reference, recovery_attempt)
            return
        observed_attempt = store.read_record(attempt_reference)
        if observed_attempt is None:
            if args.command == "recovery-state":
                print('{"attempted":false,"completed":false}')
                return
            fail("cannot complete recovery without a durable recovery attempt")
        if observed_attempt != recovery_attempt:
            fail("recovery attempt journal mismatch")
        recovery_result = {
            **observed_attempt,
            "kind": "recovery-result",
            "observed_digest": observed_attempt["previous_digest"],
            "attempt_sha256": hashlib.sha256(
                canonical_bytes(observed_attempt)
            ).hexdigest(),
        }
        if args.command == "recovery-complete":
            if DIGEST_RE.fullmatch(args.observed_digest) is None:
                fail("invalid recovery read-back digest")
            if args.observed_digest != observed_attempt["previous_digest"]:
                fail("recovery read-back does not match the frozen prior digest")
            ensure_exact_record(store, result_reference, recovery_result)
            return
        observed_result = store.read_record(result_reference)
        if observed_result is None:
            print('{"attempted":true,"completed":false}')
            return
        if observed_result != recovery_result:
            fail("recovery result journal mismatch")
        print('{"attempted":true,"completed":true}')
        return

    if args.command in {"referrer-attempt", "referrer-state", "referrer-complete"}:
        snapshot, plan_record = require_owner(
            store, plan, plan_sha256, {"ACTIVE"}
        )
        if args.command == "referrer-state":
            print(
                json.dumps(
                    referrer_state(
                        store, plan, plan_sha256, args.minor, plan_record
                    ),
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return
        attempt_reference = referrer_ref(
            plan, plan_sha256, args.minor, False
        )
        attempt_payload = referrer_attempt_payload(
            plan,
            plan_sha256,
            args.minor,
            plan_record,
            snapshot,
        )
        if args.command == "referrer-attempt":
            ensure_exact_record(store, attempt_reference, attempt_payload)
            return
        observed_attempt = store.read_record(attempt_reference)
        if observed_attempt is None:
            fail("cannot complete referrer write without a durable attempt")
        validate_referrer_attempt(
            observed_attempt, plan, plan_sha256, args.minor, plan_record
        )
        if args.observed_digest != observed_attempt["target_digest"]:
            fail("referrer subject digest mismatch")
        result_payload = {
            **observed_attempt,
            "kind": "referrer-result",
            "observed_digest": args.observed_digest,
            "attempt_sha256": hashlib.sha256(
                canonical_bytes(observed_attempt)
            ).hexdigest(),
        }
        ensure_exact_record(
            store,
            referrer_ref(plan, plan_sha256, args.minor, True),
            result_payload,
        )
        return

    if args.command in {
        "recovery-referrer-attempt",
        "recovery-referrer-complete",
    }:
        snapshot, plan_record = require_owner(
            store, plan, plan_sha256, {"ACTIVE", "RECOVERING"}
        )
        attempt_reference = recovery_referrer_ref(
            plan, plan_sha256, args.minor, False
        )
        attempt_payload = recovery_referrer_attempt_payload(
            plan,
            plan_sha256,
            args.minor,
            plan_record,
            snapshot,
        )
        if args.command == "recovery-referrer-attempt":
            ensure_exact_record(store, attempt_reference, attempt_payload)
            return
        observed_attempt = store.read_record(attempt_reference)
        if observed_attempt is None:
            fail("cannot complete recovery referrer without a durable attempt")
        validate_recovery_referrer_attempt(
            observed_attempt, plan, plan_sha256, args.minor, plan_record
        )
        if args.observed_digest != observed_attempt["target_digest"]:
            fail("recovery referrer subject digest mismatch")
        result_payload = {
            **observed_attempt,
            "kind": "recovery-referrer-result",
            "observed_digest": args.observed_digest,
            "attempt_sha256": hashlib.sha256(
                canonical_bytes(observed_attempt)
            ).hexdigest(),
        }
        ensure_exact_record(
            store,
            recovery_referrer_ref(plan, plan_sha256, args.minor, True),
            result_payload,
        )
        return

    fail("unsupported transaction journal command")


if __name__ == "__main__":
    main()
