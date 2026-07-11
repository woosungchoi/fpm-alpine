#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$repo_root"
fail() { echo "FAIL: $*" >&2; exit 1; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat > "$tmp/bin/docker" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
{
  printf '%s\n' '---'
  printf '%s\n' "$@"
} >> "$DOCKER_LOG"
case "${1:-}" in
  run) printf '%s\n' mock-container ;;
  inspect)
    if [ "${MOCK_NOT_READY:-}" ]; then printf '%s\n' false; else printf '%s\n' true; fi
    ;;
  logs)
    if [ -z "${MOCK_NOT_READY:-}" ]; then printf '%s\n' 'NOTICE: ready to handle connections'; fi
    ;;
  exec)
    if [ "${MOCK_FAIL_EXEC_CONTAINS:-}" ] && [[ "${*: -1}" == *"$MOCK_FAIL_EXEC_CONTAINS"* ]]; then exit 1; fi
    exit 0
    ;;
  rm) exit 0 ;;
  *) exit 64 ;;
esac
MOCK
chmod +x "$tmp/bin/docker"
printf '#!/usr/bin/env bash\nexit 0\n' > "$tmp/bin/sleep"
chmod +x "$tmp/bin/sleep"
for platform in linux/amd64 linux/arm64; do
  arch="${platform#linux/}"
  log="$tmp/docker-$arch.log"
  report="$tmp/report-$arch.md"
  DOCKER_LOG="$log" PATH="$tmp/bin:$PATH" \
    EXPECTED_PHP_MINOR=8.5 EXPECTED_PLATFORM="$platform" \
    EXPECTED_IMAGICK_VERSION=3.8.1 EXPECTED_REDIS_VERSION=6.3.0 EXPECTED_APCU_VERSION=5.1.28 \
    EXPECTED_ICONV_IMPLEMENTATION=libiconv EXPECTED_ICONV_VERSION=1.18 EXPECTED_ICONV_PACKAGE=gnu-libiconv-libs EXPECTED_ICONV_PACKAGE_VERSION=1.18-r0 EXPECTED_ICONV_OWNER_PATH=/usr/lib/libiconv.so.2 EXPECTED_ICONV_TARGET=/usr/lib/libiconv.so.2.7.0 \
    SMOKE_REPORT_MD="$report" ./scripts/smoke-test-image.sh "fixture:$arch" >/dev/null
  python3 - "$log" "$platform" <<'PY'
from pathlib import Path
import sys
log, platform = sys.argv[1:]
calls=[part.strip().splitlines() for part in Path(log).read_text().split('---') if part.strip()]
run=[c for c in calls if c[0]=='run']; assert len(run)==1
assert '--platform' in run[0] and run[0][run[0].index('--platform')+1]==platform
assert 'fixture:'+platform.split('/')[1] in run[0]
assert '--entrypoint' in run[0] and run[0][run[0].index('--entrypoint')+1]=='php-fpm'
assert run[0][-1]=='-F'
assert [c for c in calls if c[0]=='inspect']
assert [c for c in calls if c[0]=='logs']
commands=[c[-1] for c in calls if c[0]=='exec' and len(c)>=5 and c[-3:-1]==['sh','-lc']]
need=('PHP_MAJOR_VERSION','PHP_MINOR_VERSION','8.5','php-fpm -t',
      'phpversion("imagick")','3.8.1','phpversion("redis")','6.3.0','phpversion("apcu")','5.1.28',
      'ICONV_IMPL','libiconv','ICONV_VERSION','1.18','gnu-libiconv-libs=1.18-r0','apk info -W','/usr/lib/libiconv.so.2','/usr/lib/libiconv.so.2.7.0','readlink -f','apk audit --system /usr/lib','usr/lib/(libiconv|libcharset)','ldd /usr/local/bin/php','not found','ASCII//TRANSLIT','café','caf','new Imagick()')
for value in need: assert any(value in command for command in commands), value
assert not any('LD_PRELOAD' in command or 'preloadable_libiconv.so' in command for command in commands)
architecture='x86_64' if platform=='linux/amd64' else 'aarch64|arm64'
assert any('uname -m' in c and architecture in c for c in commands)
cleanup=[c for c in calls if c[0]=='rm']; assert len(cleanup)==1
assert cleanup[0][1:]==['-f','mock-container']
PY
done
readiness_failure_log="$tmp/docker-readiness-failure.log"
readiness_failure_report="$tmp/report-readiness-failure.md"
if DOCKER_LOG="$readiness_failure_log" MOCK_NOT_READY=1 PATH="$tmp/bin:$PATH" \
  EXPECTED_PHP_MINOR=8.5 EXPECTED_PLATFORM=linux/amd64 \
  EXPECTED_IMAGICK_VERSION=3.8.1 EXPECTED_REDIS_VERSION=6.3.0 EXPECTED_APCU_VERSION=5.1.28 \
  EXPECTED_ICONV_IMPLEMENTATION=libiconv EXPECTED_ICONV_VERSION=1.18 EXPECTED_ICONV_PACKAGE=gnu-libiconv-libs EXPECTED_ICONV_PACKAGE_VERSION=1.18-r0 EXPECTED_ICONV_OWNER_PATH=/usr/lib/libiconv.so.2 EXPECTED_ICONV_TARGET=/usr/lib/libiconv.so.2.7.0 \
  SMOKE_REPORT_MD="$readiness_failure_report" ./scripts/smoke-test-image.sh fixture:not-ready >/dev/null 2>&1; then
  fail "smoke script accepted a container that never became ready"
fi
grep -Fq -- '- ❌ php-fpm process ready' "$readiness_failure_report" || fail "readiness failure report was not recorded"
python3 - "$readiness_failure_log" <<'PY'
from pathlib import Path
import sys
calls=[part.strip().splitlines() for part in Path(sys.argv[1]).read_text().split('---') if part.strip()]
assert len([call for call in calls if call[0]=='inspect']) == 40
assert [call for call in calls if call[0]=='rm'] == [['rm', '-f', 'mock-container']]
PY
for missing in EXPECTED_ICONV_IMPLEMENTATION EXPECTED_ICONV_VERSION EXPECTED_ICONV_PACKAGE EXPECTED_ICONV_PACKAGE_VERSION EXPECTED_ICONV_OWNER_PATH EXPECTED_ICONV_TARGET; do
  args=(EXPECTED_PHP_MINOR=8.5 EXPECTED_PLATFORM=linux/amd64 EXPECTED_IMAGICK_VERSION=3.8.1 EXPECTED_REDIS_VERSION=6.3.0 EXPECTED_APCU_VERSION=5.1.28 EXPECTED_ICONV_IMPLEMENTATION=libiconv EXPECTED_ICONV_VERSION=1.18 EXPECTED_ICONV_PACKAGE=gnu-libiconv-libs EXPECTED_ICONV_PACKAGE_VERSION=1.18-r0 EXPECTED_ICONV_OWNER_PATH=/usr/lib/libiconv.so.2 EXPECTED_ICONV_TARGET=/usr/lib/libiconv.so.2.7.0)
  for i in "${!args[@]}"; do [[ "${args[$i]}" == "$missing="* ]] && args[$i]="$missing="; done
  if env DOCKER_LOG="$tmp/missing-$missing.log" PATH="$tmp/bin:$PATH" "${args[@]}" SMOKE_REPORT_MD="$tmp/missing-$missing.md" ./scripts/smoke-test-image.sh fixture:missing >/dev/null 2>&1; then
    fail "smoke script accepted missing $missing"
  fi
done
failure_log="$tmp/docker-failure.log"
failure_report="$tmp/report-failure.md"
if DOCKER_LOG="$failure_log" MOCK_FAIL_EXEC_CONTAINS='php-fpm -t' PATH="$tmp/bin:$PATH" \
  EXPECTED_PHP_MINOR=8.5 EXPECTED_PLATFORM=linux/amd64 \
  EXPECTED_IMAGICK_VERSION=3.8.1 EXPECTED_REDIS_VERSION=6.3.0 EXPECTED_APCU_VERSION=5.1.28 \
  EXPECTED_ICONV_IMPLEMENTATION=libiconv EXPECTED_ICONV_VERSION=1.18 EXPECTED_ICONV_PACKAGE=gnu-libiconv-libs EXPECTED_ICONV_PACKAGE_VERSION=1.18-r0 EXPECTED_ICONV_OWNER_PATH=/usr/lib/libiconv.so.2 EXPECTED_ICONV_TARGET=/usr/lib/libiconv.so.2.7.0 \
  SMOKE_REPORT_MD="$failure_report" ./scripts/smoke-test-image.sh fixture:failure >/dev/null 2>&1; then
  fail "smoke script accepted a failed php-fpm -t check"
fi
grep -Fq -- '- ❌ php-fpm -t' "$failure_report" || fail "failure report did not record php-fpm -t"
python3 - "$failure_log" <<'PY'
from pathlib import Path
import sys
calls=[part.strip().splitlines() for part in Path(sys.argv[1]).read_text().split('---') if part.strip()]
cleanup=[call for call in calls if call[0]=='rm']
assert cleanup == [['rm', '-f', 'mock-container']], cleanup
PY
audit_failure_report="$tmp/report-audit-failure.md"
if DOCKER_LOG="$tmp/docker-audit-failure.log" MOCK_FAIL_EXEC_CONTAINS='apk audit --system /usr/lib' PATH="$tmp/bin:$PATH" \
  EXPECTED_PHP_MINOR=8.5 EXPECTED_PLATFORM=linux/amd64 \
  EXPECTED_IMAGICK_VERSION=3.8.1 EXPECTED_REDIS_VERSION=6.3.0 EXPECTED_APCU_VERSION=5.1.28 \
  EXPECTED_ICONV_IMPLEMENTATION=libiconv EXPECTED_ICONV_VERSION=1.18 EXPECTED_ICONV_PACKAGE=gnu-libiconv-libs EXPECTED_ICONV_PACKAGE_VERSION=1.18-r0 EXPECTED_ICONV_OWNER_PATH=/usr/lib/libiconv.so.2 EXPECTED_ICONV_TARGET=/usr/lib/libiconv.so.2.7.0 \
  SMOKE_REPORT_MD="$audit_failure_report" ./scripts/smoke-test-image.sh fixture:audit-failure >/dev/null 2>&1; then
  fail "smoke script accepted a failed apk audit check"
fi
grep -Fq -- '- ❌ iconv package audit' "$audit_failure_report" || fail "failure report did not record apk audit failure"
echo "smoke script execution tests passed"
