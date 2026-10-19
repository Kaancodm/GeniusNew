"""The product client: the key stays a header, the digest comes back out."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tools.genius_client import build_request, main, parse_outcome, read_api_key

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

    def test_unreadable_answers_are_refusals_not_crashes(self):
        self.assertEqual(parse_outcome(502, b"<html>proxy</html>").kind, "refused")
        self.assertEqual(parse_outcome(202, body(status="SUCCEEDED",
                                                 output=None)).kind, "failed")


if __name__ == "__main__":
    unittest.main()
