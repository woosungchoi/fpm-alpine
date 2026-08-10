#!/usr/bin/env bash
set -euo pipefail

platform="${1:-}"
report_path="${2:-}"
separator="${3:-}"
if [[ ! "$platform" =~ ^linux/(amd64|arm64)$ ]] || [ -z "$report_path" ] || [ "$separator" != -- ]; then
  echo "usage: $0 <linux/amd64|linux/arm64> <report-path> -- <runtime-command> [args...]" >&2
  exit 64
fi
shift 3
[ "$#" -gt 0 ] || { echo "runtime command is required" >&2; exit 64; }

attempts="${QEMU_RUNTIME_RETRY_ATTEMPTS:-3}"
base_delay="${QEMU_RUNTIME_RETRY_BASE_DELAY_SECONDS:-2}"
[[ "$attempts" =~ ^[1-5]$ ]] || {
  echo "QEMU_RUNTIME_RETRY_ATTEMPTS must be between 1 and 5" >&2
  exit 64
}
[[ "$base_delay" =~ ^([0-9]|[12][0-9]|30)$ ]] || {
  echo "QEMU_RUNTIME_RETRY_BASE_DELAY_SECONDS must be between 0 and 30" >&2
  exit 64
}

marker="qemu-aarch64: QEMU internal SIGSEGV"
for ((attempt = 1; attempt <= attempts; attempt++)); do
  rm -f -- "$report_path"
  set +e
  output="$("$@" 2>&1)"
  status=$?
  set -e
  if [ -n "$output" ]; then
    printf '%s\n' "$output"
  fi
  if [ "$status" -eq 0 ]; then
    exit 0
  fi

  transient=0
  if [ "$platform" = linux/arm64 ] && \
     { [ "$status" -eq 139 ] || [[ "$output" == *"$marker"* ]]; }; then
    transient=1
  fi
  if [ "$transient" -ne 1 ] || [ "$attempt" -eq "$attempts" ]; then
    rm -f -- "$report_path"
    exit "$status"
  fi

  delay=$((base_delay * (1 << (attempt - 1))))
  echo "QEMU runtime SIGSEGV; retrying attempt $((attempt + 1))/${attempts} after ${delay}s"
  if [ "$delay" -gt 0 ]; then
    sleep "$delay"
  fi
done

exit 1
