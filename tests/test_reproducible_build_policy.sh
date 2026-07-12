#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$repo_root"
fail() { echo "FAIL: $*" >&2; exit 1; }
assert_contains() { grep -Fq -- "$2" "$1" || fail "expected $1 to contain: $2"; }
assert_not_contains() { ! grep -Fq -- "$2" "$1" || fail "expected $1 not to contain: $2"; }

./scripts/validate-versions.py
[ "$(./scripts/validate-versions.py --get-base 8.5)" = "php:8.5-fpm-alpine@sha256:79def1d16ece3ab1a6656c46a23bfd80ad33887fbd33626e7bd743cef54ef9c6" ] || fail "--get-base mismatch"
python3 - <<'PY'
import json, subprocess
rows=json.loads(subprocess.check_output(["./scripts/validate-versions.py", "--matrix"]))["include"]
versions = {
 "8.2": ("8.2.32", "php:8.2-fpm-alpine@sha256:41ddda74d95c43518c3e4414e6c1c99f9c062d397f0c7a2d8cadf8d1f035d196"),
 "8.3": ("8.3.32", "php:8.3-fpm-alpine@sha256:9fcec48321d890240d700ccdc2b475420c87d398826e68c3d8830b8fca663e5c"),
 "8.4": ("8.4.23", "php:8.4-fpm-alpine@sha256:913ddd6934a805429618a16aa36da47cd8a8aec8b2f111c294936ba4003fded6"),
 "8.5": ("8.5.8", "php:8.5-fpm-alpine@sha256:79def1d16ece3ab1a6656c46a23bfd80ad33887fbd33626e7bd743cef54ef9c6"),
}
deps = {
 "imagick": ("3.8.1", "https://pecl.php.net/get/imagick-3.8.1.tgz", "3a3587c0a524c17d0dad9673a160b90cd776e836838474e173b549ed864352ee"),
 "redis": ("6.3.0", "https://pecl.php.net/get/redis-6.3.0.tgz", "0d5141f634bd1db6c1ddcda053d25ecf2c4fc1c395430d534fd3f8d51dd7f0b5"),
 "apcu": ("5.1.28", "https://pecl.php.net/get/apcu-5.1.28.tgz", "ca9c1820810a168786f8048a4c3f8c9e3fd941407ad1553259fb2e30b5f057bf"),
}
iconv={"iconv_implementation":"libiconv","iconv_version":"1.18","iconv_package":"gnu-libiconv-libs","iconv_package_version":"1.18-r0","iconv_owner_path":"/usr/lib/libiconv.so.2","iconv_target":"/usr/lib/libiconv.so.2.7.0"}
expected=[]
for minor in ("8.2", "8.3", "8.4", "8.5"):
 for platform, arch in (("linux/amd64", "amd64"), ("linux/arm64", "arm64")):
  patch, base = versions[minor]
  row={"php_minor":minor,"php_patch":patch,"php_base_image":base,"platform":platform,"arch":arch}
  for name,(version,url,sha) in deps.items():
   row.update({f"{name}_version":version,f"{name}_url":url,f"{name}_sha256":sha})
  row.update(iconv); expected.append(row)
assert rows == expected, (rows, expected)
PY

mutation_dir="$(mktemp -d)"; trap 'rm -rf "$mutation_dir"' EXIT
expect_mutation_invalid() {
  local description="$1" code="$2"
  python3 - "$code" build/versions.json "$mutation_dir/versions.json" <<'PY'
import json, sys
code, source, target = sys.argv[1:]
data=json.load(open(source)); exec(code, {"data": data}); json.dump(data, open(target,"w"))
PY
  if ./scripts/validate-versions.py "$mutation_dir/versions.json" >/dev/null 2>&1; then fail "validator accepted mutation: $description"; fi
}
expect_mutation_invalid "missing schemaVersion" 'del data["schemaVersion"]'
expect_mutation_invalid "extra root field" 'data["extra"]=1'
expect_mutation_invalid "wrong schemaVersion" 'data["schemaVersion"]=1'
expect_mutation_invalid "boolean schemaVersion" 'data["schemaVersion"]=True'
expect_mutation_invalid "extra version field" 'data["versions"]["8.4"]["extra"]=1'
expect_mutation_invalid "extra dependency field" 'data["dependencies"]["imagick"]["extra"]=1'
expect_mutation_invalid "wrong minor field" 'data["versions"]["8.4"]["minor"]="8.3"'
expect_mutation_invalid "wrong patch minor" 'data["versions"]["8.4"]["patch"]="8.5.24"'
expect_mutation_invalid "wrong dependency URL" 'data["dependencies"]["redis"]["url"]="https://example.com/redis-6.3.0.tgz"'
expect_mutation_invalid "duplicate base ref" 'data["versions"]["8.3"]["base_image"]=data["versions"]["8.2"]["base_image"]'
expect_mutation_invalid "wrong support" 'data["versions"]["8.4"]["support"]="security-only"'
expect_mutation_invalid "missing eol" 'del data["versions"]["8.2"]["eol"]'
expect_mutation_invalid "wrong eol" 'data["versions"]["8.5"]["eol"]="2030-01-01"'
expect_mutation_invalid "EOL minor insertion" 'data["versions"]={"8.1":{"minor":"8.1","patch":"8.1.99","base_image":"php:8.1-fpm-alpine@sha256:"+"0"*64,"support":"security-only","eol":"2025-12-31"},**data["versions"]}'
expect_mutation_invalid "wrong order" 'data["versions"]={k:data["versions"][k] for k in ("8.3","8.2","8.4","8.5")}'
expect_mutation_invalid "bad digest" 'data["versions"]["8.2"]["base_image"]="php:8.2-fpm-alpine@sha256:bad"'
expect_mutation_invalid "floating dependency" 'data["dependencies"]["redis"]["version"]="latest"'
expect_mutation_invalid "missing runtime contract" 'del data["runtimeContracts"]'
expect_mutation_invalid "wrong iconv package version" 'data["runtimeContracts"]["libiconv"]["packageVersion"]="1.18-r1"'
expect_mutation_invalid "wrong iconv target" 'data["runtimeContracts"]["libiconv"]["target"]="/usr/lib/libiconv.so.2.8.0"'

python3 - <<'PY'
from pathlib import Path
import re
text=Path("Dockerfile").read_text()
assert not re.search(r"curl[^\n]*(?:\||&&)\s*tar\b", text), "curl-pipe-tar is forbidden"
for artifact in ("/usr/src/vendor/imagick.tgz", "/usr/src/vendor/redis.tgz", "/usr/src/vendor/apcu.tgz"):
    downloads=[m.start() for m in re.finditer(r"curl[^\n]*-o\s+"+re.escape(artifact)+r"(?:[;\s]|$)", text)]
    checks=[m.start() for m in re.finditer(re.escape(artifact)+r'[^\n]*\|\s*sha256sum\s+-c', text)]
    extracts=[m.start() for m in re.finditer(r"tar[^\n]*"+re.escape(artifact), text)]
    assert len(downloads)==len(checks)==len(extracts)==1, (artifact, downloads, checks, extracts)
    assert downloads[0] < checks[0] < extracts[0], artifact
PY

python3 - <<'PY'
from pathlib import Path
import re
workflow=Path('.github/workflows/smoke-test.yml').read_text()
trigger=workflow.split('\npermissions:',1)[0]
assert '  push:\n    branches: ["main"]\n' in trigger
assert '  pull_request:\n' in trigger
assert '  pull_request:\n    branches:' not in trigger
assert '  workflow_dispatch:\n' in trigger
assert trigger.count('  push:\n') == 1
def steps(text):
    starts=list(re.finditer(r'^\s{6}- name:\s*(.+?)\s*$', text, re.M))
    return [(m.group(1), text[m.start():starts[i+1].start() if i+1<len(starts) else len(text)]) for i,m in enumerate(starts)]
def workflow_policy(text):
    assert '  docker-smoke-matrix:\n    name: docker-smoke-matrix (${{ matrix.php_minor }} / ${{ matrix.arch }})\n' in text
    aggregate=re.search(r'^  docker-smoke:\n(?P<body>(?:(?!^  \S).*(?:\n|$))*)', text, re.M)
    assert aggregate, 'docker-smoke aggregate job'
    aggregate=aggregate.group('body')
    for value in ('name: docker-smoke','if: ${{ always() }}','needs: [dependency-safety, docker-smoke-matrix]','permissions: {}','SAFETY_RESULT: ${{ needs.dependency-safety.result }}','MATRIX_RESULT: ${{ needs.docker-smoke-matrix.result }}','test "$SAFETY_RESULT" = success','test "$MATRIX_RESULT" = success'):
        assert value in aggregate, value
    for forbidden in ('actions/checkout','docker/','secrets.','build','artifact','registry'):
        assert forbidden not in aggregate, forbidden
    blocks=steps(text)
    def one(name):
        found=[body for title,body in blocks if title==name]
        assert len(found)==1, name
        return found[0]
    qemu=one('Set up QEMU'); assert 'docker/setup-qemu-action@' in qemu
    build=one('Build source-only smoke image')
    required=(
      'platforms: ${{ matrix.platform }}','load: true','push: false',
      'PHP_BASE_IMAGE=${{ matrix.php_base_image }}','IMAGICK_URL=${{ matrix.imagick_url }}','IMAGICK_SHA256=${{ matrix.imagick_sha256 }}',
      'REDIS_URL=${{ matrix.redis_url }}','REDIS_SHA256=${{ matrix.redis_sha256 }}','APCU_URL=${{ matrix.apcu_url }}','APCU_SHA256=${{ matrix.apcu_sha256 }}',

      'OCI_SOURCE=${{ github.server_url }}/${{ github.repository }}','OCI_REVISION=${{ github.sha }}','OCI_VERSION=${{ matrix.php_patch }}','OCI_CREATED=${{ needs.prepare.outputs.created }}')
    for value in required: assert value in build, value
    smoke=one('Run smoke test under target platform')
    for value in ('EXPECTED_PHP_MINOR: ${{ matrix.php_minor }}','EXPECTED_PLATFORM: ${{ matrix.platform }}','EXPECTED_IMAGICK_VERSION: ${{ matrix.imagick_version }}','EXPECTED_REDIS_VERSION: ${{ matrix.redis_version }}','EXPECTED_APCU_VERSION: ${{ matrix.apcu_version }}','EXPECTED_ICONV_IMPLEMENTATION: ${{ matrix.iconv_implementation }}','EXPECTED_ICONV_VERSION: ${{ matrix.iconv_version }}','EXPECTED_ICONV_PACKAGE: ${{ matrix.iconv_package }}','EXPECTED_ICONV_PACKAGE_VERSION: ${{ matrix.iconv_package_version }}','EXPECTED_ICONV_OWNER_PATH: ${{ matrix.iconv_owner_path }}','EXPECTED_ICONV_TARGET: ${{ matrix.iconv_target }}'):
        assert value in smoke, value
    tag='fpm-alpine:smoke-${{ matrix.php_minor }}-${{ matrix.arch }}'
    assert f'tags: {tag}' in build
    assert f'SMOKE_IMAGE: {tag}' in smoke
    assert './scripts/smoke-test-image.sh "$SMOKE_IMAGE"' in smoke
    for required_step in ('Run policy and mutation tests','Replay pinned source checksums','Build reproducibility probe image','Require reproducible local image','Compare package and runtime contract with published baseline','Scan source-only image'):
        one(required_step)
    upload=one('Upload smoke and dependency-safety reports')
    assert 'actions/upload-artifact@' in upload
    for report_path in ('smoke-reports/','contract-reports/','scan-reports/','reproducibility-reports/'):
        assert report_path in upload, report_path
workflow_policy(workflow)
mutations=[]
def remove_step(name):
    blocks=steps(workflow); body=next(body for title,body in blocks if title==name); return workflow.replace(body,'',1)
mutations += [remove_step('Set up QEMU'), remove_step('Upload smoke and dependency-safety reports'), remove_step('Run policy and mutation tests'), remove_step('Require reproducible local image'), remove_step('Compare package and runtime contract with published baseline'), remove_step('Scan source-only image')]
aggregate_start=workflow.index('  docker-smoke:\n')
mutations.append(workflow[:aggregate_start])
mutations.append(workflow.replace('needs: [dependency-safety, docker-smoke-matrix]','needs: prepare',1))
mutations.append(workflow.replace('if: ${{ always() }}','if: ${{ success() }}',1))
mutations.append(workflow.replace('test "$MATRIX_RESULT" = success','exit 0',1))
for field in ('load: true','push: false','platforms: ${{ matrix.platform }}','smoke-reports/'):
 mutations.append(workflow.replace(field,'',1))
for old,new in (
 ('load: true','load: false'),('push: false','push: true'),('platforms: ${{ matrix.platform }}','platforms: linux/amd64'),
 ('./scripts/smoke-test-image.sh "$SMOKE_IMAGE"','./scripts/smoke-test-image.sh wrong-tag'),('smoke-reports/','elsewhere/')):
 mutations.append(workflow.replace(old,new,1))
for field in ('EXPECTED_PHP_MINOR: ${{ matrix.php_minor }}','EXPECTED_PLATFORM: ${{ matrix.platform }}','EXPECTED_IMAGICK_VERSION: ${{ matrix.imagick_version }}','EXPECTED_REDIS_VERSION: ${{ matrix.redis_version }}','EXPECTED_APCU_VERSION: ${{ matrix.apcu_version }}','EXPECTED_ICONV_IMPLEMENTATION: ${{ matrix.iconv_implementation }}','EXPECTED_ICONV_VERSION: ${{ matrix.iconv_version }}','EXPECTED_ICONV_PACKAGE: ${{ matrix.iconv_package }}','EXPECTED_ICONV_PACKAGE_VERSION: ${{ matrix.iconv_package_version }}','EXPECTED_ICONV_OWNER_PATH: ${{ matrix.iconv_owner_path }}','EXPECTED_ICONV_TARGET: ${{ matrix.iconv_target }}'):
 mutations.append(workflow.replace(field,'',1))
for arg in ('PHP_BASE_IMAGE','IMAGICK_URL','IMAGICK_SHA256','REDIS_URL','REDIS_SHA256','APCU_URL','APCU_SHA256','OCI_SOURCE','OCI_REVISION','OCI_VERSION','OCI_CREATED'):
    mutations.append(re.sub(r'^\s+'+arg+r'=.*\n','',workflow,count=1,flags=re.M))
    mutations.append(re.sub(r'(^\s+'+arg+r'=).+$',r'\1changed',workflow,count=1,flags=re.M))
for i,mutated in enumerate(mutations):
    try: workflow_policy(mutated)
    except AssertionError: continue
    raise AssertionError(f'workflow mutation {i} was accepted')
PY

assert_not_contains Dockerfile "alpine/edge"
assert_not_contains Dockerfile "--allow-untrusted"
assert_not_contains Dockerfile 'LIBICONV_URL'
assert_not_contains Dockerfile 'LIBICONV_SHA256'
assert_contains Dockerfile 'apk info -e gnu-libiconv-libs=1.18-r0'
assert_contains Dockerfile 'apk info -W /usr/lib/libiconv.so.2'
assert_contains Dockerfile '/usr/lib/libiconv.so.2.7.0'
assert_contains Dockerfile 'apk audit --system /usr/lib'
assert_contains Dockerfile 'apk audit failed with status $auditRc'
assert_contains Dockerfile "^[^[:space:]]+[[:space:]]+usr/lib/(libiconv|libcharset)\\.so([./]|$)"
assert_contains Dockerfile 'ICONV_IMPL !== "libiconv"'
assert_contains Dockerfile 'ICONV_VERSION !== "1.18"'
assert_contains Dockerfile "ldd /usr/local/bin/php"
assert_contains Dockerfile 'php-fpm -t'
assert_contains Dockerfile 'stripos($result, "caf") !== 0'
for file in Dockerfile scripts/smoke-test-image.sh .github/workflows/smoke-test.yml; do
  assert_not_contains "$file" 'LD_PRELOAD'
  assert_not_contains "$file" 'preloadable_libiconv.so'
done
audit_pattern='^[^[:space:]]+[[:space:]]+usr/lib/(libiconv|libcharset)\.so([./]|$)'
printf '%s\n' 'U usr/lib/libiconv.so.2.7.0' 'A usr/lib/libcharset.so.1' | grep -Eq "$audit_pattern"
! printf '%s\n' 'U etc/passwd' 'A usr/lib/libzip.so.5' | grep -Eq "$audit_pattern"
if sh -c 'apk() { return 2; }; iconvAudit="$(apk audit --system /usr/lib)" || { auditRc=$?; exit "$auditRc"; }; ! printf "%s\n" "$iconvAudit" | grep -Eq "$1"' sh "$audit_pattern"; then
  fail "iconv audit expression accepted an apk audit command failure"
fi
python3 - <<'PY'
from pathlib import Path
import re
active='\n'.join(Path(path).read_text() for path in ('Dockerfile', '.github/workflows/smoke-test.yml', 'scripts/smoke-test-image.sh'))
for pattern in (
    r'LIBICONV_(?:URL|SHA)', r'curl[^\n]*libiconv', r'libiconv[^\n]*(?:configure|make install)',
    r'apk\s+(?:mkpkg|index)', r'apk[^\n]*(?:sign|--allow-untrusted)', r'file://|/etc/apk/repositories',
    r'rm[^\n]*/usr/lib/libiconv\.so\.2\.7\.0', r'LD_PRELOAD', r'preloadable_libiconv',
):
    assert not re.search(pattern, active, re.I), pattern
PY
for key in source revision version created; do assert_contains Dockerfile "org.opencontainers.image.${key}"; done
for entry in .git worktrees/ reports/ artifacts/ smoke-reports/ '__pycache__' '*.swp'; do assert_contains .dockerignore "$entry"; done
assert_not_contains .dockerignore "Dockerfile"
assert_not_contains .dockerignore "build/"
assert_not_contains .dockerignore "scripts/"
assert_contains .github/workflows/dependency-freshness.yml 'VERSIONS_PATH: build/versions.json'
assert_not_contains .github/workflows/dependency-freshness.yml 'PHP_TAGS:'
assert_contains scripts/report-freshness.sh 'python3 "$VALIDATOR_PATH" "$VERSIONS_PATH"'
assert_contains scripts/report-freshness.sh 'DIGEST_RESOLVER_PATH="${DIGEST_RESOLVER_PATH:-scripts/resolve-image-digest.sh}"'
assert_contains scripts/report-freshness.sh '"$DIGEST_RESOLVER_PATH" "$1"'
assert_not_contains scripts/report-freshness.sh "awk '/^Digest:/"
assert_not_contains scripts/report-freshness.sh 'IMAGICK_VERSION'
assert_not_contains scripts/report-freshness.sh 'allow-untrusted'
assert_not_contains scripts/report-freshness.sh 'alpine/edge'
echo "reproducible build policy tests passed"
