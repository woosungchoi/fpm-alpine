#!/usr/bin/env python3
import json
import re
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <repository@sha256:index-digest> <os/architecture>")

    subject, expected_platform = sys.argv[1:]
    if not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", subject):
        raise SystemExit("subject must be an exact digest-qualified image index")
    if not re.fullmatch(r"[a-z0-9]+/[a-z0-9_]+", expected_platform):
        raise SystemExit("platform must be os/architecture")

    inspected = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "--raw", subject],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if inspected.returncode != 0:
        if inspected.stderr:
            print(inspected.stderr, file=sys.stderr, end="")
        raise SystemExit(inspected.returncode)

    try:
        index = json.loads(inspected.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(f"image index is not valid JSON: {error}") from error

    matches: list[str] = []
    for descriptor in index.get("manifests", []):
        platform = descriptor.get("platform") or {}
        actual_platform = f"{platform.get('os', '')}/{platform.get('architecture', '')}"
        if actual_platform == expected_platform:
            digest = descriptor.get("digest", "")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise SystemExit(f"invalid {expected_platform} manifest digest")
            matches.append(digest)

    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly one {expected_platform} manifest descriptor, found {len(matches)}"
        )

    repository = subject.rsplit("@", 1)[0]
    print(f"{repository}@{matches[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
