import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib import request as urllib_request

from experiments.laya_shadow import (
    ShadowError,
    build_request,
    http_decider,
    observe,
    parse_choice,
)


class LayaShadowTests(unittest.TestCase):
    def test_request_is_bounded_and_lists_only_configured_agents(self):
        wire = build_request("review the gateway")
        payload = json.loads(wire)
        question = payload["questions"]["agent"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(
            set(question["criteria"]),
            {"kiro", "copilot", "gemini", "abacus"},
        )
        self.assertNotIn("allow", payload)
        self.assertNotIn("deny", payload)

    def test_shadow_match_never_changes_authoritative_choice(self):
        seen = []

        def decide(wire):
            seen.append(json.loads(wire))
            return b'{"answers":{"agent":{"type":"choice","choice":"gemini"}}}'

        result = observe("database design", "kiro", decide)
        self.assertEqual(result.authoritative_choice, "kiro")
        self.assertEqual(result.model_choice, "gemini")
        self.assertFalse(result.matched)
        self.assertEqual(result.status, "OBSERVED")
        self.assertEqual(len(seen), 1)

    def test_model_failure_is_unknown_not_a_fallback_decision(self):
        def decide(_wire):
            raise OSError("offline")

        result = observe("security review", "copilot", decide)
        self.assertEqual(result.authoritative_choice, "copilot")
        self.assertIsNone(result.model_choice)
        self.assertIsNone(result.matched)
        self.assertEqual(result.status, "UNKNOWN")

    def test_invalid_model_choice_is_unknown(self):
        result = observe(
            "route this",
            "abacus",
            lambda _wire: b'{"answers":{"agent":{"type":"choice","choice":"untrusted-agent"}}}',
        )
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.authoritative_choice, "abacus")

    def test_parser_accepts_jev_answer_shape(self):
        self.assertEqual(
            parse_choice(
                b'{"answers":{"agent":{"type":"choice","choice":"kiro"}}}'
            ),
            "kiro",
        )

    def test_task_size_is_bounded(self):
        with self.assertRaisesRegex(ShadowError, "task"):
            build_request("x" * 4097)

    def test_http_endpoint_is_loopback_only(self):
        for endpoint in (
            "https://example.com/v1/systemone",
            "http://192.0.2.1:8000/v1/systemone",
            "http://localhost:8000/v1/systemone",
            "file:///tmp/socket",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ShadowError, "loopback"):
                    http_decider(endpoint)

    def test_legacy_agent_choices_are_rejected(self):
        for choice in ("claude", "zen"):
            with self.subTest(choice=choice):
                raw = json.dumps({
                    "answers": {"agent": {"type": "choice", "choice": choice}}
                }).encode("utf-8")
                with self.assertRaisesRegex(ShadowError, "configured agents"):
                    parse_choice(raw)

    def test_http_endpoint_accepts_ipv4_and_ipv6_loopback(self):
        http_decider("http://127.0.0.1:8000/v1/systemone")
        http_decider("http://[::1]:8000/v1/systemone")

    def test_http_endpoint_rejects_userinfo_host_confusion(self):
        with self.assertRaisesRegex(ShadowError, "loopback"):
            http_decider("http://127.0.0.1:8000@evil.example/v1/systemone")

    def test_http_decider_rejects_redirects(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", "/final")
                self.end_headers()

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    b'{"answers":{"agent":{"type":"choice","choice":"kiro"}}}'
                )

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            decide = http_decider(
                f"http://127.0.0.1:{server.server_port}/v1/systemone"
            )
            with self.assertRaisesRegex(ShadowError, "redirect"):
                decide(b"{}")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_http_decider_ignores_proxy_environment(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    b'{"answers":{"agent":{"type":"choice","choice":"kiro"}}}'
                )

            def log_message(self, _format, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        poisoned = {
            "http_proxy": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "no_proxy": "",
            "NO_PROXY": "",
        }
        try:
            with (
                mock.patch.dict(os.environ, poisoned, clear=False),
                mock.patch.object(urllib_request, "_opener", None),
            ):
                raw = http_decider(
                    f"http://127.0.0.1:{server.server_port}/v1/systemone"
                )(b"{}")
            self.assertEqual(parse_choice(raw), "kiro")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_http_timeout_must_be_positive(self):
        with self.assertRaisesRegex(ShadowError, "timeout"):
            http_decider("http://127.0.0.1:8000/v1/systemone", 0)


if __name__ == "__main__":
    unittest.main()
