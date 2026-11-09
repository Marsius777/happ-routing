import contextlib
import gzip
import http.client
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

from scripts import fetch_opencck


DATA = {"service": {"domains": ["example.org"], "cidr4": ["192.0.2.0/24"]}}
PAYLOAD = json.dumps(DATA).encode()


@contextlib.contextmanager
def serve(responses):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            index = min(len(requests), len(responses) - 1)
            requests.append(self.headers)
            status, body, headers = responses[index]
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, str(value))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def full(body=PAYLOAD, **headers):
    return 200, body, {"Content-Length": len(body), **headers}


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(fetch_opencck.time, "sleep").start()
        self.addCleanup(patch.stopall)
        patch("sys.stderr", new_callable=io.StringIO).start()

    def test_incomplete_read_retries_from_start_with_and_without_gzip(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                body = gzip.compress(PAYLOAD) if compressed else PAYLOAD
                headers = {"Content-Encoding": "gzip"} if compressed else {}
                truncated = (200, body[:10], {"Content-Length": len(body), **headers})
                with serve([truncated, full(body, **headers)]) as (url, requests):
                    self.assertEqual(fetch_opencck.load(url), DATA)
                    self.assertEqual(len(requests), 2)
                    self.assertEqual(requests[0]["Accept-Encoding"], "gzip")
                    self.assertEqual(requests[0]["User-Agent"], "happ-routing-build/1.0")

    def test_truncated_gzip_and_json_retry(self):
        bad_responses = [
            full(gzip.compress(PAYLOAD)[:-5], **{"Content-Encoding": "gzip"}),
            full(b'{"service":'),
        ]
        for bad_response in bad_responses:
            with self.subTest(response=bad_response):
                with serve([bad_response, full()]) as (url, requests):
                    self.assertEqual(fetch_opencck.load(url), DATA)
                    self.assertEqual(len(requests), 2)

    def test_transient_http_error_retries(self):
        with serve([(503, b"unavailable", {}), full()]) as (url, requests):
            self.assertEqual(fetch_opencck.load(url), DATA)
            self.assertEqual(len(requests), 2)

    def test_permanent_http_error_does_not_retry(self):
        with serve([(404, b"missing", {})]) as (url, requests):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                fetch_opencck.load(url)
            self.assertEqual(raised.exception.code, 404)
            self.assertEqual(len(requests), 1)
            self.sleep.assert_not_called()

    def test_exhausted_retries_preserve_generated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            outdir = Path(directory)
            files = [outdir / "russia-inside", outdir / "russia-inside.txt"]
            for path in files:
                path.write_text("previous contents\n")
            truncated = (200, PAYLOAD[:10], {"Content-Length": len(PAYLOAD)})
            with serve([truncated]) as (url, requests):
                with patch("sys.argv", ["fetch_opencck.py", "--source", url,
                                        "--outdir", str(outdir)]):
                    with self.assertRaises(RuntimeError) as raised:
                        fetch_opencck.main()
                self.assertIsInstance(raised.exception.__cause__, http.client.IncompleteRead)
                self.assertEqual(len(requests), 5)
                self.assertEqual([call.args[0] for call in self.sleep.call_args_list],
                                 [2, 4, 8, 16])
            for path in files:
                self.assertEqual(path.read_text(), "previous contents\n")

    def test_local_source_does_not_use_network(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            source.write_bytes(PAYLOAD)
            with patch.object(fetch_opencck.urllib.request, "urlopen") as request:
                self.assertEqual(fetch_opencck.load(str(source)), DATA)
                request.assert_not_called()
            self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
