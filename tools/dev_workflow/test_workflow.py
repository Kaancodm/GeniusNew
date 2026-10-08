"""Checks for real Git workspaces and exclusive implementation sessions."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("workflow", Path(__file__).with_name("genius_workflow.py"))
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class WorkspaceChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "--initial-branch=main")
        self.git("config", "user.name", "Workflow Test")
        self.git("config", "user.email", "workflow@example.invalid")
        self.git("commit", "-q", "--allow-empty", "-m", "test fixture")
        self.git("remote", "add", "origin", "https://github.com/Kaancodm/GeniusNew.git")
        self.state = Path(self.temp.name) / "runtime"

    def tearDown(self):
        subprocess.run(["tmux", "-S", str(self.state / "tmux.sock"), "kill-server"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_main_is_not_a_writing_workspace(self):
        with self.assertRaises(SystemExit):
            workflow.workspace(str(self.repo))

    def test_named_geniusnew_branch_is_accepted(self):
        self.git("switch", "-q", "-c", "workflow/test")
        self.assertEqual(workflow.workspace(str(self.repo)), self.repo.resolve())

    def test_detached_head_is_rejected(self):
        self.git("checkout", "-q", "--detach")
        with self.assertRaises(SystemExit):
            workflow.workspace(str(self.repo))

    def test_other_repository_is_rejected(self):
        self.git("switch", "-q", "-c", "workflow/test")
        self.git("remote", "set-url", "origin", "https://github.com/other/repo.git")
        with self.assertRaises(SystemExit):
            workflow.workspace(str(self.repo))

    def test_second_writer_stops_but_review_can_start(self):
        self.git("switch", "-q", "-c", "workflow/test")
        with patch.object(workflow, "RUNTIME", self.state):
            base = workflow.tmux_base()
            first = workflow.session_name("codex", self.repo)
            subprocess.run(base + ["new-session", "-d", "-s", first, "cat"], check=True)
            with patch.object(workflow, "argv_for", return_value=(["cat"], dict(os.environ))):
                with self.assertRaises(SystemExit):
                    workflow.start("claude", str(self.repo), False, False)
                workflow.start("claude", str(self.repo), True, False)
            rc = subprocess.run(base + ["has-session", "-t", "=" +
                                        workflow.session_name("claude", self.repo, True)])
            self.assertEqual(rc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
