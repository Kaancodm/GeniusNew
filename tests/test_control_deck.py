from __future__ import annotations

import argparse
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.control_deck import actions, checks, harpa_inbox, mail_center, server
from scripts import refusals


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
            {"git_status", "tests", "demo", "docker_status", "hermes_status"},
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

    def test_harpa_inbox_accepts_only_the_fixed_report_contract(self):
        """Accept valid reports and reject extra fields, invalid text, kinds, IDs, and URLs."""
        payload = {
            "event_id": "prices:2026-10-08T08:00:00Z",
            "kind": "monitor",
            "title": "Pricing changed",
            "summary": "The public pricing page changed.",
            "source_url": "https://example.test/pricing",
        }
        item = harpa_inbox.normalize(payload, received_at=123)
        self.assertEqual(item["received_at"], 123)
        for changed in (
            {**payload, "command": "git push"},
            {**payload, "kind": "shell"},
            {**payload, "title": 7},
            {**payload, "summary": ""},
            {**payload, "summary": "x" * 8001},
            {**payload, "summary": "hidden\x00value"},
            {**payload, "event_id": "invalid event"},
            {**payload, "source_url": "file:///etc/passwd"},
            {**payload, "source_url": "https://user:pass@example.test/"},
            {**payload, "source_url": "https://[broken"},
            {**payload, "source_url": "https://example.test/\nscript"},
            {**payload, "summary": "hidden\u202evalue"},
            {**payload, "summary": "hidden\x7fvalue"},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(harpa_inbox.HarpaPayloadError):
                    harpa_inbox.normalize(changed)

    def test_harpa_inbox_deduplicates_event_ids(self):
        """A repeated event ID must acknowledge a duplicate without adding a record."""
        payload = {
            "event_id": "weekly:42",
            "kind": "report",
            "title": "Weekly report",
            "summary": "No material changes.",
            "source_url": "https://example.test/status",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.jsonl"
            first = harpa_inbox.store(payload, path)
            second = harpa_inbox.store(payload, path)
            state = harpa_inbox.snapshot(path)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(state["items"]), 1)

    def test_harpa_refusals_are_mutation_guarded(self):
        """Keep HARPA validation errors and the inbox module covered by the refusal guard."""
        self.assertIn("tools/control_deck/harpa_inbox.py", refusals.GUARDED)
        self.assertIn("HarpaPayloadError", refusals._REFUSAL_RAISES)
        self.assertIn("HarpaStorageError", refusals._REFUSAL_RAISES)
        guarded = refusals.find_refusals(refusals.ROOT / "tools/control_deck/harpa_inbox.py")
        messages = {item.message for item in guarded}
        self.assertLessEqual({"HARPA inbox cannot be read", "invalid HARPA inbox",
                              "HARPA inbox write failed"}, messages)

    def test_harpa_inbox_storage_is_bounded_and_private(self):
        """Retain only the newest MAX_ITEMS reports in a file with private permissions."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.jsonl"
            for number in range(harpa_inbox.MAX_ITEMS + 5):
                harpa_inbox.store({
                    "event_id": f"monitor:{number}", "kind": "monitor",
                    "title": f"Monitor {number}", "summary": "No material change.",
                    "source_url": "https://example.test/status",
                }, path)
            lines = path.read_text().splitlines()
            mode = path.stat().st_mode & 0o777
        self.assertEqual(len(lines), harpa_inbox.MAX_ITEMS)
        self.assertEqual(json.loads(lines[0])["event_id"], "monitor:5")
        self.assertEqual(mode, 0o600)

    def test_harpa_inbox_refuses_to_overwrite_corrupted_state(self):
        """Keep corrupt bytes intact and report them as unavailable, never ready."""
        payload = {
            "event_id": "monitor:1", "kind": "monitor", "title": "Monitor",
            "summary": "No material change.", "source_url": "https://example.test/",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.jsonl"
            for content in (b"not-json\n", b"[]\n", b"\xff\n"):
                with self.subTest(content=content):
                    path.write_bytes(content)
                    with self.assertRaises(harpa_inbox.HarpaStorageError):
                        harpa_inbox.store(payload, path)
                    self.assertEqual(path.read_bytes(), content)
                    self.assertEqual(harpa_inbox.snapshot(path), {"status": "error", "items": []})

    def test_harpa_stored_reports_are_revalidated_before_display_or_overwrite(self):
        payload = {"event_id": "monitor:1", "kind": "monitor", "title": "Monitor",
                   "summary": "No change.", "source_url": "https://example.test/"}
        original = harpa_inbox.normalize(payload, received_at=123)
        for item in ({**original, "source_url": "javascript:alert(1)"},
                     {**original, "received_at": True}, {**original, "received_at": -1},
                     {**original, "command": "git push"}, {"event_id": "missing-fields"}):
            with self.subTest(item=item), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "inbox.jsonl"
                content = (json.dumps(item) + "\n").encode()
                path.write_bytes(content)
                self.assertEqual(harpa_inbox.snapshot(path), {"status": "error", "items": []})
                with self.assertRaises(harpa_inbox.HarpaStorageError):
                    harpa_inbox.store(payload, path)
                self.assertEqual(path.read_bytes(), content)

    def test_harpa_inbox_read_failure_is_not_empty_ready_state(self):
        """A restored unreadable inbox must block writes and signal dashboard failure."""
        payload = {"event_id": "monitor:1", "kind": "monitor", "title": "Monitor",
                   "summary": "No change.", "source_url": "https://example.test/"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.jsonl"
            with patch.object(Path, "read_text", side_effect=PermissionError("private canary")):
                self.assertEqual(harpa_inbox.snapshot(path), {"status": "error", "items": []})
                with self.assertRaises(harpa_inbox.HarpaStorageError):
                    harpa_inbox.store(payload, path)
            self.assertFalse(path.exists())

    def test_harpa_failed_write_keeps_existing_state_and_removes_temporary_file(self):
        """Zero writes, fsync and rename failures must not acknowledge or replace reports."""
        payload = {"event_id": "monitor:1", "kind": "monitor", "title": "Monitor",
                   "summary": "No change.", "source_url": "https://example.test/"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inbox.jsonl"
            harpa_inbox.store(payload, path)
            before = path.read_bytes()
            for name, options in (("write", {"side_effect": [0, AssertionError("retried a zero write")]}),
                                  ("write", {"side_effect": OSError("disk full")}),
                                  ("fsync", {"side_effect": OSError("flush failed")}),
                                  ("replace", {"side_effect": OSError("rename failed")})):
                with self.subTest(operation=name, options=options):
                    with patch.object(harpa_inbox.os, name, **options):
                        with self.assertRaises(OSError):
                            harpa_inbox.store({**payload, "event_id": "monitor:2"}, path)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(list(path.parent.iterdir()), [path])


class HostCheckTest(unittest.TestCase):
    """A browser page must not reach the deck through a rebound DNS name."""

    def setUp(self):
        """Start an isolated HTTP server with temporary HARPA state and tracked actions."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
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
        self.deck.harpa_inbox = Path(self.tmp.name) / "harpa.jsonl"
        self.deck.harpa_token_file = Path(self.tmp.name) / "harpa-token"
        self.deck.harpa_token_file.write_text("t" * 48)
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

    def post_harpa(self, *, token="t" * 48, payload=None):
        """Post a supplied or default report using a trusted host and HARPA bearer token."""
        body = json.dumps(payload or {
            "event_id": "monitor:1",
            "kind": "monitor",
            "title": "Public page changed",
            "summary": "A public page changed.",
            "source_url": "https://example.test/page",
        }).encode()
        return self.request(
            "POST", "/api/harpa", f"127.0.0.1:{self.port}",
            {"Content-Type": "application/json", "Authorization": f"Bearer {token}",
             "Origin": "chrome-extension://harpa"}, body,
        )

    def test_loopback_host_reads_mail(self):
        for name in ("127.0.0.1", "localhost"):
            with self.subTest(name=name):
                status, body = self.request("GET", "/api/mail", f"{name}:{self.port}")
                self.assertEqual(status, 200)
                self.assertIn(b"MAIL-CANARY", body)

    def test_agents_route_uses_the_selected_repository(self):
        with patch.object(server, "agent_hub", return_value={"agents": []}) as hub:
            status, _ = self.request("GET", "/api/agents", f"127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        hub.assert_called_once_with(self.deck.repo)

    def test_foreign_host_is_refused_before_any_route(self):
        """Reject foreign Host headers before any dashboard or snapshot route is served."""
        for path in ("/", "/api/status", "/api/mail", "/api/agents", "/api/harpa"):
            with self.subTest(path=path):
                status, body = self.request("GET", path, f"attacker.example:{self.port}")
                self.assertEqual(status, 421)
                self.assertNotIn(b"MAIL-CANARY", body)

    def test_harpa_report_requires_its_own_token(self):
        """Reject an incorrect HARPA token without creating an inbox."""
        self.assertEqual(self.post_harpa(token="wrong" * 8)[0], 401)
        self.assertFalse(self.deck.harpa_inbox.exists())

    def test_harpa_ingress_refusals_never_store_a_report(self):
        body = json.dumps({"event_id": "guard", "kind": "monitor", "title": "guard",
                           "summary": "guard", "source_url": "https://example.test"}).encode()
        cases = [
            ({"Content-Type": "application/json"}, body, 401),
            ({"Content-Type": "text/plain", "Authorization": "Bearer " + "t" * 48}, body, 415),
            ({"Content-Type": "application/json", "Authorization": "Bearer " + "t" * 48},
             b" " * 16385, 413),
            ({"Content-Type": "application/json", "Authorization": "Bearer " + "t" * 48},
             b"", 413),
        ]
        for headers, data, status in cases:
            with self.subTest(status=status, length=len(data)):
                self.assertEqual(self.request("POST", "/api/harpa", f"127.0.0.1:{self.port}",
                                              headers, data)[0], status)
                self.assertFalse(self.deck.harpa_inbox.exists())

    def test_harpa_missing_short_or_unreadable_credentials_disable_ingress(self):
        original = self.deck.harpa_token_file
        for token in ("short", "t" * 257, "t" * 33 + "\n" + "t" * 33):
            with self.subTest(token_length=len(token)):
                original.write_text(token)
                self.assertEqual(self.post_harpa()[0], 404)
                self.assertFalse(self.deck.harpa_inbox.exists())
        original.unlink()
        self.assertEqual(self.post_harpa()[0], 404)
        self.deck.harpa_token_file = None
        self.assertEqual(self.post_harpa()[0], 404)
        original.write_text("t" * 48)
        self.deck.harpa_token_file = original
        self.deck.harpa_inbox = None
        self.assertEqual(self.post_harpa()[0], 404)

    def test_harpa_report_is_stored_but_cannot_request_an_action(self):
        """Accept and expose a report while leaving the action runner untouched."""
        status, body = self.post_harpa(payload={
            "event_id": "research:1",
            "kind": "research",
            "title": "Research result",
            "summary": "Public-source summary only.",
            "source_url": "https://example.test/research",
        })
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(body)["accepted"])
        self.assertEqual(self.actions, [])
        status, body = self.request("GET", "/api/harpa", f"127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["items"][0]["event_id"], "research:1")

    def test_harpa_report_rejects_command_fields(self):
        """Reject reports containing an action field without invoking the action runner."""
        status, _ = self.post_harpa(payload={
            "event_id": "attack:1", "kind": "report", "title": "No",
            "summary": "No", "source_url": "https://example.test/", "action": "tests",
        })
        self.assertEqual(status, 400)
        self.assertEqual(self.actions, [])

    def test_harpa_invalid_utf8_and_non_object_json_return_400(self):
        """Malformed bytes are client errors without tracebacks or stored reports."""
        for body in (b"\xff", b"[]", b"null"):
            with self.subTest(body=body):
                status, _ = self.request(
                    "POST", "/api/harpa", f"127.0.0.1:{self.port}",
                    {"Content-Type": "application/json", "Authorization": "Bearer " + "t" * 48},
                    body)
                self.assertEqual(status, 400)
        self.assertFalse(self.deck.harpa_inbox.exists())

    def test_harpa_malformed_authority_returns_400(self):
        self.assertEqual(self.post_harpa(payload={
            "event_id": "monitor:1", "kind": "monitor", "title": "Monitor",
            "summary": "No change.", "source_url": "https://[broken",
        })[0], 400)
        self.assertFalse(self.deck.harpa_inbox.exists())

    def test_harpa_persistence_failures_are_503_without_exception_details(self):
        for error in (OSError("private-storage-canary"),
                      harpa_inbox.HarpaStorageError("private-corruption-canary")):
            with self.subTest(error=error):
                with patch.object(server, "store_harpa", side_effect=error):
                    status, body = self.post_harpa()
                self.assertEqual(status, 503)
                self.assertNotIn(b"private-", body)

    def test_harpa_corrupt_inbox_stays_unavailable_over_http(self):
        self.deck.harpa_inbox.write_text("not-json\n")
        self.assertEqual(self.post_harpa()[0], 503)
        status, body = self.request("GET", "/api/harpa", f"127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "error", "items": []})
        self.assertEqual(self.deck.harpa_inbox.read_text(), "not-json\n")

    def test_harpa_invalid_utf8_credential_disables_ingress(self):
        self.deck.harpa_token_file.write_bytes(b"\xff" * 48)
        self.assertEqual(self.post_harpa()[0], 404)

    def test_harpa_non_ascii_authorization_returns_401(self):
        self.assertEqual(self.post_harpa(token="ä" * 48)[0], 401)
        self.assertFalse(self.deck.harpa_inbox.exists())

    def test_harpa_non_ascii_credential_disables_ingress(self):
        self.deck.harpa_token_file.write_text("ä" * 48, encoding="utf-8")
        self.assertEqual(self.post_harpa()[0], 404)
        self.assertFalse(self.deck.harpa_inbox.exists())

    def test_harpa_parser_recursion_is_a_payload_or_storage_error(self):
        """Exercise stdlib's Python scanner because C decoder depth limits vary by version."""
        nested = b"[" * 1100 + b"0" + b"]" * 1100
        decoder = json.JSONDecoder()
        decoder.scan_once = json.scanner.py_make_scanner(decoder)

        def decode(value):
            return decoder.decode(value.decode("utf-8") if isinstance(value, bytes) else value)

        with patch.object(json, "loads", side_effect=decode):
            with self.assertRaises(RecursionError):
                decode(nested)
            status, _ = self.request(
                "POST", "/api/harpa", f"127.0.0.1:{self.port}",
                {"Content-Type": "application/json", "Authorization": "Bearer " + "t" * 48},
                nested)
            self.assertEqual(status, 400)
            self.assertFalse(self.deck.harpa_inbox.exists())
            self.deck.harpa_inbox.write_bytes(nested + b"\n")
            self.assertEqual(harpa_inbox.snapshot(self.deck.harpa_inbox),
                             {"status": "error", "items": []})
            self.assertEqual(self.post_harpa()[0], 503)
        self.assertEqual(self.deck.harpa_inbox.read_bytes(), nested + b"\n")

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

    def test_action_route_type_and_size_refusals_precede_execution(self):
        valid = b'{"action":"git_status"}'
        for path, content_type, body, expected in (
            ("/api/unknown", "application/json", valid, 404),
            ("/api/action", "text/plain", valid, 415),
            ("/api/action", "application/json", valid + b" " * 1024, 413),
            ("/api/action", "application/json", b"", 413),
        ):
            with self.subTest(path=path, content_type=content_type, length=len(body)):
                status, _ = self.request("POST", path, f"127.0.0.1:{self.port}",
                                         {"Content-Type": content_type}, body)
                self.assertEqual(status, expected)
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
    @patch("tools.control_deck.checks._which", return_value="/x/hermes")
    @patch("tools.control_deck.checks._run")
    def test_hermes_installation_probe_never_invokes_the_writing_launcher(self, run, which):
        result = checks.tool_status("hermes")
        self.assertEqual(result["status"], "green")
        self.assertIn("ungeprüft", result["detail"])
        run.assert_not_called()

    @patch("tools.control_deck.actions.subprocess.run")
    def test_agent_start_is_not_an_http_action(self, run):
        for name in ("grok_build", "hermes", "start_agent", "grok; id"):
            with self.assertRaises(ValueError):
                actions.run_action(name)
        run.assert_not_called()

    @patch("tools.control_deck.actions._which", return_value="/bin/tool")
    @patch("tools.control_deck.actions.subprocess.run")
    def test_unknown_terminal_agent_is_refused(self, run, which):
        with self.assertRaisesRegex(actions.AgentStartRefused, "allowlisted"):
            actions.start_agent("../../shell", detach=True)
        run.assert_not_called()
        which.assert_not_called()

    @patch("tools.control_deck.actions._which", return_value=None)
    @patch("tools.control_deck.actions.subprocess.run")
    def test_missing_client_is_refused(self, run, which):
        with self.assertRaises(actions.AgentStartRefused):
            actions.start_agent("hermes", detach=True)
        run.assert_not_called()

    @patch("tools.control_deck.actions.subprocess.run")
    def test_grok_build_is_paused_and_cannot_start(self, run):
        with self.assertRaises(actions.AgentStartRefused):
            actions.start_agent("grok_build", detach=True)
        run.assert_not_called()

    def test_agent_start_refusals_are_mutation_guarded(self):
        self.assertIn("tools/control_deck/actions.py", refusals.GUARDED)
        self.assertIn("AgentStartRefused", refusals._REFUSAL_RAISES)

    @patch("tools.control_deck.checks._run", return_value=(0, "1:hermes"))
    def test_dead_terminal_is_not_running(self, run):
        self.assertEqual(checks.agent_session("hermes")["status"], "yellow")

    @patch("tools.control_deck.checks._run", return_value=(0, "0:hermes"))
    def test_live_terminal_is_reported(self, run):
        self.assertEqual(checks.agent_session("hermes")["status"], "green")

    @patch("tools.control_deck.checks.agent_session", return_value={"status": "yellow", "detail": "stopped"})
    @patch("tools.control_deck.checks.tool_status", return_value={"status": "green", "detail": "installed"})
    def test_abacus_replaces_grok_build_without_a_server_start(self, tool, session):
        cards = checks.agent_hub()["agents"]
        self.assertEqual([c["id"] for c in cards], ["grok", "grok_bot", "abacus", "hermes"])
        abacus = cards[2]
        self.assertEqual(abacus["url"], "https://apps.abacus.ai/chatllm/")
        self.assertNotIn("command", abacus)
        self.assertNotIn("action", abacus)
        self.assertIn("Browser", abacus["detail"])
        self.assertNotIn("command", cards[1])
        self.assertIn("kein unterstützter", cards[1]["detail"])

    def test_clipboard_success_is_only_reported_after_success(self):
        html = (Path(__file__).parents[1] / "tools/control_deck/static/index.html").read_text()
        self.assertIn("if(await copyCommand(b.dataset.resumeCmd)){", html)
        self.assertNotIn("await copyCommand(b.dataset.resumeCmd);const old=b.textContent", html)

    def test_control_deck_docs_list_agents_route_and_action_commas(self):
        """Keep the documented route and action lists consistent with the dashboard API."""
        text = (Path(__file__).parents[1] / "docs/CONTROL-DECK.md").read_text()
        self.assertIn("`/`, `/api/status`, `/api/mail`, `/api/agents` und `/api/harpa`", text)
        self.assertIn("`git_status`, `tests`, `demo`, `docker_status`", text)


if __name__ == "__main__":
    unittest.main()
