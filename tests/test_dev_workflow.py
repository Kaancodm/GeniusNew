"""Checks for real Git workspaces and exclusive implementation sessions."""
from __future__ import annotations
import os
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.dev_workflow import genius_workflow as workflow


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
        self.main_repo = self.repo
        linked = Path(self.temp.name) / "linked"
        self.git("worktree", "add", "-q", "-b", "workflow/fixture", str(linked))
        self.repo = linked
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
            workflow.workspace(str(self.main_repo))

    def test_named_shared_checkout_is_rejected(self):
        subprocess.run(["git", "-C", str(self.main_repo), "switch", "-q", "-c", "shared/task"], check=True)
        with self.assertRaises(workflow.WorkflowRefused):
            workflow.workspace(str(self.main_repo))

    def test_subdirectory_is_not_a_task_root(self):
        child = self.repo / "child"
        child.mkdir()
        with self.assertRaises(workflow.WorkflowRefused):
            workflow.workspace(str(child))

    def test_named_geniusnew_branch_is_accepted(self):
        self.git("switch", "-q", "-c", "workflow/test")
        for remote in (
            "https://github.com/Kaancodm/GeniusNew.git",
            "https://github.com/Kaancodm/GeniusNew",
            "git@github.com:Kaancodm/GeniusNew.git",
            "ssh://git@github.com/Kaancodm/GeniusNew.git",
        ):
            with self.subTest(remote=remote):
                self.git("remote", "set-url", "origin", remote)
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

    def test_other_hosts_and_ambiguous_remote_urls_are_rejected(self):
        self.git("switch", "-q", "-c", "workflow/test")
        for remote in (
            "https://evilgithub.com/Kaancodm/GeniusNew.git",
            "ssh://git@evilgithub.com/Kaancodm/GeniusNew.git",
            "git@evilgithub.com:Kaancodm/GeniusNew.git",
            "https://github.com@evil.example/Kaancodm/GeniusNew.git",
            "https://evil.example/github.com/Kaancodm/GeniusNew.git",
            "https://github.com/Kaancodm/GeniusNew.git?redirect=evil.example",
            "https://github.com/Kaancodm/GeniusNew.git#fragment",
            "https://github.com/other/../Kaancodm/GeniusNew.git",
            "http://github.com/Kaancodm/GeniusNew.git",
            "file:///github.com/Kaancodm/GeniusNew.git",
        ):
            with self.subTest(remote=remote):
                self.git("remote", "set-url", "origin", remote)
                with self.assertRaises(SystemExit):
                    workflow.workspace(str(self.repo))

    def test_second_session_stops_even_if_a_review_can_change_mode(self):
        self.git("switch", "-q", "-c", "workflow/test")
        with patch.object(workflow, "RUNTIME", self.state):
            base = workflow.tmux_base()
            first = workflow.session_name("codex", self.repo)
            subprocess.run(base + ["new-session", "-d", "-s", first, "cat"], check=True)
            with patch.object(workflow, "argv_for", return_value=(["cat"], dict(os.environ))):
                with self.assertRaises(SystemExit):
                    workflow.start("claude", str(self.repo), False, False)
                for tool, review in (("claude", True), ("gemini-a", False), ("gemini-b", True)):
                    with self.subTest(tool=tool), self.assertRaises(workflow.WorkflowRefused):
                        workflow.start(tool, str(self.repo), review, False)

    def test_existing_review_also_retains_exclusive_ownership(self):
        with patch.object(workflow, "RUNTIME", self.state):
            base = workflow.tmux_base()
            name = workflow.session_name("claude", self.repo, True)
            subprocess.run(base + ["new-session", "-d", "-s", name, "cat"], check=True)
            with patch.object(workflow, "argv_for", return_value=(["cat"], dict(os.environ))), \
                    self.assertRaises(workflow.WorkflowRefused):
                workflow.start("codex", str(self.repo), False, False)

    def test_existing_same_session_is_not_replaced(self):
        with patch.object(workflow, "RUNTIME", self.state), \
                patch.object(workflow, "argv_for", return_value=(["cat"], dict(os.environ))):
            workflow.start("codex", str(self.repo), False, False)
            with self.assertRaisesRegex(workflow.WorkflowRefused, "Sitzung vorhanden: genius-workflow attach"):
                workflow.start("codex", str(self.repo), False, False)

    def test_all_fetch_push_and_rewritten_destinations_are_validated(self):
        for args in (
            ("remote", "set-url", "--add", "origin", "https://other.test/repo"),
            ("remote", "set-url", "--push", "origin", "https://other.test/repo"),
            ("config", "url.https://other.test/.insteadOf", "https://github.com/"),
            ("config", "url.https://other.test/.pushInsteadOf", "https://github.com/"),
        ):
            with self.subTest(args=args):
                self.git(*args)
                with self.assertRaises(workflow.WorkflowRefused):
                    workflow.workspace(str(self.repo))
                self.git("config", "--remove-section", "remote.origin")
                self.git("remote", "add", "origin", "https://github.com/Kaancodm/GeniusNew.git")
                subprocess.run(["git", "-C", str(self.repo), "config", "--remove-section",
                                "url.https://other.test/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_new_task_rejects_foreign_source_before_fetch(self):
        self.git("remote", "set-url", "origin", "https://other.test/repo")
        with patch.object(workflow, "REPO", self.main_repo), \
                patch.object(workflow, "ROOT", Path(self.temp.name) / "new-tasks"), \
                self.assertRaises(workflow.WorkflowRefused):
            workflow.new_task("valid-task")
        self.assertFalse((Path(self.temp.name) / "new-tasks").exists())

    def test_invalid_and_existing_tasks_preserve_existing_files(self):
        with patch.object(workflow, "ROOT", self.repo):
            with self.assertRaises(workflow.WorkflowRefused):
                workflow.new_task("../outside")
            (self.repo / "existing").mkdir()
            with self.assertRaises(workflow.WorkflowRefused):
                workflow.new_task("existing")
            self.assertTrue((self.repo / "existing").is_dir())

    def test_gemini_profiles_do_not_inherit_another_authentication(self):
        ambient = {name: "synthetic-conflicting-value" for name in workflow.GEMINI_AUTH_ENV}
        with patch.dict(workflow.os.environ, ambient), \
                patch.object(workflow.shutil, "which", return_value="/synthetic/gemini"):
            for tool, home in (("gemini-a", workflow.HOME_DIR),
                               ("gemini-b", workflow.CONFIG / "gemini-b")):
                argv, env = workflow.argv_for(tool, self.repo, True)
                self.assertEqual(env["GEMINI_CLI_HOME"], str(home))
                self.assertFalse(any(name in env for name in workflow.GEMINI_AUTH_ENV
                                     if name != "GEMINI_CLI_HOME"))

    def test_missing_cli_and_unverified_warp_review_refuse(self):
        with patch.object(workflow.shutil, "which", return_value=None), \
                self.assertRaises(workflow.WorkflowRefused):
            workflow.argv_for("codex", self.repo, False)
        with self.assertRaises(workflow.WorkflowRefused):
            workflow.argv_for("warp", self.repo, True)

    def test_local_ai_reported_error_is_refused_without_network(self):
        with patch.object(sys, "argv", ["workflow", "ai", "synthetic prompt"]), \
                patch.object(workflow, "local_open",
                             return_value=io.BytesIO(b'{"error":"synthetic failure"}')), \
                self.assertRaises(workflow.WorkflowRefused):
            workflow.main()

    def test_linked_master_branch_is_refused(self):
        self.git("switch", "-q", "-c", "master")
        with self.assertRaises(workflow.WorkflowRefused):
            workflow.workspace(str(self.repo))

    def test_runtime_symlink_or_public_permissions_are_refused(self):
        target = Path(self.temp.name) / "other-runtime"
        target.mkdir(mode=0o700)
        self.state.symlink_to(target)
        with patch.object(workflow, "RUNTIME", self.state), self.assertRaises(workflow.WorkflowRefused):
            workflow.tmux_base()
        self.state.unlink()
        self.state.mkdir(mode=0o755)
        with patch.object(workflow, "RUNTIME", self.state), self.assertRaises(workflow.WorkflowRefused):
            workflow.tmux_base()

    def test_local_requests_disable_environment_proxies(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.invalid:8080"}), \
                patch.object(workflow.urllib.request, "build_opener") as build:
            workflow.local_open("http://127.0.0.1:11434/api/tags", timeout=3)
            self.assertEqual(build.call_args.args[0].proxies, {})
            build.return_value.open.assert_called_once_with("http://127.0.0.1:11434/api/tags", timeout=3)

    def test_auth_probe_can_observe_failure_on_stderr(self):
        rc, output = workflow.probe([sys.executable, "-c", "import sys; print('not logged in', file=sys.stderr)"],
                                    include_stderr=True)
        self.assertEqual(rc, 0)
        self.assertIn("not logged in", output)


if __name__ == "__main__":
    unittest.main()
