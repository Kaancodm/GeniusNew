"""Contract tests for the A2/C5 system probe (`tools/a2c5_probe`).

These run unprivileged and without systemd. They drive every refusal in
`contract.py`, read real kernel credentials through socket pairs, and show
that the probe's judgements never turn missing or unknown data into PASS.
They are not the OS-isolation evidence itself: two UIDs, systemd units and
cgroups exist only in a run of `python3 -m tools.a2c5_probe run` on a
disposable host (`docs/A2-C5-SYSTEM-PROBE.md`).
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from array import array
from pathlib import Path

from tools.a2c5_probe import contract, driver
from tools.a2c5_probe.contract import (
    ABSENT,
    DENIED,
    EMPTY,
    FAIL,
    HOLD,
    MISSING,
    PASS,
    POPULATED,
    PRESENT,
    READABLE,
    UNKNOWN,
    Credentials,
    CredentialStream,
    ProbeRefused,
)

ROOT = Path(__file__).resolve().parent.parent
LINUX = sys.platform.startswith("linux")
NONCE = "a" * 64


def _creds(pid: int = 4242, uid: int = 1000, gid: int = 1000) -> tuple[int, int, bytes]:
    return (socket.SOL_SOCKET, socket.SCM_CREDENTIALS, struct.pack("iII", pid, uid, gid))


def _body(**changes) -> bytes:
    value = {"kind": "hello", "nonce": NONCE, "version": 1}
    value.update(changes)
    return contract.canonical(value)


def _write_from_child(fd: int, data: bytes) -> None:
    """Write from a separate process, so the kernel credits another pid."""
    subprocess.run([sys.executable, "-c",
                    "import os, sys; os.write(int(sys.argv[1]), bytes.fromhex(sys.argv[2]))",
                    str(fd), data.hex()], pass_fds=(fd,), check=True)


class FramingTest(unittest.TestCase):
    def test_a_frame_round_trips(self):
        frame = contract.encode_frame(contract.hello(NONCE), contract.HANDSHAKE_MAX)
        self.assertEqual(contract.frame_length(frame[:4], contract.HANDSHAKE_MAX), len(frame) - 4)
        value = contract.decode_body(frame[4:], "hello", contract.HELLO_FIELDS)
        self.assertEqual(value["nonce"], NONCE)

    def test_an_outgoing_frame_over_the_limit_is_refused(self):
        with self.assertRaisesRegex(ProbeRefused, "outgoing frame is too large"):
            contract.encode_frame({"text": "x" * 300}, contract.HANDSHAKE_MAX)

    def test_header_must_be_complete_non_empty_and_bounded(self):
        with self.assertRaisesRegex(ProbeRefused, "frame header is truncated"):
            contract.frame_length(b"\x00\x00\x01", 256)
        with self.assertRaisesRegex(ProbeRefused, "frame is empty"):
            contract.frame_length(struct.pack("!I", 0), 256)
        with self.assertRaisesRegex(ProbeRefused, "incoming frame is too large"):
            contract.frame_length(struct.pack("!I", 257), 256)

    def test_body_must_be_a_canonical_object(self):
        with self.assertRaisesRegex(ProbeRefused, "not a JSON object"):
            contract.decode_body(b"[]", "hello", contract.HELLO_FIELDS)
        with self.assertRaisesRegex(ProbeRefused, "not a JSON object"):
            contract.decode_body(b'{"kind":NaN}', "hello", contract.HELLO_FIELDS)
        with self.assertRaisesRegex(ProbeRefused, "not canonical"):
            contract.decode_body(_body().replace(b",", b", "), "hello", contract.HELLO_FIELDS)

    def test_body_fields_kind_version_and_nonce_are_exact(self):
        with self.assertRaisesRegex(ProbeRefused, "unexpected fields"):
            contract.decode_body(_body(extra=1), "hello", contract.HELLO_FIELDS)
        with self.assertRaisesRegex(ProbeRefused, "unexpected kind"):
            contract.decode_body(_body(kind="ready"), "hello", contract.HELLO_FIELDS)
        with self.assertRaisesRegex(ProbeRefused, "unsupported version"):
            contract.decode_body(_body(version=2), "hello", contract.HELLO_FIELDS)
        with self.assertRaisesRegex(ProbeRefused, "unsupported version"):
            contract.decode_body(_body(version=True), "hello", contract.HELLO_FIELDS)
        for nonce in ("A" * 64, "a" * 63, 7):
            with self.assertRaisesRegex(ProbeRefused, "nonce must be"):
                contract.decode_body(_body(nonce=nonce), "hello", contract.HELLO_FIELDS)

    def test_request_and_result_are_bound_to_the_handshake(self):
        with self.assertRaisesRegex(ProbeRefused, "nonce does not match"):
            contract.require_nonce({"nonce": "b" * 64}, NONCE)
        self.assertEqual(contract.check_request(contract.request(NONCE, "text"), NONCE), "text")
        with self.assertRaisesRegex(ProbeRefused, "request text has the wrong shape"):
            contract.check_request({"nonce": NONCE, "text": 5}, NONCE)
        with self.assertRaisesRegex(ProbeRefused, "request text has the wrong shape"):
            contract.check_request({"nonce": NONCE, "text": "x" * (contract.TEXT_MAX + 1)}, NONCE)

        good = contract.result(NONCE, "d" * 64, "ok", {"length": 4})
        self.assertEqual(contract.check_result(good, NONCE, "d" * 64), {"length": 4})
        with self.assertRaisesRegex(ProbeRefused, "not bound to this request"):
            contract.check_result(good, NONCE, "e" * 64)
        with self.assertRaisesRegex(ProbeRefused, "outside the closed set"):
            contract.check_result({**good, "status": "fine"}, NONCE, "d" * 64)
        with self.assertRaisesRegex(ProbeRefused, "output must be an object"):
            contract.check_result({**good, "output": []}, NONCE, "d" * 64)


class CredentialMessageTest(unittest.TestCase):
    def test_exactly_one_well_formed_credential_is_accepted(self):
        self.assertEqual(contract.credentials_from([_creds(7, 8, 9)], 0), Credentials(7, 8, 9))

    def test_passed_descriptors_are_closed_and_refused(self):
        descriptor = os.open(os.devnull, os.O_RDONLY)
        rights = (socket.SOL_SOCKET, socket.SCM_RIGHTS, array("i", [descriptor]).tobytes())
        with self.assertRaisesRegex(ProbeRefused, "descriptor passing"):
            contract.credentials_from([_creds(), rights], 0)
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_truncation_unknown_types_and_counts_are_refused(self):
        with self.assertRaisesRegex(ProbeRefused, "truncated"):
            contract.credentials_from([_creds()], socket.MSG_CTRUNC)
        with self.assertRaisesRegex(ProbeRefused, "unknown type"):
            contract.credentials_from([(socket.SOL_SOCKET, 99, b"")], 0)
        with self.assertRaisesRegex(ProbeRefused, "exactly one kernel credential"):
            contract.credentials_from([], 0)
        with self.assertRaisesRegex(ProbeRefused, "exactly one kernel credential"):
            contract.credentials_from([_creds(), _creds()], 0)
        with self.assertRaisesRegex(ProbeRefused, "wrong size"):
            contract.credentials_from([(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, b"\0" * 8)], 0)
        with self.assertRaisesRegex(ProbeRefused, "no sender process"):
            contract.credentials_from([_creds(pid=0)], 0)


@unittest.skipUnless(LINUX, "SCM_CREDENTIALS is Linux-only")
class KernelCredentialTest(unittest.TestCase):
    """Real kernel credentials on a socket pair: same UID, but real pids and chunks."""

    def setUp(self):
        self.reader, self.writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.reader.close)
        self.addCleanup(self.writer.close)

    def stream(self, **changes) -> CredentialStream:
        self.reader.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        options = {"uid": os.getuid(), "gid": os.getgid()}
        options.update(changes)
        return CredentialStream(self.reader, **options)

    def test_a_fragmented_frame_is_checked_chunk_by_chunk(self):
        stream = self.stream()
        frame = contract.encode_frame(contract.ready(NONCE), contract.HANDSHAKE_MAX)
        for start, end in ((0, 2), (2, 4), (4, 30), (30, len(frame))):
            self.writer.sendall(frame[start:end])
        body = stream.read_frame(contract.HANDSHAKE_MAX, time.monotonic() + 2)
        self.assertEqual(contract.decode_body(body, "ready", contract.READY_FIELDS)["nonce"], NONCE)
        self.assertGreaterEqual(stream.chunks, 2)
        self.assertEqual(stream.sender, Credentials(os.getpid(), os.getuid(), os.getgid()))

    def test_another_uid_is_refused(self):
        stream = self.stream(uid=os.getuid() + 1)
        self.writer.sendall(b"\x00\x00\x00\x01x")
        with self.assertRaisesRegex(ProbeRefused, "configured worker UID"):
            stream.read_exact(4, time.monotonic() + 2)

    def test_another_gid_is_refused(self):
        stream = self.stream(gid=os.getgid() + 1)
        self.writer.sendall(b"\x00\x00\x00\x01x")
        with self.assertRaisesRegex(ProbeRefused, "configured worker GID"):
            stream.read_exact(4, time.monotonic() + 2)

    def test_a_second_process_inside_one_frame_is_refused(self):
        stream = self.stream()
        frame = contract.encode_frame(contract.ready(NONCE), contract.HANDSHAKE_MAX)
        _write_from_child(self.writer.fileno(), frame[:20])
        _write_from_child(self.writer.fileno(), frame[20:])
        with self.assertRaisesRegex(ProbeRefused, "sender changed inside the stream"):
            stream.read_frame(contract.HANDSHAKE_MAX, time.monotonic() + 2)

    def test_data_written_before_so_passcred_has_no_sender(self):
        # The order the probe's core keeps: SO_PASSCRED before HELLO. A READY
        # written earlier cannot be attributed and is refused, not trusted.
        self.writer.sendall(b"\x00\x00\x00\x01x")
        stream = self.stream()
        with self.assertRaisesRegex(ProbeRefused, "no sender process"):
            stream.read_exact(4, time.monotonic() + 2)

    def test_a_passed_descriptor_is_refused_and_closed(self):
        before = len(os.listdir("/proc/self/fd"))
        stream = self.stream()
        descriptor = os.open(os.devnull, os.O_RDONLY)
        try:
            self.writer.sendmsg([b"\x00\x00\x00\x01x"],
                               [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array("i", [descriptor]))])
        finally:
            os.close(descriptor)
        with self.assertRaisesRegex(ProbeRefused, "descriptor passing"):
            stream.read_exact(4, time.monotonic() + 2)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_end_of_stream_inside_a_frame_is_refused(self):
        stream = self.stream()
        self.writer.sendall(b"\x00\x00")
        self.writer.shutdown(socket.SHUT_WR)
        with self.assertRaisesRegex(ProbeRefused, "stream ended inside a frame"):
            stream.read_exact(4, time.monotonic() + 2)

    def test_silence_and_a_passed_deadline_are_refused(self):
        stream = self.stream()
        with self.assertRaisesRegex(ProbeRefused, "deadline passed"):
            stream.read_exact(4, time.monotonic() + 0.1)
        late = CredentialStream(self.reader, uid=os.getuid(), gid=os.getgid(),
                                clock=lambda: 100.0)
        with self.assertRaisesRegex(ProbeRefused, "deadline passed"):
            late.read_exact(4, 50.0)

    def test_bytes_after_the_last_frame_are_refused(self):
        stream = self.stream()
        self.writer.sendall(b"!")
        with self.assertRaisesRegex(ProbeRefused, "bytes after its last frame"):
            stream.expect_eof(time.monotonic() + 2)
        self.writer.shutdown(socket.SHUT_WR)
        stream.expect_eof(time.monotonic() + 2)


class BootstrapTest(unittest.TestCase):
    def test_socket_activation_must_name_this_process_and_one_fd(self):
        contract.require_listen_fds({"LISTEN_PID": "12", "LISTEN_FDS": "1"}, 12)
        with self.assertRaisesRegex(ProbeRefused, "not meant for this process"):
            contract.require_listen_fds({"LISTEN_PID": "13", "LISTEN_FDS": "1"}, 12)
        with self.assertRaisesRegex(ProbeRefused, "exactly one accepted connection"):
            contract.require_listen_fds({"LISTEN_PID": "12", "LISTEN_FDS": "2"}, 12)

    def test_the_peer_must_be_a_process_of_the_core_uid(self):
        contract.require_peer(Credentials(5, 100, 100), 100)
        with self.assertRaisesRegex(ProbeRefused, "no peer process"):
            contract.require_peer(Credentials(0, 100, 100), 100)
        with self.assertRaisesRegex(ProbeRefused, "configured core UID"):
            contract.require_peer(Credentials(5, 101, 100), 100)

    def test_the_worker_must_be_undumpable_without_core_dumps(self):
        contract.require_hardened(0, (0, 0))
        with self.assertRaisesRegex(ProbeRefused, "still dumpable"):
            contract.require_hardened(1, (0, 0))
        with self.assertRaisesRegex(ProbeRefused, "core dump"):
            contract.require_hardened(0, (0, 1))


SLICE = "gnprobe0011aabbw.slice"
UNIT_RE = re.compile(r"gnprobe0011aabb-[a-z]+@[A-Za-z0-9_.:\\-]+\.service")
UNIT = "gnprobe0011aabb-ok@3-1234-1001.service"
INVOCATION = "0123456789abcdef0123456789abcdef"
PROPERTIES = {"LoadState": "loaded", "ActiveState": "active", "MainPID": "1234",
              "ControlGroup": f"/{SLICE}/{UNIT}", "InvocationID": INVOCATION}


class LifecycleTest(unittest.TestCase):
    def test_systemd_text_must_be_present(self):
        self.assertEqual(contract.require_text("x"), "x")
        with self.assertRaisesRegex(ProbeRefused, "systemd state is unknown"):
            contract.require_text(None)

    def test_cgroup_events_must_state_populated(self):
        self.assertTrue(contract.cgroup_populated("populated 1\nfrozen 0\n"))
        self.assertFalse(contract.cgroup_populated("populated 0\nfrozen 0\n"))
        for text in ("frozen 0\n", "populated 2\n", ""):
            with self.assertRaisesRegex(ProbeRefused, "cgroup.events state is unknown"):
                contract.cgroup_populated(text)

    def test_a_sender_maps_to_exactly_one_worker_unit(self):
        text = f"0::/{SLICE}/{UNIT}\n"
        self.assertEqual(contract.unit_of_process(text, SLICE, UNIT_RE),
                         (f"/{SLICE}/{UNIT}", UNIT))
        for broken in ("12:pids:/x\n", text + text):
            with self.assertRaisesRegex(ProbeRefused, "exactly one unified cgroup"):
                contract.unit_of_process(broken, SLICE, UNIT_RE)
        for outside in (f"0::/system.slice/{UNIT}\n", f"0::/{SLICE}/{UNIT}/sub\n"):
            with self.assertRaisesRegex(ProbeRefused, "outside the worker slice"):
                contract.unit_of_process(outside, SLICE, UNIT_RE)
        with self.assertRaisesRegex(ProbeRefused, "not in a worker instance unit"):
            contract.unit_of_process(f"0::/{SLICE}/gnprobe0011aabb-core1.service\n", SLICE, UNIT_RE)

    def test_properties_must_be_complete(self):
        text = "".join(f"{key}={value}\n" for key, value in PROPERTIES.items())
        self.assertEqual(contract.parse_properties(text), PROPERTIES)
        with self.assertRaisesRegex(ProbeRefused, "incomplete"):
            contract.parse_properties(text.replace("MainPID=1234\n", ""))

    def test_ready_binds_to_the_running_main_process_of_one_invocation(self):
        self.assertEqual(contract.bind_invocation(PROPERTIES, 1234, f"/{SLICE}/{UNIT}"), INVOCATION)
        cases = (({"LoadState": "not-found"}, "not loaded"),
                 ({"ActiveState": "deactivating"}, "not active"),
                 ({"MainPID": "1235"}, "main process"),
                 ({"ControlGroup": f"/{SLICE}/other.service"}, "cgroup does not match"),
                 ({"InvocationID": ""}, "no invocation ID"))
        for change, message in cases:
            with self.assertRaisesRegex(ProbeRefused, message):
                contract.bind_invocation({**PROPERTIES, **change}, 1234, f"/{SLICE}/{UNIT}")

    def test_unit_end_is_read_only_from_known_states(self):
        self.assertFalse(contract.unit_has_ended(PROPERTIES, INVOCATION))
        self.assertTrue(contract.unit_has_ended({**PROPERTIES, "ActiveState": "failed"}, INVOCATION))
        self.assertTrue(contract.unit_has_ended(
            {**PROPERTIES, "LoadState": "not-found", "ActiveState": "inactive",
             "InvocationID": ""}, INVOCATION))
        with self.assertRaisesRegex(ProbeRefused, "load state is unknown"):
            contract.unit_has_ended({**PROPERTIES, "LoadState": "masked"}, INVOCATION)
        with self.assertRaisesRegex(ProbeRefused, "active state is unknown"):
            contract.unit_has_ended({**PROPERTIES, "ActiveState": "maintenance"}, INVOCATION)
        with self.assertRaisesRegex(ProbeRefused, "another invocation"):
            contract.unit_has_ended({**PROPERTIES, "InvocationID": "f" * 32}, INVOCATION)

    def test_end_needs_systemd_cgroup_and_job_directory_to_agree(self):
        self.assertTrue(contract.end_confirmed(True, ABSENT, ABSENT))
        self.assertTrue(contract.end_confirmed(True, EMPTY, ABSENT))
        self.assertFalse(contract.end_confirmed(False, ABSENT, ABSENT))
        self.assertFalse(contract.end_confirmed(True, POPULATED, ABSENT))
        self.assertFalse(contract.end_confirmed(True, EMPTY, PRESENT))
        with self.assertRaisesRegex(ProbeRefused, "unit cgroup state is unknown"):
            contract.end_confirmed(True, UNKNOWN, ABSENT)
        with self.assertRaisesRegex(ProbeRefused, "job directory state is unknown"):
            contract.end_confirmed(True, ABSENT, UNKNOWN)

    def test_unit_lists_are_parsed_or_refused(self):
        text = (f"{UNIT} loaded active running worker\n"
                "● gnprobe0011aabb-ok@4-1-2.service loaded failed failed worker\n\n")
        self.assertEqual(contract.parse_unit_list(text),
                         [(UNIT, "active"), ("gnprobe0011aabb-ok@4-1-2.service", "failed")])
        with self.assertRaisesRegex(ProbeRefused, "unknown shape"):
            contract.parse_unit_list(f"{UNIT} loaded\n")

    def test_idle_means_empty_slice_ended_units_and_no_job_paths(self):
        contract.require_idle(ABSENT, [], [])
        contract.require_idle(EMPTY, [(UNIT, "inactive"), (UNIT, "failed")], [])
        with self.assertRaisesRegex(ProbeRefused, "worker slice state is unknown"):
            contract.require_idle(UNKNOWN, [], [])
        with self.assertRaisesRegex(ProbeRefused, "still alive"):
            contract.require_idle(POPULATED, [], [])
        with self.assertRaisesRegex(ProbeRefused, "worker instance state is unknown"):
            contract.require_idle(EMPTY, [(UNIT, "maintenance")], [])
        with self.assertRaisesRegex(ProbeRefused, "has not ended"):
            contract.require_idle(EMPTY, [(UNIT, "deactivating")], [])
        with self.assertRaisesRegex(ProbeRefused, "listing is unknown"):
            contract.require_idle(EMPTY, [], None)
        with self.assertRaisesRegex(ProbeRefused, "cleanup of an earlier job"):
            contract.require_idle(EMPTY, [], ["3-1234-1001"])


class VerdictTest(unittest.TestCase):
    def test_overall_is_pass_only_for_a_non_empty_set_of_pass(self):
        self.assertEqual(contract.overall([]), HOLD)
        self.assertEqual(contract.overall([PASS, PASS]), PASS)
        self.assertEqual(contract.overall([PASS, HOLD]), HOLD)
        self.assertEqual(contract.overall([HOLD, FAIL, PASS]), FAIL)
        self.assertEqual(contract.overall([PASS, "ok"]), HOLD)

    def test_only_eacces_and_eperm_count_as_denied(self):
        self.assertEqual(contract.classify_errno(None), READABLE)
        self.assertEqual(contract.classify_errno(errno.EACCES), DENIED)
        self.assertEqual(contract.classify_errno(errno.EPERM), DENIED)
        self.assertEqual(contract.classify_errno(errno.ENOENT), MISSING)
        self.assertEqual(contract.classify_errno(errno.EIO), contract.ERROR)

    def test_access_is_pass_only_when_every_target_was_denied(self):
        self.assertEqual(contract.access_verdict({"a": DENIED, "b": DENIED}, ["a", "b"]), PASS)
        self.assertEqual(contract.access_verdict({"a": DENIED, "b": READABLE}, ["a", "b"]), FAIL)
        self.assertEqual(contract.access_verdict({"a": DENIED, "b": MISSING}, ["a", "b"]), HOLD)
        self.assertEqual(contract.access_verdict({"a": DENIED}, ["a", "b"]), HOLD)
        self.assertEqual(contract.access_verdict({}, []), HOLD)

    def test_report_content_is_limited_to_safe_values(self):
        contract.require_safe_observations({"a": [1, True, None, 1.5, "EACCES"], "b": {"c": "x-y"}})
        nested: list = []
        for _ in range(10):
            nested = [nested]
        with self.assertRaisesRegex(ProbeRefused, "too deep"):
            contract.require_safe_observations(nested)
        with self.assertRaisesRegex(ProbeRefused, "key has an unsafe shape"):
            contract.require_safe_observations({"bad\nkey": 1})
        with self.assertRaisesRegex(ProbeRefused, "key has an unsafe shape"):
            contract.require_safe_observations({1: 1})
        with self.assertRaisesRegex(ProbeRefused, "text has an unsafe shape"):
            contract.require_safe_observations({"a": "quote\"d"})
        with self.assertRaisesRegex(ProbeRefused, "unsupported value"):
            contract.require_safe_observations({"a": b"bytes"})
        self.assertEqual(contract.safe_text('Ubuntu "24.04"\n'), "Ubuntu _24.04__")

    def test_a_report_with_a_secret_or_payload_is_refused(self):
        contract.require_no_secret("all clear", ["s3cr3t", ""])
        with self.assertRaisesRegex(ProbeRefused, "secret or payload"):
            contract.require_no_secret("leaked s3cr3t here", ["s3cr3t"])


class JudgementTest(unittest.TestCase):
    """Missing or partial evidence is HOLD; a violated property is FAIL."""

    REFUSED = {"outcome": "refused_before_payload", "payload_bytes_sent": 0,
               "reason": "sender is not the configured worker UID", "end_confirmed": True}

    def test_a_refusal_passes_only_with_zero_payload_bytes_on_both_sides(self):
        seen = {"bytes_after_ready": 0}
        reason = self.REFUSED["reason"]
        self.assertEqual(driver.judge_refusal(self.REFUSED, seen, reason), PASS)
        self.assertEqual(driver.judge_refusal(None, seen, reason), HOLD)
        self.assertEqual(driver.judge_refusal(self.REFUSED, None, reason), HOLD)
        self.assertEqual(driver.judge_refusal(self.REFUSED, seen, "other"), HOLD)
        self.assertEqual(driver.judge_refusal({**self.REFUSED, "end_confirmed": False}, seen), HOLD)
        self.assertEqual(driver.judge_refusal({**self.REFUSED, "payload_bytes_sent": 90}, seen), FAIL)
        self.assertEqual(driver.judge_refusal(self.REFUSED, {"bytes_after_ready": 90}), FAIL)
        self.assertEqual(driver.judge_refusal({**self.REFUSED, "outcome": "completed"}, seen), HOLD)

    def test_a_completed_handshake_needs_fragments_binding_and_end(self):
        done = {"outcome": "completed", "ready_chunks": 5, "sender_is_main_pid": True,
                "end_confirmed": True}
        self.assertEqual(driver.judge_completed(done, min_ready_chunks=3,
                                                observation={"request_read": True}), PASS)
        self.assertEqual(driver.judge_completed({**done, "ready_chunks": 1},
                                                min_ready_chunks=3), HOLD)
        self.assertEqual(driver.judge_completed({**done, "end_confirmed": False}), HOLD)
        self.assertEqual(driver.judge_completed(done, observation={}), HOLD)

    def test_serial_dispatch_needs_exactly_one_run_and_one_refusal(self):
        done = {"outcome": "completed", "end_confirmed": True}
        busy = {"outcome": "refused_lease_busy", "connected": False}
        self.assertEqual(driver.judge_pair([done, busy], 1, 1), PASS)
        self.assertEqual(driver.judge_pair([done, done], 2, 1), HOLD)
        self.assertEqual(driver.judge_pair([done, busy], 2, 1), HOLD)
        self.assertEqual(driver.judge_pair([done, None], 1, 1), HOLD)
        self.assertEqual(driver.judge_pair([done, busy], 1, None), HOLD)
        self.assertEqual(driver.judge_pair([done, done], 2, 2), FAIL)

    def test_a_second_connection_must_not_get_an_answer(self):
        first = {"outcome": "completed", "end_confirmed": True}
        quiet = {"outcome": "stranger", "bytes_received": 0, "eof": True}
        self.assertEqual(driver.judge_raw_second({"first": first, "second": quiet}, 1), PASS)
        self.assertEqual(driver.judge_raw_second(
            {"first": first, "second": {**quiet, "bytes_received": 50}}, 1), FAIL)
        self.assertEqual(driver.judge_raw_second({"first": first, "second": quiet}, 2), FAIL)
        self.assertEqual(driver.judge_raw_second({"first": first, "second": {}}, 1), HOLD)

    def test_an_end_is_confirmed_only_with_no_process_left(self):
        done = {"outcome": "no_valid_result", "end_confirmed": True}
        self.assertEqual(driver.judge_end(done, "no_valid_result", jobs_seen=1, leftover=0), PASS)
        self.assertEqual(driver.judge_end(done, "no_valid_result", jobs_seen=1, leftover=1), FAIL)
        self.assertEqual(driver.judge_end(done, "no_valid_result", jobs_seen=0, leftover=0), HOLD)
        self.assertEqual(driver.judge_end(done, "no_valid_result", jobs_seen=1, leftover=None), HOLD)
        self.assertEqual(driver.judge_end({**done, "end_confirmed": False}, "no_valid_result",
                                          jobs_seen=1, leftover=0), HOLD)

    def test_linger_and_core_restart_keep_the_slot_closed(self):
        first = {"outcome": "completed", "end_confirmed": True, "end_wait_ms": 4000}
        busy = {"outcome": "refused_lease_busy"}
        self.assertEqual(driver.judge_linger(first, busy, max_instances=1, leftover=0), PASS)
        self.assertEqual(driver.judge_linger({**first, "end_wait_ms": 10}, busy,
                                             max_instances=1, leftover=0), HOLD)
        self.assertEqual(driver.judge_linger(first, {"outcome": "completed"},
                                             max_instances=2, leftover=0), FAIL)

        refused = {"outcome": "refused_before_connect", "connected": False,
                   "reason": "a worker process is still alive"}
        later = {"outcome": "completed", "end_confirmed": True}
        options = {"killed": True, "second": refused, "accepted_delta": 0, "third": later,
                   "max_instances": 1, "leftover": 0}
        self.assertEqual(driver.judge_core_restart(**options), PASS)
        self.assertEqual(driver.judge_core_restart(**{**options, "killed": False}), HOLD)
        self.assertEqual(driver.judge_core_restart(
            **{**options, "second": {**refused, "reason": "worker slice state is unknown"}}), HOLD)
        self.assertEqual(driver.judge_core_restart(
            **{**options, "second": {**refused, "connected": True}}), FAIL)

    def test_unreadable_state_must_refuse_before_connect(self):
        refused = {"outcome": "refused_before_connect", "connected": False,
                   "reason": "job directory listing is unknown"}
        self.assertEqual(driver.judge_unknown_states({"a": refused}), PASS)
        self.assertEqual(driver.judge_unknown_states({"a": refused, "b": None}), HOLD)
        self.assertEqual(driver.judge_unknown_states(
            {"a": {**refused, "reason": "a worker process is still alive"}}), HOLD)
        self.assertEqual(driver.judge_unknown_states(
            {"a": {"outcome": "completed", "connected": True}}), FAIL)
        self.assertEqual(driver.judge_unknown_states({}), HOLD)

    def test_identity_and_access_need_positive_controls(self):
        uids = {"core": 901, "worker": 902, "anchor": 903, "foreign": 904}
        gids = {**uids, "ipc": 905}
        identity = {"resuid": [902] * 3, "resgid": [902] * 3, "groups": [902],
                    "cap_effective_zero": True, "cap_permitted_zero": True, "dumpable": 0}
        self.assertEqual(driver.judge_identity(identity, uids, gids), PASS)
        self.assertEqual(driver.judge_identity({**identity, "groups": [902, 905]}, uids, gids), FAIL)
        self.assertEqual(driver.judge_identity({**identity, "resuid": [902, 901, 902]},
                                               uids, gids), FAIL)
        self.assertEqual(driver.judge_identity({**identity, "dumpable": 1}, uids, gids), HOLD)
        self.assertEqual(driver.judge_identity(None, uids, gids), HOLD)

        flat = {"a:python": DENIED, "a:libc": DENIED}
        self.assertEqual(driver.judge_access(flat, ["a:python", "a:libc"], {"c": True}), PASS)
        self.assertEqual(driver.judge_access(flat, ["a:python", "a:libc"], {"c": False}), HOLD)
        self.assertEqual(driver.judge_access({**flat, "a:libc": READABLE},
                                             ["a:python", "a:libc"], {"c": False}), FAIL)

    def test_strangers_concurrency_preflight_and_cleanup(self):
        denied = {"outcome": "connect_failed", "access": DENIED}
        quiet = {"outcome": "stranger", "bytes_received": 0, "eof": True}
        self.assertEqual(driver.judge_strangers(denied, quiet), PASS)
        self.assertEqual(driver.judge_strangers(quiet, quiet), FAIL)
        self.assertEqual(driver.judge_strangers(denied, {**quiet, "bytes_received": 9}), FAIL)
        self.assertEqual(driver.judge_strangers(denied, {"outcome": "stranger"}), HOLD)

        self.assertEqual(driver.judge_concurrency(1, 0, 100), PASS)
        self.assertEqual(driver.judge_concurrency(2, 0, 100), FAIL)
        self.assertEqual(driver.judge_concurrency(1, 3, 100), HOLD)
        self.assertEqual(driver.judge_concurrency(0, 0, 2), HOLD)

        good = {"disposable": True, "euid": 0, "systemd_init": True, "cgroup2": True,
                "system_bus": True, "tools": {"systemctl": "/usr/bin/systemctl"},
                "interpreter_trusted": True, "commit": {"sha": "a" * 40, "clean": True}}
        self.assertEqual({f.verdict for f in driver.judge_preflight(**good)}, {PASS})
        for change in ({"disposable": False}, {"euid": 1000}, {"systemd_init": False},
                       {"tools": {"systemctl": None}}, {"commit": {"sha": "a" * 40, "clean": False}}):
            verdicts = [f.verdict for f in driver.judge_preflight(**{**good, **change})]
            self.assertEqual(contract.overall(verdicts), HOLD, change)

        state = dict.fromkeys(("processes_gone", "units_gone", "unit_files_gone",
                               "run_directory_gone", "users_gone", "groups_gone"), True)
        self.assertEqual(driver.judge_cleanup(state), PASS)
        self.assertEqual(driver.judge_cleanup({**state, "users_gone": False}), HOLD)


class ReportTest(unittest.TestCase):
    def report(self, observations):
        finding = driver.Finding("C1.example", "1", PASS, observations)
        return driver.build_report(facts={"kernel": "6.8.0"}, commit={"sha": "a" * 40,
                                                                      "clean": True},
                                   findings=[finding], started="2026-10-17T00:00:00Z",
                                   finished="2026-10-17T00:01:00Z", run={})

    def test_a_clean_report_keeps_its_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            verdict, path = driver.write_report(self.report({"ready_chunks": 5}),
                                                Path(directory), ["s3cr3t"])
            self.assertEqual(verdict, PASS)
            self.assertEqual(json.loads(path.read_text())["findings"][0]["id"], "C1.example")
            self.assertTrue(path.with_suffix(".md").exists())

    def test_a_report_that_would_leak_is_replaced_by_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            verdict, path = driver.write_report(self.report({"note": "s3cr3t"}),
                                                Path(directory), ["s3cr3t"])
            text = path.read_text() + path.with_suffix(".md").read_text()
            self.assertEqual(verdict, HOLD)
            self.assertNotIn("s3cr3t", text)
            self.assertIn("X.report_redaction", text)

    def test_without_the_disposable_flag_the_host_is_not_touched(self):
        with tempfile.TemporaryDirectory() as directory:
            status = driver.run(argparse.Namespace(disposable_host=False, report_dir=directory))
            report = json.loads(next(Path(directory).glob("*.json")).read_text())
        self.assertEqual(status, 2)
        self.assertEqual(report["verdict"], HOLD)
        self.assertEqual(report["run"], {})
        self.assertEqual(report["findings"][0], {"id": "P.disposable_host", "check": "preflight",
                                                 "verdict": HOLD,
                                                 "observations": {"flag_given": False}})


_WORKER = """
import os, sys
os.dup2(int(sys.argv[3]), 3)
os.environ["LISTEN_PID"] = str(os.getpid())
os.environ["LISTEN_FDS"] = "1"
from tools.a2c5_probe.worker import main
sys.exit(main(sys.argv[:3]))
"""


@unittest.skipUnless(LINUX, "the probe worker is Linux-only")
class WorkerProtocolTest(unittest.TestCase):
    """Drive the real worker code with one UID: protocol order, not isolation."""

    def start(self, scenario: str):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        os.mkdir(os.path.join(directory.name, scenario))
        config = {"uids": {"core": os.getuid()}, "observe": directory.name, "targets": {}}
        path = os.path.join(directory.name, "config.json")
        with open(path, "w", encoding="ascii") as handle:
            json.dump(config, handle)
        core, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(core.close)
        process = subprocess.Popen([sys.executable, "-c", _WORKER, path, scenario,
                                    str(worker.fileno())], pass_fds=(worker.fileno(),),
                                   cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        worker.close()
        self.addCleanup(process.wait, 10)
        core.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        return core, process, directory.name

    def handshake(self, core):
        core.sendall(contract.encode_frame(contract.hello(NONCE), contract.HANDSHAKE_MAX))
        stream = CredentialStream(core, uid=os.getuid(), gid=os.getgid())
        body = stream.read_frame(contract.HANDSHAKE_MAX, time.monotonic() + 5)
        contract.require_nonce(contract.decode_body(body, "ready", contract.READY_FIELDS), NONCE)
        return stream

    def test_fragmented_ready_then_one_request_and_one_result(self):
        core, process, directory = self.start("fragmented")
        stream = self.handshake(core)
        self.assertGreaterEqual(stream.chunks, 3)
        self.assertEqual(stream.sender.pid, process.pid)
        frame = contract.encode_frame(contract.request(NONCE, "four"), contract.BODY_MAX)
        core.sendall(frame)
        deadline = time.monotonic() + 5
        value = contract.decode_body(stream.read_frame(contract.BODY_MAX, deadline),
                                     "result", contract.RESULT_FIELDS)
        self.assertEqual(contract.check_result(value, NONCE, contract.digest(frame[4:])),
                         {"length": 4})
        stream.expect_eof(deadline)
        self.assertEqual(process.wait(10), 0)
        observed = json.loads(Path(directory, "fragmented", f"{process.pid}.json").read_text())
        self.assertEqual(observed, {"bytes_after_ready": len(frame), "request_read": True})

    def test_a_ready_split_across_two_processes_is_refused(self):
        core, process, directory = self.start("sender")
        with self.assertRaisesRegex(ProbeRefused, "sender changed inside the stream"):
            self.handshake(core)
        core.close()
        process.wait(10)
        observed = json.loads(next(Path(directory, "sender").glob("*.json")).read_text())
        self.assertEqual(observed["bytes_after_ready"], 0)

    def test_a_ready_with_a_descriptor_is_refused(self):
        core, process, _ = self.start("rights")
        with self.assertRaisesRegex(ProbeRefused, "descriptor passing"):
            self.handshake(core)

    def test_an_early_ready_does_not_carry_this_handshake(self):
        core, process, _ = self.start("early")
        with self.assertRaisesRegex(ProbeRefused, "nonce does not match"):
            self.handshake(core)

    def test_the_worker_says_nothing_to_another_uid(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = os.path.join(directory.name, "config.json")
        with open(path, "w", encoding="ascii") as handle:
            json.dump({"uids": {"core": os.getuid() + 1}, "observe": directory.name}, handle)
        core, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(core.close)
        process = subprocess.Popen([sys.executable, "-c", _WORKER, path, "ok",
                                    str(worker.fileno())], pass_fds=(worker.fileno(),),
                                   cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        worker.close()
        self.assertEqual(process.wait(10), 0)
        core.settimeout(2)
        self.assertEqual(core.recv(16), b"")


if __name__ == "__main__":
    unittest.main()
