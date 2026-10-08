import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "protect-critical.py"
SETTINGS = ROOT / ".claude" / "settings.json"


def run_hook(tool_name, tool_input):
    payload = {"cwd": str(ROOT), "tool_name": tool_name, "tool_input": tool_input}
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(ROOT)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=3,
        check=False,
    )


def decision(result):
    if not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return data.get("hookSpecificOutput", {}).get("permissionDecision")


class ProtectCriticalNegativeTest(unittest.TestCase):
    def assertDenied(self, result):
        self.assertTrue(
            result.returncode == 2 or decision(result) == "deny",
            msg=(
                f"expected deny; rc={result.returncode}, "
                f"stdout={result.stdout!r}, stderr={result.stderr!r}"
            ),
        )

    def assertAsked(self, result):
        self.assertEqual(
            decision(result),
            "ask",
            msg=(
                f"expected ask; rc={result.returncode}, "
                f"stdout={result.stdout!r}, stderr={result.stderr!r}"
            ),
        )

    def test_guard_denies_writes_to_its_own_settings(self):
        self.assertDenied(run_hook("Write", {"file_path": ".claude/settings.json"}))

    def test_malformed_tool_input_blocks_with_exit_2(self):
        result = run_hook("Write", "not-an-object")
        self.assertEqual(
            result.returncode,
            2,
            msg=(
                "malformed input must fail closed; "
                f"stdout={result.stdout!r}, stderr={result.stderr!r}"
            ),
        )

    def test_root_level_pem_is_denied(self):
        self.assertDenied(run_hook("Write", {"file_path": "server.pem"}))

    def test_notebook_edit_is_covered_by_pretooluse_matcher(self):
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        matchers = [entry["matcher"] for entry in settings["hooks"]["PreToolUse"]]
        self.assertTrue(
            any(re.fullmatch(matcher, "NotebookEdit") for matcher in matchers),
            msg=f"NotebookEdit bypasses PreToolUse matchers: {matchers!r}",
        )

    def test_bash_shell_indirection_cannot_hide_critical_path(self):
        command = 'p=SECURITY; p="$p.md"; printf "%s\\n" changed > "$p"'
        self.assertAsked(run_hook("Bash", {"command": command}))

    def test_path_outside_project_root_requires_approval(self):
        self.assertAsked(run_hook("Write", {"file_path": "../outside.txt"}))


class ProtectCriticalBoundaryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.hooks = self.root / ".claude" / "hooks"
        self.hooks.mkdir(parents=True)
        self.hook = self.hooks / HOOK.name
        self.policy = self.hooks / "critical-paths.txt"
        shutil.copy2(HOOK, self.hook)
        shutil.copy2(HOOK.with_name("critical-paths.txt"), self.policy)

    def run_payload(self, payload, *, raw=None, project=None):
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(self.root if project is None else project)
        return subprocess.run(
            [sys.executable, str(self.hook)],
            input=json.dumps(payload) if raw is None else raw,
            text=True,
            capture_output=True,
            cwd=self.root,
            env=env,
            timeout=3,
            check=False,
        )

    def call(self, name, tool_input, *, cwd=None):
        return self.run_payload({
            "cwd": str(self.root if cwd is None else cwd),
            "tool_name": name,
            "tool_input": tool_input,
        })

    def test_hardcoded_self_protection_survives_policy_change(self):
        self.policy.write_text("ask:.claude/**\nask:.git/**\n", encoding="utf-8")
        for path in (
            ".claude", ".claude/settings.json", ".claude/settings.local.json",
            ".claude/hooks/protect-critical.py", ".claude/hooks/critical-paths.txt",
            ".git", ".git/config", ".",
        ):
            with self.subTest(path=path):
                self.assertEqual(decision(self.call("Write", {"file_path": path})), "deny")

    def test_secret_patterns_cover_root_and_nested_files(self):
        for name in ("server.pem", "server.key", "db-dsn.txt", "root-secret", ".env"):
            for prefix in ("", "nested/", "nested/deep/"):
                with self.subTest(path=prefix + name):
                    self.assertEqual(
                        decision(self.call("Write", {"file_path": prefix + name})), "deny"
                    )

    def test_deny_takes_precedence_over_ask_in_all_path_orders(self):
        for paths in (("SECURITY.md", "server.pem"), ("server.pem", "SECURITY.md")):
            with self.subTest(paths=paths):
                self.assertEqual(decision(self.call("Write", {"paths": list(paths)})), "deny")

    def test_every_tool_is_matched_and_unknown_tools_require_approval(self):
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        matchers = [entry["matcher"] for entry in settings["hooks"]["PreToolUse"]]
        for name in ("NotebookEdit", "MultiEdit", "Task", "Agent", "mcp__fs__write_file",
                     "mcp__exec__execute", "NewFutureTool"):
            with self.subTest(tool=name):
                self.assertTrue(any(re.fullmatch(pattern, name) for pattern in matchers))
                if name != "NotebookEdit":
                    self.assertEqual(decision(self.call(name, {"data": "opaque"})), "ask")

    def test_direct_write_tool_paths_are_checked(self):
        for name, field in (("Write", "file_path"), ("Edit", "path"),
                            ("NotebookEdit", "notebook_path")):
            with self.subTest(tool=name):
                self.assertEqual(decision(self.call(name, {field: "SECURITY.md"})), "ask")
                self.assertEqual(decision(self.call(name, {field: ".claude/settings.json"})), "deny")
                ordinary = self.call(name, {field: "docs/ordinary.md"})
                self.assertEqual(ordinary.returncode, 0)
                self.assertIsNone(decision(ordinary))

    def test_read_only_tools_preserve_normal_permissions(self):
        for name in ("Read", "Grep", "Glob"):
            with self.subTest(tool=name):
                result = self.call(name, {"path": "docs"})
                self.assertEqual(result.returncode, 0)
                self.assertIsNone(decision(result))

    def test_all_shell_execution_including_git_mutators_needs_approval(self):
        for command in (
            "git commit -am change", "git reset --hard", "git checkout main",
            "git rebase main", "git config core.hooksPath /tmp/hooks",
            "git -C . -c alias.save=commit save", "sh -c 'echo changed > SECURITY.md'",
            "python3 -c 'open(chr(97), chr(119)).write(chr(120))'", "cat README.md",
        ):
            with self.subTest(command=command):
                self.assertEqual(decision(self.call("Bash", {"command": command})), "ask")

    def test_existing_literal_shell_denials_are_not_weakened(self):
        for command in ("cat .env", "cat server.pem", "cat db-dsn.txt",
                        "echo change > .git/config", "echo change > .claude/settings.json"):
            with self.subTest(command=command):
                self.assertEqual(decision(self.call("Bash", {"command": command})), "deny")

    def test_symlinks_cannot_hide_protected_or_external_targets(self):
        (self.root / "alias").symlink_to(self.root / ".claude", target_is_directory=True)
        (self.root / "external").symlink_to(self.root.parent, target_is_directory=True)
        (self.root / "server.key").symlink_to(self.root / "ordinary.txt")
        for path, expected in (("alias/settings.json", "deny"),
                               ("external/outside.txt", "ask"), ("server.key", "deny")):
            with self.subTest(path=path):
                self.assertEqual(decision(self.call("Write", {"file_path": path})), expected)

    def test_relative_paths_use_call_working_directory(self):
        folder = self.root / "docs"
        folder.mkdir()
        self.assertEqual(decision(self.call("Write", {"file_path": "../.claude/settings.json"},
                                            cwd=folder)), "deny")
        self.assertEqual(decision(self.call("Write", {"file_path": "../../outside.txt"},
                                            cwd=folder)), "ask")

    def test_malformed_payloads_block_with_exit_2(self):
        for payload in (None, [], "data", {},
                        {"tool_name": "Write", "tool_input": {}, "cwd": "relative"},
                        {"tool_name": [], "tool_input": {}, "cwd": str(self.root)}):
            with self.subTest(payload=payload):
                self.assertEqual(self.run_payload(payload).returncode, 2)
        for raw in ("{", "x" * 1_048_577):
            with self.subTest(length=len(raw)):
                self.assertEqual(self.run_payload(None, raw=raw).returncode, 2)

    def test_missing_or_malformed_paths_and_commands_block(self):
        for value in (None, False, [], "text", {}, {"file_path": 1}, {"file_path": ""},
                      {"file_path": "bad\0path"}, {"paths": "file"}, {"paths": [1]}):
            with self.subTest(value=value):
                self.assertEqual(self.call("Write", value).returncode, 2)
        for value in ({}, {"command": None}, {"command": "  "}):
            with self.subTest(value=value):
                self.assertEqual(self.call("Bash", value).returncode, 2)

    def test_invalid_and_missing_policy_block(self):
        for policy in ("", "# comment only\n", "allow:*\n", "deny:\n", "invalid\n"):
            with self.subTest(policy=policy):
                self.policy.write_text(policy, encoding="utf-8")
                self.assertEqual(self.call("Write", {"file_path": "ordinary.txt"}).returncode, 2)
        self.policy.unlink()
        self.assertEqual(self.call("Write", {"file_path": "ordinary.txt"}).returncode, 2)

    def test_configured_root_cannot_relocate_protected_paths(self):
        payload = {"cwd": str(self.root), "tool_name": "Write",
                   "tool_input": {"file_path": ".claude/settings.json"}}
        self.assertEqual(self.run_payload(payload, project=self.root.parent).returncode, 2)

    def test_unexpected_path_resolution_error_blocks(self):
        (self.root / "loop").symlink_to("loop")
        self.assertEqual(self.call("Write", {"file_path": "loop/file"}).returncode, 2)

    def test_watchdog_blocks_stalled_input_before_hook_timeout(self):
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        timeout = settings["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"]
        with subprocess.Popen([sys.executable, str(self.hook)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            # Leave stdin open: the hook must stop its own blocked read.
            self.assertEqual(process.wait(timeout=timeout - 1), 2)
            stdout, stderr = process.communicate()
        self.assertEqual(stdout, "")
        self.assertIn("tool call blocked", stderr)


if __name__ == "__main__":
    unittest.main()
