"""The product client: the key stays a header, the digest comes back out."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from tools.genius_client import _open, build_request, main, parse_outcome, read_api_key

KEY = b"CLIENT-KEY-CANARY-0123456789"


def body(**fields):
    return json.dumps(fields).encode()


class GeniusClientTest(unittest.TestCase):
    def opener_returning(self, status, raw, record=None):
        def opener(request, *, timeout):
            if record is not None:
                record.append(request)
            return status, raw
        return opener

    def text_file(self):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8")
        handle.write("Ein kurzer Text über Anker und Ketten.\n")
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def submit(self, status, raw, argv=()):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--file", self.text_file(), *argv],
                        opener=self.opener_returning(status, raw),
                        environ={"GENIUSNEW_API_KEY": KEY.decode()})
        return code, out.getvalue(), err.getvalue()

    def test_a_succeeded_job_prints_its_digest(self):
        code, out, _ = self.submit(202, body(
            job_id="job-1", status="SUCCEEDED",
            output={"text": "# Text-Digest\nDigest!\n"}))
        self.assertEqual(code, 0)
        self.assertIn("Digest!", out)

    def test_refusals_and_failures_name_their_reason(self):
        code, _, err = self.submit(409, body(error="REJECTED"))
        self.assertEqual(code, 1)
        self.assertIn("REJECTED", err)
        code, _, err = self.submit(202, body(job_id="j", status="FAILED",
                                             reason_code="WORKER_FAILED",
                                             output=None))
        self.assertEqual(code, 1)
        self.assertIn("WORKER_FAILED", err)
        code, _, err = self.submit(202, body(job_id="job-9",
                                             status="PENDING_APPROVAL"))
        self.assertEqual(code, 1)
        self.assertIn("job-9", err)

    def test_the_key_travels_as_a_header_and_nowhere_else(self):
        sent = []
        request_code, out, err = None, io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            request_code = main(
                ["--file", self.text_file()],
                opener=self.opener_returning(
                    202, body(job_id="j", status="SUCCEEDED",
                              output={"text": "ok\n"}), record=sent),
                environ={"GENIUSNEW_API_KEY": KEY.decode()})
        self.assertEqual(request_code, 0)
        self.assertIn(f"Bearer {KEY.decode()}", sent[0].headers.get("Authorization", ""))
        self.assertNotIn(KEY.decode(), out.getvalue() + err.getvalue())
        self.assertNotIn(KEY.decode(), sent[0].data.decode())

    def test_oversized_text_is_refused_before_the_wire(self):
        with self.assertRaises(ValueError):
            build_request("http://127.0.0.1:8080", KEY, "wort " * 4000)

    def test_url_and_key_shapes_are_checked(self):
        for url in ("ftp://host", "https://user@host", "http://host/pfad"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    build_request(url, KEY, "text")
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "key"
            key_file.write_bytes(KEY + b"\n")
            self.assertEqual(read_api_key(key_file=key_file, environ={}), KEY)
        with self.assertRaises(ValueError):
            read_api_key(key_file=None, environ={})
        with self.assertRaises(ValueError):
            read_api_key(key_file=None, environ={"GENIUSNEW_API_KEY": "kurz"})

    def test_unencrypted_http_requires_a_literal_loopback_ip(self):
        for url in ("http://example.com", "http://192.0.2.12:8080",
                    "http://localhost:8080", "http://[::ffff:127.0.0.1]:8080"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "loopback"):
                    build_request(url, KEY, "text")
        for url in ("http://127.0.0.1:8080", "http://[::1]:8080",
                    "https://example.com"):
            with self.subTest(url=url):
                self.assertEqual(build_request(url, KEY, "text").full_url,
                                 url + "/jobs")

    def test_invalid_key_controls_refuse_before_sending_without_echoing(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "key"
            for control in ("\r", "\n", "\t", "\x00", "\x7f"):
                bad_key = KEY.decode() + control + "suffix"
                for source in ("environment", "file"):
                    with self.subTest(control=repr(control), source=source):
                        key_file.write_bytes(bad_key.encode("ascii"))
                        argv = ["--file", self.text_file()]
                        if source == "file":
                            argv += ["--api-key-file", str(key_file)]
                        sent, out, err = [], io.StringIO(), io.StringIO()
                        with redirect_stdout(out), redirect_stderr(err):
                            code = main(argv, opener=self.opener_returning(
                                202, body(status="SUCCEEDED", output={"text": "ok"}),
                                record=sent), environ={"GENIUSNEW_API_KEY": bad_key})
                        self.assertEqual(code, 2)
                        self.assertEqual(sent, [])
                        self.assertNotIn(KEY.decode(), out.getvalue() + err.getvalue())
                        self.assertNotIn("Traceback", err.getvalue())

    def test_local_http_bypasses_environment_proxies(self):
        target_headers, proxy_headers = [], []

        def handler(received):
            class Receiver(BaseHTTPRequestHandler):
                def do_POST(self):
                    received.append(self.headers.get("Authorization"))
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

                def log_message(self, *args):
                    pass
            return Receiver

        with ThreadingHTTPServer(("127.0.0.1", 0), handler(target_headers)) as target:
            with ThreadingHTTPServer(("127.0.0.1", 0), handler(proxy_headers)) as proxy:
                threads = [Thread(target=server.serve_forever, daemon=True)
                           for server in (target, proxy)]
                for thread in threads:
                    thread.start()
                try:
                    request = build_request(
                        f"http://127.0.0.1:{target.server_port}", KEY, "text")
                    with patch.dict(os.environ, {
                            "http_proxy": f"http://127.0.0.1:{proxy.server_port}",
                            "no_proxy": ""}, clear=True):
                        status, _ = _open(request, timeout=3)
                    self.assertEqual(status, 202)
                    self.assertEqual(target_headers, [f"Bearer {KEY.decode()}"])
                    self.assertEqual(proxy_headers, [])
                finally:
                    target.shutdown()
                    proxy.shutdown()
                    for thread in threads:
                        thread.join(timeout=3)

    def test_redirects_are_never_followed_with_an_api_key(self):
        received = []

        class Destination(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Destination) as target:
            target_thread = Thread(target=target.serve_forever, daemon=True)
            target_thread.start()

            class Redirecting(BaseHTTPRequestHandler):
                def do_POST(self):
                    self.send_response(302)
                    self.send_header("Location",
                                     f"http://127.0.0.1:{target.server_port}/capture")
                    self.end_headers()

                def log_message(self, *args):
                    pass

            with ThreadingHTTPServer(("127.0.0.1", 0), Redirecting) as source:
                source_thread = Thread(target=source.serve_forever, daemon=True)
                source_thread.start()
                try:
                    request = build_request(
                        f"http://127.0.0.1:{source.server_port}", KEY, "text")
                    status, _ = _open(request, timeout=3)
                    self.assertEqual(status, 302)
                    self.assertEqual(received, [])
                finally:
                    source.shutdown()
                    source_thread.join(timeout=3)
                    target.shutdown()
                    target_thread.join(timeout=3)

    def test_output_file_error_has_a_documented_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "missing" / "digest.md"
            code, out, err = self.submit(
                202, body(job_id="job-3", status="SUCCEEDED",
                          output={"text": "summary"}),
                argv=("--out", str(destination)))
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertIn("genius_client: cannot write output", err)

    def test_unreadable_answers_are_refusals_not_crashes(self):
        self.assertEqual(parse_outcome(502, b"<html>proxy</html>").kind, "refused")
        self.assertEqual(parse_outcome(202, body(status="SUCCEEDED",
                                                 output=None)).kind, "failed")


if __name__ == "__main__":
    unittest.main()
