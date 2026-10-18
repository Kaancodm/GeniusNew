"""Pure unit tests for the acceptance client; never contact an installed worker.

Fakes describe the peer and lifecycle at the client boundary. These tests check
that the acceptance client cannot turn a wrong result, uncertain cleanup, or an
unbound observation into a PASS. They are not host-isolation evidence.
"""

from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import errno
import io
import json
from pathlib import Path
import socket
import struct
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

helper_dir = Path(__file__).resolve().parent.parent / "tools" / "worker_acceptance"
if not helper_dir.is_dir():
    helper_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(helper_dir))
import core


NONCE = "a" * 64
EXPECTED_RESULT = {"text": "2 words; sha256:1a989ea86150171c"}


class ContractError(Exception):
    pass


class WorkerBoundaryError(Exception):
    pass


def request(text, nonce):
    """Fixed observed REQUEST contract, independent of the installed service."""
    return {
        "kind": "REQUEST", "version": 1, "nonce": nonce,
        "worker": "summarize", "payload": dict(text),
        "limits": {"wall_seconds": 2.0, "cpu_seconds": 1,
                   "memory_bytes": 536870912, "max_file_bytes": 1048576,
                   "max_open_files": 64},
    }


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


class Connection:
    def __init__(self, response=b""):
        self.response = response
        self.frames = []
        self.timeouts = []
        self.shutdowns = []
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.exited = True

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, frame):
        self.frames.append(frame)

    def shutdown(self, how):
        self.shutdowns.append(how)

    def recv(self, size):
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response[:size]


def runtime(response=b""):
    """Build an inert fake runtime, with no file descriptors or sockets opened."""
    identity = SimpleNamespace(pid=319, pidfd=719, close=mock.Mock())
    invocation = SimpleNamespace(pid=319, unit="geniusnew-worker@7.service",
                                 invocation_id="b" * 32,
                                 cgroup=Path("/example/cgroup/worker7"))
    lifecycle = SimpleNamespace(
        preflight=mock.Mock(), acquire=mock.Mock(return_value=419),
        assert_idle=mock.Mock(), bind=mock.Mock(return_value=invocation),
        await_end=mock.Mock(), _check_core_identity=mock.Mock())
    connection = Connection(response)
    service = SimpleNamespace(
        ContractError=ContractError, WorkerBoundaryError=WorkerBoundaryError,
        FrameIdentity=mock.Mock(return_value=identity),
        _write_frame=mock.Mock(), _read_frame=mock.Mock(return_value={"kind": "READY"}),
        _validate_ready=mock.Mock(), _request=request, canonical=canonical)
    runner = SimpleNamespace(
        lifecycle=lifecycle, _connect=mock.Mock(return_value=connection),
        _run_worker=mock.Mock(return_value=dict(EXPECTED_RESULT)), preflight=mock.Mock())
    return service, runner, identity, invocation, connection


class CoreTest(unittest.TestCase):
    def setUp(self):
        core.CONNECT_CALLS = 0

    def assert_refused(self, code, fn, *args, **kwargs):
        with self.assertRaises(core.Refused) as caught:
            fn(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)


class OutputAndControlTests(CoreTest):
    def test_emit_is_one_bounded_json_line(self):
        output = io.StringIO()
        with redirect_stdout(output):
            core.emit({"z": 2, "a": "alpha"})
        self.assertEqual(output.getvalue(), '{"a":"alpha","z":2}\n')

    def test_emit_refuses_over_bound_before_printing(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assert_refused("OUTPUT_BOUND", core.emit, {"data": "a" * 2048})
        self.assertEqual(output.getvalue(), "")

    def test_control_requires_ready_stdin(self):
        with mock.patch.object(core.select, "select", return_value=([], [], [])), \
                mock.patch.object(core.os, "read") as read:
            self.assert_refused("CONTROL_TIMEOUT", core.control)
        read.assert_not_called()

    def test_control_accepts_only_exact_continue_line(self):
        for value in (b"", b"CONTINUE", b"continue\n", b"CONTINUE\nextra"):
            with self.subTest(value=value), \
                    mock.patch.object(core.select, "select", return_value=([0], [], [])), \
                    mock.patch.object(core.os, "read", return_value=value):
                self.assert_refused("CONTROL_INVALID", core.control)
        with mock.patch.object(core.select, "select", return_value=([0], [], [])) as select_, \
                mock.patch.object(core.os, "read", return_value=b"CONTINUE\n") as read:
            core.control()
        select_.assert_called_once_with((0,), (), (), 8)
        read.assert_called_once_with(0, 32)

    def test_finish_reports_actual_connection_count(self):
        core.CONNECT_CALLS = 3
        with mock.patch.object(core, "emit") as emit:
            core.finish("REQUEST_REFUSED")
        emit.assert_called_once_with({"event": "DONE", "code": "REQUEST_REFUSED",
                                      "connect_calls": 3})


class BaselineAndOccupiedTests(CoreTest):
    def test_baseline_requires_known_deterministic_result_and_preflight(self):
        service, runner, *_ = runtime()
        with mock.patch.object(core, "finish") as finish:
            core.baseline(service, runner)
        runner._run_worker.assert_called_once_with({"text": "alpha beta"})
        runner.preflight.assert_called_once_with()
        finish.assert_called_once_with("BASELINE_OK")

    def test_wrong_or_extra_baseline_output_is_not_pass(self):
        for output in ({"text": "wrong"}, {}, None,
                       {**EXPECTED_RESULT, "unexpected": True}):
            service, runner, *_ = runtime()
            runner._run_worker.return_value = output
            with self.subTest(output=output), mock.patch.object(core, "finish") as finish:
                self.assert_refused("BASELINE_OUTPUT", core.baseline, service, runner)
            runner.preflight.assert_not_called()
            finish.assert_not_called()

    def test_baseline_preflight_failure_cannot_finish(self):
        service, runner, *_ = runtime()
        runner.preflight.side_effect = ContractError("still occupied")
        with mock.patch.object(core, "finish") as finish:
            with self.assertRaises(ContractError):
                core.baseline(service, runner)
        finish.assert_not_called()

    def test_occupied_lease_requires_exact_contract_cause_and_no_connection(self):
        service, runner, *_ = runtime()
        error = WorkerBoundaryError()
        error.__cause__ = ContractError("worker service slot is occupied")
        runner._run_worker.side_effect = error
        with mock.patch.object(core, "finish") as finish:
            core.occupied(service, runner, lease=True)
        runner.lifecycle.preflight.assert_called_once_with()
        runner.lifecycle.acquire.assert_not_called()
        runner._connect.assert_not_called()
        finish.assert_called_once_with("LEASE_OCCUPIED")

    def test_lease_wrong_cause_type_or_text_is_refused(self):
        class DerivedContractError(ContractError):
            pass
        for cause in (None, ValueError("worker service slot is occupied"),
                      ContractError("another refusal"),
                      DerivedContractError("worker service slot is occupied")):
            service, runner, *_ = runtime()
            error = WorkerBoundaryError()
            error.__cause__ = cause
            runner._run_worker.side_effect = error
            with self.subTest(cause=type(cause).__name__), \
                    mock.patch.object(core, "finish") as finish:
                self.assert_refused("WRONG_REFUSAL", core.occupied, service, runner, lease=True)
            finish.assert_not_called()

    def test_any_observed_connection_invalidates_occupied_refusal(self):
        service, runner, *_ = runtime()
        runner._run_worker.side_effect = WorkerBoundaryError()
        core.CONNECT_CALLS = 1
        with mock.patch.object(core, "finish") as finish:
            self.assert_refused("UNEXPECTED_CONNECT", core.occupied, service, runner, lease=True)
        finish.assert_not_called()

    def test_missing_run_refusal_never_passes(self):
        service, runner, *_ = runtime()
        with mock.patch.object(core, "finish") as finish:
            self.assert_refused("REFUSAL_MISSING", core.occupied, service, runner, lease=True)
        finish.assert_not_called()

    def test_live_worker_accepts_only_observed_live_worker_refusals(self):
        for reason in ("previous worker invocation is still active",
                       "worker UID still has a live process"):
            service, runner, *_ = runtime()
            runner.lifecycle.assert_idle.side_effect = ContractError(reason)
            runner._run_worker.side_effect = WorkerBoundaryError()
            with self.subTest(reason=reason), mock.patch.object(core.os, "close") as close, \
                    mock.patch.object(core, "finish") as finish:
                core.occupied(service, runner, lease=False)
            close.assert_called_once_with(419)
            runner._connect.assert_not_called()
            finish.assert_called_once_with("LIVE_WORKER_REFUSED")

    def test_live_worker_idle_or_wrong_refusal_closes_lease_without_pass(self):
        for error, code in ((None, "IDLE_NOT_REFUSED"),
                            (ContractError("permission denied"), "WRONG_REFUSAL")):
            service, runner, *_ = runtime()
            runner.lifecycle.assert_idle.side_effect = error
            with self.subTest(code=code), mock.patch.object(core.os, "close") as close, \
                    mock.patch.object(core, "finish") as finish:
                self.assert_refused(code, core.occupied, service, runner, lease=False)
            close.assert_called_once_with(419)
            runner._run_worker.assert_not_called()
            finish.assert_not_called()


class RawWireTests(CoreTest):
    def run_case(self, mode, *, response=b"", edit=None):
        service, runner, identity, invocation, connection = runtime(response)
        if edit is not None:
            edit(service, runner, identity)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(core.secrets, "token_hex", return_value=NONCE))
            stack.enter_context(mock.patch.object(core.time, "monotonic", return_value=100.0))
            close = stack.enter_context(mock.patch.object(core.os, "close"))
            finish = stack.enter_context(mock.patch.object(core, "finish"))
            core.raw_case(service, runner, mode)
        service.FrameIdentity.assert_called_once_with(20002, 20002)
        service._write_frame.assert_called_once_with(
            connection, {"kind": "HELLO", "version": 1, "nonce": NONCE},
            limit=256, deadline=105.0)
        service._read_frame.assert_called_once_with(connection, limit=256,
                                                    deadline=105.0, identity=identity)
        service._validate_ready.assert_called_once_with({"kind": "READY"}, NONCE)
        runner.lifecycle.bind.assert_called_once_with(319, 719)
        runner.lifecycle.await_end.assert_called_once_with(invocation, 719, 125.0)
        self.assertEqual(runner.lifecycle.assert_idle.call_count, 2)
        self.assertEqual(connection.timeouts, [5])
        self.assertEqual(connection.shutdowns, [socket.SHUT_WR])
        self.assertTrue(connection.exited)
        close.assert_called_once_with(419)
        identity.close.assert_called_once_with()
        finish.assert_called_once_with("REQUEST_REFUSED")
        return connection.frames[0]

    def decode(self, frame):
        self.assertGreaterEqual(len(frame), 4)
        declared, = struct.unpack("!I", frame[:4])
        self.assertEqual(declared, len(frame) - 4)
        return json.loads(frame[4:])

    def test_nonce_is_changed_to_a_different_fixed_nonce(self):
        value = self.decode(self.run_case("raw_nonce"))
        expected = request({"text": "alpha beta"}, "0" * 64)
        self.assertEqual(value, expected)

    def test_zero_nonce_also_gets_a_different_nonce(self):
        service, runner, *_ = runtime()
        connection = runner._connect.return_value
        with mock.patch.object(core.secrets, "token_hex", return_value="0" * 64), \
                mock.patch.object(core.os, "close"), mock.patch.object(core, "finish"):
            core.raw_case(service, runner, "raw_nonce")
        self.assertEqual(self.decode(connection.frames[0])["nonce"], "1" * 64)

    def test_extra_field_is_the_only_extra_field(self):
        value = self.decode(self.run_case("raw_extra"))
        self.assertEqual(value, {**request({"text": "alpha beta"}, NONCE), "unexpected": True})

    def test_wall_limit_is_exactly_three_seconds(self):
        value = self.decode(self.run_case("raw_limits"))
        expected = request({"text": "alpha beta"}, NONCE)
        expected["limits"]["wall_seconds"] = 3.0
        self.assertEqual(value, expected)

    def test_noncanonical_json_retains_semantics_but_changes_wire(self):
        frame = self.run_case("raw_noncanonical")
        value = self.decode(frame)
        self.assertEqual(value, request({"text": "alpha beta"}, NONCE))
        self.assertNotEqual(frame[4:], canonical(value))
        self.assertIn(b'": ', frame[4:])

    def test_oversized_request_is_complete_canonical_json_beyond_frame_limit(self):
        frame = self.run_case("raw_oversized")
        expected = request({"text": "x" * 32768}, NONCE)
        self.assertEqual(self.decode(frame), expected)
        self.assertGreater(len(frame) - 4, 32768)
        self.assertEqual(frame[4:], canonical(expected))

    def test_truncated_frame_preserves_header_with_seven_bytes_missing(self):
        frame = self.run_case("raw_truncated")
        wire = canonical(request({"text": "alpha beta"}, NONCE))
        self.assertEqual(frame, struct.pack("!I", len(wire)) + wire[:-7])

    def test_trailing_frame_has_one_byte_beyond_declared_body(self):
        frame = self.run_case("raw_trailing")
        wire = canonical(request({"text": "alpha beta"}, NONCE))
        self.assertEqual(frame, struct.pack("!I", len(wire)) + wire + b"!")

    def test_connection_reset_is_accepted_only_with_successful_cleanup(self):
        self.run_case("raw_extra", response=ConnectionResetError(errno.ECONNRESET, "reset"))

    def test_peer_bytes_are_a_failure_even_if_they_are_not_a_valid_reply(self):
        service, runner, identity, *_ = runtime(b"!")
        with mock.patch.object(core.os, "close") as close, \
                mock.patch.object(core, "finish") as finish:
            self.assert_refused("NEGATIVE_RECEIVED_RESPONSE", core.raw_case,
                                service, runner, "raw_extra")
        runner.lifecycle.await_end.assert_not_called()
        close.assert_not_called()
        identity.close.assert_not_called()
        finish.assert_not_called()

    def test_timeout_or_wrong_reset_errno_cannot_count_as_refusal(self):
        for error in (socket.timeout("not EOF"),
                      ConnectionResetError(errno.EIO, "not ECONNRESET")):
            service, runner, identity, *_ = runtime(error)
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(core.os, "close") as close, \
                    mock.patch.object(core, "finish") as finish:
                with self.assertRaises(type(error)):
                    core.raw_case(service, runner, "raw_extra")
            close.assert_not_called()
            identity.close.assert_not_called()
            finish.assert_not_called()

    def test_known_send_errors_still_require_eof_and_cleanup_before_pass(self):
        for method in ("sendall", "shutdown"):
            for number in (errno.EPIPE, errno.ECONNRESET, errno.ENOTCONN):
                service, runner, identity, invocation, connection = runtime()
                with self.subTest(method=method, errno=number), \
                        mock.patch.object(connection, method, side_effect=OSError(number, "peer gone")), \
                        mock.patch.object(core.os, "close") as close, \
                        mock.patch.object(core, "finish") as finish:
                    core.raw_case(service, runner, "raw_extra")
                runner.lifecycle.await_end.assert_called_once()
                self.assertEqual(runner.lifecycle.assert_idle.call_count, 2)
                close.assert_called_once_with(419)
                identity.close.assert_called_once_with()
                finish.assert_called_once_with("REQUEST_REFUSED")

    def test_unknown_send_error_or_timeout_after_known_send_error_does_not_pass(self):
        for send_error, recv_error in ((OSError(errno.EACCES, "permission denied"), b""),
                                       (OSError(errno.EPIPE, "peer gone"), socket.timeout("not EOF"))):
            service, runner, identity, invocation, connection = runtime(recv_error)
            with self.subTest(errno=send_error.errno), \
                    mock.patch.object(connection, "sendall", side_effect=send_error), \
                    mock.patch.object(core.os, "close") as close, \
                    mock.patch.object(core, "finish") as finish:
                with self.assertRaises(OSError):
                    core.raw_case(service, runner, "raw_extra")
            runner.lifecycle.await_end.assert_not_called()
            close.assert_not_called()
            identity.close.assert_not_called()
            finish.assert_not_called()

    def test_ready_requires_both_pid_and_pidfd_before_bind(self):
        for field in ("pid", "pidfd"):
            service, runner, identity, *_ = runtime()
            setattr(identity, field, None)
            with self.subTest(field=field), mock.patch.object(core.os, "close") as close, \
                    mock.patch.object(core, "finish") as finish:
                self.assert_refused("READY_UNBOUND", core.raw_case,
                                    service, runner, "raw_extra")
            runner.lifecycle.bind.assert_not_called()
            close.assert_not_called()
            identity.close.assert_not_called()
            finish.assert_not_called()

    def test_eof_without_await_end_or_idle_proof_is_inconclusive(self):
        for stage in ("await_end", "assert_idle"):
            service, runner, identity, *_ = runtime()
            if stage == "await_end":
                runner.lifecycle.await_end.side_effect = ContractError("still running")
            else:
                runner.lifecycle.assert_idle.side_effect = [None, ContractError("still active")]
            with self.subTest(stage=stage), mock.patch.object(core.os, "close") as close, \
                    mock.patch.object(core, "finish") as finish:
                with self.assertRaises(ContractError):
                    core.raw_case(service, runner, "raw_extra")
            close.assert_not_called()
            identity.close.assert_not_called()
            finish.assert_not_called()


class ObserverTests(CoreTest):
    def traced_runtime(self, *, invocation=True, pidfd=True, wrong_exchange=False,
                       write_result=...):
        events = []

        def observed_request(*args):
            events.append("request-body")

        def observed_write_frame(connection, value, *, fail=False):
            events.append({"event": "write-body", "kind": value["kind"]})
            if fail:
                raise OSError(errno.EPIPE, "peer gone")
            return canonical(value) if write_result is ... else write_result

        class Runner:
            def _connect(self):
                events.append("connect-body")

        class Exchange:
            def __init__(self):
                self.identity = SimpleNamespace(pidfd=21 if pidfd else None)
                self.invocation = SimpleNamespace(
                    pid=17, unit="worker@17.service", invocation_id="c" * 32,
                    cgroup=Path("/example/job17")) if invocation else None

            def dispatch(self):
                observed_request()

            def run(self, *, kind="REQUEST", fail=False):
                observed_request()
                observed_write_frame(None, {"kind": kind}, fail=fail)
                events.append("before-eof")

        class OtherExchange(Exchange):
            pass

        service = SimpleNamespace(ServiceWorkerRunner=Runner, _request=observed_request,
                                  _write_frame=observed_write_frame, canonical=canonical,
                                  _CoreExchange=Exchange)
        return service, Runner(), OtherExchange() if wrong_exchange else Exchange(), events

    def test_trace_counts_connections_and_pauses_at_bound_request_before_body(self):
        service, runner, exchange, events = self.traced_runtime()
        prior = sys.gettrace()
        with mock.patch.object(core, "emit", side_effect=lambda value: events.append(value)), \
                mock.patch.object(core, "control", side_effect=lambda: events.append("continued")):
            try:
                core.install_observer(service, runner, pause=True)
                runner._connect()
                runner._connect()
                exchange.dispatch()
            finally:
                sys.settrace(prior)
        self.assertEqual(core.CONNECT_CALLS, 2)
        self.assertEqual(events, ["connect-body", "connect-body", {
            "event": "BOUND", "pid": 17, "unit": "worker@17.service",
            "invocation": "c" * 32, "cgroup": "/example/job17", "connect_calls": 2,
            "checkpoint": "before_request"},
            "continued", "request-body"])

    def test_after_write_checkpoint_occurs_after_request_write_before_eof(self):
        service, runner, exchange, events = self.traced_runtime()
        prior = sys.gettrace()
        with mock.patch.object(core, "emit", side_effect=lambda value: events.append(value)), \
                mock.patch.object(core, "control", side_effect=lambda: events.append("continued")):
            try:
                core.install_observer(service, runner, pause=True, checkpoint="after_request_write")
                runner._connect()
                exchange.run()
            finally:
                sys.settrace(prior)
        self.assertEqual(events, ["connect-body", "request-body",
            {"event": "write-body", "kind": "REQUEST"}, {
                "event": "BOUND", "pid": 17, "unit": "worker@17.service",
                "invocation": "c" * 32, "cgroup": "/example/job17", "connect_calls": 1,
                "checkpoint": "after_request_write"}, "continued", "before-eof"])

    def test_hello_write_does_not_hold_after_request_checkpoint(self):
        service, runner, exchange, events = self.traced_runtime(invocation=False)
        prior = sys.gettrace()
        with mock.patch.object(core, "emit") as emit, mock.patch.object(core, "control") as control:
            try:
                core.install_observer(service, runner, pause=True, checkpoint="after_request_write")
                exchange.run(kind="HELLO")
            finally:
                sys.settrace(prior)
        self.assertEqual(events, ["request-body", {"event": "write-body", "kind": "HELLO"},
                                  "before-eof"])
        emit.assert_not_called()
        control.assert_not_called()

    def test_failed_request_write_cannot_claim_after_write_checkpoint(self):
        service, runner, exchange, events = self.traced_runtime()
        prior = sys.gettrace()
        with mock.patch.object(core, "emit") as emit, mock.patch.object(core, "control") as control:
            try:
                core.install_observer(service, runner, pause=True, checkpoint="after_request_write")
                self.assert_refused("CHECKPOINT_UNWRITTEN", exchange.run, fail=True)
            finally:
                sys.settrace(prior)
        self.assertEqual(events, ["request-body", {"event": "write-body", "kind": "REQUEST"}])
        emit.assert_not_called()
        control.assert_not_called()

    def test_wrong_write_return_cannot_claim_a_written_request(self):
        for value in (None, b"wrong", bytearray(canonical({"kind": "REQUEST"}))):
            service, runner, exchange, events = self.traced_runtime(write_result=value)
            prior = sys.gettrace()
            with self.subTest(value=type(value).__name__), \
                    mock.patch.object(core, "emit") as emit, mock.patch.object(core, "control") as control:
                try:
                    core.install_observer(service, runner, pause=True, checkpoint="after_request_write")
                    self.assert_refused("CHECKPOINT_UNWRITTEN", exchange.run)
                finally:
                    sys.settrace(prior)
            self.assertEqual(events, ["request-body", {"event": "write-body", "kind": "REQUEST"}])
            emit.assert_not_called()
            control.assert_not_called()

    def test_after_write_also_requires_exact_bound_exchange(self):
        for options in ({"invocation": False}, {"pidfd": False}, {"wrong_exchange": True}):
            service, runner, exchange, events = self.traced_runtime(**options)
            prior = sys.gettrace()
            with self.subTest(options=options), mock.patch.object(core, "emit") as emit, \
                    mock.patch.object(core, "control") as control:
                try:
                    core.install_observer(service, runner, pause=True, checkpoint="after_request_write")
                    self.assert_refused("CHECKPOINT_UNBOUND", exchange.run)
                finally:
                    sys.settrace(prior)
            self.assertEqual(events, ["request-body", {"event": "write-body", "kind": "REQUEST"}])
            emit.assert_not_called()
            control.assert_not_called()

    def test_trace_rejects_request_without_exact_bound_exchange(self):
        for options in ({"invocation": False}, {"pidfd": False}, {"wrong_exchange": True}):
            service, runner, exchange, events = self.traced_runtime(**options)
            prior = sys.gettrace()
            with self.subTest(options=options), mock.patch.object(core, "emit") as emit, \
                    mock.patch.object(core, "control") as control:
                try:
                    core.install_observer(service, runner, pause=True)
                    self.assert_refused("CHECKPOINT_UNBOUND", exchange.dispatch)
                finally:
                    sys.settrace(prior)
            self.assertEqual(events, [])
            emit.assert_not_called()
            control.assert_not_called()

    def test_nonpaused_trace_observes_connect_without_controlling_request(self):
        service, runner, exchange, events = self.traced_runtime(invocation=False)
        prior = sys.gettrace()
        with mock.patch.object(core, "emit") as emit, mock.patch.object(core, "control") as control:
            try:
                core.install_observer(service, runner, pause=False)
                runner._connect()
                exchange.dispatch()
            finally:
                sys.settrace(prior)
        self.assertEqual(core.CONNECT_CALLS, 1)
        self.assertEqual(events, ["connect-body", "request-body"])
        emit.assert_not_called()
        control.assert_not_called()


class RuntimeLoadingTests(CoreTest):
    def identity(self, **override):
        values = dict(isolated=1, bytecode=True, uid=20001, euid=20001,
                      gid=20001, groups=[20003])
        values.update(override)
        stack = ExitStack()
        stack.enter_context(mock.patch.object(core.sys, "flags", SimpleNamespace(isolated=values["isolated"])))
        stack.enter_context(mock.patch.object(core.sys, "dont_write_bytecode", values["bytecode"]))
        for method, value in (("getuid", values["uid"]), ("geteuid", values["euid"]),
                              ("getgid", values["gid"]), ("getgroups", values["groups"])):
            stack.enter_context(mock.patch.object(core.os, method, return_value=value))
        return stack

    def fake_modules(self, service_path=None):
        service = ModuleType("geniusnew.worker_service")
        service.__file__ = str(service_path or core.PACKAGE / "worker_service.py")
        service.WorkerServiceProfile = mock.Mock(return_value=object())
        service.DeterministicSummarizer = mock.Mock(return_value=object())
        service.WorkerAuthority = mock.Mock(return_value=object())
        service.ServiceWorkerRunner = mock.Mock(return_value=runtime()[1])
        package = ModuleType("geniusnew")
        package.__path__ = []
        package.worker_service = service
        return service, {"geniusnew": package, "geniusnew.worker_service": service}

    def test_every_identity_requirement_is_checked_before_source_or_import(self):
        variants = ({"isolated": 0}, {"bytecode": False}, {"uid": 0}, {"euid": 0},
                    {"gid": 0}, {"groups": []}, {"groups": [20003, 0]},
                    {"groups": [20001]})
        for invalid in variants:
            service, modules = self.fake_modules()
            with self.subTest(invalid=invalid), self.identity(**invalid), \
                    mock.patch.dict(sys.modules, modules), \
                    mock.patch.object(core, "verify_installed") as verify:
                self.assert_refused("CORE_IDENTITY", core.load_runtime, {})
            verify.assert_not_called()
            service.ServiceWorkerRunner.assert_not_called()

    def test_source_verification_failure_prevents_runtime_construction(self):
        service, modules = self.fake_modules()
        with self.identity(), mock.patch.dict(sys.modules, modules), \
                mock.patch.object(core, "verify_installed", side_effect=core.Refused("SOURCE_MISMATCH")):
            self.assert_refused("SOURCE_MISMATCH", core.load_runtime, {})
        service.WorkerServiceProfile.assert_not_called()
        service.ServiceWorkerRunner.assert_not_called()

    def test_imported_source_path_must_be_exact_installed_path(self):
        service, modules = self.fake_modules(Path("/example/unverified/worker_service.py"))
        with self.identity(), mock.patch.dict(sys.modules, modules), \
                mock.patch.object(core, "verify_installed") as verify:
            self.assert_refused("SOURCE_MISMATCH", core.load_runtime, {"pinned": True})
        verify.assert_called_once_with({"pinned": True})
        service.WorkerServiceProfile.assert_not_called()

    def test_valid_runtime_uses_fixed_identities_and_fresh_private_authority(self):
        service, modules = self.fake_modules()
        with self.identity(), mock.patch.dict(sys.modules, modules), \
                mock.patch.object(core, "verify_installed") as verify, \
                mock.patch.object(core.secrets, "token_bytes", return_value=b"k" * 32) as token:
            loaded, runner = core.load_runtime({"pinned": True})
        self.assertIs(loaded, service)
        verify.assert_called_once_with({"pinned": True})
        token.assert_called_once_with(32)
        service.WorkerServiceProfile.assert_called_once_with(
            core_uid=20001, worker_uid=20002, worker_gid=20002, socket_gid=20003,
            socket_path="/run/geniusnew-worker/ipc/worker.sock",
            lease_path="/run/geniusnew-worker/ipc/lease")
        service.WorkerAuthority.assert_called_once_with(result_key=b"k" * 32)
        runner.lifecycle._check_core_identity.assert_called_once_with()


class MainDispatchTests(CoreTest):
    def main_context(self, mode, service, runner):
        stack = ExitStack()
        patches = {}
        for name, replacement in (("load_manifest", mock.Mock(return_value={"manifest": True})),
                                  ("load_runtime", mock.Mock(return_value=(service, runner))),
                                  ("install_observer", mock.Mock()), ("emit", mock.Mock()),
                                  ("finish", mock.Mock()), ("control", mock.Mock())):
            patches[name] = stack.enter_context(mock.patch.object(core, name, replacement))
        patches["alarm"] = stack.enter_context(mock.patch.object(core.signal, "alarm"))
        patches["signal"] = stack.enter_context(mock.patch.object(core.signal, "signal"))
        patches["sigmask"] = stack.enter_context(mock.patch.object(core.signal, "pthread_sigmask"))
        patches["trace"] = stack.enter_context(mock.patch.object(core.sys, "settrace"))
        patches["close"] = stack.enter_context(mock.patch.object(core.os, "close"))
        return stack, patches, ["--manifest", "/example/manifest.json", "--mode", mode]

    def test_invalid_or_check_arguments_cannot_start_runtime_or_signals(self):
        for args in (["--check"], ["--manifest", "/example/m.json", "--mode", "check"], []):
            with self.subTest(args=args), mock.patch.object(core, "load_runtime") as load, \
                    mock.patch.object(core.signal, "signal") as signal_, \
                    mock.patch.object(core.signal, "alarm") as alarm, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    core.main(args)
            self.assertEqual(caught.exception.code, 2)
            load.assert_not_called()
            signal_.assert_not_called()
            alarm.assert_not_called()

    def test_baseline_mode_dispatches_once_and_disables_alarm_and_trace(self):
        service, runner, *_ = runtime()
        context, calls, args = self.main_context("baseline", service, runner)
        with context:
            self.assertEqual(core.main(args), 0)
        calls["load_manifest"].assert_called_once_with(Path("/example/manifest.json"))
        calls["load_runtime"].assert_called_once_with({"manifest": True})
        calls["install_observer"].assert_called_once_with(
            service, runner, pause=False, checkpoint="before_request")
        calls["finish"].assert_called_once_with("BASELINE_OK")
        self.assertEqual(calls["alarm"].call_args_list, [mock.call(45), mock.call(0)])
        calls["trace"].assert_called_once_with(None)
        calls["sigmask"].assert_called_once_with(core.signal.SIG_UNBLOCK, {
            core.signal.SIGALRM, core.signal.SIGINT, core.signal.SIGTERM, core.signal.SIGHUP})
        self.assertEqual([call.args[0] for call in calls["signal"].call_args_list],
                         [core.signal.SIGALRM, core.signal.SIGTERM, core.signal.SIGHUP])

    def test_alarm_callback_has_fixed_child_timeout_refusal(self):
        service, runner, *_ = runtime()
        context, calls, args = self.main_context("baseline", service, runner)
        with context:
            self.assertEqual(core.main(args), 0)
        for call in calls["signal"].call_args_list:
            self.assert_refused("CHILD_TIMEOUT", call.args[1], None, None)

    def test_controlled_crash_requires_boundary_error_then_preflight(self):
        for mode, checkpoint in (("controlled", "before_request"),
                                 ("controlled_after", "after_request_write")):
            service, runner, *_ = runtime()
            runner._run_worker.side_effect = WorkerBoundaryError("private detail")
            context, calls, args = self.main_context(mode, service, runner)
            with self.subTest(mode=mode), context:
                self.assertEqual(core.main(args), 0)
            runner.preflight.assert_called_once_with()
            calls["install_observer"].assert_called_once_with(
                service, runner, pause=True, checkpoint=checkpoint)
            calls["finish"].assert_called_once_with("WORKER_CRASH_REFUSED")

    def test_controlled_success_is_inconclusive_and_never_crash_pass(self):
        for mode in ("controlled", "controlled_after"):
            service, runner, *_ = runtime()
            context, calls, args = self.main_context(mode, service, runner)
            failures = []
            real_fail = core._fail
            def observe_fail(code):
                failures.append(code)
                real_fail(code)
            with self.subTest(mode=mode), context, \
                    mock.patch.object(core, "_fail", side_effect=observe_fail):
                self.assertEqual(core.main(args), 2)
            self.assertEqual(failures, ["CRASH_NOT_REFUSED"])
            calls["finish"].assert_not_called()
            calls["emit"].assert_called_once_with({"event": "ERROR", "code": "CHILD_INCONCLUSIVE"})

    def test_raw_modes_dispatch_only_to_the_selected_case(self):
        for mode in ("raw_nonce", "raw_extra", "raw_limits", "raw_noncanonical",
                     "raw_oversized", "raw_truncated", "raw_trailing"):
            service, runner, *_ = runtime()
            context, calls, args = self.main_context(mode, service, runner)
            with self.subTest(mode=mode), context, mock.patch.object(core, "raw_case") as raw:
                self.assertEqual(core.main(args), 0)
            raw.assert_called_once_with(service, runner, mode)
            runner._run_worker.assert_not_called()

    def test_occupied_modes_preserve_lease_choice(self):
        for mode, lease in (("lease_busy", True), ("idle_busy", False)):
            service, runner, *_ = runtime()
            context, calls, args = self.main_context(mode, service, runner)
            with self.subTest(mode=mode), context, mock.patch.object(core, "occupied") as occupied:
                self.assertEqual(core.main(args), 0)
            occupied.assert_called_once_with(service, runner, lease=lease)

    def test_lease_holder_releases_after_control_and_even_after_control_failure(self):
        for error, result in ((None, 0), (core.Refused("CONTROL_TIMEOUT"), 2)):
            service, runner, *_ = runtime()
            context, calls, args = self.main_context("lease_holder", service, runner)
            calls["control"].side_effect = error
            with self.subTest(result=result), context:
                self.assertEqual(core.main(args), result)
            calls["close"].assert_called_once_with(419)
            runner._run_worker.assert_not_called()
            if error is None:
                calls["finish"].assert_called_once_with("LEASE_RELEASED")
            else:
                calls["finish"].assert_not_called()

    def test_idle_retries_until_cleanup_and_releases_each_lease(self):
        service, runner, *_ = runtime()
        runner.lifecycle.assert_idle.side_effect = [ContractError("busy"), None]
        context, calls, args = self.main_context("idle", service, runner)
        with context, mock.patch.object(core.time, "monotonic", return_value=100), \
                mock.patch.object(core.time, "sleep") as sleep:
            self.assertEqual(core.main(args), 0)
        self.assertEqual(calls["close"].call_args_list, [mock.call(419), mock.call(419)])
        sleep.assert_called_once_with(0.05)
        calls["finish"].assert_called_once_with("IDLE_CONFIRMED")

    def test_idle_deadline_cannot_pass_cleanup(self):
        service, runner, *_ = runtime()
        runner.lifecycle.assert_idle.side_effect = ContractError("busy")
        context, calls, args = self.main_context("idle", service, runner)
        failures = []
        real_fail = core._fail
        def observe_fail(code):
            failures.append(code)
            real_fail(code)
        with context, mock.patch.object(core.time, "monotonic", side_effect=[100, 120]), \
                mock.patch.object(core.time, "sleep", side_effect=AssertionError("continued after deadline")), \
                mock.patch.object(core, "_fail", side_effect=observe_fail):
            self.assertEqual(core.main(args), 2)
        self.assertEqual(failures, ["CLEANUP_UNCONFIRMED"])
        calls["close"].assert_called_once_with(419)
        calls["finish"].assert_not_called()

    def test_unexpected_error_is_redacted_and_alarm_and_trace_are_cleared(self):
        service, runner, *_ = runtime()
        context, calls, args = self.main_context("baseline", service, runner)
        calls["load_runtime"].side_effect = RuntimeError("secret=/private/key")
        with context:
            self.assertEqual(core.main(args), 2)
        calls["emit"].assert_called_once_with({"event": "ERROR", "code": "CHILD_INCONCLUSIVE"})
        calls["finish"].assert_not_called()
        self.assertEqual(calls["alarm"].call_args_list, [mock.call(45), mock.call(0)])
        calls["trace"].assert_called_once_with(None)


if __name__ == "__main__":
    unittest.main()
