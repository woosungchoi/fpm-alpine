#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${1:-}"
SMOKE_REPORT_DIR="${SMOKE_REPORT_DIR:-smoke-reports}"
SMOKE_REPORT_MD="${SMOKE_REPORT_MD:-${SMOKE_REPORT_DIR}/smoke-test.md}"

if [ -z "$IMAGE_NAME" ]; then
  echo "usage: $0 <image-name>" >&2
  exit 64
fi

mkdir -p "$(dirname "$SMOKE_REPORT_MD")"
: > "$SMOKE_REPORT_MD"

append_summary() {
  printf '%s\n' "$*" >> "$SMOKE_REPORT_MD"
}

container_id=""
cleanup() {
  if [ -n "$container_id" ]; then
    docker rm -f "$container_id" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

run_in_container() {
  docker exec "$container_id" sh -lc "$1"
}

run_check() {
  local name="$1"
  local command="$2"
  printf '\n== %s ==\n' "$name"
  append_summary "- ⏳ ${name}"
  if run_in_container "$command"; then
    # Replace the pending marker with a passing marker while keeping order stable.
    python3 - "$SMOKE_REPORT_MD" "$name" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
name = sys.argv[2]
text = path.read_text()
text = text.replace(f"- ⏳ {name}\n", f"- ✅ {name}\n", 1)
path.write_text(text)
PY
  else
    python3 - "$SMOKE_REPORT_MD" "$name" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
name = sys.argv[2]
text = path.read_text()
text = text.replace(f"- ⏳ {name}\n", f"- ❌ {name}\n", 1)
path.write_text(text)
PY
    echo "smoke check failed: ${name}" >&2
    echo "php-fpm log follows, if available:" >&2
    docker exec "$container_id" sh -lc 'cat /tmp/php-fpm.log 2>/dev/null || true' >&2 || true
    exit 1
  fi
}

cat > "$SMOKE_REPORT_MD" <<EOF
# fpm-alpine smoke test report

- Image: \`${IMAGE_NAME}\`
- Mode: container runtime validation, not publish

## Checks

EOF

container_id="$(docker run -d --rm --entrypoint sh "$IMAGE_NAME" -c 'php-fpm -F >/tmp/php-fpm.log 2>&1 & while :; do sleep 3600; done')"

run_check "php -v" 'php -v'
run_check "php -m" 'php -m | sort'
run_check "php-fpm -t" 'php-fpm -t'
run_check "extension: imagick" "php -r 'if (!extension_loaded(\"imagick\")) { fwrite(STDERR, \"imagick extension missing\\n\"); exit(1); } echo \"imagick=loaded\\n\";'"
run_check "extension: redis" "php -r 'if (!extension_loaded(\"redis\")) { fwrite(STDERR, \"redis extension missing\\n\"); exit(1); } echo \"redis=loaded\\n\";'"
run_check "extension: apcu" "php -r 'if (!extension_loaded(\"apcu\")) { fwrite(STDERR, \"apcu extension missing\\n\"); exit(1); } echo \"apcu=loaded\\n\";'"
run_check "ffmpeg" 'command -v ffmpeg && ffmpeg -version | head -n 1'
run_check "iconv runtime" "php -r 'echo iconv(\"UTF-8\", \"UTF-8\", \"iconv-ok\") . PHP_EOL;'"
run_check "Imagick class runtime" "php -r '\$i = new Imagick(); echo \"imagick-class-ok\\n\";'"

append_summary ""
append_summary "Smoke test passed for \`${IMAGE_NAME}\`."

cat "$SMOKE_REPORT_MD"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$SMOKE_REPORT_MD" >> "$GITHUB_STEP_SUMMARY"
fi

printf '\nSmoke test passed for %s\n' "$IMAGE_NAME"
