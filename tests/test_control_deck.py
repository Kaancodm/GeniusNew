from __future__ import annotations

import argparse
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.control_deck import actions, checks, mail_center, server


class ControlDeckTest(unittest.TestCase):
    def test_beta_gate_count_is_fixed(self):
        self.assertEqual(len(checks.BETA_GATES), 21)
        self.assertEqual(checks.BETA_GATES[0][0], "A1")
        self.assertEqual(checks.BETA_GATES[-1][0], "E3")

    def test_commands_are_fixed_copy_only_commands(self):
        self.assertTrue(checks.COMMANDS)
        for name, command in checks.COMMANDS.items():
            self.assertIsInstance(name, str)
            self.assertIsInstance(command, str)
            self.assertNotIn("\n", command)

    @patch("tools.control_deck.checks.shutil.which", return_value="/x/claude")
    @patch("tools.control_deck.checks._run")
    def test_claude_login_status_is_read_only(self, run, _which):
        run.return_value = (0, json.dumps({"loggedIn": False}))
        self.assertEqual(checks.tool_status("claude")["status"], "yellow")
        run.assert_called_once_with(["/x/claude", "auth", "status"], cwd=checks.Path.home())

    @patch("tools.control_deck.checks._run")
    @patch("tools.control_deck.checks._which")
    def test_gemini_status_prefers_antigravity(self, which, run):
        which.side_effect = lambda name: "/x/agy" if name == "agy" else None
        run.return_value = (0, "gemini-3.8-flash-high\tGemini 3.8 Flash (High)")
        state = checks.tool_status("gemini")
        self.assertEqual(state["status"], "green")
        self.assertIn("Antigravity", state["detail"])
        run.assert_called_once_with(
            ["/x/agy", "models"], cwd=checks.Path.home(), timeout=8.0
        )

    @patch("tools.control_deck.checks._run")
    def test_missing_gate_branch_is_red(self, run):
        run.return_value = (1, "")
        self.assertEqual(checks.gate_status(checks.DEFAULT_REPO, "origin/x"), "red")

    def test_unimplemented_gate_is_red(self):
        self.assertEqual(checks.gate_status(checks.DEFAULT_REPO, None), "red")

    def test_action_allowlist_is_fixed(self):
        self.assertEqual(
            set(actions.allowed_actions()),
            {"git_status", "tests", "demo", "docker_status", "grok_build_status", "hermes_status"},
        )

    def test_unknown_action_is_rejected_before_shell(self):
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            actions.run_action("rm_everything")

    @patch("tools.control_deck.checks.repo_status", return_value={"head": "abc123"})
    def test_project_resume_starts_at_gate_zero_when_logins_are_missing(self, _repo):
        gates = [{"id": gate, "name": name, "status": "yellow" if ref else "red"}
                 for gate, name, ref in checks.BETA_GATES]
        tools = {
            "claude": {"status": "green", "detail": "logged in"},
            "codex": {"status": "yellow", "detail": "login required"},
            "gemini": {"status": "yellow", "detail": "login required"},
            "gh": {"status": "yellow", "detail": "login required"},
        }
        state = checks.project_resume(gates, tools, checks.DEFAULT_REPO)
        self.assertIn("Gate 0", state["focus"])
        self.assertEqual(state["main_head"], "abc123")
        self.assertEqual(state["resume"][0]["value"], "codex login")
        self.assertTrue(any(step["value"].endswith("/pull/57") for step in state["resume"]))


    def test_mail_snapshot_missing_is_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = mail_center.snapshot(Path(tmp) / "missing.json")
        self.assertEqual(state["status"], "offline")
        self.assertEqual(state["unread"], 0)
        self.assertEqual(state["threads"], [])

    def test_mail_snapshot_counts_categories_and_attention(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mail.json"
            path.write_text(
                json.dumps(
                    {
                        "provider": "superhuman",
                        "updated_at": 123,
                        "threads": [
                            {
                                "id": "sale-1",
                                "category": "sales",
                                "subject": "Demo Anfrage",
                                "sender": "lead@example.test",
                                "unread": True,
                                "important": True,
                                "received_at": 120,
                            },
                            {
                                "id": "sponsor-1",
                                "split": "Sponsoring",
                                "subject": "Partnerschaft",
                                "sender": "partner@example.test",
                                "unread": True,
                                "received_at": 121,
                            },
                            {
                                "id": "system-1",
                                "split": "Security",
                                "subject": "Security alert",
                                "sender": "system@example.test",
                                "unread": True,
                                "critical": True,
                                "received_at": 122,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            state = mail_center.snapshot(path)

        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["provider"], "superhuman")
        self.assertEqual(state["unread"], 3)
        self.assertEqual(state["important"], 2)
        self.assertEqual(state["critical"], 1)
        self.assertEqual(state["attention"], "critical")
        self.assertEqual(state["categories"]["sales"]["unread"], 1)
        self.assertEqual(state["categories"]["sponsoring"]["unread"], 1)
        self.assertEqual(state["categories"]["system"]["critical"], 1)
        self.assertNotIn("body", state["threads"][0])


class HostCheckTest(unittest.TestCase):
    """A browser page must not reach the deck through a rebound DNS name."""

    def setUp(self):
        self.actions = []
        patches = [
            patch.object(server, "mail_snapshot", return_value={"subject": "MAIL-CANARY"}),
            patch.object(server, "run_action",
                         side_effect=lambda name, repo: self.actions.append(name) or {"ok": True}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.deck = server.Server(("127.0.0.1", 0), server.Handler)
        self.deck.repo = checks.DEFAULT_REPO
        self.port = self.deck.server_address[1]
        loop = threading.Thread(target=self.deck.serve_forever, daemon=True)
        loop.start()
        self.addCleanup(loop.join, 10)
        self.addCleanup(self.deck.server_close)
        self.addCleanup(self.deck.shutdown)

    def request(self, method, path, host, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True)
            if host is not None:
                conn.putheader("Host", host)
            for name, value in (headers or {}).items():
                conn.putheader(name, value)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def post_action(self, host, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        return self.request("POST", "/api/action", host, headers, b'{"action":"git_status"}')

    def test_loopback_host_reads_mail(self):
        for name in ("127.0.0.1", "localhost"):
            with self.subTest(name=name):
                status, body = self.request("GET", "/api/mail", f"{name}:{self.port}")
                self.assertEqual(status, 200)
                self.assertIn(b"MAIL-CANARY", body)

    def test_foreign_host_is_refused_before_any_route(self):
        for path in ("/", "/api/status", "/api/mail", "/api/agents"):
            with self.subTest(path=path):
                status, body = self.request("GET", path, f"attacker.example:{self.port}")
                self.assertEqual(status, 421)
                self.assertNotIn(b"MAIL-CANARY", body)

    def test_missing_host_and_wrong_port_are_refused(self):
        self.assertEqual(self.request("GET", "/api/mail", None)[0], 421)
        self.assertEqual(self.request("GET", "/api/mail", "127.0.0.1:1")[0], 421)

    def test_foreign_host_cannot_start_an_action(self):
        status, _ = self.post_action(f"attacker.example:{self.port}")
        self.assertEqual(status, 421)
        self.assertEqual(self.actions, [])

    def test_foreign_origin_cannot_start_an_action(self):
        status, _ = self.post_action(f"127.0.0.1:{self.port}", origin="http://attacker.example")
        self.assertEqual(status, 403)
        self.assertEqual(self.actions, [])

    def test_own_origin_starts_an_action(self):
        status, _ = self.post_action(f"127.0.0.1:{self.port}",
                                     origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        self.assertEqual(self.actions, ["git_status"])

    def test_a_named_proxy_host_is_accepted_with_its_https_origin(self):
        self.deck.extra_hosts = frozenset({"deck.tailnet.example"})
        status, body = self.request("GET", "/api/mail", "deck.tailnet.example")
        self.assertEqual(status, 200)
        self.assertIn(b"MAIL-CANARY", body)
        status, _ = self.post_action("deck.tailnet.example", origin="https://deck.tailnet.example")
        self.assertEqual((status, self.actions), (200, ["git_status"]))

    def test_proxy_host_names_are_bare(self):
        self.assertEqual(server.host_name("deck.tailnet.example"), "deck.tailnet.example")
        for value in ("", "a b", "http://x", "x/y", "user@x", "x\\y", "a" * 254):
            with self.subTest(value=value):
                with self.assertRaisesRegex(argparse.ArgumentTypeError, "bare host"):
                    server.host_name(value)

    def test_only_loopback_or_tailnet_may_be_bound(self):
        for value in ("127.0.0.1", "127.0.0.2", "localhost", "100.64.0.1", "100.101.102.103"):
            self.assertEqual(server.bind_host(value), value)
        # IPv6 is refused here rather than failing later at bind time.
        for value in ("0.0.0.0", "::", "::1", "fd7a:115c:a1e0::1", "192.168.1.10",
                      "10.0.0.5", "100.128.0.1", "203.0.113.7", "example.org", ""):
            with self.subTest(value=value):
                with self.assertRaisesRegex(argparse.ArgumentTypeError,
                                            "IPv4 loopback or Tailscale"):
                    server.bind_host(value)

    def test_port_80_accepts_a_host_without_port(self):
        deck = server.Server(("127.0.0.1", 0), server.Handler, bind_and_activate=False)
        self.addCleanup(deck.server_close)
        deck.server_address = ("127.0.0.1", 80)
        self.assertLessEqual({"127.0.0.1", "localhost", "127.0.0.1:80"}, deck.allowed_hosts())
        deck.server_address = ("127.0.0.1", 8787)
        self.assertNotIn("127.0.0.1", deck.allowed_hosts())


class AgentControlTest(unittest.TestCase):
    @patch("tools.control_deck.actions.subprocess.run")
    def test_agent_start_is_not_an_http_action(self, run):
        for name in ("grok_build", "hermes", "start_agent", "grok; id"):
            with self.assertRaises(ValueError):
                actions.run_action(name)
        run.assert_not_called()

    @patch("tools.control_deck.actions.subprocess.run")
    def test_unknown_terminal_agent_is_refused(self, run):
        with self.assertRaises(ValueError):
            actions.start_agent("../../shell", detach=True)
        run.assert_not_called()

    @patch("tools.control_deck.actions._which", return_value=None)
    @patch("tools.control_deck.actions.subprocess.run")
    def test_missing_client_is_refused(self, run, which):
        with self.assertRaises(RuntimeError):
            actions.start_agent("hermes", detach=True)
        run.assert_not_called()

    @patch("tools.control_deck.actions._which")
    @patch("tools.control_deck.actions.subprocess.run")
    def test_grok_never_starts_without_bubblewrap(self, run, which):
        which.side_effect = lambda name: None if name == "bwrap" else "/bin/" + name
        with self.assertRaisesRegex(RuntimeError, "bubblewrap"):
            actions.start_agent("grok_build", detach=True)
        run.assert_not_called()

    @patch("tools.control_deck.checks._run", return_value=(0, "1:grok"))
    def test_dead_terminal_is_not_running(self, run):
        self.assertEqual(checks.agent_session("grok-build")["status"], "yellow")

    @patch("tools.control_deck.checks._run", return_value=(0, "0:grok"))
    def test_live_terminal_is_reported(self, run):
        self.assertEqual(checks.agent_session("grok-build")["status"], "green")

    @patch("tools.control_deck.checks._which", return_value="/bin/bwrap")
    @patch("tools.control_deck.checks.agent_session", return_value={"status": "yellow", "detail": "stopped"})
    @patch("tools.control_deck.checks.tool_status", return_value={"status": "green", "detail": "installed"})
    def test_installation_does_not_claim_model_or_session_success(self, tool, session, which):
        cards = checks.agent_hub()["agents"]
        self.assertEqual([c["id"] for c in cards], ["grok", "grok_bot", "grok_build", "hermes"])
        self.assertTrue(all(c["status"] == "yellow" for c in cards))
        self.assertNotIn("command", cards[1])
        self.assertIn("kein unterstützter", cards[1]["detail"])

    @patch("tools.control_deck.actions.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_status_program_returns_a_bounded_error(self, run):
        result = actions.run_action("grok_build_status")
        self.assertFalse(result["ok"])
        self.assertEqual(result["output"], "Programm nicht verfügbar.")


if __name__ == "__main__":
    unittest.main()