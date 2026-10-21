from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from geniusnew.contracts import ContractError
from tools.control_deck import command_journal as j

HEAD = "a" * 40
OLD_HEAD = "b" * 40
SOURCE = "c" * 64
RAN = "Ran 17 tests in 0.123s"
TAIL = "....\n\nOK"


def evidence(ran=RAN, tail=TAIL):
    return hashlib.sha256((ran + "\n" + tail).encode("utf-8")).hexdigest()


class JournalTest(unittest.TestCase):
    def event(self, **overrides):
        result = {"command_id": "decision-tests", "target": "server", "exit_code": 0, "tests": 17,
                  "finished_at": datetime.now(timezone.utc).isoformat(),
                  "head": HEAD, "source_sha256": SOURCE,
                  "ran_line": RAN, "output_tail": TAIL, "evidence_sha256": evidence(),
                  "repo": str(Path.cwd())}
        result.update(overrides)
        return result

    def load(self, events, head=HEAD, digest=SOURCE):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(j, "source_digest", return_value=digest), \
                patch.object(j, "_git_head", return_value=head):
            p = Path(tmp) / "commands.json"
            p.write_text(json.dumps({"schema": 1, "events": events}))
            return j.snapshot(Path.cwd(), p)

    def command(self, snapshot, key="decision-tests"):
        return [c for c in snapshot["commands"] if c["id"] == key][0]

    def variant(self, snapshot, target, key="decision-tests"):
        return [v for v in self.command(snapshot, key)["variants"] if v["target"] == target][0]

    def test_missing_journal_is_pending_not_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = j.snapshot(Path.cwd(), Path(tmp) / "absent")
        self.assertEqual(s["status"], "unavailable")
        self.assertTrue(all(c["status"] == "pending" for c in s["commands"]))

    def test_only_zero_exit_with_tests_is_green(self):
        for code, count, status in [(0, 17, "passed"), (1, 17, "failed"), (0, 0, "failed")]:
            ran = f"Ran {count} tests in 0.1s" if count else ""
            event = self.event(exit_code=code, tests=count, ran_line=ran, evidence_sha256=evidence(ran, TAIL))
            self.assertEqual(self.load([event])["events"][0]["status"], status)

    def test_changed_source_turns_last_pass_stale_not_green(self):
        s = self.load([self.event()], digest="d" * 64)
        self.assertEqual(self.command(s)["status"], "stale")
        self.assertEqual(self.variant(s, "server")["status"], "stale")

    def test_changed_head_turns_last_pass_stale_not_green(self):
        s = self.load([self.event(head=OLD_HEAD)])
        self.assertEqual(self.command(s)["status"], "stale")
        self.assertEqual(self.variant(s, "server")["status"], "stale")

    def test_unknown_head_or_unreadable_source_is_pending_never_green(self):
        self.assertEqual(self.load([self.event()], head=None)["commands"][0]["status"], "pending")
        with tempfile.TemporaryDirectory() as tmp, patch.object(j, "source_digest", side_effect=OSError):
            p = Path(tmp) / "commands.json"
            p.write_text(json.dumps({"schema": 1, "events": [self.event()]}))
            with patch.object(j, "_git_head", return_value=HEAD):
                self.assertEqual(j.snapshot(Path.cwd(), p)["commands"][0]["status"], "pending")

    def test_matching_source_head_and_target_show_pass_for_that_target_only(self):
        s = self.load([self.event(target="server")])
        self.assertEqual(self.command(s)["status"], "passed")
        self.assertEqual(self.variant(s, "server")["status"], "passed")
        self.assertEqual(self.variant(s, "termius")["status"], "pending")

    def test_termius_run_does_not_turn_server_or_powershell_green(self):
        s = self.load([self.event(target="termius")])
        self.assertEqual(self.variant(s, "termius")["status"], "passed")
        self.assertEqual(self.variant(s, "server")["status"], "pending")
        self.assertEqual(self.variant(s, "powershell")["status"], "pending")

    def test_latest_run_per_target_decides_that_target(self):
        s = self.load([self.event(target="server"), self.event(target="server", exit_code=1, tests=3,
                                                              ran_line="Ran 3 tests in 0.1s",
                                                              evidence_sha256=evidence("Ran 3 tests in 0.1s", TAIL))])
        self.assertEqual(self.variant(s, "server")["status"], "failed")

    def test_forged_test_count_without_matching_evidence_is_rejected(self):
        forged = self.event(tests=99)
        self.assertEqual(self.load([forged])["status"], "unavailable")

    def test_edited_output_without_recomputed_hash_is_rejected(self):
        self.assertEqual(self.load([self.event(output_tail="all good")])["status"], "unavailable")

    def test_arbitrary_commands_unknown_targets_and_malformed_evidence_are_rejected(self):
        for event in [self.event(command_id="curl-secret"), self.event(target="laptop"),
                      self.event(head="not-a-sha"), self.event(evidence_sha256=""),
                      self.event(exit_code=False), self.event(output_tail="x" * (j.MAX_TAIL + 1))]:
            self.assertEqual(self.load([event])["status"], "unavailable")

    def test_reader_only_asks_git_for_head_and_never_runs_catalog_commands(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(j.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            p = Path(tmp) / "commands.json"
            p.write_text(json.dumps({"schema": 1, "events": [self.event()]}))
            j.snapshot(Path.cwd(), p)
        self.assertTrue(run.call_args_list)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:2], ["git", "-C"])
            self.assertEqual(call.args[0][3:], ["rev-parse", "--verify", "HEAD"])

    def test_fifo_and_symlink_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fifo = root / "fifo"; os.mkfifo(fifo)
            link = root / "link"; link.symlink_to(fifo)
            for path in [fifo, link]:
                self.assertEqual(j.snapshot(root, path)["status"], "unavailable")

    def test_runner_rejects_unknown_command_or_target_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(j.subprocess, "run") as run:
            with self.assertRaises(ContractError):
                j.run("shell", Path(tmp), Path(tmp) / "journal", "server")
            with self.assertRaises(ContractError):
                j.run("decision-tests", Path(tmp), Path(tmp) / "journal", "laptop")
            run.assert_not_called()

    def test_runner_refuses_to_record_without_a_known_head(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(j, "_git_head", return_value=None), \
                patch.object(j.subprocess, "run") as run:
            with self.assertRaises(ContractError):
                j.run("decision-tests", Path(tmp), Path(tmp) / "journal", "server")
            run.assert_not_called()

    def test_runner_refuses_when_source_changes_during_the_run(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(j, "_git_head", return_value=HEAD), \
                patch.object(j, "source_digest", side_effect=[SOURCE, "e" * 64]), \
                patch.object(j.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"Ran 17 tests in 0.1s\n\nOK\n"
            run.return_value.stderr = b""
            event = j.run("decision-tests", Path(tmp), Path(tmp) / "journal", "server")
        self.assertEqual(event["exit_code"], -2)

    def test_server_variant_copies_the_runner_not_a_raw_unittest(self):
        result = j.variants(Path("/home/kaan/check out's"), "decision-tests", {}, {})
        self.assertIn(" && python3 -B -m tools.control_deck.command_journal --run decision-tests", result[0]["command"])
        self.assertIn("--target server", result[0]["command"])
        self.assertIn("--target termius", result[1]["command"])
        self.assertNotIn(" -m unittest", result[0]["command"])
        self.assertTrue(result[0]["command"].startswith("cd -- "))
        self.assertFalse(result[2]["copy_ready"])

    def test_powershell_never_inherits_green_from_a_server_run(self):
        result = j.variants(Path('/checked/repo'), "decision-tests", {"server": "passed"},
                            {"ssh_host": "server.example.test", "ssh_user": "kaan"})
        self.assertEqual(result[2]["status"], "pending")
        self.assertTrue(result[2]["command"].startswith("ssh -T kaan@server.example.test 'cd -- "))
        self.assertIn("--target powershell", result[2]["command"])
        self.assertNotIn("\"", result[2]["command"])

    def test_ssh_profile_rejects_command_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            for host in ["-oProxyCommand=bad", "server;bad", "$(bad)", "server.test"]:
                path.write_text(json.dumps({"ssh_host": host, "ssh_user": "kaan"}))
                with patch.dict(os.environ, {"GENIUSNEW_COMMAND_TARGETS": str(path)}):
                    self.assertEqual(bool(j.target_profile()), host == "server.test")


class RefusalTest(unittest.TestCase):
    """Each test passes only if its own refusal is in place; see scripts/refusals.py."""

    event = JournalTest.event

    def journal(self, events, tmp, **raw):
        path = Path(tmp) / "commands.json"
        path.write_text(json.dumps({"schema": 1, **raw, "events": events}))
        return path

    def assert_refused(self, events, pattern, **raw):
        with tempfile.TemporaryDirectory() as tmp, patch.object(j, "_git_head", return_value=HEAD), \
                patch.object(j, "source_digest", return_value=SOURCE):
            path = self.journal(events, tmp, **raw)
            with self.assertRaisesRegex(ContractError, pattern):
                j._events(path, datetime.now(timezone.utc).timestamp())

    def test_a_non_regular_journal_is_refused_even_with_valid_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.journal([], tmp)
            with patch.object(j.stat, "S_ISREG", return_value=False):
                with self.assertRaisesRegex(ContractError, "^journal type$"):
                    j._events(path, datetime.now(timezone.utc).timestamp())

    def test_journal_read_is_bounded_even_with_trailing_padding(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "commands.json"
            path.write_text(json.dumps({"schema": 1, "events": []}) + " " * (j.MAX_BYTES + 10))
            with self.assertRaisesRegex(ContractError, "^journal size$"):
                j._events(path, datetime.now(timezone.utc).timestamp())

    def test_unknown_journal_schemas_are_refused(self):
        self.assert_refused([], "^journal schema$", schema=3)

    def test_more_than_one_hundred_events_are_refused(self):
        self.assert_refused([self.event()] * 101, "^journal capacity$")

    def test_an_event_from_the_future_or_out_of_order_is_refused(self):
        later = self.event(finished_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        self.assert_refused([later], "^journal time$")
        earlier = self.event(finished_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
        with tempfile.TemporaryDirectory() as tmp, patch.object(j, "_git_head", return_value=HEAD), \
                patch.object(j, "source_digest", return_value=SOURCE):
            path = self.journal([self.event(), earlier], tmp)
            with self.assertRaisesRegex(ContractError, "^journal time$"):
                j._events(path, datetime.now(timezone.utc).timestamp())

    def test_a_malformed_source_digest_is_refused(self):
        self.assert_refused([self.event(source_sha256="not-a-digest")], "^source digest$")

    def test_a_tail_over_the_bound_is_refused_with_consistent_evidence(self):
        tail = "x" * (j.MAX_TAIL + 1)
        self.assert_refused([self.event(output_tail=tail, evidence_sha256=evidence(RAN, tail))], "^evidence text$")

    def test_a_forged_count_is_refused_with_consistent_hash(self):
        self.assert_refused([self.event(tests=99, evidence_sha256=evidence(RAN, TAIL))], "^evidence count$")

    def test_an_overlong_repository_context_is_refused(self):
        self.assert_refused([self.event(repo="/" + "r" * 240)], "^context$")

    def test_a_journal_at_capacity_refuses_the_next_run(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(j, "_git_head", return_value=HEAD), \
                patch.object(j, "source_digest", return_value=SOURCE), patch.object(j.subprocess, "run") as run:
            path = self.journal([self.event()] * 100, tmp)
            with self.assertRaisesRegex(ContractError, "^journal full$"):
                j.run("decision-tests", Path(tmp), path, "server")
            run.assert_not_called()

    def test_a_journal_that_would_exceed_the_bound_is_not_written(self):
        big = self.event(output_tail="x" * j.MAX_TAIL, evidence_sha256=evidence(RAN, "x" * j.MAX_TAIL))
        existing = json.dumps({"schema": 1, "events": [big]}).encode()
        # The existing journal still reads; the one new event pushes the write over the bound.
        with tempfile.TemporaryDirectory() as tmp, patch.object(j, "MAX_BYTES", len(existing) + 10), \
                patch.object(j, "_git_head", return_value=HEAD), patch.object(j, "source_digest", return_value=SOURCE), \
                patch.object(j.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"Ran 17 tests in 0.1s\n\nOK\n"
            run.return_value.stderr = b""
            path = self.journal([big], tmp)
            before = path.read_bytes()
            with self.assertRaisesRegex(ContractError, "^journal size$"):
                j.run("decision-tests", Path(tmp), path, "server")
            self.assertEqual(path.read_bytes(), before)

    def test_a_lock_that_is_not_a_regular_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(j, "_git_head", return_value=HEAD), \
                patch.object(j.subprocess, "run") as run:
            with patch.object(j.stat, "S_ISREG", return_value=False):
                with self.assertRaisesRegex(ContractError, "^lock type$"):
                    j.run("decision-tests", Path(tmp), Path(tmp) / "journal", "server")
            run.assert_not_called()


class ArchiveTest(unittest.TestCase):
    event = JournalTest.event
    command = JournalTest.command
    variant = JournalTest.variant

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "commands.json"
        self.repo = Path.cwd()
        for name, value in (("_git_head", HEAD), ("source_digest", SOURCE)):
            patcher = patch.object(j, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, events, **metadata):
        self.path.write_text(json.dumps({"schema": 1, **metadata, "events": events}))

    def run_command(self):
        with patch.object(j.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"Ran 17 tests in 0.1s\n\nOK\n"
            run.return_value.stderr = b""
            return j.run("decision-tests", self.repo, self.path, "server")

    def test_archive_preserves_hundred_events_and_allows_event_101(self):
        events = [self.event()] * 100
        self.write(events)
        with patch.object(j.subprocess, "run") as run:
            result = j.rotate(self.path)
            run.assert_not_called()
        archive = Path(result["archive"])
        original = archive.read_bytes()
        self.assertEqual(result["events"], 100)
        self.assertEqual(hashlib.sha256(original).hexdigest(), result["sha256"])
        self.assertEqual(j._events(self.path, float("inf")), events)
        new = self.run_command()
        self.assertEqual(j._events(self.path, float("inf")), events + [new])
        self.assertEqual(archive.read_bytes(), original)
        self.assertEqual(len(json.loads(self.path.read_bytes())["events"]), 1)

    def test_archived_results_keep_target_source_head_and_failure_binding(self):
        self.write([self.event(target="termius", exit_code=1)])
        j.rotate(self.path)
        self.run_command()
        snapshot = j.snapshot(self.repo, self.path)
        self.assertEqual(self.variant(snapshot, "server")["status"], "passed")
        self.assertEqual(self.variant(snapshot, "termius")["status"], "failed")
        self.assertEqual(self.variant(snapshot, "powershell")["status"], "pending")
        with patch.object(j, "_git_head", return_value=OLD_HEAD):
            self.assertEqual(self.variant(j.snapshot(self.repo, self.path), "termius")["status"], "stale")
        with patch.object(j, "source_digest", return_value=None):
            self.assertEqual(self.variant(j.snapshot(self.repo, self.path), "termius")["status"], "pending")

    def test_eight_archives_are_a_hard_limit_without_deletion(self):
        self.write([self.event()])
        for _ in range(j.MAX_ARCHIVES):
            j.rotate(self.path)
            self.run_command()
        before = {p.name: p.read_bytes() for p in self.path.parent.glob("*.json")}
        with self.assertRaisesRegex(ContractError, "^archive capacity$"):
            j.rotate(self.path)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.path.parent.glob("*.json")})
        self.assertEqual(len(before), 9)

    def test_empty_segment_cannot_consume_an_archive_slot(self):
        self.write([])
        with self.assertRaisesRegex(ContractError, "^nothing to archive$"):
            j.rotate(self.path)

    def test_invalid_manifests_are_rejected(self):
        for values in (None, [], ["bad"], [123], ["a" * 64] * (j.MAX_ARCHIVES + 1)):
            self.write([], schema=2, archives=values)
            with self.assertRaisesRegex(ContractError, "^archive manifest$"):
                j._events(self.path, float("inf"))

    def test_archive_hash_is_checked_before_trusting_a_valid_segment(self):
        self.write([self.event()])
        result = j.rotate(self.path)
        Path(result["archive"]).write_text(json.dumps({"schema": 1, "events": [self.event(exit_code=1)]}))
        with self.assertRaisesRegex(ContractError, "^archive digest$"):
            j._events(self.path, float("inf"))
        self.assertEqual(j.snapshot(self.repo, self.path)["status"], "unavailable")

    def test_empty_archive_and_cross_segment_time_reversal_are_rejected(self):
        for events, active, message in (([], [], "archive empty"),
                ([self.event()], [self.event(finished_at="2000-01-01T00:00:00+00:00")], "archive order")):
            data = json.dumps({"schema": 1, "events": events}).encode()
            j._archive_path(self.path, 1).write_bytes(data)
            self.write(active, schema=2, archives=[hashlib.sha256(data).hexdigest()])
            with self.assertRaisesRegex(ContractError, "^" + message + "$"):
                j._events(self.path, float("inf"))

    def test_missing_active_or_archive_cannot_start_a_new_history(self):
        self.write([self.event()])
        j.rotate(self.path)
        for path in (self.path, j._archive_path(self.path, 1)):
            data = path.read_bytes()
            path.unlink()
            try:
                with patch.object(j.subprocess, "run") as run:
                    with self.assertRaisesRegex(ContractError, "^journal missing$"):
                        j.run("decision-tests", self.repo, self.path, "server")
                    run.assert_not_called()
                self.assertEqual(j.snapshot(self.repo, self.path)["status"], "unavailable")
            finally:
                path.write_bytes(data)

    def test_failed_manifest_write_keeps_evidence_and_retry_uses_same_slot(self):
        events = [self.event()]
        self.write(events)
        original = self.path.read_bytes()
        with patch.object(j.os, "replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                j.rotate(self.path)
        self.assertEqual(self.path.read_bytes(), original)
        archive = j._archive_path(self.path, 1)
        saved = archive.read_bytes()
        with patch.object(j.subprocess, "run") as run:
            with self.assertRaisesRegex(ContractError, "^archive pending$"):
                j.run("decision-tests", self.repo, self.path, "server")
            run.assert_not_called()
        j.rotate(self.path)
        self.assertEqual(archive.read_bytes(), saved)
        self.assertEqual(j._events(self.path, float("inf")), events)
        self.assertEqual(json.loads(self.path.read_bytes())["events"], [])

    def test_archive_collision_never_overwrites_evidence(self):
        self.write([self.event()])
        archive = j._archive_path(self.path, 1)
        archive.write_text("different evidence")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ContractError, "^archive collision$"):
            j.rotate(self.path)
        self.assertEqual(archive.read_text(), "different evidence")
        self.assertEqual(self.path.read_bytes(), before)

    def test_link_and_fsync_failures_preserve_active_evidence(self):
        self.write([self.event()])
        before = self.path.read_bytes()
        for name in ("link", "fsync"):
            with patch.object(j.os, name, side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    j.rotate(self.path)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertFalse(j._archive_path(self.path, 1).exists())
            self.assertEqual(list(self.path.parent.glob(".commands-*")), [])

    def test_archive_reads_reject_symlinks_fifo_and_oversized_files(self):
        self.write([self.event()])
        result = j.rotate(self.path)
        archive = Path(result["archive"])
        archive.unlink()
        archive.symlink_to(self.path)
        self.assertEqual(j.snapshot(self.repo, self.path)["status"], "unavailable")
        archive.unlink()
        os.mkfifo(archive)
        self.assertEqual(j.snapshot(self.repo, self.path)["status"], "unavailable")
        archive.unlink()
        archive.write_bytes(b" " * (j.MAX_BYTES + 1))
        with self.assertRaisesRegex(ContractError, "^journal size$"):
            j._events(self.path, float("inf"))

    def test_archive_releases_lock_after_failure_and_rejects_nonregular_lock(self):
        self.write([self.event()])
        with patch.object(j.stat, "S_ISREG", return_value=False):
            with self.assertRaisesRegex(ContractError, "^lock type$"):
                j.rotate(self.path)
        j.rotate(self.path)

    def test_byte_limit_can_be_recovered_by_archiving_without_losing_events(self):
        tail = "x" * j.MAX_TAIL
        event = self.event(output_tail=tail, evidence_sha256=evidence(RAN, tail))
        events = [event] * 20
        self.write(events)
        # Keep the read legal while making the next append too large.
        limit = len(self.path.read_bytes()) + 10
        with patch.object(j, "MAX_BYTES", limit):
            with self.assertRaisesRegex(ContractError, "^journal size$"):
                self.run_command()
            j.rotate(self.path)
            new = self.run_command()
            self.assertEqual(j._events(self.path, float("inf")), events + [new])

    def test_rotation_and_execution_share_the_same_exclusive_lock(self):
        import fcntl
        self.write([self.event()])
        before = self.path.read_bytes()
        with open(str(self.path) + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(j.subprocess, "run") as run:
                for call in (lambda: j.rotate(self.path),
                             lambda: j.run("decision-tests", self.repo, self.path, "server")):
                    with self.assertRaises(BlockingIOError):
                        call()
                run.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        j.rotate(self.path)

    def test_failed_directory_sync_keeps_both_copies_and_allows_retry(self):
        self.write([self.event()])
        before = self.path.read_bytes()
        with patch.object(j, "_sync_directory", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                j.rotate(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(j._archive_path(self.path, 1).is_file())
        j.rotate(self.path)

    def test_archive_cli_never_executes_and_run_requires_its_context(self):
        import contextlib
        import io
        self.write([self.event()])
        with patch("sys.argv", ["journal", "--archive", "--output", str(self.path)]), \
                patch.object(j.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()) as output:
            j.main()
            run.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["events"], 1)
        for args in (["--run", "decision-tests"], ["--run", "decision-tests", "--archive"]):
            with patch("sys.argv", ["journal", "--output", str(self.path)] + args), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                j.main()
            self.assertEqual(exc.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
