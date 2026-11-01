#!/usr/bin/env python3
"""Network failures retry; trust/format failures never become healthy results."""
import importlib.util
import io
import json
import socket
import ssl
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('lifecycle', Path(__file__).resolve().parents[1] / 'scripts/check-php-lifecycle.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class UpstreamRetry(unittest.TestCase):
    def fetch(self, responses):
        with patch.object(module.urllib.request, 'urlopen', side_effect=responses) as request, patch.object(module.time, 'sleep') as sleep:
            result = module.fetch_upstream('8.2', 'https://www.php.net/releases/index.php?json&version=')
            return result, request, sleep

    def test_connection_reset_recovers(self):
        version, request, sleep = self.fetch([ConnectionResetError(104, 'reset'), io.StringIO('{"version":"8.2.35"}')])
        self.assertEqual(version, '8.2.35')
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_temporary_http_errors_recovers(self):
        for code in (408, 429, 500, 502, 503, 504):
            with self.subTest(code=code):
                error = urllib.error.HTTPError('https://www.php.net/', code, 'temporary', {}, None)
                self.assertEqual(self.fetch([error, io.StringIO('{"version":"8.2.35"}')])[1].call_count, 2)

    def test_bounded_failure(self):
        for error in (TimeoutError('timeout'), urllib.error.URLError(ConnectionResetError(104, 'reset')),
                      urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, 'temporary DNS'))):
            with self.subTest(error=error), patch.object(module.urllib.request, 'urlopen', side_effect=error) as request, patch.object(module.time, 'sleep') as sleep:
                with self.assertRaises(RuntimeError):
                    module.fetch_upstream('8.2', 'https://www.php.net/')
                self.assertEqual(request.call_count, 3)
                self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_permanent_transport_failures_do_not_retry(self):
        for error in (urllib.error.HTTPError('https://www.php.net/', 404, 'not found', {}, None),
                      urllib.error.URLError(ssl.SSLCertVerificationError('invalid certificate'))):
            with self.subTest(error=error), patch.object(module.urllib.request, 'urlopen', side_effect=error) as request, patch.object(module.time, 'sleep') as sleep:
                with self.assertRaises(RuntimeError):
                    module.fetch_upstream('8.2', 'https://www.php.net/')
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()

    def test_bad_payload_does_not_retry(self):
        for payload, failure in [('not json', RuntimeError), (json.dumps({'version':'9.0.0'}), ValueError), ('[]', ValueError)]:
            with self.subTest(payload=payload), patch.object(module.urllib.request, 'urlopen', return_value=io.StringIO(payload)) as request, patch.object(module.time, 'sleep') as sleep:
                with self.assertRaises(failure):
                    module.fetch_upstream('8.2', 'https://www.php.net/')
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
