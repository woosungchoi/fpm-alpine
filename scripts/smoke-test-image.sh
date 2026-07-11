#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${1:-}"
EXPECTED_PHP_MINOR="${2:-${EXPECTED_PHP_MINOR:-}}"
EXPECTED_PLATFORM="${3:-${EXPECTED_PLATFORM:-}}"
EXPECTED_IMAGICK_VERSION="${EXPECTED_IMAGICK_VERSION:-}"
EXPECTED_REDIS_VERSION="${EXPECTED_REDIS_VERSION:-}"
EXPECTED_APCU_VERSION="${EXPECTED_APCU_VERSION:-}"
EXPECTED_ICONV_IMPLEMENTATION="${EXPECTED_ICONV_IMPLEMENTATION:-}"
EXPECTED_ICONV_VERSION="${EXPECTED_ICONV_VERSION:-}"
EXPECTED_ICONV_PACKAGE="${EXPECTED_ICONV_PACKAGE:-}"
EXPECTED_ICONV_PACKAGE_VERSION="${EXPECTED_ICONV_PACKAGE_VERSION:-}"
EXPECTED_ICONV_OWNER_PATH="${EXPECTED_ICONV_OWNER_PATH:-}"
EXPECTED_ICONV_TARGET="${EXPECTED_ICONV_TARGET:-}"
SMOKE_REPORT_DIR="${SMOKE_REPORT_DIR:-smoke-reports}"
SMOKE_REPORT_MD="${SMOKE_REPORT_MD:-${SMOKE_REPORT_DIR}/smoke-test.md}"

if [ -z "$IMAGE_NAME" ]; then
  echo "usage: $0 <image-name> [expected-php-minor] [expected-platform]" >&2
  exit 64
fi

mkdir -p "$(dirname "$SMOKE_REPORT_MD")"
: > "$SMOKE_REPORT_MD"

append_summary() {
  printf '%s\n' "$*" >> "$SMOKE_REPORT_MD"
}

update_check() {
  local name="$1"
  local marker="$2"
  python3 - "$SMOKE_REPORT_MD" "$name" "$marker" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
name = sys.argv[2]
marker = sys.argv[3]
text = path.read_text()
text = text.replace(f"- ⏳ {name}\n", f"- {marker} {name}\n", 1)
path.write_text(text)
PY
}

container_id=""
cleanup() {
  if [ -n "$container_id" ]; then
    docker rm -f "$container_id" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

wait_for_fpm() {
  local attempt=0
  local running=""
  printf '\n== php-fpm process ready ==\n'
  append_summary "- ⏳ php-fpm process ready"
  while [ "$attempt" -lt 40 ]; do
    running="$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || true)"
    if [ "$running" = true ] && docker logs "$container_id" 2>&1 | grep -Fq 'ready to handle connections'; then
      update_check "php-fpm process ready" "✅"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 0.25
  done
  update_check "php-fpm process ready" "❌"
  echo "smoke check failed: php-fpm process ready" >&2
  docker logs "$container_id" >&2 || true
  return 1
}

run_in_container() {
  docker exec "$container_id" sh -lc "$1"
}

run_check() {
  local name="$1"
  local command="$2"
  printf '\n== %s ==\n' "$name"
  append_summary "- ⏳ ${name}"
  if run_in_container "$command"; then
    update_check "$name" "✅"
  else
    update_check "$name" "❌"
    echo "smoke check failed: ${name}" >&2
    echo "container log follows, if available:" >&2
    docker logs "$container_id" >&2 || true
    exit 1
  fi
}

cat > "$SMOKE_REPORT_MD" <<EOF
# fpm-alpine smoke test report

- Image: \`${IMAGE_NAME}\`
- Mode: container runtime validation, not publish

## Checks

EOF

docker_platform_args=()
if [ -n "$EXPECTED_PLATFORM" ]; then
  docker_platform_args=(--platform "$EXPECTED_PLATFORM")
fi
container_id="$(docker run -d --rm "${docker_platform_args[@]}" --entrypoint php-fpm "$IMAGE_NAME" -F)"

wait_for_fpm
run_check "php -v" 'php -v'
if [ -n "$EXPECTED_PHP_MINOR" ]; then
  run_check "PHP minor: ${EXPECTED_PHP_MINOR}" "php -r 'if (PHP_MAJOR_VERSION . \".\" . PHP_MINOR_VERSION !== \"${EXPECTED_PHP_MINOR}\") { fwrite(STDERR, \"unexpected PHP minor: \" . PHP_MAJOR_VERSION . \".\" . PHP_MINOR_VERSION . PHP_EOL); exit(1); }'"
fi
if [ -n "$EXPECTED_PLATFORM" ]; then
  case "$EXPECTED_PLATFORM" in
    linux/amd64) expected_uname="x86_64" ;;
    linux/arm64) expected_uname="aarch64|arm64" ;;
    *) echo "unsupported expected platform: $EXPECTED_PLATFORM" >&2; exit 64 ;;
  esac
  run_check "architecture: ${EXPECTED_PLATFORM}" "uname -m | grep -Ex '${expected_uname}'"
fi
run_check "php -m" 'php -m | sort'
run_check "php-fpm -t" 'php-fpm -t'
for extension in imagick redis apcu; do
  case "$extension" in
    imagick) expected_version="$EXPECTED_IMAGICK_VERSION" ;;
    redis) expected_version="$EXPECTED_REDIS_VERSION" ;;
    apcu) expected_version="$EXPECTED_APCU_VERSION" ;;
  esac
  [ -n "$expected_version" ] || { echo "missing expected version for $extension" >&2; exit 64; }
  run_check "extension: ${extension} ${expected_version}" "php -r '\$actual = phpversion(\"${extension}\"); if (\$actual !== \"${expected_version}\") { fwrite(STDERR, \"unexpected ${extension} version: \" . var_export(\$actual, true) . PHP_EOL); exit(1); }'"
done
for value in EXPECTED_ICONV_IMPLEMENTATION EXPECTED_ICONV_VERSION EXPECTED_ICONV_PACKAGE EXPECTED_ICONV_PACKAGE_VERSION EXPECTED_ICONV_OWNER_PATH EXPECTED_ICONV_TARGET; do
  [ -n "${!value}" ] || { echo "missing expected iconv runtime field: $value" >&2; exit 64; }
done
run_check "iconv implementation/version" "php -r 'if (ICONV_IMPL !== \"${EXPECTED_ICONV_IMPLEMENTATION}\" || ICONV_VERSION !== \"${EXPECTED_ICONV_VERSION}\") { fwrite(STDERR, \"unexpected iconv runtime: \" . ICONV_IMPL . \" \" . ICONV_VERSION . PHP_EOL); exit(1); }'"
run_check "iconv package" "apk info -e '${EXPECTED_ICONV_PACKAGE}=${EXPECTED_ICONV_PACKAGE_VERSION}'"
run_check "iconv package ownership" "[ \"\$(apk info -W '${EXPECTED_ICONV_OWNER_PATH}')\" = '${EXPECTED_ICONV_OWNER_PATH} is owned by ${EXPECTED_ICONV_PACKAGE}-${EXPECTED_ICONV_PACKAGE_VERSION}' ]"
run_check "iconv link target" "[ \"\$(readlink -f '${EXPECTED_ICONV_OWNER_PATH}')\" = '${EXPECTED_ICONV_TARGET}' ]"
run_check "iconv package audit" "iconvAudit=\"\$(apk audit --system /usr/lib)\" || { auditRc=\$?; echo \"apk audit failed with status \$auditRc\" >&2; exit \"\$auditRc\"; }; ! printf '%s\\n' \"\$iconvAudit\" | grep -E '^[^[:space:]]+[[:space:]]+usr/lib/(libiconv|libcharset)\\.so([./]|$)'"
run_check "PHP iconv linkage" "phpLdd=\"\$(ldd /usr/local/bin/php)\"; printf '%s\\n' \"\$phpLdd\"; ! printf '%s\\n' \"\$phpLdd\" | grep 'not found'; printf '%s\\n' \"\$phpLdd\" | grep -F '${EXPECTED_ICONV_OWNER_PATH}'"
run_check "ffmpeg" 'command -v ffmpeg && ffmpeg -version | head -n 1'
run_check "iconv transliteration" "php -r '\$value = iconv(\"UTF-8\", \"ASCII//TRANSLIT\", \"café\"); if (\$value === false || stripos(\$value, \"caf\") !== 0 || !preg_match(\"/^[\\x00-\\x7F]+$/\", \$value)) { fwrite(STDERR, \"iconv transliteration failed\\n\"); exit(1); } echo \$value . PHP_EOL;'"
run_check "Imagick class runtime" "php -r '\$i = new Imagick(); echo \"imagick-class-ok\\n\";'"

append_summary ""
append_summary "Smoke test passed for \`${IMAGE_NAME}\`."

cat "$SMOKE_REPORT_MD"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$SMOKE_REPORT_MD" >> "$GITHUB_STEP_SUMMARY"
fi

printf '\nSmoke test passed for %s\n' "$IMAGE_NAME"
