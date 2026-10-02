import json
import os
import re
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
