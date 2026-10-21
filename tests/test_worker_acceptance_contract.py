"""Unprivileged acceptance-contract and driver safety regression tests."""

import copy
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import signal
import runpy
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

helper_dir = Path(__file__).resolve().parent.parent / "tools" / "worker_acceptance"
if not helper_dir.is_dir():
    helper_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(helper_dir))
import common
with patch.object(os, "geteuid", return_value=1000):
    import driver


def manifest():
    return {"source_commit": common.SOURCE_COMMIT,
            "expected_sources": {"geniusnew/__init__.py": "a" * 64,
                                 "geniusnew/worker_service.py": "b" * 64},
            "expected_units": {name: "c" * 64 for name in common.UNITS}}


def bound():
    return {"event": "BOUND", "pid": 123, "unit": "geniusnew-worker@one.service",
            "invocation": "a" * 32, "cgroup": "/sys/fs/cgroup/system.slice/geniusnew-worker@one.service",
            "connect_calls": 1, "checkpoint": "before_request"}


def identity():
    event = bound()
    properties = {"MainPID": "123", "InvocationID": event["invocation"],
                  "ControlGroup": "/system.slice/" + event["unit"], "ActiveState": "active",
                  "User": "20002", "Group": "20002", "FragmentPath": common.UNITS[1],
                  "DropInPaths": "", "NeedDaemonReload": "no"}
    return event, {"Uid": [20002] * 4, "Gid": [20002] * 4}, properties


class ManifestTest(unittest.TestCase):
    def read(self, value):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "manifest.json")
            path.write_text(value if isinstance(value, str) else json.dumps(value))
            return common.load_manifest(path)

    def test_valid_pinned_manifest(self):
        self.assertEqual(self.read(manifest()), manifest())

    def test_manifest_rejects_ambiguous_and_unpinned_entries(self):
        variants = [[], {}, {**manifest(), "source_commit": "0" * 40},
                    {**manifest(), "expected_sources": {}},
                    {**manifest(), "expected_sources": {"../worker.py": "a" * 64}},
                    {**manifest(), "expected_sources": {"geniusnew/x.py": "a" * 64}},
                    {**manifest(), "expected_units": {}},
                    {**manifest(), "expected_units": {name: "x" for name in common.UNITS}},
                    '{"source_commit":1,"source_commit":2}']
        for value in variants:
            with self.subTest(value=value), self.assertRaises((common.Refused, ValueError)):
                self.read(value)

    def test_manifest_relative_and_oversized_fail_before_read(self):
        with self.assertRaises(common.Refused):
            common.load_manifest(Path("relative.json"))
        with patch.object(Path, "stat", return_value=SimpleNamespace(st_size=32769)):
            with self.assertRaises(common.Refused):
                common.load_manifest(Path("/manifest.json"))

    def test_duplicate_valid_key_is_rejected_even_when_both_values_match(self):
        source = json.dumps(manifest())
        prefix = '"source_commit": "' + common.SOURCE_COMMIT + '"'
        duplicate = source.replace(prefix, prefix + ", " + prefix, 1)
        with self.assertRaisesRegex(common.Refused, "^MANIFEST_INVALID$"):
            self.read(duplicate)

    def test_sources_must_be_dictionary_with_valid_names_and_hash_values(self):
        for sources in (None, [], "sources", 7):
            with self.subTest(sources=sources), self.assertRaisesRegex(common.Refused, "^MANIFEST_INVALID$"):
                self.read({**manifest(), "expected_sources": sources})
        for key, digest in (("../worker.py", "a" * 64), ("geniusnew/sub/worker.py", "a" * 64),
                            ("geniusnew/worker_service.py", None),
                            ("geniusnew/worker_service.py", 1),
                            ("geniusnew/worker_service.py", "A" * 64),
                            ("geniusnew/worker_service.py", "a" * 63)):
            value = manifest()
            value["expected_sources"][key] = digest
            with self.subTest(key=key, digest=digest), self.assertRaisesRegex(common.Refused, "^MANIFEST_INVALID$"):
                self.read(value)

    def test_protected_requires_absolute_canonical_regular_root_path(self):
        directory = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        regular = SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o444)
        with patch.object(Path, "lstat", side_effect=[directory, regular]):
            common.protected(Path("/safe/file"))
        for path in (Path("relative"), Path("/safe/../file")):
            with self.assertRaises(common.Refused):
                common.protected(path)
        for metadata in (SimpleNamespace(st_uid=1, st_mode=stat.S_IFREG | 0o444),
                         SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o664),
                         SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK | 0o777), directory):
            with patch.object(Path, "lstat", return_value=metadata), self.assertRaises(common.Refused):
                common.protected(Path("/file"))

    def test_installed_verification_checks_exact_source_set_and_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            for name in ("__init__.py", "worker_service.py"):
                (package / name).write_text("safe source\n")
            migrations = package / "migrations"
            migrations.mkdir()
            for name in ("0001_core_foundation.sql", "0002_pending_jobs.sql",
                         "0003_approval_store.sql", "0004_audit_chain.sql"):
                (migrations / name).write_text("-- fixture\n")
            value = manifest()
            value["expected_sources"] = {"geniusnew/" + name: common.digest(package / name)
                                          for name in ("__init__.py", "worker_service.py")}
            with patch.object(common, "PACKAGE", package), patch.object(common, "protected"), \
                    patch.object(common, "digest", return_value="c" * 64):
                with self.assertRaisesRegex(common.Refused, "SOURCE_MISMATCH"):
                    common.verify_installed(value)
            with patch.object(common, "PACKAGE", package), patch.object(common, "protected"), \
                    patch.object(common, "digest", side_effect=lambda p: common.hashlib.sha256(p.read_bytes()).hexdigest()
                                 if p.parent == package else "c" * 64):
                common.verify_installed(value)
                (package / "untracked.py").write_text("unexpected")
                with self.assertRaisesRegex(common.Refused, "SOURCE_MISMATCH"):
                    common.verify_installed(value)
                (package / "untracked.py").unlink()
                for extra in (package / "worker_service.so",
                              package / "__pycache__" / "worker_service.cpython-313.pyc"):
                    with self.subTest(extra=extra.name):
                        extra.parent.mkdir(exist_ok=True)
                        extra.write_bytes(b"unhashed import candidate")
                        with self.assertRaisesRegex(common.Refused, "SOURCE_MISMATCH"):
                            common.verify_installed(value)
                        extra.unlink()
                        if extra.parent != package:
                            extra.parent.rmdir()
            with patch.object(common, "PACKAGE", package), patch.object(common, "protected"), \
                    patch.object(common, "digest", side_effect=lambda p: common.hashlib.sha256(p.read_bytes()).hexdigest()
                                 if p.parent == package else "d" * 64):
                with self.assertRaisesRegex(common.Refused, "UNIT_MISMATCH"):
                    common.verify_installed(value)


class SemanticTest(unittest.TestCase):
    def test_only_exact_success_can_pass(self):
        for mode, code, connects in (("baseline", "BASELINE_OK", 1),
                                     ("lease_busy", "LEASE_OCCUPIED", 0),
                                     ("idle_busy", "LIVE_WORKER_REFUSED", 0),
                                     ("raw_extra", "REQUEST_REFUSED", 1),
                                     ("controlled_after", "WORKER_CRASH_REFUSED", 1)):
            event = {"event": "DONE", "code": code, "connect_calls": connects}
            self.assertEqual(common.evaluate(mode, event, 0), "PASS")
            for bad, returncode in ((event, None), (event, 2), ({**event, "code": "TIMEOUT"}, 0),
                                    ({**event, "connect_calls": connects + 1}, 0),
                                    ({**event, "connect_calls": bool(connects)}, 0),
                                    ({**event, "untrusted": "payload"}, 0)):
                with self.assertRaises(common.Refused):
                    common.evaluate(mode, bad, returncode)

    def test_missing_duplicate_failed_or_timeout_cases_keep_hold(self):
        cases = [{"case": name, "status": "PASS", "cleanup": "PASS"} for name in common.CASES]
        report = common.aggregate(cases, "PASS")
        self.assertEqual(report["bounded_suite"], "PASS")
        self.assertEqual(report["operational_acceptance"], "HOLD")
        self.assertEqual(len(report["uncovered_gates"]), 4)
        for rows, cleanup in ((cases[:-1], "PASS"), (cases + cases[:1], "PASS"),
                              ([{**case, "status": "TIMEOUT"} for case in cases], "PASS"),
                              (cases, "HOLD")):
            self.assertEqual(common.aggregate(rows, cleanup)["bounded_suite"], "HOLD")

    def test_worker_signal_binding_requires_independent_matching_evidence(self):
        event, status, properties = identity()
        group = properties["ControlGroup"]
        self.assertTrue(common.identity_matches(event, 123, status, properties, group, [123]))
        for field, bad in (("Uid", [20001] * 4), ("Gid", [20003] * 4)):
            with self.assertRaises(common.Refused):
                common.identity_matches(event, 123, {**status, field: bad}, properties, group, [123])
        for field in properties:
            with self.subTest(field=field), self.assertRaises(common.Refused):
                common.identity_matches(event, 123, status, {**properties, field: "wrong"}, group, [123])
        for pidfd, cgroup, members in ((124, group, [123]), (123, "/wrong", [123]), (123, group, [123, 124])):
            with self.assertRaises(common.Refused):
                common.identity_matches(event, pidfd, status, properties, cgroup, members)
        # Even matching fabricated evidence must not authorize PID 1 or invalid units/nonces.
        for field, bad in (("pid", 1), ("unit", "foreign.service"), ("invocation", "invalid")):
            changed = {**event, field: bad}
            props = {**properties, "MainPID": str(changed["pid"]), "InvocationID": changed["invocation"],
                     "ControlGroup": "/system.slice/" + changed["unit"]}
            with self.assertRaises(common.Refused):
                common.identity_matches(changed, changed["pid"], status, props,
                                        props["ControlGroup"], [changed["pid"]])


class DriverSafetyTest(unittest.TestCase):
    def test_unverified_target_never_signals(self):
        target = driver.WorkerTarget(7, bound())
        with patch.object(signal, "pidfd_send_signal") as send:
            with self.assertRaises(common.Refused):
                target.send(signal.SIGKILL)
            target.verified = True
            with self.assertRaises(common.Refused):
                target.send(signal.SIGTERM)
            send.assert_not_called()
            target.send(signal.SIGSTOP)
            self.assertTrue(target.stopped)
            target.send(signal.SIGCONT)
            self.assertFalse(target.stopped)
            self.assertEqual(send.call_args_list[0].args, (7, signal.SIGSTOP))

    def test_binding_rejects_wrong_checkpoint_before_opening_pidfd(self):
        for event in ({}, {**bound(), "checkpoint": "after_request_write"},
                      {**bound(), "unit": "--host=attacker"}, {**bound(), "connect_calls": 0}):
            with patch.object(os, "pidfd_open") as opening, self.assertRaises(common.Refused):
                driver.bind_target(event, "before_request")
            opening.assert_not_called()

    def test_binding_closes_pidfd_on_identity_or_liveness_failure(self):
        event, status, properties = identity()
        contents = iter(("0::" + properties["ControlGroup"] + "\n", "123\n"))
        with patch.object(os, "pidfd_open", return_value=7), patch.object(driver, "fd_pid", return_value=123), \
                patch.object(driver, "read_status", return_value=status), \
                patch.object(Path, "read_text", side_effect=lambda **kwargs: next(contents)), \
                patch.object(driver, "read_properties", return_value=properties), \
                patch.object(driver.WorkerTarget, "live", return_value=False), patch.object(os, "close") as closing:
            with self.assertRaises(common.Refused):
                driver.bind_target(event, "before_request")
            closing.assert_called_once_with(7)

    def test_pidfd_and_manager_parse_fail_closed(self):
        with patch.object(Path, "read_text", return_value="Pid:\t123\n"):
            self.assertEqual(driver.fd_pid(7), 123)
        with patch.object(Path, "read_text", return_value="Pid:\t-1\n"), self.assertRaises(common.Refused):
            driver.fd_pid(7)
        result = SimpleNamespace(returncode=0, stderr=b"", stdout=b"MainPID=1\n")
        with patch.object(driver.subprocess, "run", return_value=result), self.assertRaises(common.Refused):
            driver.read_properties("geniusnew-worker@one.service")
        result.stderr = b"warning"
        with patch.object(driver.subprocess, "run", return_value=result), self.assertRaises(common.Refused):
            driver.read_properties("geniusnew-worker@one.service")

    def test_process_name_bytes_do_not_obscure_kernel_identity(self):
        status = (b"Name:\t\xffworker\nUid:\t20002\t20002\t20002\t20002\n"
                  b"Gid:\t20002\t20002\t20002\t20002\n")
        with patch.object(Path, "read_bytes", return_value=status), \
                patch.object(Path, "read_text", side_effect=UnicodeDecodeError(
                    "ascii", status, 6, 7, "ordinal not in range")):
            self.assertEqual(driver.read_status(123),
                             {"Uid": [20002] * 4, "Gid": [20002] * 4})

    def test_pidfd_open_failure_reaps_owned_child_and_restores_signal_mask(self):
        child = driver.Child("baseline")
        process = Mock(pid=123, stdin=Mock(), stdout=Mock())
        with patch.object(driver.subprocess, "Popen", return_value=process), \
                patch.object(signal, "pthread_sigmask", return_value=set()) as mask, \
                patch.object(os, "pidfd_open", side_effect=OSError("not available")), \
                patch.object(signal, "pidfd_send_signal") as send:
            with self.assertRaises(OSError):
                child.start(Path("/safe/stage"))
            process.stdin.close.assert_called()
            process.stdout.close.assert_called()
            process.wait.assert_called_with(timeout=50)
            send.assert_not_called()
            self.assertEqual(mask.call_args.args[0], signal.SIG_SETMASK)

    def test_child_is_registered_before_creation_can_raise(self):
        suite = driver.Suite(Path("/stage"))
        with patch.object(driver.Child, "start", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                suite.spawn("baseline")
        self.assertEqual(len(suite.children), 1)

    def test_owned_child_identity_mismatch_never_signals(self):
        child = driver.Child("baseline")
        child.process = Mock(pid=123)
        child.process.poll.return_value = None
        child.descriptor = 7
        with patch.object(driver, "fd_pid", return_value=124), patch.object(signal, "pidfd_send_signal") as send:
            with self.assertRaises(common.Refused):
                child.kill_owned()
            send.assert_not_called()

    def test_output_timeout_eof_overflow_and_nonobject_are_not_success(self):
        for readable, chunk in ((False, b""), (True, b""), (True, b"[]\n"), (True, b"x" * 4097)):
            child = driver.Child("baseline")
            child.process = Mock(stdout=Mock())
            child.process.stdout.fileno.return_value = 9
            with patch.object(select := driver.select, "select", return_value=([9], [], []) if readable else ([], [], [])), \
                    patch.object(os, "read", side_effect=[chunk, AssertionError("unexpected repeated read")]):
                with self.assertRaises(common.Refused):
                    child.event()

    def test_check_is_read_only_and_never_imports_or_runs_geniusnew(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "manifest.json")
            path.write_text(json.dumps(manifest()))
            output = io.StringIO()
            original_import = __import__
            def forbid_runtime(name, *args, **kwargs):
                if name == "geniusnew" or name.startswith("geniusnew."):
                    raise AssertionError("check attempted a runtime import")
                return original_import(name, *args, **kwargs)
            with patch.object(driver, "run") as run, patch.object(driver, "verify_installed") as verify, \
                    patch("builtins.__import__", side_effect=forbid_runtime), \
                    patch.object(sys, "stdout", output), patch.object(signal, "alarm"):
                self.assertEqual(driver.main(["--manifest", str(path)]), 0)
            run.assert_not_called()
            verify.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["operational_acceptance"], "HOLD")

    def test_run_needs_ack_root_isolation_and_confined_report_path(self):
        for uid, isolated, no_bytecode, ack, destination in ((1000, 1, True, True, "/stage/reports"),
                                                           (0, 1, True, False, "/stage/reports"),
                                                           (0, 0, True, True, "/stage/reports"),
                                                           (0, 1, False, True, "/stage/reports"),
                                                           (0, 1, True, True, "/etc")):
            with patch.object(os, "getuid", return_value=uid), patch.object(os, "geteuid", return_value=uid), \
                    patch.object(sys, "flags", SimpleNamespace(isolated=isolated)), \
                    patch.object(sys, "dont_write_bytecode", no_bytecode), \
                    patch.object(driver, "verify_installed") as verify:
                with self.assertRaises(common.Refused):
                    driver.run(Path("/manifest.json"), Path(destination), ack)
                verify.assert_not_called()

    def test_cleanup_resumes_stopped_target_even_when_child_cleanup_fails(self):
        suite = driver.Suite(Path("/stage"))
        target = Mock(stopped=True)
        target.live.side_effect = [True, False]
        child = Mock()
        child.close.side_effect = OSError("cleanup failed")
        suite.targets.append(target)
        suite.children.append(child)
        with patch.object(suite, "one"), patch.object(driver, "ensure_no_foreign_processes"):
            self.assertEqual(suite.cleanup(), "HOLD")
        target.send.assert_called_once_with(signal.SIGCONT)
        target.close.assert_called_once()

    def test_foreign_core_or_worker_process_prevents_run(self):
        for uid in (20001, 20002):
            with patch.object(Path, "iterdir", return_value=[Path("/proc/123")]), \
                    patch.object(driver, "read_status", return_value={"Uid": [uid] * 4, "Gid": [uid] * 4}):
                with self.assertRaises(common.Refused):
                    driver.ensure_no_foreign_processes()
        with patch.object(Path, "iterdir", return_value=[Path("/proc/123")]), \
                patch.object(driver, "read_status", return_value={}):
            with self.assertRaises(common.Refused):
                driver.ensure_no_foreign_processes()

    def test_disappeared_process_does_not_hide_later_foreign_process(self):
        entries = [Path("/proc/123"), Path("/proc/124")]
        for uid, refused in ((1000, False), (20002, True)):
            with self.subTest(uid=uid), \
                    patch.object(Path, "iterdir", return_value=entries), \
                    patch.object(driver, "read_status", side_effect=[
                        ProcessLookupError("process exited"),
                        {"Uid": [uid] * 4, "Gid": [uid] * 4},
                    ]) as read:
                if refused:
                    with self.assertRaisesRegex(common.Refused, "^FOREIGN_PROCESS_PRESENT$"):
                        driver.ensure_no_foreign_processes()
                else:
                    driver.ensure_no_foreign_processes()
                self.assertEqual(read.call_count, 2)


class DriverGuardRegressionTest(unittest.TestCase):
    def test_root_bootstrap_rejects_untrusted_parent_and_helper_file(self):
        safe_directory = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        safe_file = SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o444)
        for target in (driver.HERE.parent, driver.HERE / "common.py"):
            for uid, mode in ((1000, stat.S_IFREG | 0o444), (0, stat.S_IFREG | 0o666),
                              (0, stat.S_IFLNK | 0o777)):
                def metadata(path):
                    if path == target:
                        return SimpleNamespace(st_uid=uid, st_mode=mode)
                    return safe_file if path.name in ("common.py", "core.py", "driver.py") else safe_directory
                with self.subTest(target=target, uid=uid, mode=mode), \
                        patch.object(os, "geteuid", return_value=0), \
                        patch.object(Path, "lstat", autospec=True, side_effect=metadata):
                    with self.assertRaises(SystemExit) as caught:
                        runpy.run_path(str(driver.HERE / "driver.py"), run_name="acceptance_bootstrap_probe")
                self.assertEqual(str(caught.exception), "UNTRUSTED_DRIVER")

    def test_manager_execution_failure_cannot_hide_behind_valid_properties(self):
        _, _, properties = identity()
        for returncode, stderr, long_output in ((1, b"", False), (0, b"warning", False), (0, b"", True)):
            values = dict(properties)
            if long_output:
                values["MainPID"] = "1" * 9000
            output = "\n".join(key + "=" + value for key, value in values.items()).encode("ascii")
            result = SimpleNamespace(returncode=returncode, stderr=stderr, stdout=output)
            with self.subTest(returncode=returncode, stderr=stderr, long_output=long_output), \
                    patch.object(driver.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(common.Refused, "^MANAGER_UNREADABLE$"):
                    driver.read_properties("geniusnew-worker@one.service")

    def test_invalid_kernel_cgroup_is_refused_before_manager_query(self):
        event, status, properties = identity()
        for cgroup in ("0::/user.slice/worker.service\n", "0::/system.slice/one\n1::/system.slice/two\n"):
            with self.subTest(cgroup=cgroup), patch.object(os, "pidfd_open", return_value=7), \
                    patch.object(driver, "fd_pid", return_value=123), \
                    patch.object(driver, "read_status", return_value=status), \
                    patch.object(Path, "read_text", side_effect=[cgroup, "123\n"]), \
                    patch.object(driver, "read_properties", return_value=properties) as manager, \
                    patch.object(driver, "identity_matches", return_value=True), \
                    patch.object(driver.WorkerTarget, "live", return_value=True), \
                    patch.object(os, "close") as close:
                with self.assertRaisesRegex(common.Refused, "^SIGNAL_TARGET_UNVERIFIED$"):
                    driver.bind_target(event, "before_request")
            manager.assert_not_called()
            close.assert_called_once_with(7)

    def test_child_timeout_cannot_read_unready_descriptor(self):
        child = driver.Child("baseline")
        child.process = Mock(stdout=Mock())
        for expired in (False, True):
            with self.subTest(expired=expired), \
                    patch.object(driver.time, "monotonic", return_value=child.started + (46 if expired else 0)), \
                    patch.object(driver.select, "select", return_value=([], [], [])), \
                    patch.object(os, "read", side_effect=AssertionError("read after timeout")) as read:
                with self.assertRaisesRegex(common.Refused, "^CHILD_TIMEOUT$"):
                    child.event()
            read.assert_not_called()

    def test_child_done_rejects_every_trailing_byte_before_evaluation(self):
        for buffer, trailing in ((b"extra", b""), (b"", b"!")):
            child = driver.Child("baseline")
            child.buffer = buffer
            child.process = Mock(stdout=Mock())
            child.process.wait.return_value = 0
            child.process.stdout.read.return_value = trailing
            with self.subTest(buffer=buffer, trailing=trailing), \
                    patch.object(child, "event", return_value={"event": "DONE", "code": "BASELINE_OK", "connect_calls": 1}), \
                    patch.object(driver, "evaluate") as evaluate:
                with self.assertRaisesRegex(common.Refused, "^CHILD_OUTPUT_INVALID$"):
                    child.done()
            evaluate.assert_not_called()

    def test_lease_holder_must_emit_only_exact_held_event(self):
        for value in ({"event": "DONE"}, {"event": "HELD", "extra": True}, {}):
            suite = driver.Suite(Path("/example/stage"))
            holder = Mock()
            holder.event.return_value = value
            def spawn(mode):
                suite.children.append(holder)
                return holder
            with self.subTest(value=value), patch.object(suite, "spawn", side_effect=spawn), \
                    patch.object(suite, "one") as one, patch.object(suite, "baseline") as baseline:
                with self.assertRaisesRegex(common.Refused, "^LEASE_NOT_HELD$"):
                    suite.lease()
            one.assert_not_called()
            baseline.assert_not_called()
            holder.resume.assert_not_called()
            holder.close.assert_called_once_with()
            self.assertEqual(suite.children, [])

    def crash_context(self, *, lives=(True, True), stopped=True):
        suite = driver.Suite(Path("/example/stage"))
        child = Mock()
        child.event.return_value = bound()
        target = Mock(event=bound(), stopped=False)
        target.live.side_effect = list(lives)
        def send(number):
            if number == signal.SIGSTOP:
                target.stopped = True
            elif number == signal.SIGCONT:
                target.stopped = False
        target.send.side_effect = send
        def spawn(mode):
            suite.children.append(child)
            return child
        stack = ExitStack()
        stack.enter_context(patch.object(suite, "spawn", side_effect=spawn))
        stack.enter_context(patch.object(driver, "bind_target", return_value=target))
        one = stack.enter_context(patch.object(suite, "one"))
        baseline = stack.enter_context(patch.object(suite, "baseline"))
        stack.enter_context(patch.object(Path, "read_text", return_value="State:\tT (stopped)\n" if stopped else "State:\tS (sleeping)\n"))
        stack.enter_context(patch.object(driver.time, "sleep", side_effect=AssertionError("loop exceeded expected refusal")))
        return stack, suite, child, target, one, baseline

    def test_core_crash_requires_kernel_stop_confirmation_and_resumes_on_error(self):
        context, suite, child, target, one, baseline = self.crash_context(lives=(True,), stopped=False)
        with context, patch.object(driver.time, "monotonic", side_effect=[100, 101]):
            with self.assertRaisesRegex(common.Refused, "^WORKER_NOT_STOPPED$"):
                suite.crash(kill_core=True, checkpoint="before_request")
        self.assertEqual(target.send.call_args_list, [unittest.mock.call(signal.SIGSTOP),
                                                     unittest.mock.call(signal.SIGCONT)])
        self.assertFalse(target.stopped)
        child.kill_owned.assert_not_called()
        child.close.assert_called_once_with()
        one.assert_not_called()
        baseline.assert_not_called()
        self.assertEqual(suite.children, [])

    def test_old_worker_must_survive_before_and_after_fresh_core_refusal(self):
        for lives, expected_calls in (((False, False), []), ((True, False, False), [unittest.mock.call("idle_busy")])):
            context, suite, child, target, one, baseline = self.crash_context(lives=lives)
            with self.subTest(lives=lives), context:
                with self.assertRaisesRegex(common.Refused, "^OLD_WORKER_NOT_LIVE$"):
                    suite.crash(kill_core=True, checkpoint="before_request")
            self.assertEqual(one.call_args_list, expected_calls)
            child.kill_owned.assert_called_once_with()
            child.close.assert_called_once_with()
            baseline.assert_not_called()

    def test_core_crash_positive_path_resumes_then_confirms_idle_and_baseline(self):
        context, suite, child, target, one, baseline = self.crash_context()
        with context:
            suite.crash(kill_core=True, checkpoint="after_request_write")
        self.assertEqual(one.call_args_list, [unittest.mock.call("idle_busy"), unittest.mock.call("idle")])
        self.assertEqual(target.send.call_args_list, [unittest.mock.call(signal.SIGSTOP), unittest.mock.call(signal.SIGCONT)])
        child.kill_owned.assert_called_once_with()
        baseline.assert_called_once_with()
        self.assertEqual(suite.children, [])
        self.assertEqual(suite.observations, [{"event": "BOUND", "checkpoint": "after_request_write",
                                              "mode": "controlled_after", "connect_calls": 1}])

    def test_worker_crash_positive_path_kills_bound_target_then_recovers(self):
        context, suite, child, target, one, baseline = self.crash_context(lives=())
        with context:
            suite.crash(kill_core=False, checkpoint="after_request_write")
        target.send.assert_called_once_with(signal.SIGKILL)
        child.resume.assert_called_once_with()
        child.done.assert_called_once_with()
        one.assert_called_once_with("idle")
        baseline.assert_called_once_with()
        self.assertEqual(suite.observations, [{"event": "BOUND", "checkpoint": "after_request_write",
                                              "mode": "controlled_after", "connect_calls": 1}])

    def test_done_observations_forward_validated_zero_connect_count(self):
        suite = driver.Suite(Path("/example/stage"))
        child = Mock()
        child.done.return_value = {"event": "DONE", "code": "LIVE_WORKER_REFUSED", "connect_calls": 0}
        def spawn(mode):
            suite.children.append(child)
            return child
        with patch.object(suite, "spawn", side_effect=spawn):
            self.assertEqual(suite.one("idle_busy")["connect_calls"], 0)
        self.assertEqual(suite.observations, [{"mode": "idle_busy", "connect_calls": 0}])
        child.close.assert_called_once_with()

    def test_inspect_refuses_relative_report_directory_before_reading_helpers(self):
        with patch.object(driver, "load_manifest", return_value=manifest()), \
                patch.object(Path, "read_text", side_effect=AssertionError("helper read after invalid report")):
            with self.assertRaisesRegex(common.Refused, "^REPORT_PATH_INVALID$"):
                driver.inspect(Path("/manifest.json"), Path("reports"))

    def run_context(self, *, uid=0, euid=0, isolated=1, bytecode=True, hostname="test-host"):
        stack = ExitStack()
        mocks = {}
        for owner, name, replacement in (
                (os, "getuid", Mock(return_value=uid)), (os, "geteuid", Mock(return_value=euid)),
                (sys, "flags", SimpleNamespace(isolated=isolated)), (sys, "dont_write_bytecode", bytecode),
                (driver, "protected", Mock()), (driver, "inspect", Mock(return_value=manifest())),
                (driver, "verify_installed", Mock()), (driver, "ensure_no_foreign_processes", Mock()),
                (driver.tempfile, "mkdtemp", Mock(return_value="/example/inert-stage")),
                (driver.shutil, "copyfile", Mock()), (driver.shutil, "rmtree", Mock()),
                (os, "chmod", Mock()), (signal, "alarm", Mock()),
                (driver, "digest", Mock(return_value="d" * 64)),
                (os, "uname", Mock(return_value=SimpleNamespace(nodename=hostname, release="6.12.1", machine="x86_64"))),
                (Path, "read_text", Mock(return_value="b" * 32)), (os, "open", Mock(return_value=901)),
                (os, "fdopen", Mock(return_value=io.BytesIO())),
                (os.path, "lexists", Mock(return_value=False))):
            mocks[name] = stack.enter_context(patch.object(owner, name, replacement))
        suite = Mock()
        suite.observations = []
        suite.cleanup.return_value = "PASS"
        mocks["Suite"] = stack.enter_context(patch.object(driver, "Suite", return_value=suite))
        mocks["suite"] = suite
        return stack, mocks

    def test_run_authority_startup_and_report_guards_stop_before_any_staging(self):
        variants = ((dict(uid=1000), True, "RUN_NOT_AUTHORIZED", None),
                    (dict(euid=1000), True, "RUN_NOT_AUTHORIZED", None),
                    ({}, False, "RUN_NOT_AUTHORIZED", None),
                    (dict(isolated=0), True, "DRIVER_STARTUP_INVALID", None),
                    (dict(bytecode=False), True, "DRIVER_STARTUP_INVALID", None),
                    ({}, True, "REPORT_PATH_INVALID", Path("/other/reports")))
        for options, ack, code, destination in variants:
            context, calls = self.run_context(**options)
            report = destination or driver.HERE.parent.parent / "reports"
            with self.subTest(options=options, ack=ack, code=code), context:
                with self.assertRaisesRegex(common.Refused, "^" + code + "$"):
                    driver.run(Path("/manifest.json"), report, ack)
            calls["protected"].assert_not_called()
            calls["mkdtemp"].assert_not_called()
            calls["open"].assert_not_called()

    def test_run_invalid_host_metadata_refuses_before_staging(self):
        context, calls = self.run_context(hostname="contains space")
        with context:
            with self.assertRaisesRegex(common.Refused, "^HOST_METADATA_INVALID$"):
                driver.run(Path("/manifest.json"), driver.HERE.parent.parent / "reports", True)
        calls["mkdtemp"].assert_not_called()
        calls["suite"].cleanup.assert_not_called()
        calls["rmtree"].assert_not_called()
        calls["open"].assert_not_called()

    def test_existing_report_refuses_before_live_cases(self):
        context, calls = self.run_context()
        with context, patch.object(driver.os.path, "lexists", return_value=True):
            with self.assertRaisesRegex(common.Refused, "^REPORT_EXISTS$"):
                driver.run(Path("/manifest.json"), driver.HERE.parent.parent / "reports", True)
        calls["mkdtemp"].assert_not_called()
        calls["suite"].baseline.assert_not_called()
        calls["open"].assert_not_called()

    def test_fake_successful_run_reports_every_case_and_keeps_operational_hold(self):
        context, calls = self.run_context()
        with context:
            report, code = driver.run(Path("/manifest.json"), driver.HERE.parent.parent / "reports", True)
        self.assertEqual(code, 0)
        self.assertEqual(report["bounded_suite"], "PASS")
        self.assertEqual(report["operational_acceptance"], "HOLD")
        self.assertEqual([case["case"] for case in report["cases"]], list(common.CASES))
        self.assertEqual(len(report["cases"]), 13)
        self.assertEqual(report["host"]["hostname"], "test-host")
        calls["open"].assert_called_once_with(
            driver.HERE.parent.parent / "reports" / "worker-acceptance.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)

    def test_main_run_requires_report_and_cannot_invoke_run_without_it(self):
        output = io.StringIO()
        with patch.object(driver, "inspect", return_value=manifest()), \
                patch.object(driver, "run", return_value=({}, 0)) as run, \
                patch.object(signal, "signal"), patch.object(signal, "alarm"), \
                patch.object(sys, "stdout", output):
            code = driver.main(["--manifest", "/manifest.json", "--run", "--acknowledge-worker-test"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["code"], "DRIVER_INCONCLUSIVE")
        run.assert_not_called()

    def test_main_deadline_handler_uses_fixed_refusal_code(self):
        with patch.object(driver, "inspect", return_value=manifest()), \
                patch.object(driver, "run", return_value=({}, 0)), \
                patch.object(signal, "signal") as handlers, patch.object(signal, "alarm"), \
                patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(driver.main(["--manifest", "/manifest.json", "--run",
                                           "--report-dir", "/example/reports"]), 0)
        for call in handlers.call_args_list:
            with self.assertRaisesRegex(common.Refused, "^DRIVER_TIMEOUT$"):
                call.args[1](None, None)


if __name__ == "__main__":
    unittest.main()
