#!/usr/bin/env bash
set -euo pipefail

image="${1:?image reference required}"
platform="${2:?platform required}"
output="${3:?output path required}"
[[ "$platform" =~ ^linux/(amd64|arm64)$ ]] || {
  echo "platform must be linux/amd64 or linux/arm64" >&2
  exit 64
}

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

docker run --rm --platform "$platform" --entrypoint sh "$image" -c 'apk info | sort -u' \
  > "$tmp_dir/packages"
docker run --rm --platform "$platform" --entrypoint php "$image" -r 'echo PHP_VERSION;' \
  > "$tmp_dir/php-version"
docker run --rm --platform "$platform" --entrypoint php "$image" -m \
  | sed '/^\[/d;/^$/d' | sort -u > "$tmp_dir/modules"
docker run --rm --platform "$platform" --entrypoint php "$image" -r \
  'echo json_encode(["implementation" => ICONV_IMPL, "version" => ICONV_VERSION], JSON_UNESCAPED_SLASHES);' \
  > "$tmp_dir/iconv"
if docker run --rm --platform "$platform" --entrypoint php-fpm "$image" -t >/dev/null 2>&1; then
  printf 'true\n' > "$tmp_dir/fpm-valid"
else
  printf 'false\n' > "$tmp_dir/fpm-valid"
fi

python3 - "$tmp_dir" "$platform" "$output" <<'PY'
import json, sys
from pathlib import Path
root, platform, output = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
def lines(name):
    return sorted(set(filter(None, root.joinpath(name).read_text().splitlines())))
data = {
    "schemaVersion": 1,
    "platform": platform,
    "phpVersion": root.joinpath("php-version").read_text().strip(),
    "packages": lines("packages"),
    "modules": lines("modules"),
    "iconv": json.loads(root.joinpath("iconv").read_text()),
    "fpmConfigValid": root.joinpath("fpm-valid").read_text().strip() == "true",
}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(data, indent=2) + "\n")
PY
