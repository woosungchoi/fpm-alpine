#!/usr/bin/env python3
"""Fail-closed decision classifier for future post-publish recovery wiring."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DETERMINISTIC_POST_PUBLISH = {
    "runtime-contract",
    "manifest-readback",
    "signature",
    "provenance",
    "vulnerability",
}


def _result(action: str, rollback: bool, reason: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "action": action,
        "rollbackAuthorized": rollback,
        "reason": reason,
    }


def classify(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        return _result("invalid", False, "evidence root must be an object")
    if (
        type(evidence.get("schemaVersion")) is not int
        or evidence.get("schemaVersion") != 1
    ):
        return _result("invalid", False, "schemaVersion must be integer 1")
    if type(evidence.get("registryMutationStarted")) is not bool:
        return _result("invalid", False, "registryMutationStarted must be boolean")
    if type(evidence.get("previousDigestValid")) is not bool:
        return _result("invalid", False, "previousDigestValid must be boolean")
    source = evidence.get("sourceCommit")
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        return _result("invalid", False, "sourceCommit is invalid")
    stage = evidence.get("stage")
    failure = evidence.get("failureClass")
    if stage not in {"pre-publish", "post-publish"} or not isinstance(failure, str):
        return _result("invalid", False, "stage or failureClass is invalid")
    if stage == "pre-publish" and evidence["registryMutationStarted"] is False:
        return _result("stop", False, "failure occurred before registry mutation")
    if stage != "post-publish" or evidence["registryMutationStarted"] is not True:
        return _result("freeze", False, "mutation state is ambiguous")
    if failure not in DETERMINISTIC_POST_PUBLISH:
        return _result(
            "freeze", False, "failure is transient, policy-related, or unknown"
        )
    if evidence["previousDigestValid"] is not True:
        return _result("freeze", False, "previous immutable digest is not validated")
    return _result(
        "rollback",
        True,
        "deterministic post-publish failure with validated prior digest",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    result = classify(json.loads(Path(args.input).read_text()))
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"recovery_action={result['action']} rollback={str(result['rollbackAuthorized']).lower()}"
    )
    return 0 if result["action"] != "invalid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
