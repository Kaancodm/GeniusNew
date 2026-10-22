"""Real service sandbox and Core response tests, not systemd/UID acceptance.

Only the test job-directory seam and malicious payload factory are
substituted. No installed service or production security control is changed.
"""

import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from geniusnew.contracts import ContractError
from geniusnew.worker_service import (
    FrameIdentity, WorkerServiceProfile, _CoreExchange, _pidfd_pid,
    _read_frame, _request, _validate_ready, _validate_result, _write_frame,
)


CHILD = Path(__file__).with_name("worker_service_attack_child.py")


class PeerBinding:
    """Verify the real child pidfd; deliberately no synthetic systemd verdict."""

    def __init__(self, pid):
        self.pid = pid
        self.bound = False

    def bind(self, pid, descriptor):
        if pid != self.pid or _pidfd_pid(descriptor) != self.pid:
            raise ContractError("test peer mismatch")
        self.bound = True
        return object()


class AdversarialServiceTest(unittest.TestCase):
    def setUp(self):
        self.assertNotEqual(os.geteuid(), 0, "run these disposable children as an unprivileged user")
        self.root = tempfile.TemporaryDirectory(prefix="gn-attack-")
        self.addCleanup(self.root.cleanup)
        self.base = Path(self.root.name)
        self.job = self.base / "job"
        self.job.mkdir(mode=0o700)
        self.canary = self.base / "outside-canary"
        self.canary.write_bytes(b"SYNTHETIC-READABLE-CANARY")
        self.target = self.base / "outside-created"
        self.listener = socket.socket()
        self.addCleanup(self.listener.close)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]

    def command(self, kind, mode, target=None):
        return [sys.executable, "-I", "-B", str(CHILD), kind, mode, str(self.job),
                str(target or self.canary), str(self.port)]

    def control(self, mode, target=None):
        result = subprocess.run(self.command("control", mode, target), capture_output=True,
                                timeout=6, check=True)
        self.assertEqual(json.loads(result.stdout), {"text": "NATIVE_ALLOWED"})

    def peer(self, kind, mode, target=None):
        core, worker = socket.socketpair()
        self.addCleanup(core.close)
        core.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        core.setsockopt(socket.SOL_SOCKET, 76, 1)
        try:
            process = subprocess.Popen(self.command(kind, mode, target), stdin=worker,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       close_fds=True, cwd="/")
        finally:
            worker.close()
        def reap():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        self.addCleanup(reap)
        identity = FrameIdentity(os.geteuid(), os.getegid())
        self.addCleanup(identity.close)
        return core, process, identity

    def sandbox(self, mode, target=None, killed=False):
        core, process, identity = self.peer("sandbox", mode, target)
        deadline = time.monotonic() + 6
        nonce = "a" * 64
        _write_frame(core, {"kind": "HELLO", "version": 1, "nonce": nonce},
                     limit=256, deadline=deadline)
        _validate_ready(_read_frame(core, limit=256, deadline=deadline, identity=identity), nonce)
        self.assertEqual(identity.pid, process.pid)
        request = _request({"text": "attack-fixture"}, nonce)
        wire = _write_frame(core, request, limit=32768, deadline=deadline)
        core.shutdown(socket.SHUT_WR)
        if killed:
            with self.assertRaisesRegex(ContractError, "frame was truncated"):
                _read_frame(core, limit=32768, deadline=deadline, identity=identity)
            self.assertEqual(process.wait(timeout=3), -signal.SIGSYS)
            return
        result = _read_frame(core, limit=32768, deadline=deadline, identity=identity)
        from geniusnew.worker_service import _expect_eof
        _expect_eof(core, deadline=deadline, identity=identity)
        self.assertEqual(process.wait(timeout=3), 0)
        import hashlib
        return _validate_result(result, nonce, hashlib.sha256(wire).hexdigest())

    def test_service_sandbox_positive_control(self):
        self.assertEqual(self.sandbox("baseline"), {"text": "FIXTURE_OK"})

    def test_readable_canary_is_blocked_by_python_audit_hook(self):
        self.assertEqual(self.canary.read_bytes(), b"SYNTHETIC-READABLE-CANARY")
        self.assertEqual(self.sandbox("python_read"), "ISOLATION_VIOLATED")

    def test_native_file_read_is_blocked_by_real_landlock(self):
        self.control("native_read")
        self.assertEqual(self.sandbox("native_read"), {"text": "NATIVE_DENIED:13"})

    def test_native_file_creation_is_blocked_outside_job(self):
        self.control("native_write", self.target)
        self.target.unlink()
        self.assertEqual(self.sandbox("native_write", self.target), {"text": "NATIVE_DENIED:13"})
        self.assertFalse(self.target.exists())

    def test_python_network_is_blocked_by_real_audit_hook(self):
        self.assertEqual(self.sandbox("python_network"), "ISOLATION_VIOLATED")

    def test_native_tcp_connect_to_live_listener_is_blocked_by_landlock(self):
        self.control("native_tcp")
        self.assertEqual(self.sandbox("native_tcp"), {"text": "NATIVE_DENIED:13"})

    def test_native_fork_is_killed_by_real_seccomp(self):
        self.control("native_fork")
        self.sandbox("native_fork", killed=True)

    def exchange(self, mode, wrong_uid=False):
        core, process, identity = self.peer("response", mode)
        # _CoreExchange owns its own identity; the unused fixture identity stays unbound.
        profile = WorkerServiceProfile(core_uid=os.geteuid() + 2,
                                       worker_uid=os.geteuid() + int(wrong_uid),
                                       worker_gid=os.getegid(), socket_gid=os.getegid() + 2,
                                       socket_path="/run/test-not-connected.sock",
                                       lease_path="/run/test-not-opened.lease")
        lifecycle = PeerBinding(process.pid)
        exchange = _CoreExchange(core, profile, lifecycle, {"text": "fixture"},
                                 time.monotonic() + 6)
        self.addCleanup(exchange.identity.close)
        return exchange, lifecycle, process

    def test_real_core_accepts_valid_kernel_authenticated_response(self):
        exchange, lifecycle, process = self.exchange("valid")
        self.assertEqual(exchange.run(), {"text": "FIXTURE_OK"})
        self.assertTrue(lifecycle.bound)
        self.assertEqual(process.wait(timeout=3), 0)

    def assert_response_refused(self, mode, message):
        exchange, lifecycle, process = self.exchange(mode)
        with self.assertRaisesRegex(ContractError, "^" + re.escape(message) + "$"):
            exchange.run()
        self.assertTrue(lifecycle.bound, "fixture must reach actual request/result validation")
        self.assertEqual(process.wait(timeout=3), 0, "fixture must send its intended response and exit cleanly")

    def test_real_core_refuses_ten_manipulated_response_variants(self):
        expected = {
            "nonce": "worker RESULT identity or digest is invalid",
            "digest": "worker RESULT identity or digest is invalid",
            "extra": "worker RESULT shape or error code is invalid",
            "output": "worker RESULT output is invalid",
            "error": "worker RESULT shape or error code is invalid",
            "noncanonical": "worker frame is not canonical JSON",
            "duplicate": "worker frame is not canonical JSON",
            "oversized": "worker frame length is invalid",
            "truncated": "worker frame was truncated",
            "trailing": "worker sent bytes after its result",
        }
        for mode, message in expected.items():
            with self.subTest(mode=mode):
                self.assert_response_refused(mode, message)

    def test_crashed_fixture_cannot_count_as_a_named_response_refusal(self):
        # The peer really consumes REQUEST/EOF and then exits 71 without RESULT.
        # Even matching truncation must fail the independent clean-exit check.
        for message in ("worker RESULT identity or digest is invalid",
                        "worker frame was truncated"):
            with self.subTest(message=message), self.assertRaises(AssertionError):
                self.assert_response_refused("fixture_crash", message)

    def test_wrong_kernel_uid_refuses_before_lifecycle_binding(self):
        exchange, lifecycle, _process = self.exchange("valid", wrong_uid=True)
        with self.assertRaisesRegex(ContractError, "credentials changed"):
            exchange.run()
        self.assertFalse(lifecycle.bound)


if __name__ == "__main__":
    unittest.main()
