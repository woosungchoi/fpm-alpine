#!/usr/bin/env python3
"""Strict fake `gh api` implementation for transaction-journal tests."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import NoReturn


def die(message: str, status: int = 1) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(status)


def digest(kind: str, payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha1(kind.encode() + b"\0" + raw).hexdigest()


def save(path: Path, state: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, separators=(",", ":"), sort_keys=True))
    os.replace(temporary, path)


state_path = Path(os.environ["FAKE_GH_STATE"])
calls_path = Path(os.environ["FAKE_GH_CALLS"])
state = (
    json.loads(state_path.read_text())
    if state_path.exists()
    else {"refs": {}, "tags": {}, "blobs": {}, "trees": {}, "commits": {}}
)
args = sys.argv[1:]
if len(args) < 4 or args[0] != "api" or args[1] != "--method":
    die("unsupported fake gh invocation")
method = args[2]
endpoint = args[3]
payload = json.load(sys.stdin) if "--input" in args else None
with calls_path.open("a") as calls:
    calls.write(json.dumps({"method": method, "endpoint": endpoint, "payload": payload}, sort_keys=True) + "\n")

prefix = "repos/woosungchoi/fpm-alpine/"
if not endpoint.startswith(prefix):
    die("HTTP 404: wrong repository")
route = endpoint[len(prefix):]

if method == "DELETE":
    die("DELETE is forbidden by the strict journal fake", 99)

if method == "GET" and route == "branches/fpm-transaction-lock/protection":
    protected = os.environ.get("FAKE_GH_UNPROTECTED") != "1"
    print(json.dumps({
        "allow_force_pushes": {"enabled": not protected},
        "allow_deletions": {"enabled": False},
        "required_linear_history": {"enabled": True},
    }))
    raise SystemExit(0)

if route.startswith("git/ref/") and method == "GET":
    ref = "refs/" + route.removeprefix("git/ref/")
    obj = state["refs"].get(ref)
    if obj is None:
        die("HTTP 404: Not Found")
    print(json.dumps({"ref": ref, "object": obj}))
    raise SystemExit(0)

if route == "git/tags" and method == "POST":
    if type(payload) is not dict:
        die("invalid tag payload")
    sha = digest("tag", payload)
    state["tags"][sha] = {
        "tag": payload["tag"],
        "message": payload["message"],
        "object": {"type": payload["type"], "sha": payload["object"]},
    }
    save(state_path, state)
    print(json.dumps({"sha": sha}))
    raise SystemExit(0)

if route.startswith("git/tags/") and method == "GET":
    sha = route.removeprefix("git/tags/")
    obj = state["tags"].get(sha)
    if obj is None:
        die("HTTP 404: tag missing")
    print(json.dumps(obj))
    raise SystemExit(0)

if route == "git/blobs" and method == "POST":
    if type(payload) is not dict or payload.get("encoding") != "utf-8":
        die("invalid blob payload")
    content = payload.get("content")
    if type(content) is not str:
        die("invalid blob content")
    sha = digest("blob", content)
    state["blobs"][sha] = content
    save(state_path, state)
    print(json.dumps({"sha": sha}))
    raise SystemExit(0)

if route.startswith("git/blobs/") and method == "GET":
    sha = route.removeprefix("git/blobs/")
    content = state["blobs"].get(sha)
    if content is None:
        die("HTTP 404: blob missing")
    print(json.dumps({
        "sha": sha,
        "encoding": "base64",
        "content": base64.b64encode(content.encode()).decode(),
    }))
    raise SystemExit(0)

if route == "git/trees" and method == "POST":
    if type(payload) is not dict or type(payload.get("tree")) is not list:
        die("invalid tree payload")
    sha = digest("tree", payload["tree"])
    state["trees"][sha] = payload["tree"]
    save(state_path, state)
    print(json.dumps({"sha": sha}))
    raise SystemExit(0)

if route.startswith("git/trees/") and method == "GET":
    sha = route.removeprefix("git/trees/")
    tree = state["trees"].get(sha)
    if tree is None:
        die("HTTP 404: tree missing")
    print(json.dumps({"sha": sha, "tree": tree, "truncated": False}))
    raise SystemExit(0)

if route == "git/commits" and method == "POST":
    if (
        type(payload) is not dict
        or type(payload.get("parents")) is not list
        or len(payload["parents"]) != 1
        or payload.get("tree") not in state["trees"]
    ):
        die("invalid commit payload")
    sha = digest("commit", payload)
    state["commits"][sha] = {
        "sha": sha,
        "message": payload["message"],
        "tree": {"sha": payload["tree"]},
        "parents": [{"sha": payload["parents"][0]}],
    }
    save(state_path, state)
    print(json.dumps({"sha": sha}))
    raise SystemExit(0)

if route.startswith("git/commits/") and method == "GET":
    sha = route.removeprefix("git/commits/")
    commit = state["commits"].get(sha)
    if commit is None:
        die("HTTP 404: commit missing")
    print(json.dumps(commit))
    raise SystemExit(0)

if route == "git/refs" and method == "POST":
    if type(payload) is not dict or set(payload) != {"ref", "sha"}:
        die("invalid ref-create payload")
    ref = payload["ref"]
    sha = payload["sha"]
    if ref in state["refs"]:
        die("HTTP 422: Reference already exists")
    if sha in state["tags"]:
        object_type = "tag"
    elif sha in state["commits"]:
        object_type = "commit"
    else:
        die("HTTP 422: unknown object")
    state["refs"][ref] = {"type": object_type, "sha": sha}
    save(state_path, state)
    print(json.dumps({"ref": ref, "object": state["refs"][ref]}))
    raise SystemExit(0)

if route == "git/refs/heads/fpm-transaction-lock" and method == "PATCH":
    if type(payload) is not dict or payload.get("force") is not False:
        die("HTTP 422: lock update must use force=false")
    reject_text = os.environ.get("FAKE_GH_REJECT_NEXT_PATCH")
    if reject_text:
        reject = Path(reject_text)
        if reject.exists():
            reject.unlink()
            die("HTTP 409: injected CAS conflict")
    current = state["refs"].get("refs/heads/fpm-transaction-lock")
    new_sha = payload.get("sha")
    commit = state["commits"].get(new_sha)
    if current is None or commit is None:
        die("HTTP 422: missing lock object")
    if commit["parents"] != [{"sha": current["sha"]}]:
        die("HTTP 409: non-fast-forward exact-parent conflict")
    state["refs"]["refs/heads/fpm-transaction-lock"] = {
        "type": "commit",
        "sha": new_sha,
    }
    save(state_path, state)
    print(json.dumps({
        "ref": "refs/heads/fpm-transaction-lock",
        "object": state["refs"]["refs/heads/fpm-transaction-lock"],
    }))
    raise SystemExit(0)

die(f"unsupported fake GitHub API route: {method} {route}")
