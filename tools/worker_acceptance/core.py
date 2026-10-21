"""Unprivileged real-worker client. No implementation classes are replaced."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CORE_GID, CORE_UID, PACKAGE, SOCKET_GID, WORKER_GID,
                    WORKER_UID, _fail, load_manifest, verify_installed)
from common import Refused as Refused

TEXT = "alpha beta"
CONNECT_CALLS = 0


def emit(event: dict) -> None:
    value = json.dumps(event, sort_keys=True, separators=(",", ":"))
    if len(value) > 2048:
        _fail("OUTPUT_BOUND")
    print(value, flush=True)


def control() -> None:
    if not select.select((0,), (), (), 8)[0]:
        _fail("CONTROL_TIMEOUT")
    if os.read(0, 32) != b"CONTINUE\n":
        _fail("CONTROL_INVALID")


def finish(code: str) -> None:
    emit({"event": "DONE", "code": code, "connect_calls": CONNECT_CALLS})


def load_runtime(manifest: dict):
    if (sys.flags.isolated != 1 or not sys.dont_write_bytecode
            or os.getuid() != CORE_UID or os.geteuid() != CORE_UID
            or os.getgid() != CORE_GID or set(os.getgroups()) != {SOCKET_GID}):
        _fail("CORE_IDENTITY")
    verify_installed(manifest)
    import geniusnew.worker_service as service
    if Path(service.__file__) != PACKAGE / "worker_service.py":
        _fail("SOURCE_MISMATCH")
    profile = service.WorkerServiceProfile(
        core_uid=CORE_UID, worker_uid=WORKER_UID, worker_gid=WORKER_GID,
        socket_gid=SOCKET_GID, socket_path="/run/geniusnew-worker/ipc/worker.sock",
        lease_path="/run/geniusnew-worker/ipc/lease")
    runner = service.ServiceWorkerRunner(
        service.DeterministicSummarizer(),
        authority=service.WorkerAuthority(result_key=secrets.token_bytes(32)), profile=profile)
    runner.lifecycle._check_core_identity()
    return service, runner


def install_observer(service, runner, *, pause: bool, checkpoint: str = "before_request") -> None:
    """Observe the real call boundary after bind without patching its behavior."""
    def bound(exchange):
        if (type(exchange) is not service._CoreExchange or exchange.invocation is None
                or exchange.identity.pidfd is None):
            _fail("CHECKPOINT_UNBOUND")
        invocation = exchange.invocation
        emit({"event": "BOUND", "pid": invocation.pid, "unit": invocation.unit,
              "invocation": invocation.invocation_id, "cgroup": str(invocation.cgroup),
              "connect_calls": CONNECT_CALLS, "checkpoint": checkpoint})
        control()

    def written(frame, event, arg):
        if event == "return":
            if type(arg) is not bytes or arg != service.canonical(frame.f_locals["value"]):
                _fail("CHECKPOINT_UNWRITTEN")
            bound(frame.f_back.f_locals.get("self"))
        return written

    def observe(frame, event, arg):
        global CONNECT_CALLS
        if event == "call" and frame.f_code is service.ServiceWorkerRunner._connect.__code__:
            CONNECT_CALLS += 1
        if event == "call" and frame.f_code is service._request.__code__ and pause and checkpoint == "before_request":
            bound(frame.f_back.f_locals.get("self"))
        if (pause and checkpoint == "after_request_write" and event == "call"
                and frame.f_code is service._write_frame.__code__
                and frame.f_locals.get("value", {}).get("kind") == "REQUEST"):
            return written
        return None
    sys.settrace(observe)


def baseline(service, runner) -> None:
    result = runner._run_worker({"text": TEXT})
    expected = {"text": "2 words; sha256:" + hashlib.sha256(TEXT.encode()).hexdigest()[:16]}
    if result != expected:
        _fail("BASELINE_OUTPUT")
    runner.preflight()
    finish("BASELINE_OK")


def occupied(service, runner, *, lease: bool) -> None:
    runner.lifecycle.preflight()
    if not lease:
        descriptor = runner.lifecycle.acquire()
        try:
            try:
                runner.lifecycle.assert_idle()
            except service.ContractError as reason:
                if str(reason) not in ("previous worker invocation is still active",
                                        "worker UID still has a live process"):
                    _fail("WRONG_REFUSAL")
            else:
                _fail("IDLE_NOT_REFUSED")
        finally:
            os.close(descriptor)
    try:
        runner._run_worker({"text": TEXT})
    except service.WorkerBoundaryError as exc:
        if CONNECT_CALLS != 0:
            _fail("UNEXPECTED_CONNECT")
        if lease and (type(exc.__cause__) is not service.ContractError
                      or str(exc.__cause__) != "worker service slot is occupied"):
            _fail("WRONG_REFUSAL")
        finish("LEASE_OCCUPIED" if lease else "LIVE_WORKER_REFUSED")
        return
    _fail("REFUSAL_MISSING")


def raw_case(service, runner, mode: str) -> None:
    lifecycle = runner.lifecycle
    lifecycle.preflight()
    lease = lifecycle.acquire()
    identity = service.FrameIdentity(WORKER_UID, WORKER_GID)
    invocation = None
    confirmed = False
    deadline = time.monotonic() + 25
    try:
        lifecycle.assert_idle()
        with runner._connect() as connection:
            nonce = secrets.token_hex(32)
            service._write_frame(connection, {"kind": "HELLO", "version": 1, "nonce": nonce},
                                 limit=256, deadline=time.monotonic() + 5)
            ready = service._read_frame(connection, limit=256,
                                        deadline=time.monotonic() + 5, identity=identity)
            service._validate_ready(ready, nonce)
            if identity.pid is None or identity.pidfd is None:
                _fail("READY_UNBOUND")
            invocation = lifecycle.bind(identity.pid, identity.pidfd)
            request = service._request({"text": TEXT}, nonce)
            if mode == "raw_nonce":
                request["nonce"] = "0" * 64 if nonce != "0" * 64 else "1" * 64
            if mode == "raw_extra":
                request["unexpected"] = True
            if mode == "raw_limits":
                request["limits"]["wall_seconds"] = 3.0
            if mode == "raw_oversized":
                request["payload"]["text"] = "x" * 32768
            wire = service.canonical(request)
            if mode == "raw_noncanonical":
                wire = json.dumps(request).encode("ascii")
            frame = struct.pack("!I", len(wire)) + wire
            if mode == "raw_truncated":
                frame = frame[:-7]
            if mode == "raw_trailing":
                frame += b"!"
            connection.settimeout(5)
            try:
                connection.sendall(frame)
                connection.shutdown(socket.SHUT_WR)
            except OSError as exc:
                if exc.errno not in (errno.EPIPE, errno.ECONNRESET, errno.ENOTCONN):
                    raise
            try:
                response = connection.recv(1)
            except ConnectionResetError as exc:
                if exc.errno != errno.ECONNRESET:
                    raise
                response = b""
            if response:
                _fail("NEGATIVE_RECEIVED_RESPONSE")
        lifecycle.await_end(invocation, identity.pidfd, deadline)
        lifecycle.assert_idle()
        confirmed = True
    finally:
        # Closing a client lease is safe only after cleanup proof. A failed child
        # exits, and the next real Core must independently reconcile the old slot.
        if confirmed:
            os.close(lease)
            identity.close()
    finish("REQUEST_REFUSED")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--mode", required=True, choices=(
        "baseline", "lease_holder", "lease_busy", "idle_busy", "idle", "controlled", "controlled_after",
        "raw_nonce", "raw_extra", "raw_limits", "raw_noncanonical", "raw_oversized",
        "raw_truncated", "raw_trailing"))
    args = parser.parse_args(argv)
    def alarm(_signal, _frame):
        _fail("CHILD_TIMEOUT")
    signal.signal(signal.SIGALRM, alarm)
    signal.signal(signal.SIGTERM, alarm)
    signal.signal(signal.SIGHUP, alarm)
    signal.alarm(45)
    signal.pthread_sigmask(signal.SIG_UNBLOCK,
                           {signal.SIGALRM, signal.SIGINT, signal.SIGTERM, signal.SIGHUP})
    try:
        service, runner = load_runtime(load_manifest(Path(args.manifest)))
        install_observer(service, runner, pause=args.mode in ("controlled", "controlled_after"),
                         checkpoint="after_request_write" if args.mode == "controlled_after" else "before_request")
        if args.mode == "baseline":
            baseline(service, runner)
        elif args.mode in ("lease_busy", "idle_busy"):
            occupied(service, runner, lease=args.mode == "lease_busy")
        elif args.mode == "lease_holder":
            runner.lifecycle.preflight()
            descriptor = runner.lifecycle.acquire()
            try:
                runner.lifecycle.assert_idle()
                emit({"event": "HELD"})
                control()
            finally:
                os.close(descriptor)
            finish("LEASE_RELEASED")
        elif args.mode == "idle":
            runner.lifecycle.preflight()
            end = time.monotonic() + 20
            while True:
                descriptor = runner.lifecycle.acquire()
                try:
                    try:
                        runner.lifecycle.assert_idle()
                        break
                    except service.ContractError:
                        if time.monotonic() >= end:
                            _fail("CLEANUP_UNCONFIRMED")
                finally:
                    os.close(descriptor)
                time.sleep(0.05)
            finish("IDLE_CONFIRMED")
        elif args.mode in ("controlled", "controlled_after"):
            try:
                runner._run_worker({"text": TEXT})
            except service.WorkerBoundaryError:
                runner.preflight()
                finish("WORKER_CRASH_REFUSED")
            else:
                _fail("CRASH_NOT_REFUSED")
        else:
            raw_case(service, runner, args.mode)
        return 0
    except BaseException:
        # Raw exception strings can contain paths, payloads or environment data.
        emit({"event": "ERROR", "code": "CHILD_INCONCLUSIVE"})
        return 2
    finally:
        signal.alarm(0)
        sys.settrace(None)


if __name__ == "__main__":
    raise SystemExit(main())
