from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tools.control_deck import checks


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


if __name__ == "__main__":
    unittest.main()