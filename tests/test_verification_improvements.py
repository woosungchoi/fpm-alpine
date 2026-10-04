#!/usr/bin/env python3
"""Executable regressions for pinned reads, build inputs and verification failures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]

class Improvements(unittest.TestCase):
    def test_local_build_uses_all_manifest_pins(self):
        manifest = json.loads((ROOT/'build/versions.json').read_text())
        for minor in manifest['versions']:
            result = subprocess.run([str(ROOT/'scripts/build-local-image.py'), '--minor', minor,
                                     '--platform', 'linux/arm64', '--tag', 'fixture:local', '--dry-run'],
                                    text=True, capture_output=True, check=True)
            payload=json.loads(result.stdout); command=payload['command']
            self.assertIn('PHP_BASE_IMAGE='+manifest['versions'][minor]['base_image'], command)
            self.assertEqual(payload['phpPatch'], manifest['versions'][minor]['patch'])
            for name, item in manifest['dependencies'].items():
                for key in ('url','sha256'):
                    self.assertIn(f'{name}_{key}'.upper()+'='+item[key], command)
        text=(ROOT/'Dockerfile').read_text()
        self.assertIn('\nARG PHP_BASE_IMAGE\nFROM ${PHP_BASE_IMAGE}', text)
        for name in ('PHP_BASE_IMAGE','IMAGICK_URL','IMAGICK_SHA256','REDIS_URL','REDIS_SHA256','APCU_URL','APCU_SHA256'):
            self.assertNotIn('ARG '+name+'=', text, 'stale defaults must not reappear')
        self.assertNotIn('opcache.fast_shutdown', text)
        self.assertNotIn('log_errors_max_len', text)

    def test_manifest_freezes_tag_even_when_raw_retry_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp=Path(raw); (tmp/'bin').mkdir()
            docker=tmp/'bin/docker'
            docker.write_text('''#!/usr/bin/env python3
import os,sys,json
from pathlib import Path
args=sys.argv[1:]; root=Path(os.environ['FIXTURE_ROOT'])
with (root/'calls').open('a') as f: f.write(json.dumps(args)+'\\n')
if '--raw' not in args:
    print('Digest: sha256:'+ 'a'*64)
else:
    if not (root/'retry').exists():
        (root/'retry').touch(); sys.exit(1)
    print(json.dumps({'manifests':[{'platform':{'os':'linux','architecture':arch},'digest':'sha256'+'b'*64} for arch in ('amd64','arm64')]}))
'''); docker.chmod(0o755)
            env={**os.environ,'PATH':str(tmp/'bin')+':'+os.environ['PATH'], 'FIXTURE_ROOT':str(tmp),
                 'MANIFEST_REPORT_DIR':str(tmp/'reports'),'MANIFEST_RETRY_DELAY_SECONDS':'0'}
            subprocess.run([str(ROOT/'scripts/report-manifest.sh'), 'registry.test/image:tag'], env=env, check=True, stdout=subprocess.DEVNULL)
            calls=[json.loads(line) for line in (tmp/'calls').read_text().splitlines()]
            self.assertEqual(sum('registry.test/image:tag' in call for call in calls),1)
            for call in calls:
                if '--raw' in call: self.assertEqual(call[-1], 'registry.test/image@sha256:'+'a'*64)

    def test_full_read_only_verifier_propagates_each_gate_failure(self):
        for failure in ('none','verify-published-dockerhub-image.sh','verify-image-parity.py','scan-image.sh','capture-image-contract.sh'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as raw:
                tmp=Path(raw); (tmp/'scripts').mkdir(); (tmp/'bin').mkdir()
                shutil.copy(ROOT/'scripts/verify-maintained-runtime.sh', tmp/'scripts')
                for name in ('resolve-image-digest.sh','resolve-platform-image.py','verify-published-dockerhub-image.sh','verify-image-parity.py','scan-image.sh','capture-image-contract.sh'):
                    p=tmp/'scripts'/name
                    p.write_text('''#!/usr/bin/env python3
import os,sys,json
from pathlib import Path
name=Path(sys.argv[0]).name
with open(os.environ['TRACE'],'a') as f: f.write(json.dumps([name,*sys.argv[1:]])+'\\n')
if name=='resolve-image-digest.sh': print('sha256:'+'a'*64)
if name=='resolve-platform-image.py': print('docker.io/woosungchoi/fpm-alpine@sha256:'+'b'*64)
if name==os.environ['FAIL_GATE']: sys.exit(1)
'''); p.chmod(0o755)
                git=tmp/'bin/git'; git.write_text('''#!/usr/bin/env python3
import sys,json
if sys.argv[1]=='show': print(json.dumps({'versions':{'8.5':{'patch':'8.5.11'}}}))
'''); git.chmod(0o755)
                docker=tmp/'bin/docker'; docker.write_text('''#!/usr/bin/env python3
import json
labels={'org.opencontainers.image.revision':'b'*40,'org.opencontainers.image.version':'8.5.11'}
print(json.dumps({p:{'config':{'Labels':labels}} for p in ('linux/amd64','linux/arm64')}))
'''); docker.chmod(0o755)
                env={**os.environ,'PATH':str(tmp/'bin')+':'+os.environ['PATH'],'TRACE':str(tmp/'trace'),'FAIL_GATE':failure}
                result=subprocess.run([str(tmp/'scripts/verify-maintained-runtime.sh'),'8.5'],cwd=tmp,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                self.assertEqual(result.returncode == 0, failure == 'none', result.stderr)
                calls=[json.loads(row) for row in (tmp/'trace').read_text().splitlines()]
                resolutions=[row for row in calls if row[0]=='resolve-image-digest.sh']
                self.assertEqual(len(resolutions),2)
                for row in calls:
                    if row[0] in ('verify-published-dockerhub-image.sh','verify-image-parity.py','capture-image-contract.sh'):
                        self.assertIn('@sha256:',row[1])

    def test_lifecycle_issue_deduplicates_day_counts_and_updates_state(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp=Path(raw); (tmp/'bin').mkdir()
            p=tmp/'bin/gh'
            p.write_text("""#!/usr/bin/env python3
import os,json,sys
from pathlib import Path
root=Path(os.environ['FIXTURE_ROOT']); args=sys.argv[1:]
with (root/'calls').open('a') as f: f.write(json.dumps(args)+'\\n')
if args[:2]==['issue','list']: print('123' if (root/'body').exists() else '')
if args[:2]==['issue','view']: print((root/'body').read_text())
if args[:2] in (['issue','create'],['issue','edit']):
    (root/'body').write_text(Path(args[args.index('--body-file')+1]).read_text())
"""); p.chmod(0o755)
            env={**os.environ,'PATH':str(tmp/'bin')+':'+os.environ['PATH'],'FIXTURE_ROOT':str(tmp),'GH_TOKEN':'fixture'}
            for state,days in [('warning-90',90),('warning-90',89),('warning-30',30)]:
                (tmp/'report.json').write_text(json.dumps({'records':[{'minor':'8.2','eol':'2026-12-31','support':'security-only','state':state,'daysUntilEol':days}]}))
                (tmp/'report.md').write_text(f'{state} {days} days')
                subprocess.run([str(ROOT/'scripts/create-php-lifecycle-issue.sh'),str(tmp/'report.json'),str(tmp/'report.md')],env=env,check=True,stdout=subprocess.DEVNULL)
            calls=[json.loads(row) for row in (tmp/'calls').read_text().splitlines()]
            self.assertEqual(sum(row[:2]==['issue','create'] for row in calls),1)
            self.assertEqual(sum(row[:2]==['issue','edit'] for row in calls),1)
            self.assertEqual(sum(row[:2]==['issue','comment'] for row in calls),0)

    def test_lifecycle_and_runtime_workflow_scopes(self):
        lifecycle=yaml.safe_load((ROOT/'.github/workflows/php-lifecycle.yml').read_text())
        trigger=lifecycle.get('on',lifecycle.get(True))
        self.assertIn({'cron':'19 5 * * *'},trigger['schedule'])
        run=next(s for s in lifecycle['jobs']['lifecycle']['steps'] if s.get('id')=='lifecycle')['run']
        self.assertIn('--skip-upstream',run)
        workflow=yaml.safe_load((ROOT/'.github/workflows/published-runtime-smoke.yml').read_text())
        self.assertEqual(workflow['permissions'],{'contents':'read'})
        text=(ROOT/'.github/workflows/published-runtime-smoke.yml').read_text()
        for forbidden in ('secrets.','login-action','packages: write','id-token: write','push: true'):
            self.assertNotIn(forbidden,text)
        self.assertIn('verify-maintained-runtime.sh',text)

if __name__ == '__main__': unittest.main(verbosity=2)
