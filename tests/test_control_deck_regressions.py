from __future__ import annotations

import copy
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from tools.control_deck import actions, checks


def gate(gate_id, status="red", *, implementation_status="red"):
    return {"id": gate_id, "name": f"{gate_id} name", "status": status,
            "detail": f"{gate_id} evidence", "implementation_status": implementation_status}


def logged_in_tools():
    return {name: {"status": "green", "detail": "logged in"}
            for name in ("codex", "gh", "gemini")}


class GateEvidenceTest(unittest.TestCase):
    @patch.object(checks, "_run")
    def test_unimplemented_gate_does_not_query_git(self, run):
        self.assertEqual(checks.gate_status(Path("checkout"), None), "red")
        run.assert_not_called()

    @patch.object(checks, "_run")
    def test_commit_evidence_requires_a_commit_before_ancestry(self, run):
        repo = Path("selected checkout")
        proof = "0123456789abcdef0123456789abcdef01234567"
        for exit_code in (1, 128):
            with self.subTest(exit_code=exit_code):
                run.reset_mock()
                run.return_value = (exit_code, "missing object")
                self.assertEqual(checks.gate_status(repo, proof), "red")
                run.assert_called_once_with(
                    ["git", "cat-file", "-e", f"{proof}^{{commit}}"], cwd=repo)

    @patch.object(checks, "_run")
    def test_commit_ancestry_failure_never_reports_merged(self, run):
        repo = Path("selected checkout")
        proof = "0123456789abcdef0123456789abcdef01234567"
        for exit_code, expected in ((0, "green"), (1, "yellow"), (128, "yellow")):
            with self.subTest(exit_code=exit_code):
                run.reset_mock()
                run.side_effect = [(0, ""), (exit_code, "")]
                self.assertEqual(checks.gate_status(repo, proof), expected)
                self.assertEqual(run.call_args_list, [
                    call(["git", "cat-file", "-e", f"{proof}^{{commit}}"], cwd=repo),
                    call(["git", "merge-base", "--is-ancestor", proof,
                          "refs/remotes/origin/main"], cwd=repo),
                ])

    @patch.object(checks, "_run")
    def test_named_evidence_uses_the_remote_ref_and_origin_main(self, run):
        repo = Path("selected checkout")
        for ref in ("feature/review", "origin/feature/review"):
            for available, ancestor, expected in (
                    (1, 0, "red"), (0, 0, "green"), (0, 1, "yellow"), (0, 128, "yellow")):
                with self.subTest(ref=ref, available=available, ancestor=ancestor):
                    run.reset_mock()
                    run.side_effect = [(available, ""), (ancestor, "")]
                    self.assertEqual(checks.gate_status(repo, ref), expected)
                    expected_calls = [call(
                        ["git", "show-ref", "--verify", "--quiet",
                         "refs/remotes/origin/feature/review"], cwd=repo)]
                    if available == 0:
                        expected_calls.append(call(
                            ["git", "merge-base", "--is-ancestor", "origin/feature/review",
                             "refs/remotes/origin/main"], cwd=repo))
                    self.assertEqual(run.call_args_list, expected_calls)


class SnapshotEvidenceTest(unittest.TestCase):
    def setUp(self):
        # No host service or CLI should influence the evidence fixtures.
        replacements = {
            "_run": (0, "cached-main"), "_which": None,
            "tool_status": {"status": "green", "detail": "logged in"},
            "repo_status": {"status": "green", "head": "checkout-head"},
            "service_status": {"status": "green"},
            "docker_status": {"status": "green"}, "research_items": [],
        }
        for name, value in replacements.items():
            mocker = patch.object(checks, name, return_value=value)
            mocker.start()
            self.addCleanup(mocker.stop)
        mocker = patch.object(checks, "gate_status")
        self.gate_status = mocker.start()
        self.addCleanup(mocker.stop)

    def test_pending_reviews_do_not_replace_missing_or_unmerged_evidence(self):
        for implementation, expected_detail in (
                ("red", "Implementierung auf origin/main nicht nachgewiesen"),
                ("yellow", "Implementierungsnachweis noch nicht auf origin/main")):
            with self.subTest(implementation=implementation):
                self.gate_status.return_value = implementation
                snapshot = checks.snapshot(Path("checkout"))
                for entry in snapshot["gates"]:
                    self.assertEqual(entry["status"], implementation, entry["id"])
                    self.assertEqual(entry["implementation_status"], implementation)
                    self.assertEqual(entry["detail"], expected_detail)
                    self.assertNotIn("gemergt", entry["detail"])
                    self.assertNotIn("HOLD", entry["detail"])
                self.assertEqual(snapshot["project"]["merged_gates"], 0)

    def test_merged_count_uses_implementation_even_when_gate_is_on_hold(self):
        self.gate_status.side_effect = lambda repo, ref: "green" if ref else "red"
        snapshot = checks.snapshot(Path("checkout"))
        entries = {entry["id"]: entry for entry in snapshot["gates"]}
        held = {"B4", "B5", "C3", "A2", "C2", "C5"}
        self.assertEqual({entry["id"] for entry in entries.values()
                          if entry["status"] == "yellow"}, held)
        merged = {gate_id for gate_id, _, ref in checks.BETA_GATES if ref}
        self.assertEqual(snapshot["project"]["merged_gates"], len(merged))
        self.assertEqual({entry["id"] for entry in entries.values()
                          if entry["status"] == "green"}, merged - held)
        self.assertFalse(snapshot["project"]["release_ready"])

    def test_snapshot_uses_the_selected_checkout_for_every_gate_and_commands(self):
        repo = Path("selected checkout")
        self.gate_status.return_value = "red"
        snapshot = checks.snapshot(repo)
        self.assertEqual(self.gate_status.call_args_list,
                         [call(repo, ref) for _, _, ref in checks.BETA_GATES])
        checks.repo_status.assert_called_once_with(repo)
        self.assertEqual(snapshot["commands"], checks.commands_for(repo))
        checks._run.assert_called_once_with(
            ["git", "rev-parse", "--short=12", "refs/remotes/origin/main"], cwd=repo)


class ProjectResumeEvidenceTest(unittest.TestCase):
    def setUp(self):
        mocker = patch.object(checks, "_run", return_value=(0, "cached-main"))
        self.run = mocker.start()
        self.addCleanup(mocker.stop)
        mocker = patch.object(checks, "_which", return_value=None)
        mocker.start()
        self.addCleanup(mocker.stop)
        self.repo = Path("selected checkout")
        self.tools = logged_in_tools()

    def test_all_implementations_merged_still_does_not_grant_a_release(self):
        gates = [gate("A1", "green", implementation_status="green"),
                 gate("B0", "green", implementation_status="green")]
        result = checks.project_resume(gates, self.tools, self.repo)
        self.assertEqual(result["focus"], "Implementierungen auf main nachgewiesen")
        self.assertIn("Betriebs- und Security-Nachweise", result["stopped_at"])
        self.assertEqual(result["resume"], [])
        self.assertEqual(result["merged_gates"], 2)
        self.assertEqual(result["total_gates"], 2)
        self.assertIs(result["release_ready"], False)
        self.assertEqual(result["ci"], {"status": "yellow", "detail": "nicht live abgefragt"})
        self.run.assert_called_once_with(
            ["git", "rev-parse", "--short=12", "refs/remotes/origin/main"], cwd=self.repo)

    def test_empty_gate_list_does_not_imply_a_release(self):
        result = checks.project_resume([], self.tools, self.repo)
        self.assertEqual(result["merged_gates"], 0)
        self.assertEqual(result["total_gates"], 0)
        self.assertEqual(result["resume"], [])
        self.assertFalse(result["release_ready"])
        self.assertEqual(checks._priorities([], self.tools), [])

    def test_missing_local_main_does_not_trigger_a_live_query(self):
        self.run.return_value = (128, "")
        result = checks.project_resume([gate("E3")], self.tools, self.repo)
        self.assertEqual(result["main_head"], "")
        self.assertEqual(result["focus"], "E3 – E3 name")
        self.assertFalse(result["release_ready"])
        self.run.assert_called_once_with(
            ["git", "rev-parse", "--short=12", "refs/remotes/origin/main"], cwd=self.repo)

    def test_open_gate_steps_preserve_their_evidence_and_action_type(self):
        cases = (("A1", "action", "tests"), ("B0", "action", "tests"),
                 ("B4", "url", "https://github.com/Kaancodm/GeniusNew/pull/94"),
                 ("B5", "url", "https://github.com/Kaancodm/GeniusNew/pull/95"),
                 ("C3", "url", "https://github.com/Kaancodm/GeniusNew/pull/111"),
                 ("A2", "info", ""), ("C2", "info", ""), ("C5", "info", ""),
                 ("E3", "info", ""), ("D1", "info", ""))
        for gate_id, step_type, value in cases:
            for status in ("red", "yellow"):
                with self.subTest(gate=gate_id, status=status):
                    entry = gate(gate_id, status)
                    result = checks.project_resume(
                        [gate("B1", "green"), entry], self.tools, self.repo)
                    self.assertEqual(result["focus"], f"{gate_id} – {gate_id} name")
                    self.assertEqual(result["stopped_at"], entry["detail"])
                    self.assertEqual(result["resume"], [{
                        "title": f"{gate_id} {gate_id} name", "type": step_type,
                        "value": value, "detail": entry["detail"],
                    }])

    def test_login_order_and_bounded_lists_keep_the_first_open_gates(self):
        self.tools = {name: {"status": "red", "detail": f"{name} missing"}
                      for name in ("gemini", "gh", "codex")}
        gates = [gate(f"D{number}") for number in range(1, 7)]
        priorities = checks._priorities(gates, self.tools)
        self.assertEqual([step["title"] for step in priorities],
                         ["codex anmelden", "gh anmelden", "gemini anmelden"])
        self.assertTrue(all(step["action"] is None for step in priorities))
        result = checks.project_resume(gates, self.tools, self.repo)
        self.assertIn("Gate 0", result["focus"])
        self.assertEqual([step["value"] for step in result["resume"][:3]],
                         ["codex login", "gh auth login", "gemini"])
        self.assertEqual([step["title"] for step in result["resume"][3:]],
                         ["D1 D1 name", "D2 D2 name"])

    def test_priorities_skip_merged_gates_and_only_offer_executable_checks(self):
        gates = [gate("B1", "green"), gate("A1"), gate("B0", "yellow"),
                 gate("A2", "yellow"), gate("C5", "yellow")]
        self.assertEqual(checks._priorities(gates, self.tools), [
            {"title": "A1 A1 name", "detail": "A1 evidence", "action": "tests"},
            {"title": "B0 B0 name", "detail": "B0 evidence", "action": "tests"},
            {"title": "A2 A2 name", "detail": "A2 evidence", "action": None},
        ])

    def test_unverified_auth_marker_cannot_hide_a_missing_cli(self):
        self.tools["gemini"] = {"status": "red", "auth_status": "unverified",
                                "detail": "missing"}
        result = checks.project_resume([gate("B4", "yellow")], self.tools, self.repo)
        self.assertIn("Gate 0", result["focus"])
        self.assertEqual(checks._priorities([], self.tools)[0],
                         {"title": "gemini anmelden", "detail": "missing", "action": None})

    def test_optional_gemini_step_does_not_consume_a_priority_slot(self):
        self.tools["gemini"] = {"status": "yellow", "auth_status": "unverified",
                                "detail": "CLI installed"}
        gates = [gate(f"D{number}") for number in range(1, 7)]
        before = copy.deepcopy((gates, self.tools))
        result = checks.project_resume(gates, self.tools, self.repo)
        self.assertEqual(result["focus"], "D1 – D1 name")
        self.assertEqual(len(result["resume"]), 5)
        self.assertEqual(result["resume"][0]["value"], "gemini")
        self.assertIn("Optionale", result["resume"][0]["detail"])
        self.assertEqual([step["title"] for step in result["resume"][1:]],
                         [f"D{number} D{number} name" for number in range(1, 5)])
        self.assertEqual([step["title"] for step in checks._priorities(gates, self.tools)],
                         [f"D{number} D{number} name" for number in range(1, 4)])
        self.assertEqual((gates, self.tools), before)


class CheckoutCommandTest(unittest.TestCase):
    def test_commands_quote_shell_metacharacters_as_single_path_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "checkout's $HOME; $(echo unsafe) & dir"
            python = repo / ".venv/bin/python3"
            python.parent.mkdir(parents=True)
            python.touch()
            commands = checks.commands_for(repo)
            expected = {
                "git": ["git", "status", "--short", "--branch"],
                "tests": [str(python), "-m", "unittest", "discover", "-s", "tests", "-q"],
                "demo": ["./scripts/demo.sh"],
                "refusals": [str(python), "scripts/refusals.py"],
            }
            for name, argv in expected.items():
                with self.subTest(command=name):
                    self.assertEqual(shlex.split(commands[name]), ["cd", str(repo), "&&", *argv])
            self.assertEqual(commands["tmux"], "tmux attach -t genius")
            self.assertEqual(commands["docker"], "docker info")

    def test_missing_or_broken_venv_uses_python3_in_both_python_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            python = repo / ".venv/bin/python3"
            python.parent.mkdir(parents=True)
            for broken_link in (False, True):
                with self.subTest(broken_link=broken_link):
                    if broken_link:
                        python.symlink_to(repo / "missing interpreter")
                    self.assertEqual(checks.python_for(repo), "python3")
                    commands = checks.commands_for(repo)
                    self.assertEqual(shlex.split(commands["tests"])[3], "python3")
                    self.assertEqual(shlex.split(commands["refusals"])[3], "python3")

    @patch.object(actions.subprocess, "run")
    def test_test_action_does_not_leak_one_checkouts_interpreter_into_another(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "OK")
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first checkout"
            second = Path(tmp) / "second checkout"
            second.mkdir()
            python = first / ".venv/bin/python3"
            python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)
            for repo, expected in ((first, str(python)), (second, "python3"), (first, str(python))):
                with self.subTest(repo=repo):
                    self.assertTrue(actions.run_action("tests", repo)["ok"])
                    self.assertEqual(run.call_args.args[0], [
                        expected, "-m", "unittest", "discover", "-s", "tests", "-q"])
                    self.assertEqual(run.call_args.kwargs["cwd"], repo)
                    self.assertFalse(run.call_args.kwargs.get("shell", False))
            self.assertEqual(run.call_count, 3)

    @patch.object(actions.subprocess, "run")
    def test_selected_interpreters_failure_is_reported_without_retry(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            python = repo / ".venv/bin/python3"
            python.parent.mkdir(parents=True)
            python.touch()
            failures = (
                (subprocess.CompletedProcess([], 7, "test failure"), None, 7, "test failure"),
                (None, PermissionError("not executable"), None, "Programm nicht verfügbar."),
                (None, subprocess.TimeoutExpired([str(python)], 30, output=b"partial\xff"),
                 None, "partial\ufffd\nTIMEOUT"),
            )
            for completed, error, exit_code, output in failures:
                with self.subTest(error=type(error).__name__, exit_code=exit_code):
                    run.reset_mock()
                    run.return_value, run.side_effect = completed, error
                    self.assertEqual(actions.run_action("tests", repo), {
                        "action": "tests", "ok": False, "exit_code": exit_code, "output": output,
                    })
                    run.assert_called_once()
                    self.assertEqual(run.call_args.args[0][0], str(python))


class AgentDiscoveryRegressionTest(unittest.TestCase):
    @patch.object(checks, "_run")
    @patch.object(checks, "_which", side_effect=lambda name: f"/cli/{name}")
    def test_failed_antigravity_listing_does_not_probe_the_standalone_cli(self, which, run):
        for code, output in ((1, "gemini-3.8"), (0, ""), (0, "no models"), (127, "")):
            with self.subTest(code=code, output=output):
                which.reset_mock()
                run.reset_mock()
                run.return_value = (code, output)
                self.assertEqual(checks.tool_status("gemini"), {
                    "status": "yellow", "detail": "Antigravity login required",
                })
                which.assert_called_once_with("agy")
                run.assert_called_once_with(["/cli/agy", "models"], cwd=Path.home(), timeout=8.0)

    @patch.object(checks, "agent_session", return_value={"status": "yellow", "detail": "stopped"})
    @patch.object(checks, "tool_status", return_value={"status": "red", "detail": "missing"})
    def test_abacus_profiles_use_the_current_users_quoted_home(self, tool, session):
        user_home = Path("/synthetic home/user's $HOME")
        with patch.object(checks.Path, "home", return_value=user_home):
            cards = checks.agent_hub(Path("selected checkout"))["agents"]
        abacus = next(card for card in cards if card["id"] == "abacus")
        self.assertEqual(abacus["status"], "yellow")
        self.assertNotIn("action", abacus)
        self.assertNotIn("command", abacus)
        self.assertEqual([shlex.split(model["term_command"]) for model in abacus["models"]], [
            [str(user_home / "src/geniusnew-abacus-setup-20260930/start.sh"), "flash"],
            [str(user_home / "src/geniusnew-abacus-setup-20260930/start.sh"), "sonnet"],
        ])
        self.assertTrue(all(model["verified_on"] == "05.10.2026" for model in abacus["models"]))
        tool.assert_called_once_with("hermes")
        session.assert_called_once_with("hermes")


if __name__ == "__main__":
    unittest.main()
