"""Post-edit checks cover the edited file and fail closed before hook timeout."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "post_python", Path(__file__).resolve().parents[1] / ".claude/hooks/post-python-check.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class PostPythonTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.file = self.root / "tests" / "test_edited.py"
        self.file.parent.mkdir()
        self.file.write_text("valid = True\n")

    def invoke(self, payload=None):
        if payload is None:
            payload = {"cwd": str(self.root), "tool_input": {"file_path": str(self.file)}}
        with patch.object(hook, "ROOT", self.root), \
                patch.dict(hook.os.environ, {"CLAUDE_PROJECT_DIR": str(self.root)}), \
                patch.object(hook.sys, "stdin", io.StringIO(json.dumps(payload))), \
                patch.object(hook.sys, "stderr", io.StringIO()), \
                patch.object(hook.sys, "stdout", io.StringIO()):
            return hook.run()

    def test_compiles_the_actual_edited_test_even_without_core_directories(self):
        # Real compiler, no geniusnew/scripts/tools directories in the fixture.
        self.assertEqual(self.invoke(), 0)
        self.file.write_text("def syntax error\n")
        self.assertEqual(self.invoke(), 2)

    def test_external_file_and_symlink_target_are_refused(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "outside.py"
            outside.write_text("valid = True\n")
            for path in (outside, self.root / "alias.py"):
                if path != outside:
                    path.symlink_to(outside)
                with self.subTest(path=path), patch.object(hook.subprocess, "run") as run:
                    self.assertEqual(self.invoke({"cwd": str(self.root),
                                                "tool_input": {"file_path": str(path)}}), 2)
                    run.assert_not_called()

    def test_malformed_payload_or_missing_file_blocks(self):
        for payload in ([], {"tool_input": "bad"},
                        {"cwd": "relative", "tool_input": {"file_path": str(self.file)}},
                        {"cwd": str(self.root), "tool_input": {"file_path": str(self.root / "missing.py")}}):
            with self.subTest(payload=payload):
                self.assertEqual(self.invoke(payload), 2)

    def test_malformed_protocol_is_an_explicit_refusal(self):
        for payload in ([], {"tool_input": "bad"}, {"tool_input": {}},
                        {"tool_input": {"file_path": 1}}):
            with self.subTest(payload=payload), \
                    patch.object(hook.sys, "stdin", io.StringIO(json.dumps(payload))), \
                    self.assertRaises(hook.PostCheckRefused):
                hook.main()

    def test_valid_json_over_input_limit_is_refused_before_skipping_non_python(self):
        raw = json.dumps({"tool_input": {"file_path": "ordinary.txt"}}) + " " * (hook.MAX_INPUT + 1)
        with patch.object(hook.sys, "stdin", io.StringIO(raw)), \
                self.assertRaises(hook.PostCheckRefused):
            hook.main()

    def test_directory_cannot_be_the_edited_python_file(self):
        directory = self.root / "directory.py"
        directory.mkdir()
        self.assertEqual(self.invoke({"cwd": str(self.root),
                                     "tool_input": {"file_path": str(directory)}}), 2)

    def test_command_failure_unavailable_tool_and_timeout_block(self):
        for error in (OSError(), subprocess.TimeoutExpired("compiler", 15),
                      TimeoutError("watchdog")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(hook.subprocess, "run", side_effect=error):
                self.assertEqual(self.invoke(), 2)
        with patch.object(hook.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(self.invoke(), 2)

    def test_configured_project_mismatch_blocks(self):
        with patch.object(hook, "ROOT", self.root):
            # The edited file belongs to ROOT; only the configured project is wrong.
            with patch.dict(hook.os.environ, {"CLAUDE_PROJECT_DIR": str(self.root / "other")}), \
                    patch.object(hook.sys, "stdin", io.StringIO(json.dumps({
                        "cwd": str(self.root), "tool_input": {"file_path": str(self.file)}}))), \
                    patch.object(hook.subprocess, "run") as run:
                with self.assertRaises(hook.PostCheckRefused):
                    hook.main()
                run.assert_not_called()

    def test_commands_are_bounded_and_watchdog_precedes_outer_timeout(self):
        with patch.object(hook.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(self.invoke(), 0)
        self.assertEqual(run.call_args_list[0].args[0][-1], str(self.file))
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 15)
        self.assertLess(hook.WATCHDOG_SECONDS, 60)
