#!/usr/bin/env python3
"""Retry transient dependency PR checks only; never replay a publisher."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import re
import subprocess

TRANSIENT = re.compile(r"i/o timeout|TLS handshake timeout|connection reset by peer|Temporary failure in name resolution|503 Service Unavailable|502 Bad Gateway|504 Gateway Timeout|curl: \(22\) The requested URL returned error: (?:429|500|502|503|504)\b", re.I)
PERMANENT = re.compile(r"image contract rejected|checksum mismatch|sha256sum:.*(?:did NOT match|FAILED)|FAILED \(.*failures|AssertionError|undefined reference|fatal error:|CVE-\d{4}-\d+", re.I)


def eligible(run, pr, repo):
    return (run.get('event') == 'pull_request' and run.get('path') == '.github/workflows/smoke-test.yml'
            and run.get('status') == 'completed' and run.get('conclusion') == 'failure'
            and type(run.get('run_attempt')) is int and 1 <= run['run_attempt'] < 3
            and (run.get('head_repository') or {}).get('full_name') == repo
            and pr.get('state') == 'open' and not pr.get('draft')
            and pr.get('head', {}).get('sha') == run.get('head_sha')
            and pr.get('head', {}).get('repo', {}).get('full_name') == repo
            and pr.get('head', {}).get('ref') == run.get('head_branch')
            and ((repo.endswith('/fpm-alpine') and pr.get('user', {}).get('login') == 'fpm-alpine-dependency-updater[bot]' and run.get('head_branch', '').startswith('automation/'))
                 or (repo.endswith('/docker-nginx-brotli') and pr.get('user', {}).get('login') == 'docker-nginx-brotli-automation[bot]' and run.get('head_branch') == 'ci/update-pinned-versions')))


def retryable(jobs, logs):
    failed = [j for j in jobs if j.get('conclusion') == 'failure' and j.get('name') != 'docker-smoke']
    return bool(failed) and all(TRANSIENT.search(logs.get(j['id'], '')) and not PERMANENT.search(logs.get(j['id'], '')) for j in failed)


def api(path, method='GET'):
    return json.loads(subprocess.check_output(['gh', 'api', '--method', method, path], text=True))


def health(repo):
    workflow = 'dependency-update-pr.yml' if repo.endswith('/fpm-alpine') else 'update-versions.yml'
    runs = api(f'repos/{repo}/actions/workflows/{workflow}/runs?event=schedule&per_page=10')['workflow_runs']
    successful = [r for r in runs if r.get('conclusion') == 'success']
    now = datetime.now(timezone.utc)
    fresh = bool(successful) and (now - datetime.fromisoformat(successful[0]['created_at'].replace('Z','+00:00'))).total_seconds() < 48*3600
    summary = f"Dependency scheduler: {'healthy' if fresh else 'STALE: no successful scheduled check in 48 hours'}\n"
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as out:out.write(summary)
    if not fresh:raise SystemExit(1)


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    if os.environ.get('MAINTENANCE_MODE') == 'health':
        health(repo); return
    run_id = os.environ['UPSTREAM_RUN_ID']
    if not re.fullmatch(r'woosungchoi/(fpm-alpine|docker-nginx-brotli)', repo) or not run_id.isdigit():
        raise SystemExit('invalid recovery scope')
    run = api(f'repos/{repo}/actions/runs/{run_id}')
    # Events sometimes omit pull_requests: search only this repository's exact branch.
    prs = api(f'repos/{repo}/pulls?state=open&per_page=100')
    selected = [p for p in prs if eligible(run, p, repo)]
    if len(selected) != 1:
        print('No current trusted dependency PR eligible for retry.'); return
    pages = json.loads(subprocess.check_output(['gh','api','--paginate','--slurp',f'repos/{repo}/actions/runs/{run_id}/jobs?per_page=100'],text=True))
    jobs = [j for page in pages for j in page['jobs']]
    logs = {j['id']: subprocess.check_output(['gh','run','view',run_id,'--repo',repo,'--job',str(j['id']),'--log-failed'],text=True) for j in jobs if j.get('conclusion')=='failure' and j.get('name') != 'docker-smoke'}
    if not retryable(jobs, logs):
        print('Failure is not exclusively transient; retain evidence for review.'); return
    # Revalidate both attempt and current head immediately before the bounded retry.
    fresh = api(f'repos/{repo}/actions/runs/{run_id}')
    pr = api(f'repos/{repo}/pulls/{selected[0]["number"]}')
    if fresh.get('run_attempt') != run['run_attempt'] or not eligible(fresh, pr, repo):
        raise SystemExit('run or PR changed during recovery')
    subprocess.run(['gh','api','--method','POST',f'repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs'],check=True)
    print(f'Requested bounded retry {run["run_attempt"]+1}/3 for current dependency PR checks.')


if __name__ == '__main__':
    main()
