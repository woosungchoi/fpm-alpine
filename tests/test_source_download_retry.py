"""Exercise the Dockerfile's real curl/checksum commands against a local server."""
import hashlib
import http.server
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = b"pinned source archive\n"


class DownloadTests(unittest.TestCase):
    def exercise(self, statuses, checksum=None):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status = statuses[min(len(requests), len(statuses) - 1)]
                requests.append(status)
                self.send_response(status)
                self.end_headers()
                self.wfile.write(PAYLOAD if status == 200 else b"upstream unavailable")

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                text = (ROOT / "Dockerfile").read_text()
                commands = re.search(r'(curl[^\n]+\$IMAGICK_URL[^\n]+)\n\s*(echo[^\n]+IMAGICK_SHA256[^\n]+)', text)
                self.assertIsNotNone(commands)
                shell = "\n".join(line.rstrip(" \\") for line in commands.groups())
                shell = shell.replace("/usr/src/vendor/imagick.tgz", str(Path(tmp) / "source.tgz"))
                env = os.environ | {
                    "IMAGICK_URL": f"http://127.0.0.1:{server.server_port}/archive",
                    "IMAGICK_SHA256": checksum or hashlib.sha256(PAYLOAD).hexdigest(),
                    "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
                }
                result = subprocess.run(["sh", "-ec", shell], env=env, capture_output=True, text=True, timeout=20)
                return result, requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_transient_500_recovers_and_checksum_passes(self):
        result, requests = self.exercise([500, 200])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(requests, [500, 200])

    def test_permanent_404_not_retried(self):
        result, requests = self.exercise([404])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(requests, [404])

    def test_persistent_outage_exhausts_budget(self):
        result, requests = self.exercise([503])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(requests), 4)

    def test_checksum_mismatch_not_retried(self):
        result, requests = self.exercise([200], checksum="0" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(requests, [200])


if __name__ == "__main__":
    unittest.main()
