from __future__ import annotations

import argparse
import ast
import http.client
import json
import os
import re
import runpy
import shlex
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.control_deck import actions, checks, mail_center, server
from scripts import refusals


class ControlDeckTest(unittest.TestCase):
    def test_default_repo_follows_the_checkout_without_an_environment_override(self):
        """Resolve the default checkout from the module rather than the working directory."""
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp).resolve() / "another checkout"
            module = checkout / "tools/control_deck/checks.py"
            module.parent.mkdir(parents=True)
            module.write_text(Path(checks.__file__).read_text(encoding="utf-8"),
                              encoding="utf-8")
            with patch.dict(os.environ):
                os.environ.pop("GENIUSNEW_REPO", None)
                loaded = runpy.run_path(str(module))
            self.assertNotEqual(checkout, Path.cwd())
            self.assertEqual(loaded["DEFAULT_REPO"], checkout)

    def test_explicit_repo_override_is_preserved_even_when_missing(self):
        """Keep an explicit missing checkout visible as a red repository status."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "configured checkout"
            with patch.dict(os.environ, {"GENIUSNEW_REPO": str(repo)}):
                loaded = runpy.run_path(checks.__file__)
            self.assertEqual(loaded["DEFAULT_REPO"], repo)
            self.assertEqual(loaded["repo_status"](),
                             {"status": "red", "detail": "repo missing"})

    def test_fresh_host_probe_precedes_the_portal(self):
        """Place the fresh-host gate before portal work and select it as the next focus."""
        order = [gate for gate, _, _ in checks.BETA_GATES]
        self.assertLess(order.index("C5"), order.index("E3"))
        for gate in ("D1", "D2", "D3"):
            self.assertLess(order.index("E3"), order.index(gate))
        gates = [{"id": gate, "name": name,
                  "status": "red" if gate in ("E3", "D1", "D2", "D3", "E2") else "green",
                  "detail": "evidence pending"}
                 for gate, name, _ in checks.BETA_GATES]
        tools = {name: {"status": "green"} for name in ("codex", "claude", "gemini", "gh")}
        with patch.object(checks, "_run", return_value=(0, "abc123")):
            state = checks.project_resume(gates, tools, checks.DEFAULT_REPO)
        self.assertTrue(state["focus"].startswith("E3"))


    @patch("tools.control_deck.checks._run", return_value=(0, ""))
    def test_merged_gate_proof_is_checked_against_origin_main(self, run):
        """Use the locally cached origin/main ref to verify merged gate evidence."""
        proof = next(ref for gate, _, ref in checks.BETA_GATES if gate == "B2")
        self.assertEqual(checks.gate_status(checks.DEFAULT_REPO, proof), "green")
        self.assertEqual(run.call_args_list[-1].args[0],
                         ["git", "merge-base", "--is-ancestor", proof,
                          "refs/remotes/origin/main"])


    @patch("tools.control_deck.checks._run", return_value=(0, "abc123"))
    @patch("tools.control_deck.checks.tool_status", return_value={"status": "green"})
    def test_merged_code_keeps_missing_review_and_operational_evidence_open(self, _tools, _run):
        """Keep review and operational holds open despite merged implementations."""
        snapshot = checks.snapshot(checks.DEFAULT_REPO)
        gates = {gate["id"]: gate for gate in snapshot["gates"]}
        for gate in ("B4", "B5", "A2", "C2", "C5"):
            self.assertEqual(gates[gate]["status"], "yellow")
            self.assertIn("HOLD", gates[gate]["detail"])
        self.assertEqual(gates["C3"]["status"], "green")
        self.assertEqual(gates["E1"]["status"], "green")
        self.assertEqual(gates["C5"]["implementation_status"], "green")
        for gate in ("B4", "B5"):
            self.assertIn("Gemini-Sicherheitsreview zu B4/B5 noch offen", gates[gate]["detail"])
            self.assertIn("E2-Gesamtreview noch offen", gates[gate]["detail"])
            self.assertNotIn("Claude-DB-Review am PR-Head", gates[gate]["detail"])
            self.assertNotIn("vorhanden", gates[gate]["detail"])
        state = snapshot["project"]
        self.assertGreater(state["merged_gates"],
                           sum(gate["status"] == "green" for gate in gates.values()))
        self.assertEqual(state["focus"], "B4 – Pending Jobs + Approvals")
        self.assertTrue(state["resume"][0]["value"].endswith("/pull/94"))
        self.assertFalse(any(step["title"].startswith("B1 ") for step in state["resume"]))


    @patch("tools.control_deck.checks._run", return_value=(0, "abc123"))
    @patch("tools.control_deck.checks.tool_status", return_value={"status": "green"})
    def test_merged_c3_uses_its_documented_head_review(self, _tools, _run):
        """Do not reopen C3 after its head review was documented in decisions."""
        gates = checks.snapshot(checks.DEFAULT_REPO)["gates"]
        c3 = next(gate for gate in gates if gate["id"] == "C3")
        self.assertEqual(c3["implementation_status"], "green")
        self.assertEqual(c3["status"], "green")
        self.assertNotIn("HOLD", c3["detail"])
        tools = {name: {"status": "green"} for name in ("codex", "gh", "gemini")}
        project = checks.project_resume([c3], tools, checks.DEFAULT_REPO)
        self.assertEqual(project["focus"], "Implementierungen auf main nachgewiesen")
        self.assertEqual(project["resume"], [])


    @patch("tools.control_deck.checks._run", return_value=(0, "abc123"))
    @patch("tools.control_deck.checks.tool_status", return_value={"status": "green"})
    def test_local_snapshot_never_claims_live_ci_or_release_approval(self, _tools, _run):
        """Label local evidence explicitly and withhold CI and release approval."""
        project = checks.snapshot(checks.DEFAULT_REPO)["project"]
        self.assertFalse(project["release_ready"])
        self.assertEqual(project["ci"]["status"], "yellow")
        self.assertIn("nicht live abgefragt", project["ci"]["detail"])
        self.assertIn("Lokaler origin/main-Stand", project["evidence_note"])
        self.assertIn("keine Live-Abfrage", project["evidence_note"])


    def test_commands_follow_selected_checkout_including_spaces(self):
        """Quote the selected checkout and its virtualenv in copied commands."""
        import shlex
        with tempfile.TemporaryDirectory(prefix="deck checkout ") as tmp:
            root = Path(tmp)
            executable = root / ".venv/bin/python3"
            executable.parent.mkdir(parents=True)
            executable.touch()
            commands = checks.commands_for(root)
            self.assertTrue(commands["tests"].startswith("cd " + shlex.quote(str(root)) + " && "))
            self.assertIn(shlex.quote(str(executable)), commands["tests"])
            self.assertTrue(all("~/GeniusNew" not in command for command in commands.values()))


    @patch("tools.control_deck.checks._run", return_value=(0, "abc123"))
    def test_gemini_resume_uses_the_available_cli_without_probing_a_model(self, run):
        """Choose an available Gemini launcher for manual authentication checks."""
        gates = [{"id": gate, "name": name, "status": "green", "detail": "merged"}
                 for gate, name, _ in checks.BETA_GATES]
        tools = {name: {"status": "green"} for name in ("codex", "gh", "gemini")}
        tools["gemini"] = {"status": "yellow"}
        for available, expected in ((None, "gemini"), ("/x/agy", "agy")):
            with self.subTest(antigravity=available), patch.object(checks, "_which", return_value=available):
                step = checks.project_resume(gates, tools, checks.DEFAULT_REPO)["resume"][0]
                self.assertEqual(step["value"], expected)
                self.assertIn("Anmeldung prüfen", step["title"])
                self.assertIn("keine Anmeldung", step["detail"])
        self.assertFalse(any("Reply only OK" in str(call) for call in run.call_args_list))

    @patch("tools.control_deck.checks._run", return_value=(0, "abc123"))
    def test_unverified_gemini_login_does_not_hide_the_next_gate(self, run):
        """Keep gates actionable when Gemini is installed with unverified authentication."""
        gates = [{"id": gate, "name": name, "status": "green", "detail": "merged"}
                 for gate, name, _ in checks.BETA_GATES]
        next(gate for gate in gates if gate["id"] == "B4")["status"] = "yellow"
        tools = {name: {"status": "green"} for name in ("codex", "gh", "gemini")}
        with patch.object(checks, "_which", side_effect=lambda name: "/x/gemini" if name == "gemini" else None):
            tools["gemini"] = checks.tool_status("gemini")
            self.assertEqual(tools["gemini"]["status"], "yellow")
            state = checks.project_resume(gates, tools, checks.DEFAULT_REPO)
            self.assertEqual(state["focus"], "B4 – Pending Jobs + Approvals")
            self.assertTrue(checks._priorities(gates, tools)[0]["title"].startswith("B4 "))
            manual = next(step for step in state["resume"] if step["value"] == "gemini")
            self.assertIn("ungeprüft", manual["title"])
            self.assertFalse(state["release_ready"])
            for status, detail in (("red", "missing"), ("yellow", "login required")):
                tools["gemini"] = {"status": status, "detail": detail}
                self.assertIn("Gate 0", checks.project_resume(gates, tools, checks.DEFAULT_REPO)["focus"])
                self.assertEqual(checks._priorities(gates, tools)[0]["title"], "gemini anmelden")

    def test_relative_checkout_keeps_an_absolute_virtualenv_symlink_path(self):
        """Make a relative checkout's venv path absolute without resolving its Python link."""
        import sys
        with tempfile.TemporaryDirectory(dir=Path.cwd(), prefix="deck relative ") as tmp:
            root = Path(tmp)
            executable = root / ".venv/bin/python3"
            executable.parent.mkdir(parents=True)
            executable.symlink_to(sys.executable)
            relative = root.relative_to(Path.cwd())
            self.assertEqual(checks.python_for(relative), str(executable))
            command = shlex.split(checks.commands_for(relative)["tests"])
            self.assertEqual(command[3], str(executable))
            self.assertNotEqual(command[3], str(executable.resolve()))

    def test_beta_gate_count_is_fixed(self):
        """Preserve the 21-gate roadmap with A1 first and the final security review last."""
        self.assertEqual(len(checks.BETA_GATES), 21)
        self.assertEqual(checks.BETA_GATES[0][0], "A1")
        self.assertEqual(checks.BETA_GATES[-1][0], "E2")

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
        self.assertIn("quota unverified", state["detail"])
        run.assert_called_once_with(
            ["/x/agy", "models"], cwd=checks.Path.home(), timeout=8.0
        )

    @patch("tools.control_deck.checks.subprocess.run")
    @patch("tools.control_deck.checks._which")
    def test_gemini_cli_status_never_starts_a_process(self, which, run):
        """Report standalone Gemini authentication as unverified without spawning a process."""
        which.side_effect = lambda name: "/x/gemini" if name == "gemini" else None
        run.return_value.returncode = 0
        run.return_value.stdout = '{"response":"OK"}'
        for _ in range(2):
            state = checks.tool_status("gemini")
            self.assertEqual(run.call_count, 0, "Status probes must not start a process")
            self.assertEqual(state, {
                "status": "yellow",
                "detail": "CLI installiert · Anmeldung ungeprüft",
                "auth_status": "unverified",
            })

    @patch("tools.control_deck.checks.subprocess.run")
    @patch("tools.control_deck.checks._which", return_value=None)
    def test_missing_gemini_cli_does_not_start_a_process(self, which, run):
        """Report a missing Gemini installation without spawning a process."""
        self.assertEqual(checks.tool_status("gemini"),
                         {"status": "red", "detail": "missing"})
        self.assertEqual(run.call_count, 0, "Missing clients must not start a process")

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

    @patch("tools.control_deck.actions.subprocess.run")
    def test_concurrent_action_is_refused_before_process_start(self, run):
        with actions._ACTION_LOCK:
            result = actions.run_action("tests")
        self.assertFalse(result["ok"])
        self.assertIn("läuft bereits", result["output"])
        run.assert_not_called()

    @patch("tools.control_deck.actions.subprocess.run")
    def test_action_lock_is_released_after_process_error(self, run):
        run.side_effect = [UnicodeDecodeError("utf8", b"x", 0, 1, "bad"),
                           subprocess.CompletedProcess([], 0, "OK")]
        with self.assertRaises(UnicodeDecodeError):
            actions.run_action("git_status")
        self.assertTrue(actions.run_action("git_status")["ok"])
        self.assertEqual(run.call_count, 2)

    @patch("tools.control_deck.actions.subprocess.run")
    def test_tests_action_uses_the_same_interpreter_as_terminal_commands(self, run):
        """Use the same checkout interpreter for HTTP test actions and copied commands."""
        run.return_value.returncode = 0
        run.return_value.stdout = "OK"
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "checkout with spaces"
            venv_python = repo / ".venv/bin/python3"
            venv_python.parent.mkdir(parents=True)
            for has_venv in (True, False):
                with self.subTest(has_venv=has_venv):
                    if has_venv:
                        venv_python.touch()
                    else:
                        venv_python.unlink()
                    expected = str(venv_python) if has_venv else "python3"
                    result = actions.run_action("tests", repo)
                    self.assertTrue(result["ok"])
                    argv = run.call_args.args[0]
                    self.assertEqual(argv, [expected, "-m", "unittest", "discover",
                                            "-s", "tests", "-q"])
                    self.assertEqual(shlex.split(checks.commands_for(repo)["tests"])[3:], argv)
                    self.assertEqual(run.call_args.kwargs["cwd"], repo)
                    self.assertEqual(run.call_args.kwargs["timeout"], 240.0)
                    self.assertNotIn("shell", run.call_args.kwargs)

    @patch("tools.control_deck.checks._run", return_value=(0, "abc123"))
    def test_project_resume_starts_at_gate_zero_when_logins_are_missing(self, _run):
        """Prioritize missing tool logins while retaining the next open gate."""
        gates = [{"id": gate, "name": name, "status": "yellow" if ref else "red", "detail": "review pending"}
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
        self.assertTrue(any(step["title"].startswith("A1 ") for step in state["resume"]))


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

    def test_agents_route_uses_the_selected_repository(self):
        with patch.object(server, "agent_hub", return_value={"agents": []}) as hub:
            status, _ = self.request("GET", "/api/agents", f"127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        hub.assert_called_once_with(self.deck.repo)

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


class ControlDeckInterfaceTest(unittest.TestCase):
    """Keep the dashboard's safety context wired into its rendered interface."""

    def setUp(self):
        """Load the dashboard source for interface contract assertions."""
        self.html = (Path(__file__).parents[1] /
                     "tools/control_deck/static/index.html").read_text(encoding="utf-8")

    def test_four_tabs_dark_mobile_layout_and_all_copy_controls_remain(self):
        """Preserve the four tabs, dark mobile layout, and guarded copy confirmations."""
        self.assertEqual(self.html.count('class="tab'), 5)  # four buttons and their container
        for view in ("project", "dev", "market", "reports"):
            self.assertIn('data-tab="' + view + '"', self.html)
        self.assertIn("color-scheme:dark", self.html)
        self.assertIn("@media", self.html)
        self.assertIn('name="viewport"', self.html)
        for dataset in ("cmd", "resumeCmd", "agentCmd"):
            self.assertIn("if(await copyText(b.dataset." + dataset + ")){", self.html)

    def test_research_source_is_escaped_in_cards_and_visible_in_details(self):
        """Escape research sources in cards and include them in the detail panel."""
        self.assertIn("data-source=", self.html)
        self.assertIn("esc(x.source||'Quelle ungeprüft')", self.html)
        self.assertIn("Quelle: '+b.dataset.source", self.html)

    def test_mail_renders_all_categories_and_escapes_message_metadata(self):
        """Render every mail category with escaped subject and sender metadata."""
        rendering = self.html.split("function mailRows(", 1)[1].split("function wire()", 1)[0]
        for category, target in (("sales", "salesMail"), ("sponsoring", "sponsorMail"),
                                 ("general", "generalMail"), ("system", "systemMail")):
            with self.subTest(category=category):
                self.assertIn(f'id="{target}"', self.html)
                self.assertIn(f"mailRows('{category}',$('{target}'))", rendering)
        rows = rendering.split("function renderMail()", 1)[0]
        self.assertIn("esc(x.subject||'(ohne Betreff)')", rows)
        self.assertIn("esc(x.sender||'')", rows)

    def test_agent_cards_render_escaped_limits_status_and_model_check_dates(self):
        """Retain escaped agent caveats, availability labels, and model verification dates."""
        agents = self.html.split("async function loadAgents(){", 1)[1].split(
            "document.querySelectorAll('.tab')", 1)[0]
        card = agents.split("return '<article", 1)[1].split("</article>", 1)[0]
        self.assertIn("esc(a.note||'')", card)
        self.assertIn("esc(a.status_label||'')", card)
        self.assertIn("+disabled+", card)
        self.assertIn("'<button disabled>'+esc(a.disabled_label)+'</button>'", agents)
        models = agents.split(".map(m=>", 1)[1].split(".join('')", 1)[0]
        self.assertIn("Geprüft: ", models)
        self.assertIn("esc(m.verified_on||'ungeprüft')", models)

    def test_run_all_locks_before_dispatch_and_releases_in_finally(self):
        """Guard batch actions against duplicate dispatch and always restore button state."""
        handler = self.html.split("$('runAll').onclick=async()=>{", 1)[1].split(
            "notesLoad();load();", 1)[0]
        for required in ("if($('runAll').disabled)return", "$('runAll').disabled=true",
                         "try{", "finally{", "$('runAll').disabled=!state"):
            self.assertIn(required, handler)
        guard = handler.index("if($('runAll').disabled)return")
        lock = handler.index("$('runAll').disabled=true")
        protected = handler.index("try{")
        dispatch = handler.index("await fetch('/api/action'")
        cleanup = handler.index("finally{")
        unlock = handler.index("$('runAll').disabled=!state")
        self.assertLess(guard, lock)
        self.assertLess(lock, protected)
        self.assertLess(protected, handler.index("for(const a of acts)"))
        self.assertLess(protected, dispatch)
        self.assertLess(dispatch, cleanup)
        self.assertLess(cleanup, unlock)
        self.assertIn("$('runAll').disabled=!state",
                      handler.rsplit("finally{", 1)[1].split("}", 1)[0])

    def test_run_all_waits_for_refresh_before_releasing_locks_even_on_failure(self):
        """Keep batch locks through refresh and release them even when it fails."""
        handler = self.html.split("$('runAll').onclick=async()=>{", 1)[1].split(
            "notesLoad();load();", 1)[0]
        cleanup = handler.split("finally{", 1)[1]
        self.assertIn("await load()", cleanup)
        refresh = cleanup.index("await load()")
        self.assertIn("try{", cleanup[:refresh])
        self.assertIn("}finally{", cleanup[refresh:])
        release = cleanup.index("}finally{", refresh)
        finalizer = cleanup[release:].split("}", 2)[1]
        for reset in ("runAllRunning=false", "$('runAll').disabled=!state"):
            self.assertNotIn(reset, cleanup[:release])
            self.assertIn(reset, finalizer)
        self.assertLess(finalizer.index("runAllRunning=false"),
                        finalizer.index("$('runAll').disabled=!state"))

    def test_mail_and_status_load_independently_and_report_failures(self):
        """Load status and mail independently and expose each request's failure state."""
        self.assertIn("async function loadStatus(){", self.html)
        self.assertIn("async function loadMail(){", self.html)
        load = self.html.split("async function load(){", 1)[1].split("function wire()", 1)[0]
        self.assertIn("Promise.all([loadStatus(),loadMail()])", load)
        mail = self.html.split("async function loadMail(){", 1)[1].split("async function", 1)[0]
        status = self.html.split("async function loadStatus(){", 1)[1].split("async function", 1)[0]
        for handler, endpoint in ((mail, "/api/mail"), (status, "/api/status")):
            self.assertIn(f"fetch('{endpoint}'", handler)
            self.assertIn("if(!r.ok)throw Error('HTTP '+r.status)", handler)
            self.assertIn("catch(e)", handler)
        self.assertNotIn("/api/mail", status)
        self.assertNotIn("/api/status", mail)
        self.assertIn("mail=null;renderMail()", mail.split("catch(e)", 1)[1])
        self.assertIn("$('focus').textContent='Status nicht verfügbar: '", status)
        rendering = self.html.split("function renderMail(){", 1)[1].split("async function", 1)[0]
        self.assertIn("Mail-Snapshot nicht synchronisiert", rendering)

    def test_status_failure_invalidates_every_rendered_status_field_and_action(self):
        """Clear stale status fields and disable their actions after a failed refresh."""
        status = self.html.split("async function loadStatus(){", 1)[1].split(
            "async function load(){", 1)[0]
        success, failure = status.split("}catch(e){", 1)
        reports = self.html.split("function renderReports(){", 1)[1].split(
            "function mailRows(", 1)[0]
        rendered = set(re.findall(
            r"\$\('([^']+)'\)\.(?:innerHTML|textContent)=", success + reports))
        cleared = re.search(
            r"for\(const id of (\[[^]]+\])\)\$\(id\)\.textContent=''", failure)
        self.assertIsNotNone(cleared, "Status failure must clear the rendered snapshot")
        cleared_ids = set(ast.literal_eval(cleared.group(1)))
        self.assertFalse(rendered - {"stamp", "focus"} - cleared_ids,
                         "Every status-derived field, including reports, must be invalidated")
        self.assertIn("if($('poutput').hidden){$('ptitle').textContent='';$('pbody').textContent=''", failure)
        for required in ("state=null", "$('runAll').dataset.actions=''",
                         "$('runAll').disabled=true", "$('prun').dataset.action=''",
                         "$('prun').hidden=true"):
            self.assertIn(required, failure)
        self.assertIn("$('runAll').disabled=runAllRunning", success)

    def test_action_buttons_show_failures_unlock_and_refresh_after_errors(self):
        """Show action errors and restore button state in the required refresh order."""
        single = self.html.split("async function runAction(name){", 1)[1].split("function itemRow", 1)[0]
        all_actions = self.html.split("$('runAll').onclick=async()=>{", 1)[1].split(
            "notesLoad();load();", 1)[0]
        for handler, button in ((single, "prun"), (all_actions, "runAll")):
            with self.subTest(button=button):
                for required in ("try{", "catch(e){", "finally{",
                                 "if(!r.ok)throw Error('HTTP '+r.status)"):
                    self.assertIn(required, handler)
                error = handler.split("catch(e){", 1)[1].split("finally{", 1)[0]
                self.assertIn("$('poutput').textContent", error)
                self.assertIn("': FAIL\\n'" if button == "runAll" else "'FAIL\\n'", error)
                if button == "runAll":
                    self.assertIn("active||'Prüfkette'", error)
                self.assertIn("e?.message", error)
                cleanup = handler.split("finally{", 1)[1]
                disabled = "!state" if button == "runAll" else "false"
                self.assertIn(f"$('{button}').disabled={disabled}", cleanup)
                self.assertIn("load()", cleanup)
                if button == "runAll":
                    self.assertLess(cleanup.index("await load()"),
                                    cleanup.index("runAllRunning=false"))
                    self.assertLess(cleanup.index("runAllRunning=false"),
                                    cleanup.index("disabled="))
                else:
                    self.assertLess(cleanup.index("disabled="), cleanup.index("load()"))


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

    @patch("tools.control_deck.checks._which", return_value="/usr/bin/agy")
    @patch("tools.control_deck.checks.agent_session", return_value={"status": "yellow", "detail": "stopped"})
    @patch("tools.control_deck.checks.tool_status", return_value={"status": "green", "detail": "installed"})
    def test_agent_hub_marks_reported_pauses_without_launchers(self, tool, session, which):
        repo = Path("/tmp/genius checkout")
        cards = checks.agent_hub(repo)["agents"]
        self.assertEqual([c["id"] for c in cards],
                         ["codex", "claude", "gemini", "antigravity", "copilot", "kiro",
                          "grok", "grok_bot", "abacus", "hermes"])
        by_id = {card["id"]: card for card in cards}
        self.assertIn("100 %", by_id["codex"]["status_label"])
        self.assertEqual(by_id["codex"]["status"], "yellow")
        self.assertEqual(by_id["gemini"]["status"], "yellow")
        self.assertNotIn("command", by_id["gemini"])
        self.assertEqual(by_id["antigravity"]["command"],
                         f"cd {shlex.quote(str(repo))} && /usr/bin/agy")
        for name in ("copilot", "kiro", "abacus"):
            with self.subTest(name=name):
                card = by_id[name]
                self.assertEqual(card["status"], "red")
                self.assertIn("03.11.2026", card["detail"])
                for key in ("command", "url", "models", "action"):
                    self.assertNotIn(key, card)
        self.assertNotIn("command", by_id["grok_bot"])
        self.assertIn("kein unterstützter", by_id["grok_bot"]["detail"])

    @patch("tools.control_deck.checks._which", return_value=None)
    @patch("tools.control_deck.checks.agent_session", return_value={"status": "yellow", "detail": "stopped"})
    @patch("tools.control_deck.checks.tool_status", return_value={"status": "green", "detail": "installed"})
    def test_agent_hub_does_not_offer_antigravity_when_missing(self, tool, session, which):
        antigravity = next(card for card in checks.agent_hub()["agents"]
                           if card["id"] == "antigravity")
        self.assertEqual(antigravity["status"], "yellow")
        self.assertIsNone(antigravity["command"])
        tool.assert_called_once_with("hermes")

    def test_clipboard_success_is_only_reported_after_success(self):
        """Confirm a copied resume command only after the clipboard helper succeeds."""
        html = (Path(__file__).parents[1] / "tools/control_deck/static/index.html").read_text()
        self.assertIn("if(await copyText(b.dataset.resumeCmd)){", html)
        self.assertNotIn("await copyText(b.dataset.resumeCmd);const old=b.textContent", html)

    def test_clipboard_falls_back_to_a_selection_copy_on_private_http(self):
        """Use selection copying on private HTTP and show a panel if copying fails."""
        html = (Path(__file__).parents[1] / "tools/control_deck/static/index.html").read_text()
        start = html.index("async function copyText(")
        body = html[start:html.index("async function", start + 10)]
        self.assertIn("navigator.clipboard&&window.isSecureContext", body)
        self.assertIn("document.execCommand('copy')", body)
        self.assertLess(body.index("isSecureContext"), body.index("execCommand"))
        self.assertIn("openPanel('TERM-Befehl',v,null);return false", body)

    def test_control_deck_docs_list_agents_route_and_action_commas(self):
        text = (Path(__file__).parents[1] / "docs/CONTROL-DECK.md").read_text()
        self.assertIn("`/`, `/api/status`, `/api/mail` und `/api/agents`", text)
        self.assertIn("`git_status`, `tests`, `demo`, `docker_status`", text)


if __name__ == "__main__":
    unittest.main()
