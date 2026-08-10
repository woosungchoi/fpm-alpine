#!/usr/bin/env bash
set -euo pipefail

DOCKERHUB_SUBJECT="${1:-}"
GHCR_SUBJECT="${2:-}"
PHP_MINOR="${3:-}"
REPORT_DIR="${4:-rollback-reports}"
PLATFORMS=(linux/amd64 linux/arm64)

if [[ ! "$DOCKERHUB_SUBJECT" =~ @sha256:[0-9a-f]{64}$ ]] || \
   [[ ! "$GHCR_SUBJECT" =~ @sha256:[0-9a-f]{64}$ ]] || \
   [[ ! "$PHP_MINOR" =~ ^8\.[0-5]$ ]]; then
  echo "usage: $0 <dockerhub@sha256:digest> <ghcr@sha256:digest> <8.0-8.5> [report-dir]" >&2
  exit 64
fi

mkdir -p "$REPORT_DIR/manifests" "$REPORT_DIR/smoke"
for subject in "$DOCKERHUB_SUBJECT" "$GHCR_SUBJECT"; do
  PUBLISHER_MODE=github-actions MANIFEST_REPORT_DIR="$REPORT_DIR/manifests" \
    ./scripts/report-manifest.sh "$subject" "${PLATFORMS[@]}"
done

for entry in "dockerhub|$DOCKERHUB_SUBJECT" "ghcr|$GHCR_SUBJECT"; do
  IFS='|' read -r registry subject <<< "$entry"
  for platform in "${PLATFORMS[@]}"; do
    platform_subject="$(./scripts/resolve-platform-image.py "$subject" "$platform")"
    report_path="$REPORT_DIR/smoke/${registry}-${platform//\//-}.md"
    # The inner script expands only inside the container.
    # shellcheck disable=SC2016
    ./scripts/run-qemu-runtime-with-retry.sh "$platform" "$report_path" -- \
      docker run --rm --platform "$platform" \
      -e EXPECTED_PHP_MINOR="$PHP_MINOR" \
      --entrypoint sh "$platform_subject" -ec '
        test "$(php -r '\''echo PHP_MAJOR_VERSION, ".", PHP_MINOR_VERSION;'\'')" = "$EXPECTED_PHP_MINOR"
        php -r '\''foreach (["imagick", "redis", "apcu"] as $extension) { if (!extension_loaded($extension)) { fwrite(STDERR, "missing extension: $extension\n"); exit(1); } }'\''
        php -r '\''$output = iconv("UTF-8", "ASCII//TRANSLIT", "café"); if ($output === false || preg_match("/[^\\x20-\\x7E]/", $output)) { fwrite(STDERR, "iconv transliteration failed\n"); exit(1); }'\''
        php-fpm -t
        php-fpm -F >/tmp/php-fpm-rollback.log 2>&1 &
        fpm_pid=$!
        fpm_ready=0
        for attempt in $(seq 1 40); do
          if php -r '\''$socket = @fsockopen("127.0.0.1", 9000, $errno, $errstr, 0.2); if ($socket === false) { exit(1); } fclose($socket);'\''; then
            fpm_ready=1
            break
          fi
          if ! kill -0 "$fpm_pid" 2>/dev/null; then
            cat /tmp/php-fpm-rollback.log >&2
            exit 1
          fi
          sleep 0.25
        done
        if [ "$fpm_ready" -ne 1 ]; then
          cat /tmp/php-fpm-rollback.log >&2
          kill "$fpm_pid" 2>/dev/null || true
          wait "$fpm_pid" 2>/dev/null || true
          exit 1
        fi
        kill "$fpm_pid"
        wait "$fpm_pid" 2>/dev/null || true
        ffmpeg -version >/dev/null
      '
    cat > "$report_path" <<EOF
# Rollback compatibility smoke

- Index subject: \`$subject\`
- Platform subject: \`$platform_subject\`
- Platform: \`$platform\`
- PHP minor: \`$PHP_MINOR\`
- Required extensions: imagick, redis, apcu
- Status: passed
EOF
  done
done

echo "rollback registry-local manifest and runtime verification passed"
