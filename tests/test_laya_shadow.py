import json
import unittest

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
            {"claude", "gemini", "codex", "zen"},
        )
        self.assertNotIn("allow", payload)
        self.assertNotIn("deny", payload)

    def test_shadow_match_never_changes_authoritative_choice(self):
        seen = []

        def decide(wire):
            seen.append(json.loads(wire))
            return b'{"answers":{"agent":{"type":"choice","choice":"gemini"}}}'

        result = observe("database design", "claude", decide)
        self.assertEqual(result.authoritative_choice, "claude")
        self.assertEqual(result.model_choice, "gemini")
        self.assertFalse(result.matched)
        self.assertEqual(result.status, "OBSERVED")
        self.assertEqual(len(seen), 1)

    def test_model_failure_is_unknown_not_a_fallback_decision(self):
        def decide(_wire):
            raise OSError("offline")

        result = observe("security review", "codex", decide)
        self.assertEqual(result.authoritative_choice, "codex")
        self.assertIsNone(result.model_choice)
        self.assertIsNone(result.matched)
        self.assertEqual(result.status, "UNKNOWN")

    def test_invalid_model_choice_is_unknown(self):
        result = observe(
            "route this",
            "zen",
            lambda _wire: b'{"answers":{"agent":{"type":"choice","choice":"untrusted-agent"}}}',
        )
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.authoritative_choice, "zen")

    def test_parser_accepts_jev_answer_shape(self):
        self.assertEqual(
            parse_choice(
                b'{"answers":{"agent":{"type":"choice","choice":"claude"}}}'
            ),
            "claude",
        )

    def test_task_size_is_bounded(self):
        with self.assertRaisesRegex(ShadowError, "task"):
            build_request("x" * 4097)

    def test_http_endpoint_is_loopback_only(self):
        for endpoint in (
            "https://example.com/v1/systemone",
            "http://192.0.2.1:8000/v1/systemone",
            "file:///tmp/socket",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(ShadowError, "loopback"):
                    http_decider(endpoint)

    def test_http_timeout_must_be_positive(self):
        with self.assertRaisesRegex(ShadowError, "timeout"):
            http_decider("http://127.0.0.1:8000/v1/systemone", 0)


if __name__ == "__main__":
    unittest.main()
