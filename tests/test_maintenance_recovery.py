import importlib.util
import pathlib
import unittest
import urllib.error
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class MaintenanceTests(unittest.TestCase):
    def test_transient_read_recovers_and_budget_is_bounded(self):
        m=load('network_retry');op=Mock(side_effect=[TimeoutError(), b'complete'])
        with patch.object(m.time,'sleep'):
            self.assertEqual(m.read_with_retry(op), b'complete')
            bad=Mock(side_effect=TimeoutError())
            with self.assertRaises(TimeoutError):m.read_with_retry(bad)
            self.assertEqual(bad.call_count,3)

    def test_permanent_read_error_is_not_retried(self):
        m=load('network_retry')
        for error in [urllib.error.HTTPError('https://example.test',404,'missing',{},None),ValueError('checksum mismatch')]:
            op=Mock(side_effect=error)
            with self.assertRaises(type(error)):m.read_with_retry(op)
            self.assertEqual(op.call_count,1)

    def test_retry_never_accepts_publisher_stale_foreign_or_exhausted_run(self):
        m=load('retry_dependency_ci');repo='woosungchoi/fpm-alpine'
        run=dict(event='pull_request',path='.github/workflows/smoke-test.yml',status='completed',conclusion='failure',run_attempt=1,head_repository={'full_name':repo},head_sha='a'*40,head_branch='automation/base-8.2-fixture')
        pr=dict(state='open',draft=False,user={'login':'fpm-alpine-dependency-updater[bot]'},head={'sha':'a'*40,'ref':run['head_branch'],'repo':{'full_name':repo}})
        self.assertTrue(m.eligible(run,pr,repo))
        for changes in [{'event':'push'},{'run_attempt':3},{'path':'.github/workflows/publish.yml'},{'head_sha':'b'*40},{'head_repository':{'full_name':'other/fpm-alpine'}}]:
            self.assertFalse(m.eligible(run|changes,pr,repo))
        self.assertFalse(m.eligible(run,pr|{'state':'closed'},repo))
        self.assertFalse(m.eligible(run,pr|{'user':{'login':'someone'}},repo))

    def test_retry_requires_all_nonaggregate_failures_to_be_transient(self):
        m=load('retry_dependency_ci');jobs=[{'id':1,'name':'arm64','conclusion':'failure'},{'id':2,'name':'docker-smoke','conclusion':'failure'}]
        self.assertTrue(m.retryable(jobs,{1:'i/o timeout'}))
        self.assertFalse(m.retryable(jobs,{1:'i/o timeout\nimage contract rejected: drift'}))
        self.assertFalse(m.retryable(jobs+ [{'id':3,'name':'amd64','conclusion':'failure'}],{1:'i/o timeout',3:'compiler error'}))
        self.assertFalse(m.retryable([],{}))

    def test_scheduler_health_fails_without_recent_success(self):
        from datetime import datetime, timezone
        m=load('retry_dependency_ci')
        for rows in [[], [{'conclusion':'failure','created_at':datetime.now(timezone.utc).isoformat()}], [{'conclusion':'success','created_at':'2020-01-01T00:00:00Z'}]]:
            with patch.object(m,'api',return_value={'workflow_runs':rows}) as api:
                # GitHub endpoint returns workflow_runs, not runs.
                with self.assertRaises(SystemExit):m.health('woosungchoi/fpm-alpine')
        with patch.object(m,'api',return_value={'workflow_runs':[{'conclusion':'success','created_at':datetime.now(timezone.utc).isoformat()}]}):
            m.health('woosungchoi/fpm-alpine')
