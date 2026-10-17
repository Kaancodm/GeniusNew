"""A2/C5 worker transport refusals before a payload crosses the UID boundary."""

import os
import select
import hashlib
import io
import json
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from geniusnew.contracts import ContractError, canonical
from geniusnew.worker_service import (
    _BOOTSTRAP,
    FrameIdentity,
    WorkerLifecycle,
    WorkerServiceProfile,
    ServiceWorkerRunner,
    _Invocation,
    _CoreExchange,
    _expect_eof,
    _plain_expect_eof,
    _plain_frame,
    _plain_write,
    _request,
    _assert_no_extra_fds,
    _assert_null_outputs,
    _assert_worker_identity,
    _run_service_connection,
    _set_nondumpable,
    _worker_root,
    _pidfd_exited,
    _pidfd_pid,
    main as worker_service_main,
    _read_frame,
    _write_frame,
    _validate_ready,
    _validate_hello,
    _validate_request,
    _validate_result,
)
from geniusnew.isolation import IsolationLimits
from geniusnew.results import WorkerAuthority
from geniusnew.workers import DeterministicSummarizer, WorkerBoundaryError


class FramingTest(unittest.TestCase):
    def setUp(self):
        self.core, self.worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.core.close)
        self.addCleanup(self.worker.close)
        self.core.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        self.core.setsockopt(socket.SOL_SOCKET, 76, 1)  # SO_PASSPIDFD
        self.identity = FrameIdentity(expected_uid=os.geteuid(),
                                      expected_gid=os.getegid())
        self.addCleanup(self.identity.close)

    def read(self, limit=256):
        return _read_frame(self.core, limit=limit,
                           deadline=time.monotonic() + 1,
                           identity=self.identity)

    def test_a_kernel_authenticated_canonical_frame_is_accepted(self):
        value = {"kind": "READY", "nonce": "a" * 64, "version": 1}
        _write_frame(self.worker, value, limit=256,
                     deadline=time.monotonic() + 1)
        self.assertEqual(self.read(), value)
        self.assertEqual(self.identity.pid, os.getpid())
        self.assertIsNotNone(self.identity.pidfd)

    def test_zero_and_oversized_lengths_are_refused(self):
        for length in (0, 257):
            with self.subTest(length=length):
                self.worker.sendall(struct.pack("!I", length))
                with self.assertRaisesRegex(ContractError, "frame length is invalid"):
                    self.read()

    def test_noncanonical_json_is_refused(self):
        wire = b'{ "kind":"READY"}'
        self.worker.sendall(struct.pack("!I", len(wire)) + wire)
        with self.assertRaisesRegex(ContractError, "frame is not canonical JSON"):
            self.read()

    def test_truncated_frame_is_refused(self):
        self.worker.sendall(struct.pack("!I", 12) + b"{}")
        self.worker.shutdown(socket.SHUT_WR)
        with self.assertRaisesRegex(ContractError, "frame was truncated"):
            self.read()

    def test_descriptor_passing_is_refused_without_a_leak(self):
        with tempfile.TemporaryFile() as handle:
            wire = canonical({"kind": "READY"})
            payload = struct.pack("!I", len(wire)) + wire
            before = len(os.listdir("/proc/self/fd"))
            self.worker.sendmsg([payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                              struct.pack("i", handle.fileno()))])
            with self.assertRaises(ContractError):
                self.read()
            self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_wrong_uid_is_refused_before_frame_content(self):
        self.identity.expected_uid = os.geteuid() + 1
        _write_frame(self.worker, {"kind": "READY"}, limit=256,
                     deadline=time.monotonic() + 1)
        with self.assertRaises(ContractError):
            self.read()

    def test_phase_deadline_is_not_extended_by_fragments(self):
        self.worker.sendall(struct.pack("!I", 20) + b"{")
        def finish_after_deadline():
            time.sleep(0.03)
            self.worker.sendall(b'"a":"123456789012"}')

        thread = threading.Thread(target=finish_after_deadline)
        thread.start()
        with self.assertRaisesRegex(ContractError, "frame deadline exceeded"):
            _read_frame(self.core, limit=256, deadline=time.monotonic() + 0.01,
                        identity=self.identity)
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_bound_sender_eof_is_accepted_and_extra_bytes_are_refused(self):
        _write_frame(self.worker, {"kind": "READY"}, limit=256,
                     deadline=time.monotonic() + 1)
        self.read()
        self.worker.shutdown(socket.SHUT_WR)
        _expect_eof(self.core, deadline=time.monotonic() + 1,
                    identity=self.identity)
        self.identity.close()

    def test_extra_bytes_after_result_are_refused(self):
        _write_frame(self.worker, {"kind": "READY"}, limit=256,
                     deadline=time.monotonic() + 1)
        self.read()
        self.worker.sendall(b"X")
        with self.assertRaisesRegex(ContractError, "bytes after its result"):
            _expect_eof(self.core, deadline=time.monotonic() + 1,
                        identity=self.identity)
        self.identity.close()

    def test_ambiguous_pidfd_controls_close_all_received_descriptors(self):
        credentials = struct.pack("3i", os.getpid(), os.geteuid(), os.getegid())
        one = os.pidfd_open(os.getpid())
        two = os.pidfd_open(os.getpid())
        before = len(os.listdir("/proc/self/fd"))
        with self.assertRaisesRegex(ContractError, "pidfd is missing or ambiguous"):
            self.identity.accept(b"X", [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                                          credentials), (socket.SOL_SOCKET, 4,
                                                         struct.pack("i", one)),
                                         (socket.SOL_SOCKET, 4, struct.pack("i", two))], 0)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before - 2)

    def test_bad_pidfd_poll_state_does_not_count_as_exit(self):
        descriptor = os.pidfd_open(os.getpid())
        os.close(descriptor)
        with self.assertRaisesRegex(ContractError, "pidfd state is unreadable"):
            _pidfd_exited(descriptor)
        with patch("geniusnew.worker_service.select.poll", side_effect=OSError("denied")):
            with self.assertRaisesRegex(ContractError, "pidfd state is unreadable"):
                _pidfd_exited(123)

    def test_ambiguous_pidfd_exit_signal_is_refused(self):
        poller = Mock()
        poller.poll.return_value = [(123, select.POLLHUP)]
        with patch("geniusnew.worker_service.select.poll", return_value=poller):
            with self.assertRaisesRegex(ContractError, "exit state is ambiguous"):
                _pidfd_exited(123)

    def test_pidfd_information_must_name_exactly_one_pid(self):
        with patch("builtins.open", return_value=io.StringIO("Pid:\tunknown\n")):
            with self.assertRaisesRegex(ContractError, "no unambiguous PID"):
                _pidfd_pid(123)

    def test_missing_credentials_and_unbound_eof_are_refused(self):
        with self.assertRaisesRegex(ContractError, "credentials are missing"):
            self.identity.accept(b"X", [], 0)
        with self.assertRaisesRegex(ContractError, "EOF has invalid controls"):
            self.identity.accept(b"", [], 0)

    def test_bound_eof_rejects_extra_controls(self):
        self.identity.pid = os.getpid()
        self.identity.pidfd = os.pidfd_open(os.getpid())
        with self.assertRaisesRegex(ContractError, "EOF has invalid controls"):
            self.identity.accept(b"", [(socket.SOL_SOCKET, 999, b"x")], 0)

    def test_first_pidfd_must_identify_live_credential_pid(self):
        credentials = struct.pack("3i", os.getpid(), os.geteuid(), os.getegid())
        with self.assertRaisesRegex(ContractError, "first worker fragment"):
            self.identity.accept(b"X", [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                                          credentials),
                                         (socket.SOL_SOCKET, 4, struct.pack("i", -1))], 0)
        other_pid = os.getpid() + 1
        peer = struct.pack("3i", other_pid, os.geteuid(), os.getegid())
        descriptor = os.pidfd_open(os.getpid())
        with self.assertRaisesRegex(ContractError, "does not identify a live sender"):
            self.identity.accept(b"X", [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, peer),
                                         (socket.SOL_SOCKET, 4,
                                          struct.pack("i", descriptor))], 0)

    def test_later_pidfd_must_match_and_cannot_claim_early_exit(self):
        self.identity.pid = os.getpid()
        self.identity.pidfd = os.pidfd_open(os.getpid())
        credentials = struct.pack("3i", os.getpid(), os.geteuid(), os.getegid())
        with self.assertRaisesRegex(ContractError, "sentinel arrived before sender exit"):
            self.identity.accept(b"X", [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                                          credentials),
                                         (socket.SOL_SOCKET, 4, struct.pack("i", -1))], 0)
        self.identity.pid = os.getpid() + 1
        other = struct.pack("3i", self.identity.pid, os.geteuid(), os.getegid())
        descriptor = os.pidfd_open(os.getpid())
        with self.assertRaisesRegex(ContractError, "pidfd changed across fragments"):
            self.identity.accept(b"X", [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, other),
                                         (socket.SOL_SOCKET, 4,
                                          struct.pack("i", descriptor))], 0)

    def test_outbound_limits_and_deadline_prevent_sending(self):
        with self.assertRaisesRegex(ContractError, "outbound frame length is invalid"):
            _write_frame(self.worker, {"text": "X" * 100}, limit=8,
                         deadline=time.monotonic() + 1)
        with self.assertRaisesRegex(ContractError, "outbound frame deadline exceeded"):
            _write_frame(self.worker, {"text": "ok"}, limit=256,
                         deadline=time.monotonic() - 1)

    def test_outbound_zero_write_is_refused(self):
        sender = Mock()
        sender.fileno.return_value = self.worker.fileno()
        sender.send.return_value = 0
        with self.assertRaisesRegex(ContractError, "outbound frame was not sent"):
            _write_frame(sender, {"text": "ok"}, limit=256,
                         deadline=time.monotonic() + 1)

    def test_eof_deadline_is_hard(self):
        self.core.setblocking(False)
        self.identity.pid = os.getpid()
        self.identity.pidfd = os.pidfd_open(os.getpid())
        with self.assertRaisesRegex(ContractError, "EOF deadline exceeded"):
            _expect_eof(self.core, deadline=time.monotonic() - 1,
                        identity=self.identity)


class ReadyTest(unittest.TestCase):
    def test_a_wrong_nonce_or_unknown_field_blocks_the_request(self):
        expected = "a" * 64
        for ready in (
                {"kind": "READY", "version": 1, "nonce": "b" * 64},
                {"kind": "READY", "version": 1, "nonce": expected, "extra": 1},
                {"kind": "RESULT", "version": 1, "nonce": expected}):
            with self.subTest(ready=ready), self.assertRaises(ContractError):
                _validate_ready(ready, expected)


class ServiceRequestTest(unittest.TestCase):
    def test_hello_carries_only_a_version_and_random_nonce(self):
        nonce = "a" * 64
        self.assertEqual(_validate_hello({"kind": "HELLO", "version": 1,
                                          "nonce": nonce}), nonce)
        for bad in ({"kind": "HELLO", "version": 1, "nonce": nonce,
                     "text": "CANARY"},
                    {"kind": "HELLO", "version": 1, "nonce": "short"},
                    {"kind": "HELLO", "version": 2, "nonce": nonce}):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                _validate_hello(bad)

    def test_request_cannot_choose_code_paths_or_exceed_fixed_limits(self):
        from dataclasses import asdict

        fixed = IsolationLimits()
        request = {"kind": "REQUEST", "version": 1, "nonce": "a" * 64,
                   "worker": "summarize", "payload": {"text": "CANARY"},
                   "limits": asdict(fixed)}
        self.assertEqual(_validate_request(request, request["nonce"], fixed), fixed)
        for changed in (
                {**request, "module": "evil.module"},
                {**request, "worker": "import"},
                {**request, "payload": {"text": "CANARY", "path": "/etc/passwd"}},
                {**request, "limits": {**request["limits"], "memory_bytes":
                                         fixed.memory_bytes + 1}},
                {**request, "limits": {"cpu_seconds": 1}}):
            with self.subTest(changed=changed), self.assertRaises(ContractError):
                _validate_request(changed, request["nonce"], fixed)

    def test_result_requires_bound_digest_and_closed_error_code(self):
        common = {"kind": "RESULT", "version": 1, "nonce": "a" * 64,
                  "request_digest": "b" * 64}
        self.assertEqual(_validate_result({**common, "output": {"text": "ok"}},
                                          common["nonce"], common["request_digest"]),
                         {"text": "ok"})
        for bad in ({**common, "request_digest": "c" * 64,
                     "output": {"text": "ok"}},
                    {**common, "error": "worker says secret"},
                    {**common, "output": {"text": "ok"}, "error": "WORKER_FAILED"},
                    {**common, "output": {"text": 3}},
                    {**common, "output": {"text": "ok", "path": "secret"}}):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                _validate_result(bad, common["nonce"], common["request_digest"])

    def test_request_rejects_unknown_payload_before_serialization(self):
        with self.assertRaisesRegex(ContractError, "only a text payload"):
            _request({"text": "CANARY", "path": "/etc/shadow"}, "a" * 64)


class WorkerSideFramingTest(unittest.TestCase):
    def setUp(self):
        self.core, self.worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.core.close)
        self.addCleanup(self.worker.close)
        self.worker.setblocking(False)

    def test_one_frame_followed_by_eof_is_accepted(self):
        _write_frame(self.core, {"kind": "HELLO"}, limit=256,
                     deadline=time.monotonic() + 1)
        self.core.shutdown(socket.SHUT_WR)
        self.assertEqual(_plain_frame(self.worker.fileno(), limit=256,
                                      deadline=time.monotonic() + 1), {"kind": "HELLO"})
        _plain_expect_eof(self.worker.fileno(), time.monotonic() + 1)

    def test_second_request_byte_is_rejected_before_worker_execution(self):
        _write_frame(self.core, {"kind": "REQUEST"}, limit=256,
                     deadline=time.monotonic() + 1)
        self.core.sendall(b"X")
        self.core.shutdown(socket.SHUT_WR)
        _plain_frame(self.worker.fileno(), limit=256, deadline=time.monotonic() + 1)
        with self.assertRaisesRegex(ContractError, "bytes after its request"):
            _plain_expect_eof(self.worker.fileno(), time.monotonic() + 1)

    def test_worker_input_length_and_canonical_form_are_strict(self):
        for wire, reason in ((struct.pack("!I", 0), "input length is invalid"),
                             (struct.pack("!I", 10) + b'not-json!!',
                              "input is not ASCII JSON")):
            with self.subTest(reason=reason):
                self.core.sendall(wire)
                with self.assertRaisesRegex(ContractError, reason):
                    _plain_frame(self.worker.fileno(), limit=256,
                                 deadline=time.monotonic() + 1)

    def test_worker_input_deadline_and_truncation_are_distinct(self):
        def delayed_frame():
            time.sleep(0.03)
            _write_frame(self.core, {"kind": "HELLO"}, limit=256,
                         deadline=time.monotonic() + 1)
        thread = threading.Thread(target=delayed_frame)
        thread.start()
        with self.assertRaisesRegex(ContractError, "input deadline exceeded"):
            _plain_frame(self.worker.fileno(), limit=256,
                         deadline=time.monotonic() + 0.01)
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_worker_truncated_input_refuses_at_eof(self):
        self.core.sendall(struct.pack("!I", 10) + b"{}")
        self.core.shutdown(socket.SHUT_WR)
        with self.assertRaisesRegex(ContractError, "input was truncated"):
            _plain_frame(self.worker.fileno(), limit=256,
                         deadline=time.monotonic() + 0.1)

    def test_worker_noncanonical_input_is_refused(self):
        wire = b'{ "kind":"HELLO"}'
        self.core.sendall(struct.pack("!I", len(wire)) + wire)
        with self.assertRaisesRegex(ContractError, "input is not canonical JSON"):
            _plain_frame(self.worker.fileno(), limit=256,
                         deadline=time.monotonic() + 1)

    def test_worker_request_end_deadline_is_hard(self):
        with self.assertRaisesRegex(ContractError, "request end deadline exceeded"):
            _plain_expect_eof(self.worker.fileno(), time.monotonic() - 1)

    def test_worker_output_limit_is_checked_before_send(self):
        with self.assertRaisesRegex(ContractError, "output length is invalid"):
            _plain_write(self.worker.fileno(), {"text": "X" * 32768},
                         time.monotonic() + 1)
        self.core.settimeout(0.01)
        with self.assertRaises(TimeoutError):
            self.core.recv(1)

    def test_worker_output_deadline_and_zero_write_are_refused(self):
        with self.assertRaisesRegex(ContractError, "output deadline exceeded"):
            _plain_write(self.worker.fileno(), {"kind": "RESULT"},
                         time.monotonic() - 1)
        with patch("geniusnew.worker_service.os.write", return_value=0):
            with self.assertRaisesRegex(ContractError, "output could not be sent"):
                _plain_write(self.worker.fileno(), {"kind": "RESULT"},
                             time.monotonic() + 0.1)


class WorkerBootstrapTest(unittest.TestCase):
    def test_inherited_descriptor_refuses_before_ready(self):
        with tempfile.TemporaryFile() as handle:
            with self.assertRaisesRegex(ContractError, "inherited an extra descriptor"):
                _assert_no_extra_fds()
            self.assertFalse(handle.closed)

    def test_worker_requires_all_identity_fields_and_zero_capabilities(self):
        uid, gid = 20002, 20002
        status = (f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n"
                  f"Gid:\t{gid}\t{gid}\t{gid}\t{gid}\n"
                  "NoNewPrivs:\t1\nCapInh:\t0\nCapPrm:\t0\nCapEff:\t0\n"
                  "CapBnd:\t0\nCapAmb:\t0\n")
        with patch("os.geteuid", return_value=uid), patch("os.getegid", return_value=gid), \
                patch("os.getgroups", return_value=[]), patch.object(
                    Path, "read_text", return_value=status):
            _assert_worker_identity(uid, gid)
            for changed in (status.replace("CapAmb:\t0", "CapAmb:\t1"),
                            status.replace(f"Gid:\t{gid}", f"Gid:\t{gid + 1}", 1),
                            status.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0")):
                with self.subTest(changed=changed), patch.object(
                        Path, "read_text", return_value=changed):
                    with self.assertRaises(ContractError):
                        _assert_worker_identity(uid, gid)
            with patch("os.geteuid", return_value=uid + 1):
                with self.assertRaisesRegex(ContractError, "unexpected OS identity"):
                    _assert_worker_identity(uid, gid)

    def test_worker_allows_only_its_primary_gid_in_the_group_vector(self):
        uid, gid = 20002, 20002
        status = (f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n"
                  f"Gid:\t{gid}\t{gid}\t{gid}\t{gid}\n"
                  "NoNewPrivs:\t1\nCapInh:\t0\nCapPrm:\t0\nCapEff:\t0\n"
                  "CapBnd:\t0\nCapAmb:\t0\n")
        with patch("os.geteuid", return_value=uid), patch("os.getegid", return_value=gid), \
                patch.object(Path, "read_text", return_value=status):
            # systemd initgroups includes the primary group even with an empty
            # SupplementaryGroups= assignment. That duplicate grants no access.
            for groups in ([], [gid]):
                with self.subTest(groups=groups), patch("os.getgroups", return_value=groups):
                    _assert_worker_identity(uid, gid)
            for groups in ([20003], [gid, 20003], [gid, 0]):
                with self.subTest(groups=groups), patch("os.getgroups", return_value=groups):
                    with self.assertRaisesRegex(ContractError, "unexpected OS identity"):
                        _assert_worker_identity(uid, gid)

    def test_worker_root_identity_is_never_accepted(self):
        with patch("os.geteuid", return_value=0), patch("os.getegid", return_value=0), \
                patch("os.getgroups", return_value=[]):
            with self.assertRaisesRegex(ContractError, "unexpected OS identity"):
                _assert_worker_identity(0, 0)

    def test_stdout_and_stderr_must_be_null_devices(self):
        real_fstat = os.fstat
        def fstat(descriptor):
            if descriptor == 1:
                return Mock(st_mode=stat.S_IFREG | 0o600, st_rdev=0)
            return real_fstat(descriptor)
        with patch("geniusnew.worker_service.os.fstat", side_effect=fstat):
            with self.assertRaisesRegex(ContractError, "not /dev/null"):
                _assert_null_outputs()

    def test_dumpable_must_be_zero_before_ready(self):
        libc = Mock()
        libc.prctl.side_effect = [-1, 0]
        with patch("geniusnew.worker_service.ctypes.CDLL", return_value=libc):
            with self.assertRaisesRegex(ContractError, "still dumpable"):
                _set_nondumpable()

    def test_runtime_path_must_match_private_systemd_instance(self):
        uid, gid = os.geteuid(), os.getegid()
        path = "/run/geniusnew-worker/jobs/one"
        valid = Mock(st_mode=stat.S_IFDIR | 0o700, st_uid=uid, st_gid=gid)
        with patch.dict(os.environ, {"RUNTIME_DIRECTORY": path}), patch(
                "geniusnew.worker_service.os.lstat", return_value=valid), patch.object(
                    WorkerLifecycle, "_root_controlled"), patch.object(
                    Path, "read_text", return_value=(
                        "0::/system.slice/geniusnew-worker@one.service\n")):
            self.assertEqual(_worker_root(uid, gid), path)
            with patch.dict(os.environ, {"RUNTIME_DIRECTORY": "/tmp/evil"}):
                with self.assertRaisesRegex(ContractError, "no fixed manager-owned"):
                    _worker_root(uid, gid)
            bad = Mock(st_mode=stat.S_IFDIR | 0o777, st_uid=uid, st_gid=gid)
            with patch("geniusnew.worker_service.os.lstat", return_value=bad):
                with self.assertRaisesRegex(ContractError, "not private"):
                    _worker_root(uid, gid)
            with patch.object(Path, "read_text", return_value=(
                    "0::/system.slice/geniusnew-worker@other.service\n")):
                with self.assertRaisesRegex(ContractError, "does not match its unit"):
                    _worker_root(uid, gid)

    def test_worker_refuses_non_unix_fd_and_wrong_core_peer(self):
        uid = os.geteuid()
        with patch("geniusnew.worker_service._assert_worker_identity"), patch(
                "geniusnew.worker_service._assert_null_outputs"), patch(
                "geniusnew.worker_service._assert_no_extra_fds"), patch(
                "geniusnew.worker_service._plain_frame", side_effect=ContractError(
                    "past identity")):
            fake = Mock(family=socket.AF_INET)
            fake.getsockopt.side_effect = lambda _level, option, *_: (
                socket.SOCK_STREAM if option == socket.SO_TYPE else
                0 if option == socket.SO_ACCEPTCONN else
                struct.pack("3i", os.getpid(), uid, os.getegid()))
            with self.assertRaisesRegex(ContractError, "accepted AF_UNIX stream"):
                _run_service_connection(fake, core_uid=uid,
                                        worker_uid=uid + 1, worker_gid=os.getegid() + 1)
            core, worker = socket.socketpair()
            try:
                with self.assertRaisesRegex(ContractError, "Core peer UID is invalid"):
                    _run_service_connection(worker, core_uid=uid + 1,
                                            worker_uid=uid + 2,
                                            worker_gid=os.getegid() + 2)
            finally:
                core.close()
                worker.close()

    def test_worker_main_refuses_nonpositive_configured_identity(self):
        with patch("sys.argv", ["worker_service", "--core-uid", "0",
                                "--worker-uid", "2", "--worker-gid", "3"]), \
                patch("geniusnew.worker_service.socket.socket") as make_socket, \
                patch("geniusnew.worker_service._run_service_connection") as run:
            self.assertEqual(worker_service_main(), 2)
            make_socket.assert_not_called()
            run.assert_not_called()


class ProfileTest(unittest.TestCase):
    def test_profile_fixture_does_not_inherit_root_ids(self):
        with patch("os.geteuid", return_value=0), patch("os.getegid", return_value=0):
            profile = self.profile()
        self.assertGreater(profile.core_uid, 0)
        self.assertGreater(profile.socket_gid, 0)

    def profile(self, **changed):
        fields = {"core_uid": 20001, "worker_uid": 20002,
                  "worker_gid": 20002, "socket_gid": 20003,
                  "socket_path": "/run/geniusnew/worker.sock",
                  "lease_path": "/run/geniusnew/worker.lease"}
        fields.update(changed)
        return WorkerServiceProfile(**fields)

    def test_core_and_worker_must_have_distinct_fixed_uids(self):
        with self.assertRaisesRegex(ContractError, "different UIDs"):
            self.profile(worker_uid=self.profile().core_uid)

    def test_worker_cannot_belong_to_socket_group(self):
        with self.assertRaisesRegex(ContractError, "Core-only socket group"):
            self.profile(worker_gid=self.profile().socket_gid)

    def test_profile_rejects_bad_ids_paths_and_deadlines(self):
        for changed, reason in (
                ({"worker_uid": 0}, "positive numeric IDs"),
                ({"socket_path": "/run/geniusnew/worker.lease"},
                 "different files"),
                ({"unit_prefix": "../worker@"}, "unit prefix is invalid"),
                ({"runtime_root": "/run/other"}, "runtime root is invalid"),
                ({"phase_seconds": 0}, "phase deadline is invalid"),
                ({"total_seconds": 5.0}, "total deadline is invalid")):
            with self.subTest(changed=changed), self.assertRaisesRegex(ContractError, reason):
                self.profile(**changed)

    def test_paths_must_be_absolute_run_filesystem_paths(self):
        for socket_path, lease_path in (
                ("worker.sock", "/run/geniusnew/worker.lease"),
                ("/tmp/worker.sock", "/run/geniusnew/worker.lease"),
                ("/run/geniusnew/worker.sock", "/tmp/worker.lease")):
            with self.subTest(socket_path=socket_path, lease_path=lease_path):
                with self.assertRaises(ContractError):
                    WorkerServiceProfile(core_uid=20001, worker_uid=20002,
                                         worker_gid=20002, socket_gid=20003,
                                         socket_path=socket_path, lease_path=lease_path)


class LifecycleRefusalTest(unittest.TestCase):
    def setUp(self):
        self.profile = WorkerServiceProfile(
            core_uid=20001, worker_uid=20002,
            worker_gid=20002, socket_gid=20003,
            socket_path="/run/geniusnew-worker/ipc/worker.sock",
            lease_path="/run/geniusnew-worker/ipc/lease")
        self.lifecycle = WorkerLifecycle(self.profile)

    def test_missing_socket_or_lease_refuses_preflight(self):
        with self.assertRaises(ContractError):
            self.lifecycle.preflight()

    def test_access_denied_or_unreadable_systemd_properties_refuse(self):
        for output in (None, "AccessDenied", "ActiveState=active\n",
                       "ActiveState=active\nMainPID=1\nMainPID=2\n"):
            with self.subTest(output=output), patch.object(
                    self.lifecycle, "_systemctl", return_value=output):
                with self.assertRaises(ContractError):
                    self.lifecycle._show("geniusnew-worker@one.service",
                                         "ActiveState", "MainPID")

    def test_systemctl_nonzero_exit_refuses_without_interpreting_output(self):
        for code, stderr in ((1, b"AccessDenied"), (0, b"warning")):
            completed = Mock(returncode=code, stdout=b"ActiveState=active\n",
                             stderr=stderr)
            with self.subTest(code=code), patch(
                    "geniusnew.worker_service.subprocess.run", return_value=completed):
                with self.assertRaisesRegex(ContractError, "systemd state cannot be read"):
                    self.lifecycle._systemctl("show", "geniusnew-worker.socket")

    def test_socket_and_lease_path_metadata_must_be_root_controlled(self):
        target = self.profile.socket_path
        valid_dir = Mock(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
        parent = Mock(st_mode=stat.S_IFDIR | 0o710, st_uid=0,
                      st_gid=self.profile.socket_gid)
        node = Mock(st_mode=stat.S_IFSOCK | 0o660, st_uid=0,
                    st_gid=self.profile.socket_gid, st_nlink=1)

        def check(*, changed_path=None, changed_info=None):
            def metadata(path):
                if str(path) == changed_path:
                    return changed_info
                if str(path) == target:
                    return node
                if str(path) == str(Path(target).parent):
                    return parent
                return valid_dir
            with patch.object(Path, "lstat", autospec=True, side_effect=metadata):
                return self.lifecycle._check_node(target, kind="socket",
                                                  gid=self.profile.socket_gid,
                                                  mode=0o660)

        self.assertIs(check(), node)
        bad_dir = Mock(st_mode=stat.S_IFDIR | 0o777, st_uid=0, st_gid=0)
        with self.assertRaisesRegex(ContractError, "directory is not root controlled"):
            check(changed_path="/run/geniusnew-worker", changed_info=bad_dir)
        bad_node = Mock(st_mode=stat.S_IFREG | 0o660, st_uid=0,
                        st_gid=self.profile.socket_gid, st_nlink=1)
        with self.assertRaisesRegex(ContractError, "path metadata is unsafe"):
            check(changed_path=target, changed_info=bad_node)
        bad_parent = Mock(st_mode=stat.S_IFDIR | 0o700, st_uid=0,
                          st_gid=self.profile.socket_gid)
        with self.assertRaisesRegex(ContractError, "parent metadata is unsafe"):
            check(changed_path=str(Path(target).parent), changed_info=bad_parent)

    def test_installed_code_path_must_not_be_user_writable(self):
        with self.assertRaisesRegex(ContractError, "not absolute"):
            self.lifecycle._root_controlled(Path("geniusnew/worker_service.py"),
                                            regular=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "worker.py")
            path.write_text("# test\n")
            with self.assertRaisesRegex(ContractError, "not root controlled"):
                self.lifecycle._root_controlled(path, regular=True)

    def test_installed_paths_check_every_intermediate_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("safe", "user", "fixed"):
                (root / name).mkdir()
            (root / "safe/link").symlink_to("../user/relay")
            (root / "user/relay").symlink_to("../fixed")
            executable = root / "fixed/python"
            executable.write_bytes(b"interpreter")
            entry = root / "safe/link/python"
            real_lstat = Path.lstat
            for unsafe in (None, root / "user", root / "user/relay"):
                def metadata(path):
                    info = real_lstat(path)
                    # Only ownership is synthetic; links and paths are real.
                    owner = 1000 if path == unsafe else 0
                    mode = info.st_mode if stat.S_ISLNK(info.st_mode) else (
                        stat.S_IFMT(info.st_mode) | 0o755)
                    return SimpleNamespace(st_uid=owner, st_mode=mode)
                with self.subTest(unsafe=unsafe), patch.object(
                        Path, "lstat", autospec=True, side_effect=metadata):
                    if unsafe is None:
                        self.lifecycle._root_controlled(entry, regular=True)
                        with self.assertRaisesRegex(ContractError, "not root controlled"):
                            self.lifecycle._root_controlled(entry.parent / "..", regular=True)
                    else:
                        with self.assertRaisesRegex(ContractError, "not root controlled"):
                            self.lifecycle._root_controlled(entry, regular=True)

    def test_installed_paths_bound_symlink_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "end").write_bytes(b"interpreter")
            for index in range(41):
                (root / str(index)).symlink_to(str(index + 1) if index < 40 else "end")
            real_lstat = Path.lstat
            def metadata(path):
                info = real_lstat(path)
                mode = (info.st_mode if stat.S_ISLNK(info.st_mode)
                        else stat.S_IFMT(info.st_mode) | 0o755)
                return SimpleNamespace(st_uid=0, st_mode=mode)
            with patch.object(Path, "lstat", autospec=True, side_effect=metadata):
                self.lifecycle._root_controlled(root / "1", regular=True)
                with self.assertRaisesRegex(ContractError, "too many symlinks"):
                    self.lifecycle._root_controlled(root / "0", regular=True)

    def test_installation_checks_the_python_runtime_before_worker_start(self):
        with patch.object(self.lifecycle, "_root_controlled"), patch.object(
                self.lifecycle, "_check_python_runtime",
                side_effect=ContractError("unsafe Python runtime")):
            with self.assertRaisesRegex(ContractError, "unsafe Python runtime"):
                self.lifecycle._check_installation()

    def test_runtime_tree_checks_all_files_including_symlink_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "runtime")
            target = Path(directory, "extensions")
            root.mkdir()
            target.mkdir()
            (root / "linked").symlink_to(target, target_is_directory=True)
            for name in ("sitecustomize.py", "startup.pth", "native.so", "python.zip"):
                unsafe = target / name
                unsafe.write_bytes(b"canary")
                def inspect(path, *, regular):
                    if path.resolve() == unsafe:
                        raise ContractError("unsafe runtime leaf")
                with self.subTest(name=name), patch.object(
                        self.lifecycle, "_root_controlled", side_effect=inspect):
                    with self.assertRaisesRegex(ContractError, "unsafe runtime leaf"):
                        self.lifecycle._check_runtime_tree(root)
                unsafe.unlink()

    def test_python_runtime_requires_isolated_core_startup(self):
        with patch("geniusnew.worker_service.sys.flags", SimpleNamespace(isolated=0)):
            with self.assertRaisesRegex(ContractError, "isolated Python"):
                self.lifecycle._check_python_runtime()

    @contextmanager
    def python_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix, base = root / "venv", root / "base"
            app, stdlib, native = prefix / "site-packages", base / "stdlib", base / "dynload"
            for path in (prefix / "bin", base / "lib", app, stdlib, native):
                path.mkdir(parents=True, exist_ok=True)
            executable = prefix / "bin/python"
            executable.write_bytes(b"interpreter")
            with patch.object(self.lifecycle, "_root_controlled") as inspect, \
                    patch.object(self.lifecycle, "_check_loader_configuration"), \
                    patch.object(self.lifecycle, "_check_native_mappings"), \
                    patch("geniusnew.worker_service.sys.flags", SimpleNamespace(isolated=1)), \
                    patch("geniusnew.worker_service.sys.executable", str(executable)), \
                    patch("geniusnew.worker_service.sys._base_executable", str(executable)), \
                    patch("geniusnew.worker_service.sys.prefix", str(prefix)), \
                    patch("geniusnew.worker_service.sys.exec_prefix", str(prefix)), \
                    patch("geniusnew.worker_service.sys.base_prefix", str(base)), \
                    patch("geniusnew.worker_service.sys.base_exec_prefix", str(base)), \
                    patch("geniusnew.worker_service.sys.path", [str(base / "python.zip")]), \
                    patch("geniusnew.worker_service.sysconfig.get_config_var",
                          return_value=str(base / "lib")), \
                    patch("geniusnew.worker_service._python_runtime_paths",
                          return_value=tuple(map(str, (app, stdlib, native)))):
                yield root, inspect

    def test_python_runtime_checks_venv_configuration_and_missing_zip_parent(self):
        with self.python_runtime() as (root, inspect):
            configuration = root / "venv/pyvenv.cfg"
            configuration.write_text("home = /fixed/base\n")
            self.lifecycle._check_python_runtime()
            inspect.assert_any_call(configuration, regular=True)
            inspect.assert_any_call(root / "base", regular=False)
            def unsafe(path, *, regular):
                if path == configuration:
                    raise ContractError("unsafe venv configuration")
            inspect.side_effect = unsafe
            with self.assertRaisesRegex(ContractError, "unsafe venv configuration"):
                self.lifecycle._check_python_runtime()

    def test_python_runtime_refuses_path_overrides_and_relative_paths(self):
        with self.python_runtime() as (root, _):
            override = root / "base/lib/python313._pth"
            override.write_text("/tmp/worker-owned\nimport site\n")
            with self.assertRaisesRegex(ContractError, "path overrides are forbidden"):
                self.lifecycle._check_python_runtime()
            override.unlink()
            with patch("geniusnew.worker_service.sys.path", [""]):
                with self.assertRaisesRegex(ContractError, "fixed and absolute"):
                    self.lifecycle._check_python_runtime()

    def test_native_library_not_yet_loaded_must_be_root_controlled(self):
        with self.python_runtime() as (root, inspect):
            library = root / "base/lib/libpython-future.so"
            library.write_bytes(b"native canary")
            def unsafe(path, *, regular):
                if path == library:
                    raise ContractError("unsafe native library")
            inspect.side_effect = unsafe
            with self.assertRaisesRegex(ContractError, "unsafe native library"):
                self.lifecycle._check_python_runtime()

    def native_mapping(self, path):
        info = path.stat()
        device = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}"
        return f"1000-2000 r-xp 00000000 {device} {info.st_ino} {path}\n"

    def test_native_mappings_check_the_loader_and_libraries_outside_libdir(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / "libc.so.6"
            library.write_bytes(b"native canary")
            maps = self.native_mapping(library) + "2000-3000 r-xp 0 00:00 0 [vdso]\n"
            with patch.object(Path, "read_text", return_value=maps), patch.object(
                    self.lifecycle, "_root_controlled") as inspect:
                self.lifecycle._check_native_mappings()
                inspect.assert_any_call(library, regular=True)
                inspect.side_effect = ContractError("unsafe mapped library")
                with self.assertRaisesRegex(ContractError, "unsafe mapped library"):
                    self.lifecycle._check_native_mappings()

    def test_unknown_or_replaced_executable_mappings_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory) / "lib.so"
            library.write_bytes(b"native")
            valid = self.native_mapping(library)
            for maps in ("", "malformed\n", "1000-2000 rwxp 0 00:00 0\n",
                         "1000-2000 r-xp 0 00:00 0 [unknown]\n",
                         valid.rstrip() + " (deleted)\n",
                         valid.replace(str(library.stat().st_ino), "1")):
                with self.subTest(maps=maps), patch.object(Path, "read_text", return_value=maps), \
                        patch.object(self.lifecycle, "_root_controlled"):
                    with self.assertRaises(ContractError):
                        self.lifecycle._check_native_mappings()
        with patch.object(Path, "read_text", side_effect=PermissionError):
            with self.assertRaises(ContractError):
                self.lifecycle._check_native_mappings()

    def test_native_loader_overrides_and_preloads_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            cache, preload = Path(directory) / "cache", Path(directory) / "preload"
            cache.write_bytes(b"root loader cache")
            with patch("geniusnew.worker_service._LOADER_CACHE", cache), \
                    patch("geniusnew.worker_service._LOADER_PRELOAD", preload), \
                    patch.dict(os.environ, {}, clear=True), \
                    patch.object(self.lifecycle, "_root_controlled") as inspect:
                self.lifecycle._check_loader_configuration()
                inspect.assert_any_call(cache, regular=True)
                for variable in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT",
                                 "GLIBC_TUNABLES", "GCONV_PATH", "LOCPATH"):
                    with self.subTest(variable=variable), patch.dict(os.environ, {variable: ""}):
                        with self.assertRaisesRegex(ContractError, "loader environment"):
                            self.lifecycle._check_loader_configuration()
                preload.write_bytes(b"/untrusted.so\n")
                with self.assertRaisesRegex(ContractError, "native preloads"):
                    self.lifecycle._check_loader_configuration()
                preload.write_bytes(b"")
                self.lifecycle._check_loader_configuration()
                inspect.side_effect = ContractError("unsafe loader configuration")
                with self.assertRaisesRegex(ContractError, "unsafe loader configuration"):
                    self.lifecycle._check_loader_configuration()

    def test_unreadable_runtime_tree_and_startup_state_refuse(self):
        with patch.object(Path, "stat", side_effect=PermissionError):
            with self.assertRaisesRegex(ContractError, "runtime cannot be inspected"):
                self.lifecycle._check_runtime_tree(Path("/runtime"))
        with self.python_runtime(), patch.object(Path, "lstat", side_effect=PermissionError):
            with self.assertRaisesRegex(ContractError, "startup state cannot be inspected"):
                self.lifecycle._check_python_runtime()
        with self.python_runtime(), patch("os.listdir", side_effect=PermissionError):
            with self.assertRaisesRegex(ContractError, "startup state cannot be inspected"):
                self.lifecycle._check_python_runtime()

    def test_worker_bootstrap_ignores_site_hooks_and_selects_its_fixed_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "geniusnew"
            package.mkdir()
            (package / "__init__.py").write_text("")
            bootstrap = package / "worker_bootstrap.py"
            bootstrap.write_bytes(Path(_BOOTSTRAP).read_bytes())
            marker = root / "hook-ran"
            hook = f"open({str(marker)!r}, 'w').write('unsafe')\n"
            (root / "sitecustomize.py").write_text(hook)
            (root / "startup.pth").write_text("import sitecustomize\n")
            (package / "worker_service.py").write_text(
                "import sys\nprint(sys.flags.isolated, sys.flags.no_site, 'site' in sys.modules)\n")
            completed = subprocess.run([sys.executable, "-I", "-S", str(bootstrap)],
                                       capture_output=True, text=True, timeout=5)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "1 1 False")
            self.assertFalse(marker.exists())

    def test_service_landlock_does_not_expose_siblings_of_its_package(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            package = parent / "app/geniusnew"
            package.mkdir(parents=True)
            sibling = parent / "app/core-secret"
            sibling.write_text("SYNTHETIC-CANARY")
            job = parent / "job"
            job.mkdir()
            script = """
import os, sys
from pathlib import Path
import geniusnew.worker_service as service
from geniusnew.isolation import _install_filesystem_rules
service.__file__ = sys.argv[1]
paths = service._service_read_paths()
assert str(Path(service.__file__).parent.parent) not in paths
_install_filesystem_rules(sys.argv[2], paths)
try:
    descriptor = os.open(sys.argv[3], os.O_RDONLY)
except PermissionError:
    print("DENIED")
else:
    os.close(descriptor)
    raise AssertionError("package sibling became readable")
"""
            completed = subprocess.run(
                [sys.executable, "-c", script, str(package / "worker_service.py"),
                 str(job), str(sibling)], capture_output=True, text=True, timeout=5)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "DENIED")

    def test_template_must_be_loaded_without_dropins_or_pending_reload(self):
        properties = {"LoadState": "loaded", "FragmentPath":
                      "/etc/systemd/system/geniusnew-worker@.service",
                      "DropInPaths": "", "NeedDaemonReload": "no"}
        with patch.object(self.lifecycle, "_show", return_value={
                **properties, "DropInPaths": "/etc/systemd/system/evil.conf"}), \
                patch.object(self.lifecycle, "_root_controlled"), \
                patch.object(self.lifecycle, "_check_unit_content"):
            with self.assertRaisesRegex(ContractError, "fixed installed template"):
                self.lifecycle._check_unit_file("geniusnew-worker@.service")

    def test_template_is_inspected_through_a_valid_instance_name(self):
        template = "geniusnew-worker@.service"
        source = Path("/etc/systemd/system") / template

        def show(unit, *properties):
            # systemd cannot load a bare template through its Unit properties.
            if unit.endswith("@.service"):
                raise ContractError("template needs an instance name")
            return {"LoadState": "loaded", "FragmentPath": str(source),
                    "DropInPaths": "", "NeedDaemonReload": "no"}

        with patch.object(self.lifecycle, "_show", side_effect=show) as query, \
                patch.object(self.lifecycle, "_root_controlled"), \
                patch.object(self.lifecycle, "_check_unit_content") as content:
            self.lifecycle._check_unit_file(template)
            query.assert_called_once_with(
                "geniusnew-worker@internal.service", "LoadState", "FragmentPath",
                "DropInPaths", "NeedDaemonReload")
            content.assert_called_once_with(source, template)

    def test_unreadable_or_occupied_cgroup_refuses_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "geniusnew-worker@one.service"
            group.mkdir()
            (group / "cgroup.procs").write_text("123\n")
            with self.assertRaises(ContractError):
                self.lifecycle._require_empty_cgroup(group)
            (group / "cgroup.procs").unlink()
            with self.assertRaises(ContractError):
                self.lifecycle._require_empty_cgroup(group)

    def test_cgroup_absence_and_disappearance_are_distinct_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory, "old.service")
            with patch("os.listdir", return_value=[missing.name]):
                with self.assertRaisesRegex(ContractError, "absence is ambiguous"):
                    self.lifecycle._require_empty_cgroup(missing)
            with patch.object(Path, "lstat", return_value=Mock(
                    st_mode=stat.S_IFDIR | 0o755)), patch(
                    "os.walk", return_value=[]):
                with self.assertRaisesRegex(ContractError, "disappeared"):
                    self.lifecycle._require_empty_cgroup(missing)
            directory_node = Path(directory, "directory")
            directory_node.mkdir()
            (directory_node / "cgroup.procs").write_text("")
            link = Path(directory, "link")
            link.symlink_to(directory_node)
            with self.assertRaisesRegex(ContractError, "not a directory"):
                self.lifecycle._require_empty_cgroup(link)

    def test_cgroup_paths_cannot_escape_system_slice(self):
        with self.assertRaisesRegex(ContractError, "path is unexpected"):
            self.lifecycle._cgroup_path("/user.slice/evil")
        with patch.object(Path, "resolve", return_value=Path("/tmp/evil")):
            with self.assertRaisesRegex(ContractError, "escapes system.slice"):
                self.lifecycle._cgroup_path("/system.slice/worker.service")

    def test_uid_process_listing_requires_unambiguous_status(self):
        entry = SimpleNamespace(name="123", path="/proc/123")
        scanner = MagicMock()
        scanner.__iter__.return_value = iter([entry])
        with patch("os.scandir", return_value=scanner), patch.object(
                Path, "read_text", return_value="Uid:\tbad\n"):
            with self.assertRaisesRegex(ContractError, "UID process state is ambiguous"):
                self.lifecycle._uid_processes()

    def test_idle_refuses_malformed_active_or_residual_worker_state(self):
        root = Mock(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

        def check(output, *, state="inactive", runtime_absent=True, pids=()):
            self.lifecycle._systemctl = Mock(return_value=output)
            self.lifecycle._show = Mock(return_value={"ActiveState": state,
                                                       "MainPID": "0",
                                                       "ControlGroup": ""})
            self.lifecycle._runtime_absent = Mock(return_value=runtime_absent)
            self.lifecycle._uid_processes = Mock(return_value=list(pids))
            with patch("os.listdir", return_value=[]), patch.object(
                    Path, "lstat", return_value=root):
                self.lifecycle.assert_idle()

        unit = "geniusnew-worker@one.service loaded inactive dead\n"
        check(unit)
        for output, changes, reason in (
                ("bad-unit.service loaded inactive dead\n", {}, "unit list is ambiguous"),
                (unit, {"state": "active"}, "previous worker invocation is still active"),
                (unit, {"runtime_absent": False}, "runtime directory remains"),
                ("", {"pids": [123]}, "worker UID still has a live process")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ContractError, reason):
                check(output, **changes)

    def _bind_with(self, *, cgroup="0::/system.slice/geniusnew-worker@one.service\n",
                   active="active", runtime_mode=0o700, exited=(False, False),
                   unit_source=None):
        status = {"ActiveState": active, "InvocationID": "a" * 32,
                  "ControlGroup": (cgroup[3:].strip() if cgroup.startswith("0::")
                                   else "/system.slice/geniusnew-worker@one.service"),
                  "MainPID": "123", "LoadState": "loaded",
                  "FragmentPath": "/etc/systemd/system/geniusnew-worker@.service",
                  "DropInPaths": "", "NeedDaemonReload": "no"}
        status.update(unit_source or {})
        info = Mock(st_mode=stat.S_IFDIR | runtime_mode,
                    st_uid=self.profile.worker_uid, st_gid=self.profile.worker_gid)
        with patch("geniusnew.worker_service._pidfd_exited", side_effect=exited), \
                patch.object(Path, "read_text", return_value=cgroup), \
                patch.object(Path, "lstat", return_value=info), \
                patch.object(self.lifecycle, "_show", return_value=status), \
                patch.object(self.lifecycle, "_root_controlled"), \
                patch.object(self.lifecycle, "_check_unit_content"), \
                patch.object(self.lifecycle, "_check_instance_settings"), \
                patch.object(self.lifecycle, "_check_runtime_root"):
            return self.lifecycle.bind(123, 7)

    def test_bind_requires_live_pid_cgroup_invocation_and_private_runtime(self):
        self.assertEqual(self._bind_with().pid, 123)
        self.assertEqual(self._bind_with(cgroup=(
            "0::/system.slice/geniusnew-worker@127-0-0-1.service\n")).pid, 123)
        for changes, reason in (
                ({"exited": (True, True)}, "exited before the invocation check"),
                ({"cgroup": "bad\n"}, "PID cgroup is ambiguous"),
                ({"active": "inactive"}, "does not match an active invocation"),
                ({"runtime_mode": 0o777}, "runtime directory metadata is unsafe"),
                ({"exited": (False, True)}, "exited before payload")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ContractError, reason):
                self._bind_with(**changes)

    def test_actual_invocation_must_use_the_fixed_template_without_dropins(self):
        for changed in (
                {"LoadState": "not-found"},
                {"FragmentPath": "/etc/systemd/system/geniusnew-worker@one.service"},
                {"DropInPaths": "/etc/systemd/system/geniusnew-worker@one.service.d/env.conf"},
                {"NeedDaemonReload": "yes"}):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ContractError, "fixed installed template"):
                    self._bind_with(unit_source=changed)

    def test_invocation_change_blocks_lease_release(self):
        invocation = _Invocation("geniusnew-worker@one.service", "a" * 32,
                                 Path("/sys/fs/cgroup/system.slice/one"),
                                 Path("/run/geniusnew-worker/jobs/one"), 123)
        with patch.object(self.lifecycle, "_show", return_value={
                "ActiveState": "inactive", "InvocationID": "b" * 32,
                "ControlGroup": "", "MainPID": "0"}), \
                patch.object(self.lifecycle, "_require_empty_cgroup"), \
                patch.object(self.lifecycle, "_runtime_absent", return_value=True), \
                patch.object(self.lifecycle, "_uid_processes", return_value=[]), \
                patch("geniusnew.worker_service._pidfd_exited", return_value=True):
            with self.assertRaisesRegex(ContractError, "invocation changed"):
                self.lifecycle.await_end(invocation, 7, time.monotonic() + 1)

    def test_orphan_runtime_directory_blocks_a_new_connection(self):
        self.lifecycle._systemctl = Mock(return_value="")
        self.lifecycle._uid_processes = Mock(return_value=[])
        def listing(path):
            return ["orphan"] if str(path) == self.profile.runtime_root else []
        info = Mock(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        with patch("os.listdir", side_effect=listing), patch.object(
                Path, "lstat", return_value=info):
            with self.assertRaisesRegex(ContractError, "not empty"):
                self.lifecycle.assert_idle()

    def test_core_identity_requires_no_capabilities_and_no_new_privileges(self):
        uid = self.profile.core_uid
        status = (f"Uid:\t{uid}\t{uid}\t{uid}\t{uid}\n"
                  "NoNewPrivs:\t1\nCapInh:\t0\nCapPrm:\t0\nCapEff:\t0\n"
                  "CapBnd:\t0\nCapAmb:\t0\n")
        with patch("os.getuid", return_value=uid), patch("os.geteuid", return_value=uid), \
                patch("os.getgid", return_value=self.profile.socket_gid), \
                patch("os.getgroups", return_value=[]), patch.object(
                    Path, "read_text", return_value=status):
            self.lifecycle._check_core_identity()
            with patch("os.geteuid", return_value=uid + 1):
                with self.assertRaisesRegex(ContractError, "fixed unprivileged"):
                    self.lifecycle._check_core_identity()
            with patch("os.getgroups", return_value=[self.profile.worker_gid]):
                with self.assertRaisesRegex(ContractError, "fixed unprivileged"):
                    self.lifecycle._check_core_identity()
            for changed in (status.replace("CapBnd:\t0", "CapBnd:\t1"),
                            status.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0"),
                            status.replace(f"Uid:\t{uid}", "Uid:\tbad", 1)):
                with self.subTest(changed=changed), patch.object(
                        Path, "read_text", return_value=changed):
                    with self.assertRaises(ContractError):
                        self.lifecycle._check_core_identity()

    @contextmanager
    def host_reference(self, raw=None):
        expected = canonical({"version": 1, "boot_id": "a" * 36,
                              "namespaces": {name: [4, 123] for name in
                                             ("mnt", "pid", "user", "cgroup")},
                              "paths": {"root": [1, 2], "lease": [1, 3]}})
        with tempfile.TemporaryFile() as reference:
            reference.write(expected if raw is None else raw)
            reference.seek(0)
            info = os.fstat(reference.fileno())
            with patch.object(self.lifecycle, "_check_id_maps"), \
                    patch.object(self.lifecycle, "_host_snapshot", return_value=expected), \
                    patch.object(self.lifecycle, "_check_node", return_value=info) as node, \
                    patch("geniusnew.worker_service.os.open",
                          side_effect=lambda *args: os.dup(reference.fileno())):
                yield expected, node

    def test_core_reads_a_host_reference_without_cross_uid_namespace_access(self):
        with self.host_reference() as (_, node):
            with patch("geniusnew.worker_service.os.stat",
                       side_effect=PermissionError("no cross-UID namespace access")):
                self.lifecycle._check_host_namespaces()
            node.assert_called_once_with("/run/geniusnew-worker/ipc/host-state.json",
                                         kind="file", gid=self.profile.socket_gid,
                                         mode=0o440)

    def test_reference_must_match_boot_namespaces_and_path_inodes(self):
        with self.host_reference() as (raw, _):
            expected = json.loads(raw)
        changes = [{}, {**expected, "boot_id": "old"},
                   {**expected, "extra": True}]
        for name in expected["namespaces"]:
            changes.append({**expected, "namespaces": {
                **expected["namespaces"], name: [4, 456]}})
        for name in expected["paths"]:
            changes.append({**expected, "paths": {**expected["paths"], name: [8, 9]}})
        for raw in [b"", b"not-json", b"x" * 4097,
                    *(canonical(value) for value in changes)]:
            with self.subTest(raw=raw[:80]), self.host_reference(raw):
                with self.assertRaisesRegex(ContractError, "host reference does not match"):
                    self.lifecycle._check_host_namespaces()

    def test_reference_inode_swap_is_refused(self):
        with self.host_reference() as (_, node):
            node.return_value = Mock(st_dev=-1, st_ino=-1)
            with self.assertRaisesRegex(ContractError, "host reference inode changed"):
                self.lifecycle._check_host_namespaces()

    def test_procfs_must_still_show_all_worker_processes(self):
        for content, reason in (
                ("", "complete procfs process view"),
                ("25 30 0:23 / /proc rw,hidepid=2 - proc proc rw\n", "hides worker processes")):
            with self.subTest(reason=reason), self.host_reference(), patch.object(
                    Path, "read_text", return_value=content):
                with self.assertRaisesRegex(ContractError, reason):
                    self.lifecycle._check_host_namespaces()

    def test_initial_uid_and_gid_maps_are_required(self):
        initial = "         0          0 4294967295\n"
        with patch.object(Path, "read_text", return_value=initial):
            self.lifecycle._check_id_maps()
        for contents in (("0 1000 1\n", initial), (initial, "0 1000 1\n")):
            with self.subTest(contents=contents), patch.object(
                    Path, "read_text", side_effect=contents):
                with self.assertRaisesRegex(ContractError, "initial ID mapping"):
                    self.lifecycle._check_id_maps()

    def test_unprivileged_snapshot_uses_readable_own_namespaces(self):
        with patch.object(self.lifecycle, "_host_paths", return_value={"root": "/"}):
            snapshot = json.loads(self.lifecycle._host_snapshot())
        self.assertEqual(set(snapshot["namespaces"]), {"mnt", "pid", "user", "cgroup"})
        for identity in snapshot["namespaces"].values():
            self.assertEqual(len(identity), 2)
            self.assertTrue(all(type(value) is int and value > 0 for value in identity))

    def test_snapshot_binds_all_five_host_paths(self):
        paths = {"/": [1, 10], "/proc": [2, 20], "/sys/fs/cgroup": [3, 30],
                 "/run/geniusnew-worker/jobs": [4, 40],
                 "/run/geniusnew-worker/ipc/lease": [4, 50]}
        def metadata(path):
            device, inode = paths[path]
            return SimpleNamespace(st_dev=device, st_ino=inode)
        with patch("geniusnew.worker_service.os.stat", side_effect=metadata):
            snapshot = json.loads(self.lifecycle._host_snapshot())
        self.assertEqual(snapshot["paths"], {
            "root": [1, 10], "proc": [2, 20], "cgroup": [3, 30],
            "runtime": [4, 40], "lease": [4, 50]})

    def test_host_reference_requires_privileged_host_provisioning(self):
        with patch("os.getuid", return_value=1000), patch("os.geteuid", return_value=1000):
            with self.assertRaisesRegex(ContractError, "host root"):
                self.lifecycle.host_reference()

    def test_reference_provisioning_requires_manager_namespaces_and_root(self):
        own = SimpleNamespace(st_dev=1, st_ino=2)
        with patch("os.getuid", return_value=0), patch("os.geteuid", return_value=0), \
                patch.object(self.lifecycle, "_check_id_maps"), \
                patch.object(self.lifecycle, "_check_node"), \
                patch.object(self.lifecycle, "_check_runtime_root"), \
                patch.object(self.lifecycle, "_check_installation"), \
                patch.object(self.lifecycle, "_namespace_ids", return_value={"mnt": [4, 1]}) as ids, \
                patch.object(self.lifecycle, "_host_snapshot", return_value=b"reference") as snapshot, \
                patch("geniusnew.worker_service.os.stat", return_value=own) as metadata:
            self.assertEqual(self.lifecycle.host_reference(), b"reference")
            snapshot.assert_called_once_with(pid="1")
            ids.side_effect = [{"mnt": [4, 1]}, {"mnt": [4, 2]}]
            with self.assertRaisesRegex(ContractError, "manager namespaces"):
                self.lifecycle.host_reference()
            ids.side_effect = None
            metadata.side_effect = [own, SimpleNamespace(st_dev=1, st_ino=3)]
            with self.assertRaisesRegex(ContractError, "manager root"):
                self.lifecycle.host_reference()

    def test_host_snapshot_rejects_unknown_boot_identity(self):
        with patch.object(self.lifecycle, "_namespace_ids", return_value={}), \
                patch.object(self.lifecycle, "_host_paths", return_value={}), \
                patch.object(Path, "read_text", return_value="unknown"):
            with self.assertRaisesRegex(ContractError, "boot identity is invalid"):
                self.lifecycle._host_snapshot()

    def test_unreadable_host_evidence_is_not_an_empty_reference(self):
        with patch.object(Path, "read_text", side_effect=PermissionError):
            with self.assertRaisesRegex(ContractError, "ID mapping cannot be read"):
                self.lifecycle._check_id_maps()
        with patch("geniusnew.worker_service.os.open", side_effect=PermissionError):
            with self.assertRaisesRegex(ContractError, "namespace state cannot be read"):
                self.lifecycle._namespace_ids("self")
        with self.host_reference(), patch("geniusnew.worker_service.os.read",
                                          side_effect=PermissionError):
            with self.assertRaisesRegex(ContractError, "host reference cannot be read"):
                self.lifecycle._check_host_namespaces()

    def test_host_snapshot_binds_the_open_lease_descriptor(self):
        with tempfile.TemporaryFile() as lease, patch.object(
                self.lifecycle, "_host_paths", return_value={"lease": "/nonexistent"}):
            snapshot = json.loads(self.lifecycle._host_snapshot(lease_fd=lease.fileno()))
            info = os.fstat(lease.fileno())
            self.assertEqual(snapshot["paths"]["lease"], [info.st_dev, info.st_ino])

    def test_unconfirmed_lease_reference_closes_the_new_descriptor(self):
        descriptor = os.open(os.devnull, os.O_RDONLY)
        try:
            info = os.fstat(descriptor)
            with patch.object(self.lifecycle, "_check_node"), \
                    patch("geniusnew.worker_service.os.open", return_value=descriptor), \
                    patch("geniusnew.worker_service.os.lstat", return_value=info), \
                    patch("geniusnew.worker_service.fcntl.flock"), \
                    patch.object(self.lifecycle, "_check_host_namespaces",
                                 side_effect=ContractError("wrong lease")) as reference:
                with self.assertRaisesRegex(ContractError, "wrong lease"):
                    self.lifecycle.acquire()
                reference.assert_called_once_with(lease_fd=descriptor)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def test_socket_unit_effective_profile_refuses_wrong_listener(self):
        properties = {"ActiveState": "active", "Accept": "yes",
                      "MaxConnections": "1", "SocketUser": "root",
                      "SocketGroup": str(self.profile.socket_gid),
                      "SocketMode": "0660", "Listen": self.profile.socket_path + " (Stream)",
                      "RemoveOnStop": "yes"}
        with patch.object(self.lifecycle, "_check_core_identity"), \
                patch.object(self.lifecycle, "_check_host_namespaces"), \
                patch.object(self.lifecycle, "_check_node"), \
                patch.object(self.lifecycle, "_check_runtime_root"), \
                patch.object(self.lifecycle, "_check_installation"), \
                patch.object(self.lifecycle, "_check_unit_file"), \
                patch.object(self.lifecycle, "_show", return_value={
                    **properties, "Listen": "/run/evil.sock (Stream)"}):
            with self.assertRaisesRegex(ContractError, "socket unit does not match"):
                self.lifecycle.preflight()

    def test_lease_inode_swap_is_refused_after_open(self):
        descriptor = os.open(os.devnull, os.O_RDONLY)
        try:
            with patch.object(self.lifecycle, "_check_node"), \
                    patch("geniusnew.worker_service.os.open", return_value=descriptor), \
                    patch("geniusnew.worker_service.os.lstat", return_value=Mock(
                        st_dev=-1, st_ino=-1)):
                with self.assertRaisesRegex(ContractError, "lease inode changed"):
                    self.lifecycle.acquire()
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def test_effective_unit_must_be_hardened_before_payload(self):
        settings = {
            "User": str(self.profile.worker_uid),
            "Group": str(self.profile.worker_gid),
            "SupplementaryGroups": "", "CapabilityBoundingSet": "",
            "AmbientCapabilities": "", "NoNewPrivileges": "yes",
            "PrivateNetwork": "yes", "StandardInput": "socket",
            "StandardOutput": "null", "StandardError": "null",
            "LimitCORE": "0", "LimitCORESoft": "0",
            "KillMode": "control-group", "SendSIGKILL": "yes",
            "Restart": "no", "RuntimeMaxUSec": "12s",
            "TimeoutStopUSec": "2s",
            "RuntimeDirectory": "geniusnew-worker/jobs/one",
            "RuntimeDirectoryMode": "0700", "RuntimeDirectoryPreserve": "no",
            "Slice": "system.slice", "ExecStart": "fixed",
            "SystemCallFilter": "~socket socketpair",
            "SystemCallErrorNumber": "1",
            "UnsetEnvironment": (
                "LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_DEBUG_OUTPUT LD_DYNAMIC_WEAK "
                "LD_BIND_NOT LD_BIND_NOW LD_HWCAP_MASK LD_ORIGIN_PATH LD_PROFILE LD_PROFILE_OUTPUT "
                "LD_SHOW_AUXV LD_TRACE_LOADED_OBJECTS LD_USE_LOAD_BIAS LD_VERBOSE LD_WARN "
                "LD_PREFER_MAP_32BIT_EXEC GLIBC_TUNABLES GCONV_PATH LOCPATH"),
        }
        with patch.object(self.lifecycle, "_check_exec") as check_exec:
            self.lifecycle._check_instance_settings(settings, "one")
            check_exec.assert_called_once_with("fixed")
            for key, wrong in (("User", "123"), ("SupplementaryGroups", "other"),
                               ("StandardOutput", "journal"),
                               ("RuntimeDirectoryPreserve", "yes"),
                               ("RuntimeDirectory", "geniusnew-worker/jobs/old"),
                               ("SystemCallFilter", "socket socketpair"),
                               ("SystemCallFilter", "~socket"),
                               ("SystemCallFilter", ""),
                               ("SystemCallErrorNumber", "0"),
                               ("UnsetEnvironment", "LD_PRELOAD")):
                with self.subTest(key=key), self.assertRaisesRegex(
                        ContractError, "hardening"):
                    self.lifecycle._check_instance_settings({**settings, key: wrong}, "one")

    def test_execstart_cannot_select_another_module_or_identity(self):
        command = ("{ path=" + sys.executable + " ; argv[]=" + sys.executable + " -I -S "
                   + _BOOTSTRAP + " --core-uid " + str(self.profile.core_uid)
                   + " --worker-uid " + str(self.profile.worker_uid)
                   + " --worker-gid " + str(self.profile.worker_gid)
                   + " ; ignore_errors=no ; start_time=[n/a] }")
        with tempfile.TemporaryDirectory() as directory, patch.object(
                self.lifecycle, "_root_controlled"):
            alias = Path(directory, "same-python")
            alias.symlink_to(sys.executable)
            self.lifecycle._check_exec(command)
            for changed, reason in (
                    (command[2:-2], "ExecStart is malformed"),
                    (command.replace(" ; ignore_errors=no", " ; broken ; ignore_errors=no"),
                     "ExecStart is malformed"),
                    (command.replace(" ; argv[]=", " ; path=" + sys.executable
                                     + " ; argv[]="),
                     "ExecStart is ambiguous"),
                    (command.replace("ignore_errors=no", "ignore_errors=yes"),
                     "ExecStart is not fixed"),
                    (command.replace(_BOOTSTRAP, "/evil/worker.py"),
                     "arguments are not fixed"),
                    (command.replace(sys.executable, str(alias)),
                     "arguments are not fixed")):
                with self.subTest(reason=reason), self.assertRaisesRegex(ContractError,
                                                                          reason):
                    self.lifecycle._check_exec(changed)

    def test_rendered_unit_templates_refuse_extra_commands_and_missing_filter(self):
        root = Path(__file__).resolve().parent.parent / "ops" / "worker"
        replacements = {"@CORE_UID@": str(self.profile.core_uid),
                        "@WORKER_UID@": str(self.profile.worker_uid),
                        "@WORKER_GID@": str(self.profile.worker_gid),
                        "@SOCKET_GID@": str(self.profile.socket_gid),
                        "@PYTHON@": sys.executable, "@BOOTSTRAP@": _BOOTSTRAP}
        with tempfile.TemporaryDirectory() as directory, patch.object(
                self.lifecycle, "_root_controlled"):
            for name in ("geniusnew-worker@.service", "geniusnew-worker.socket"):
                source = (root / (name + ".in")).read_text()
                for placeholder, value in replacements.items():
                    source = source.replace(placeholder, value)
                path = Path(directory, name)
                path.write_text(source)
                self.lifecycle._check_unit_content(path, name)
                if name.endswith(".service"):
                    cases = (
                        (source + "#" * 8192, "unit is oversized"),
                        (source + "[Unit]\n", "unit repeats a section"),
                        (source + "malformed\n", "unit has malformed directives"),
                        (source + f"User={self.profile.worker_uid}\n",
                         "unit repeats a directive"),
                        (source.replace("SystemCallFilter=~socket socketpair\n", ""),
                         "service directives are not fixed"),
                        (source + "ExecStartPre=/bin/true\n",
                         "service directives are not fixed"),
                        ("\n".join(line for line in source.splitlines()
                                   if not line.startswith("ExecStart=")) + "\n",
                         "service has no fixed bootstrap"),
                        ("\n".join("ExecStart=" if line.startswith("ExecStart=")
                                   else line for line in source.splitlines()) + "\n",
                         "service has no fixed bootstrap"),
                        (source.replace(_BOOTSTRAP, "/evil/worker.py"),
                         "service bootstrap is not fixed"),
                    )
                else:
                    cases = ((source.replace("SocketMode=0660", "SocketMode=0777"),
                              "socket directives are not fixed"),)
                for bad, reason in cases:
                    with self.subTest(name=name, reason=reason):
                        path.write_text(bad)
                        if name.endswith(".service") and "ExecStart=" not in bad:
                            # Older Python shlex may read stdin for None. A
                            # missing directive must refuse before parsing.
                            with patch("geniusnew.worker_service.shlex.split",
                                       side_effect=AssertionError("parsed missing ExecStart")) as split:
                                with self.assertRaisesRegex(ContractError, reason):
                                    self.lifecycle._check_unit_content(path, name)
                                split.assert_not_called()
                        else:
                            with self.assertRaisesRegex(ContractError, reason):
                                self.lifecycle._check_unit_content(path, name)

    def test_pid_outside_expected_systemd_unit_is_refused(self):
        with patch("geniusnew.worker_service._pidfd_exited", return_value=False), \
                patch.object(Path, "read_text", return_value="0::/user.slice/a.service\n"):
            with self.assertRaisesRegex(ContractError, "outside the expected unit"):
                self.lifecycle.bind(123, 7)


class RunnerBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.core, self.worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.core.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        self.core.setsockopt(socket.SOL_SOCKET, 76, 1)
        self.addCleanup(self.core.close)
        self.addCleanup(self.worker.close)
        self.profile = WorkerServiceProfile(
            core_uid=20001, worker_uid=20002,
            worker_gid=20002, socket_gid=20003,
            socket_path="/run/geniusnew-worker/ipc/worker.sock",
            lease_path="/run/geniusnew-worker/ipc/lease")
        self.lifecycle = Mock(spec=WorkerLifecycle)
        self.lease = os.open(os.devnull, os.O_RDONLY)
        self.addCleanup(self._close_lease)
        self.lifecycle.acquire.return_value = self.lease
        authority = WorkerAuthority(result_key=b"r" * 32, integrity_key=b"i" * 32)
        self.runner = ServiceWorkerRunner(DeterministicSummarizer(),
                                          authority=authority,
                                          profile=self.profile,
                                          lifecycle=self.lifecycle)
        self.runner._connect = Mock(return_value=self.core)
        self.addCleanup(self._close_pending)

    def _close_pending(self):
        for descriptor, identity in self.runner._pending:
            if identity is not None:
                identity.close()

    def _close_lease(self):
        try:
            os.close(self.lease)
        except OSError:
            pass

    def _receive_hello(self):
        header = self.worker.recv(4)
        length = struct.unpack("!I", header)[0]
        return json.loads(self.worker.recv(length))

    def _valid_response(self, seen):
        hello = self._receive_hello()
        _write_frame(self.worker, {"kind": "READY", "version": 1,
                                   "nonce": hello["nonce"]}, limit=256,
                     deadline=time.monotonic() + 1)
        header = self.worker.recv(4)
        length = struct.unpack("!I", header)[0]
        raw = self.worker.recv(length)
        seen.append(json.loads(raw))
        self.assertEqual(self.worker.recv(1), b"")
        _write_frame(self.worker, {"kind": "RESULT", "version": 1,
                                   "nonce": hello["nonce"],
                                   "request_digest": hashlib.sha256(raw).hexdigest(),
                                   "output": {"text": "1 words; sha256:abc"}},
                     limit=32768, deadline=time.monotonic() + 1)
        self.worker.shutdown(socket.SHUT_WR)

    def test_unreadable_prior_lifecycle_prevents_connect_and_quarantines_slot(self):
        self.lifecycle.assert_idle.side_effect = ContractError("unreadable")
        with self.assertRaises(WorkerBoundaryError):
            self.runner._run_worker({"text": "CANARY"})
        self.runner._connect.assert_not_called()
        with self.assertRaises(WorkerBoundaryError):
            self.runner._run_worker({"text": "CANARY"})
        self.lifecycle.acquire.assert_called_once()

    def test_wrong_kernel_uid_gets_no_request_payload(self):
        if self.profile.worker_uid == os.geteuid():
            self.runner.profile = WorkerServiceProfile(**{
                **vars(self.profile), "worker_uid": os.geteuid() + 1})
        received = []

        def fake_service():
            hello = self._receive_hello()
            _write_frame(self.worker, {"kind": "READY", "version": 1,
                                       "nonce": hello["nonce"]}, limit=256,
                         deadline=time.monotonic() + 1)
            self.worker.settimeout(1)
            try:
                received.append(self.worker.recv(4096))
            except ConnectionResetError:
                received.append(b"")

        thread = threading.Thread(target=fake_service)
        thread.start()
        with self.assertRaises(WorkerBoundaryError):
            self.runner._run_worker({"text": "CANARY"})
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(received, [b""])
        self.lifecycle.bind.assert_not_called()

    def test_one_valid_protocol_result_waits_for_cleanup_before_return(self):
        # This exercises framing and sequencing under one UID. Distinct UID and
        # systemd acceptance are established only by the separate real probe.
        self.runner.profile = SimpleNamespace(**{
            **vars(self.profile), "worker_uid": os.geteuid(), "worker_gid": os.getegid()})
        self.lifecycle.bind.return_value = object()
        self.lifecycle.await_end.side_effect = ContractError("cleanup unreadable")
        saw_request = []

        thread = threading.Thread(target=self._valid_response, args=(saw_request,))
        thread.start()
        with self.assertRaises(WorkerBoundaryError):
            self.runner._run_worker({"text": "CANARY"})
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(saw_request[0]["payload"], {"text": "CANARY"})
        self.lifecycle.await_end.assert_called_once()

    def test_result_is_returned_only_after_authenticated_eof_and_cleanup(self):
        self.runner.profile = SimpleNamespace(**{
            **vars(self.profile), "worker_uid": os.geteuid(), "worker_gid": os.getegid()})
        self.lifecycle.bind.return_value = object()
        seen = []
        thread = threading.Thread(target=self._valid_response, args=(seen,))
        thread.start()
        output = self.runner._run_worker({"text": "CANARY"})
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(output, {"text": "1 words; sha256:abc"})
        self.assertEqual(seen[0]["payload"], {"text": "CANARY"})
        self.lifecycle.await_end.assert_called_once()
        self.assertEqual(self.runner._pending, [])
        with self.assertRaises(OSError):
            os.fstat(self.lease)

    def test_ready_requires_a_bound_live_sender_before_request(self):
        profile = self.profile
        lifecycle = Mock(spec=WorkerLifecycle)
        nonce = "a" * 64
        result = {"kind": "RESULT", "version": 1, "nonce": nonce,
                  "request_digest": hashlib.sha256(b"{}").hexdigest(),
                  "output": {"text": "ok"}}
        ready = {"kind": "READY", "version": 1, "nonce": nonce}
        exchange = _CoreExchange(self.core, profile, lifecycle, {"text": "CANARY"},
                                 time.monotonic() + 1)
        with patch("geniusnew.worker_service.secrets.token_hex", return_value=nonce), \
                patch("geniusnew.worker_service._write_frame", return_value=b"{}"), \
                patch("geniusnew.worker_service._read_frame", side_effect=[ready, result]), \
                patch("geniusnew.worker_service._expect_eof"), \
                patch("geniusnew.worker_service._pidfd_exited", return_value=False):
            with self.assertRaisesRegex(ContractError, "no bound process"):
                exchange.run()
            lifecycle.bind.assert_not_called()

        def bind_sender(_connection, *, identity, **_kwargs):
            identity.pid = 123
            identity.pidfd = 7
            return ready
        exchange = _CoreExchange(self.worker, profile, lifecycle, {"text": "CANARY"},
                                 time.monotonic() + 1)
        with patch("geniusnew.worker_service.secrets.token_hex", return_value=nonce), \
                patch("geniusnew.worker_service._write_frame", return_value=b"{}"), \
                patch("geniusnew.worker_service._read_frame", side_effect=bind_sender), \
                patch("geniusnew.worker_service._pidfd_exited", return_value=True):
            with self.assertRaisesRegex(ContractError, "exited before receiving payload"):
                exchange.run()

    def test_core_result_deadline_also_enforces_worker_wall_limit(self):
        profile = self.profile
        nonce = "a" * 64
        deadlines = []
        def read(_connection, *, deadline, identity, **_kwargs):
            if not deadlines:
                deadlines.append(None)
                identity.pid = 123
                identity.pidfd = 7
                return {"kind": "READY", "version": 1, "nonce": nonce}
            deadlines.append(deadline)
            raise ContractError("stopped at result")
        exchange = _CoreExchange(self.core, profile, Mock(spec=WorkerLifecycle),
                                 {"text": "CANARY"}, time.monotonic() + 30)
        start = time.monotonic()
        with patch("geniusnew.worker_service.secrets.token_hex", return_value=nonce), \
                patch("geniusnew.worker_service._write_frame", return_value=b"{}"), \
                patch("geniusnew.worker_service._read_frame", side_effect=read), \
                patch("geniusnew.worker_service._pidfd_exited", return_value=False):
            with self.assertRaisesRegex(ContractError, "stopped at result"):
                exchange.run()
        self.assertEqual(len(deadlines), 2)
        self.assertLessEqual(deadlines[1] - start, IsolationLimits().wall_seconds + 0.1)

    def test_runner_rejects_other_worker_types_and_unchecked_profile(self):
        authority = WorkerAuthority(result_key=b"r" * 32, integrity_key=b"i" * 32)
        with self.assertRaisesRegex(ContractError, "fixed summarizer"):
            ServiceWorkerRunner(object(), authority=authority, profile=self.profile)
        with self.assertRaisesRegex(ContractError, "fixed profile"):
            ServiceWorkerRunner(DeterministicSummarizer(), authority=authority,
                                profile="untrusted")


if __name__ == "__main__":
    unittest.main()
