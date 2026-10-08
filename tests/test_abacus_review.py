import io
import json
import unittest
from http.client import BadStatusLine, IncompleteRead
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request

from tools import abacus_review as abacus
from scripts import refusals


KEY = "synthetic-test-key-123456"
HEAD = "a" * 40
MODEL = "gemini-test-pro"


class AbacusReviewTest(unittest.TestCase):
    def setUp(self):
        self.response = Mock()
        self.response.__enter__ = Mock(return_value=self.response)
        self.response.__exit__ = Mock(return_value=False)
        self.set_content("Review text")
        self.opener = Mock()
        self.opener.open.return_value = self.response
        p = patch.object(abacus, "build_opener", return_value=self.opener)
        self.builder = p.start()
        self.addCleanup(p.stop)

    def set_content(self, content, **extra):
        self.response.read.return_value = json.dumps({"choices": [{
            "finish_reason": "stop", "message": {"content": content, **extra},
        }]}).encode()

    def review(self, **changes):
        args = dict(prompt="PR #1, supplied diff only", model=MODEL, head=HEAD, key=KEY)
        args.update(changes)
        return abacus.review(**args)

    def test_request_is_bounded_text_only_and_pinned_to_abacus(self):
        text = self.review()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, abacus.ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        body = json.loads(request.data)
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["max_tokens"], 4096)
        self.assertFalse(body["stream"])
        self.assertNotIn("tools", body)
        self.assertNotIn("abacus_tools", body)
        self.assertIn(HEAD, body["messages"][0]["content"])
        self.assertNotIn(KEY, request.data.decode())
        self.assertIn("Review text", text)
        self.assertIn(HEAD, text)
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 120)
        self.response.read.assert_called_once_with(abacus.MAX_RESPONSE + 1)
        self.assertIsInstance(self.builder.call_args.args[0], ProxyHandler)
        self.assertEqual(self.builder.call_args.args[0].proxies, {})
        self.assertIsInstance(self.builder.call_args.args[1], abacus.NoRedirect)

    def test_invalid_inputs_never_send_a_request(self):
        for changes in (dict(key=""), dict(key="x\r\nAuthorization: injected"),
                        dict(model=""), dict(model="route-llm"), dict(head="short"),
                        dict(prompt=" "), dict(prompt="ü" * abacus.MAX_PROMPT),
                        dict(prompt="secret " + KEY)):
            with self.subTest(changes=changes.keys()):
                with self.assertRaises(abacus.AbacusRefused):
                    self.review(**changes)
        self.opener.open.assert_not_called()

    def test_redirect_cannot_forward_credentials(self):
        stream = io.BytesIO(b"redirect body")
        with self.assertRaises(abacus.AbacusRefused):
            abacus.NoRedirect().redirect_request(Request(abacus.ENDPOINT), stream,
                                                307, "redirect", {}, "https://other.test")
        self.assertTrue(stream.closed)

    def test_provider_model_ids_preserve_the_exact_requested_model(self):
        self.review(model="openai/gpt-oss-120b")
        request = self.opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)["model"], "openai/gpt-oss-120b")
        for model in ("a" * 129, "gemini\npro", "provider/model?key=x"):
            with self.assertRaises(abacus.AbacusRefused):
                self.review(model=model)

    def test_terminal_controls_are_visible_text_without_losing_normal_formatting(self):
        self.set_content("Deutsch ä\n\tCode\x1b]52;c;YXR0YWNr\x07\r\b\x9b2J\u202ePASS\u2066")
        result = self.review()
        self.assertIn("Deutsch ä\n\tCode", result)
        for char in ("\x1b", "\x07", "\r", "\b", "\x9b", "\u202e", "\u2066"):
            self.assertNotIn(char, result)
            self.assertIn(f"\\u{ord(char):04x}", result)

    def test_malformed_http_is_unknown_and_never_exposes_protocol_text(self):
        self.opener.open.side_effect = BadStatusLine(KEY)
        with self.assertRaises(abacus.AbacusRefused) as result:
            self.review()
        self.assertNotIn(KEY, str(result.exception))
        self.opener.open.side_effect = None
        self.response.read.side_effect = IncompleteRead(KEY.encode(), 100)
        with self.assertRaises(abacus.AbacusRefused) as result:
            self.review()
        self.assertNotIn(KEY, str(result.exception))
        self.response.__exit__.assert_called_once()

    def test_network_and_http_errors_hide_response_and_key(self):
        for error in (URLError(KEY), TimeoutError(KEY),
                      HTTPError(abacus.ENDPOINT, 401, KEY, {}, io.BytesIO(KEY.encode()))):
            self.opener.open.side_effect = error
            with self.assertRaises(abacus.AbacusRefused) as result:
                self.review()
            self.assertNotIn(KEY, str(result.exception))

    def test_invalid_responses_are_unknown(self):
        for raw in (b"not json", b"{}", b'{"choices":[]}',
                    b'{"choices":[null]}', b'{"choices":null}'):
            self.response.read.return_value = raw
            with self.subTest(size=len(raw)):
                with self.assertRaises(abacus.AbacusRefused):
                    self.review()
        for content in (None, "", " ", ["text"]):
            self.set_content(content)
            with self.assertRaises(abacus.AbacusRefused):
                self.review()

    def test_oversized_valid_json_is_refused_before_parsing(self):
        valid = self.response.read.return_value
        self.response.read.return_value = valid + b" " * (abacus.MAX_RESPONSE + 1)
        with self.assertRaises(abacus.AbacusRefused):
            self.review()

    def test_tool_calls_and_truncated_answers_are_refused(self):
        self.set_content("run this", tool_calls=[{"function": {"name": "shell"}}])
        with self.assertRaises(abacus.AbacusRefused):
            self.review()
        self.response.read.return_value = json.dumps({"choices": [{
            "finish_reason": "length", "message": {"content": "partial"},
        }]}).encode()
        with self.assertRaises(abacus.AbacusRefused):
            self.review()

    def test_echoed_key_is_redacted(self):
        self.set_content("Echo: " + KEY)
        self.assertNotIn(KEY, self.review())

    def test_cli_reads_only_explicit_stdin_and_environment_key(self):
        stdin = Mock(buffer=io.BytesIO(b"selected diff"))
        with patch.object(abacus.sys, "stdin", stdin), \
                patch.dict(abacus.os.environ, {"ABACUS_API_KEY": KEY}), \
                patch.object(abacus.sys, "stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(abacus.main(["--model", MODEL, "--head", HEAD]), 0)
        self.assertIn("Review text", stdout.getvalue())

    def test_cli_refuses_oversized_input_before_credential_prompt(self):
        stdin = Mock(buffer=io.BytesIO(b"x" * (abacus.MAX_PROMPT + 1)))
        with patch.object(abacus.sys, "stdin", stdin), \
                patch.object(abacus.getpass, "getpass") as password, \
                patch.object(abacus.sys, "stderr", new_callable=io.StringIO):
            self.assertEqual(abacus.main(["--model", MODEL, "--head", HEAD]), 1)
        password.assert_not_called()
        self.opener.open.assert_not_called()

    def test_cli_fails_closed_when_password_would_echo(self):
        import warnings
        def unsafe_prompt(*args):
            warnings.warn("would echo", abacus.getpass.GetPassWarning)
        with patch.object(abacus.sys, "stdin", Mock(buffer=io.BytesIO(b"diff"))), \
                patch.dict(abacus.os.environ, {}, clear=True), \
                patch.object(abacus.getpass, "getpass", side_effect=unsafe_prompt), \
                patch.object(abacus.sys, "stderr", new_callable=io.StringIO):
            self.assertEqual(abacus.main(["--model", MODEL, "--head", HEAD]), 1)
        self.opener.open.assert_not_called()

    def test_refusals_are_registered(self):
        self.assertIn("tools/abacus_review.py", refusals.GUARDED)
        self.assertIn("AbacusRefused", refusals._REFUSAL_RAISES)


if __name__ == "__main__":
    unittest.main()
