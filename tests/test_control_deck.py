from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.control_deck import actions, checks, mail_center


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
    def test_missing_gate_branch_is_red(self, run):
        run.return_value = (1, "")
        self.assertEqual(checks.gate_status(checks.DEFAULT_REPO, "origin/x"), "red")

    def test_unimplemented_gate_is_red(self):
        self.assertEqual(checks.gate_status(checks.DEFAULT_REPO, None), "red")

    def test_action_allowlist_is_fixed(self):
        self.assertEqual(
            set(actions.allowed_actions()),
            {"git_status", "tests", "demo", "docker_status"},
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


if __name__ == "__main__":
    unittest.main()