#!/usr/bin/env bash
# Exercise real negative paths against an already built image, with no host ports.
set -euo pipefail
image="${1:?image required}"
: "${EXPECTED_PHP_PATCH:?}" "${EXPECTED_PLATFORM:?}"
: "${EXPECTED_IMAGICK_VERSION:?}" "${EXPECTED_REDIS_VERSION:?}" "${EXPECTED_APCU_VERSION:?}"
root="$(cd "$(dirname "$0")" && pwd)"
report="${SMOKE_REPORT_DIR:-smoke-reports}/mutations"
mkdir -p "$report"
tmp="$(mktemp -d)"; container=""
cleanup() {
  if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
  rm -rf "$tmp"
}
trap cleanup EXIT
cat > "$tmp/fpm-listener.conf" <<'CONF'
[global]
error_log = /proc/self/fd/2
[www]
user = www-data
group = www-data
listen = 9001
pm = static
pm.max_children = 1
CONF
for mutation in listener codec ini; do
  args=(-F)
  if [ "$mutation" = listener ]; then args+=(-y /tmp/fpm-listener.conf); fi
  if [ "$mutation" = ini ]; then args+=(-d opcache.jit=disable); fi
  # Copy before startup via create/start so the listener config is ready.
  container="$(docker create --platform "$EXPECTED_PLATFORM" --network none --entrypoint php-fpm "$image" "${args[@]}")"
  docker cp "$tmp/fpm-listener.conf" "$container:/tmp/fpm-listener.conf"
  cp "$root/fixtures/fpm-runtime.php" "$tmp/fpm-runtime.php"
  if [ "$mutation" = codec ]; then sed -i 's/libx264/encoder-does-not-exist/' "$tmp/fpm-runtime.php"; fi
  docker cp "$tmp/fpm-runtime.php" "$container:/tmp/fpm-runtime.php"
  docker cp "$root/fixtures/fastcgi-client.php" "$container:/tmp/fastcgi-client.php"
  docker start "$container" >/dev/null
  docker exec "$container" chmod 644 /tmp/fpm-runtime.php /tmp/fastcgi-client.php
  ready=false
  for _ in {1..40}; do
    if docker logs "$container" 2>&1 | grep -Fq 'ready to handle connections'; then ready=true; break; fi
    sleep 0.25
  done
  [ "$ready" = true ] || { docker logs "$container" >&2; exit 1; }
  if docker exec "$container" php /tmp/fastcgi-client.php "$EXPECTED_PHP_PATCH" "$EXPECTED_IMAGICK_VERSION" "$EXPECTED_REDIS_VERSION" "$EXPECTED_APCU_VERSION" > "$report/$mutation.log" 2>&1; then
    echo "runtime mutation was accepted: $mutation" >&2; exit 1
  fi
  case "$mutation" in
    listener) grep -Fq 'FPM listener:' "$report/$mutation.log" ;;
    codec) grep -Fq 'mp4 conversion failed:' "$report/$mutation.log" ;;
    ini) grep -Fq 'FPM ini mismatch: opcache.jit' "$report/$mutation.log" ;;
  esac
  docker rm -f "$container" >/dev/null; container=""
  echo "runtime mutation rejected: $mutation"
done
