"""Checks for real Git workspaces and exclusive implementation sessions."""
from __future__ import annotations
import os
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})

    def test_main_is_not_a_writing_workspace(self):
        with self.assertRaises(SystemExit):
            workflow.workspace(str(self.main_repo))

    def test_named_shared_checkout_is_rejected(self):
        old = self.repo
        self.repo = self.main_repo
        try:
            self.git("switch", "-q", "-c", "shared/task")
        finally:
            self.repo = old
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
                                "url.https://other.test/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})

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

    def test_git_overrides_cannot_redirect_workspace_validation(self):
        git_dir = (self.repo / ".git").read_text().strip().removeprefix("gitdir: ")
        overrides = {"GIT_DIR": git_dir, "GIT_WORK_TREE": str(self.main_repo),
                     "GIT_COMMON_DIR": str(self.main_repo / ".git"),
                     "GIT_INDEX_FILE": str(self.main_repo / ".git/index"),
                     "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "remote.origin.url",
                     "GIT_CONFIG_VALUE_0": "https://github.com/other/repo.git"}
        with patch.dict(os.environ, overrides):
            self.assertEqual(workflow.workspace(str(self.repo)), self.repo.resolve())
            with self.assertRaises(workflow.WorkflowRefused):
                workflow.workspace(str(self.main_repo))
            with patch.object(workflow.shutil, "which", return_value="/usr/bin/example"):
                for tool in workflow.TOOLS:
                    _, env = workflow.argv_for(tool, self.repo, False)
                    self.assertFalse(any(name.startswith("GIT_") for name in env))

    def test_new_task_scrubs_git_overrides_before_fetch_and_worktree_add(self):
        with patch.dict(os.environ, {"GIT_DIR": "/invalid/repository"}), \
                patch.object(workflow, "REPO", self.main_repo), \
                patch.object(workflow, "ROOT", Path(self.temp.name) / "tasks"), \
                patch.object(workflow, "repository", return_value=self.main_repo), \
                patch.object(workflow.subprocess, "run") as run:
            workflow.new_task("safe-task")
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                self.assertFalse(any(name.startswith("GIT_") for name in call.kwargs["env"]))

    def test_tmux_server_git_overrides_are_removed_in_the_actual_child(self):
        out = Path(self.temp.name) / "child-env.txt"
        server_names = ("GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT",
                        "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
                        "GIT_CONFIG_KEY_47", "GIT_CONFIG_VALUE_47")
        client_env = {name: value for name, value in os.environ.items()
                      if not name.startswith("GIT_")}
        with patch.object(workflow, "RUNTIME", self.state), patch.dict(os.environ, client_env, clear=True):
            base = workflow.tmux_base()
            subprocess.run(base + ["new-session", "-d", "-s", "fixture-server", "cat"], check=True)
            for name in server_names:
                subprocess.run(base + ["set-environment", "-g", name, "synthetic-override"], check=True)
            code = ("import os; from pathlib import Path; "
                    f"Path({str(out)!r}).write_text(','.join(sorted(k for k in os.environ "
                    "if k.startswith('GIT_'))))")
            with patch.object(workflow, "argv_for", return_value=([sys.executable, "-c", code], dict(os.environ))):
                workflow.start("codex", str(self.repo), False, False)
            for _ in range(50):
                if out.exists():
                    break
                time.sleep(.02)
            self.assertTrue(out.exists())
            self.assertEqual(out.read_text(), "")
            # Sanitize only the child, preserving the private fixture server.
            for name in server_names:
                result = subprocess.run(base + ["show-environment", "-g", name],
                                        capture_output=True, text=True, check=True)
                self.assertTrue(result.stdout.startswith(name + "="))


if __name__ == "__main__":
    unittest.main()
