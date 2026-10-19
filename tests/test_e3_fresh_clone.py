"""The E3 evidence runner: verdicts are earned, blockages are named.

No clone, install or suite runs here — a fake command runner and a fabricated
environment drive every branch, so what is tested is the runner's honesty:
PASS only when the suite and demo really reported success, BLOCKED with the
missing prerequisite named, FAIL on any non-zero command, and a protocol
that always carries the resolved commit and every stage.
"""

import tempfile
import unittest
from pathlib import Path

from scripts.e3_fresh_clone import (BLOCKED, FAIL, INFO, PASS, Completed, Environment,
                                    Stage, main, render_protocol,
                                    run_e3, tail, verdict)


def environment(**overrides):
    values = dict(sysname="Linux", kernel="6.8.0", machine="x86_64",
                  python="3.11.9", git="/usr/bin/git", libpq="libpq.so.5",
                  landlock_abi=5, test_dsn_set=True, systemd_running=True)
    values.update(overrides)
    return Environment(**values)


class FakeRun:
    """Answers by command shape; records everything it was asked to do."""

    def __init__(self, *, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, command, *, cwd=None, env=None):
        self.calls.append(command)
        joined = " ".join(command)
        if self.fail_on and self.fail_on in joined:
            return Completed(1, f"boom: {joined}")
        if command[:2] == ["git", "clone"]:
            Path(command[-1]).mkdir(parents=True)
            return Completed(0, "Cloning...")
        if command[:2] == ["git", "rev-parse"]:
            return Completed(0, "a" * 40 + "\n")
        if "unittest" in joined:
            return Completed(0, "Ran 1249 tests\n\nOK\n")
        if "bench_audit" in joined:
            return Completed(0, "binding ceiling                 :    62137 records")
        if "demo.sh" in joined:
            return Completed(0, "PASS — job succeeded over HTTP")
        if "import geniusnew" in joined:
            return Completed(0, "imports OK")
        return Completed(0, "done")


class E3FreshCloneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "clone"

    def run_it(self, env=None, fake=None):
        return run_e3(repo="https://example.invalid/repo.git", ref="main",
                      target=self.target, env_info=env or environment(),
                      Run=fake or FakeRun())

    def test_a_complete_host_earns_pass_with_the_resolved_commit(self):
        stages, sha = self.run_it()
        self.assertEqual(sha, "a" * 40)
        self.assertEqual(verdict(stages), PASS)
        by_name = {stage.name: stage.status for stage in stages}
        self.assertEqual(by_name["tests"], PASS)
        self.assertEqual(by_name["demo"], PASS)
        self.assertEqual(by_name["service-prereqs"], INFO)

    def test_blocked_is_named_not_failed(self):
        env = environment(landlock_abi=2, libpq=None, test_dsn_set=False)
        stages, _ = self.run_it(env=env)
        by_name = {stage.name: stage for stage in stages}
        self.assertEqual(by_name["tests"].status, BLOCKED)
        self.assertIn("libpq", by_name["tests"].evidence[0])
        self.assertEqual(by_name["demo"].status, BLOCKED)
        self.assertIn("ABI 4", by_name["demo"].evidence[0])
        # What this host *can* prove still ran.
        self.assertEqual(by_name["smoke"].status, PASS)
        self.assertEqual(verdict(stages), BLOCKED)

    def test_a_failing_command_fails_its_stage_and_the_verdict(self):
        stages, _ = self.run_it(fake=FakeRun(fail_on="unittest"))
        by_name = {stage.name: stage.status for stage in stages}
        self.assertEqual(by_name["tests"], FAIL)
        self.assertEqual(verdict(stages), FAIL)

    def test_success_words_are_required_not_just_exit_zero(self):
        class QuietRun(FakeRun):
            def __call__(self, command, *, cwd=None, env=None):
                done = super().__call__(command, cwd=cwd, env=env)
                if "demo.sh" in " ".join(command):
                    return Completed(0, "finished without the claim")
                return done

        stages, _ = self.run_it(fake=QuietRun())
        by_name = {stage.name: stage.status for stage in stages}
        self.assertEqual(by_name["demo"], FAIL)

    def test_an_existing_target_directory_is_refused(self):
        self.target.mkdir(parents=True)
        with self.assertRaises(ValueError):
            self.run_it()
        self.assertEqual(main(["--repo", "x", "--dir", str(self.target)],
                              Run=FakeRun(), env_info=environment()), 2)

    def test_the_protocol_carries_commit_stages_and_verdict(self):
        stages, sha = self.run_it()
        text = render_protocol(repo="https://example.invalid/repo.git",
                               ref="main", sha=sha, when="2026-10-19",
                               env_info=environment(), stages=stages)
        self.assertIn("a" * 40, text)
        self.assertIn("**PASS**", text)
        for stage in stages:
            self.assertIn(stage.name, text)
        self.assertIn("not claimed by this protocol", text)

    def test_verdict_ignores_info_but_not_blocked(self):
        self.assertEqual(verdict([Stage("a", PASS), Stage("b", INFO)]), PASS)
        self.assertEqual(verdict([Stage("a", PASS), Stage("b", BLOCKED)]), BLOCKED)
        self.assertEqual(verdict([Stage("a", BLOCKED), Stage("b", FAIL)]), FAIL)

    def test_tail_keeps_the_last_non_empty_lines_bounded(self):
        self.assertEqual(tail("one\n\ntwo\nthree\n", lines=2), ["two", "three"])
        self.assertEqual(tail("", lines=3), [])


if __name__ == "__main__":
    unittest.main()
