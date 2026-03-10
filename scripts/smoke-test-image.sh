#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${1:-}"
if [ -z "$IMAGE_NAME" ]; then
  echo "usage: $0 <image-name>" >&2
  exit 64
fi

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

container_id="$(docker run -d --rm --entrypoint sh "$IMAGE_NAME" -c 'php-fpm -F >/tmp/php-fpm.log 2>&1 & while :; do sleep 3600; done')"

printf '\n== php -v ==\n'
run_in_container 'php -v'

printf '\n== php -m ==\n'
run_in_container 'php -m | sort'

printf '\n== php-fpm -t ==\n'
run_in_container 'php-fpm -t'

printf '\n== extension load checks ==\n'
run_in_container "php -r 'foreach ([\"imagick\",\"redis\",\"apcu\"] as \$ext) { if (!extension_loaded(\$ext)) { fwrite(STDERR, \$ext . \" extension missing\\n\"); exit(1); } echo \$ext . \"=loaded\\n\"; }'"

printf '\n== ffmpeg ==\n'
run_in_container 'command -v ffmpeg && ffmpeg -version | head -n 1'

printf '\n== iconv ==\n'
run_in_container "php -r 'echo iconv(\"UTF-8\", \"UTF-8\", \"iconv-ok\") . PHP_EOL;'"

printf '\n== imagick runtime ==\n'
run_in_container "php -r '\$i = new Imagick(); echo \"imagick-class-ok\\n\";'"

printf '\nSmoke test passed for %s\n' "$IMAGE_NAME"
