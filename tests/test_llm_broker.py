import multiprocessing
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from geniusnew.contracts import ContractError, canonical
from geniusnew.llm_broker import (
    ProviderResult,
    _peer_uid,
    _read_exact,
    _read_frame,
    _write_frame,
    invoke,
    serve_once,
)
from geniusnew.llm_contracts import BrokerRequest, BrokerResponse, Usage


def _crash_after_accept(listener_fd):
    listener = socket.socket(fileno=listener_fd)
    connection, _ = listener.accept()
    connection.close()
    os._exit(7)


class BrokerIpcTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="geniusnew-llm-ipc-")
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "broker.sock")
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        self.listener.listen(1)
        self.addCleanup(self.listener.close)

    def request(self):
        return BrokerRequest("job-123", "a" * 64, int(time.time()) + 5,
                             "Ein deutscher Beispielsatz.")

    def server(self, handler):
        def run():
            connection, _ = self.listener.accept()
            with connection:
                handler(connection)

        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join, 2)
        return thread

    def test_one_fake_provider_call_returns_bound_text_and_usage(self):
        calls = []

        def provider(request):
            calls.append(request.text)
            return ProviderResult("Kurze Zusammenfassung.", Usage(7, 4))

        self.server(lambda connection: serve_once(connection, provider,
                                                expected_uid=os.getuid(),
                                                timeout_seconds=1))
        result = invoke(self.path, self.request(), expected_uid=os.getuid(),
                        timeout_seconds=1)
        self.assertEqual(result.text, "Kurze Zusammenfassung.")
        self.assertEqual(result.usage, Usage(7, 4))
        self.assertEqual(calls, ["Ein deutscher Beispielsatz."])

    def test_invoke_rejects_invalid_timeout_and_peer_uid(self):
        with self.assertRaisesRegex(ContractError, "timeout is outside"):
            invoke(self.path, self.request(), expected_uid=os.getuid(),
                   timeout_seconds=True)
        with self.assertRaisesRegex(ContractError, "peer uid is invalid"):
            invoke(self.path, self.request(), expected_uid=-1,
                   timeout_seconds=1)

    def test_peer_uid_rejects_platforms_without_socket_credentials(self):
        with patch("geniusnew.llm_broker.socket", object()):
            with self.assertRaisesRegex(ContractError, "credentials are unavailable"):
                _peer_uid(object())

    def test_read_exact_rejects_expired_deadline_and_unexpected_eof(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        with self.assertRaisesRegex(ContractError, "IPC timed out"):
            _read_exact(reader, 1, time.monotonic() - 1)

        writer.close()
        with self.assertRaisesRegex(ContractError, "ended before a complete frame"):
            _read_exact(reader, 1, time.monotonic() + 0.02)

    def test_frame_helpers_reject_invalid_lengths_and_expired_write(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        writer.sendall(struct.pack("!I", 0))
        with self.assertRaisesRegex(ContractError, "frame length is invalid"):
            _read_frame(reader, time.monotonic() + 1)

        with self.assertRaisesRegex(ContractError, "frame length is invalid"):
            _write_frame(writer, b"", time.monotonic() + 1)
        with self.assertRaisesRegex(ContractError, "IPC timed out"):
            _write_frame(writer, b"x", time.monotonic() - 1)

    def test_invalid_request_is_refused_before_provider(self):
        called = []

        def handler(connection):
            serve_once(connection, lambda request: called.append(request),
                       expected_uid=os.getuid(), timeout_seconds=1)

        self.server(handler)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(self.path)
            bad = canonical({"version": 1, "text": "x", "url": "https://elsewhere.invalid"})
            client.sendall(struct.pack("!I", len(bad)) + bad)
            self.assertIn(b"failed", client.recv(100))
        self.assertEqual(called, [])

    def test_wrong_peer_uid_is_refused_before_provider(self):
        called = []

        def handler(connection):
            serve_once(connection, lambda request: called.append(request),
                       expected_uid=os.getuid() + 1, timeout_seconds=1)

        self.server(handler)
        with self.assertRaises(ContractError):
            invoke(self.path, self.request(), expected_uid=os.getuid(),
                   timeout_seconds=1)
        self.assertEqual(called, [])

    def test_invoke_checks_the_connected_broker_uid(self):
        self.server(lambda connection: time.sleep(0.1))
        with self.assertRaisesRegex(ContractError, "peer uid does not match"):
            invoke(self.path, self.request(), expected_uid=os.getuid() + 1,
                   timeout_seconds=1)

    def test_invoke_rejects_invalid_paths_request_types_and_expired_requests(self):
        request = self.request()
        for path in ("relative.sock", "invalid\x00.sock", "/" + "x" * 120):
            with self.subTest(path=path), self.assertRaisesRegex(
                    ContractError, "socket path is invalid"):
                invoke(path, request, expected_uid=os.getuid(), timeout_seconds=1)

        with self.assertRaisesRegex(ContractError, "request is invalid"):
            invoke(self.path, object(), expected_uid=os.getuid(), timeout_seconds=1)

        expired = BrokerRequest("expired-job", "b" * 64,
                                int(time.time()) - 1, "Expired text.")
        with self.assertRaisesRegex(ContractError, "request has expired"):
            invoke(self.path, expired, expected_uid=os.getuid(), timeout_seconds=1)

    def test_malformed_and_oversized_response_are_refused(self):
        for payload in (b'{"nonsense":1}', b'x' * 32769):
            with self.subTest(size=len(payload)):
                self.server(lambda connection: (
                    connection.recv(32768),
                    connection.sendall(struct.pack("!I", len(payload)) + payload)))
                expected = ("frame length is invalid" if len(payload) > 32768
                            else "message fields are not exact")
                with self.assertRaisesRegex(ContractError, expected):
                    invoke(self.path, self.request(), expected_uid=os.getuid(),
                           timeout_seconds=1)

    def test_invoke_rejects_a_refusal_sentinel_and_late_response(self):
        def refused(connection):
            connection.recv(32768)
            connection.sendall(struct.pack("!I", len(b'{"kind":"failed"}'))
                               + b'{"kind":"failed"}')

        self.server(refused)
        with self.assertRaisesRegex(ContractError, "broker refused the request"):
            invoke(self.path, self.request(), expected_uid=os.getuid(),
                   timeout_seconds=1)

        request = self.request()

        def respond(connection):
            _read_frame(connection, time.monotonic() + 1)
            response = BrokerResponse(request.job_id, request.handoff_sha256,
                                      "Spät.", None).to_bytes()
            _write_frame(connection, response, time.monotonic() + 1)

        self.server(respond)
        with patch("geniusnew.llm_broker.time.time",
                   side_effect=[request.expires_at - 1, request.expires_at]):
            with self.assertRaisesRegex(ContractError, "arrived after"):
                invoke(self.path, request, expected_uid=os.getuid(),
                       timeout_seconds=1)

    def test_process_abort_is_refused(self):
        process = multiprocessing.get_context("fork").Process(
            target=_crash_after_accept, args=(self.listener.fileno(),))
        process.start()
        try:
            with self.assertRaises(ContractError):
                invoke(self.path, self.request(), expected_uid=os.getuid(),
                       timeout_seconds=1)
            process.join(2)
            self.assertEqual(process.exitcode, 7)
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            process.close()

    def test_timeout_is_refused(self):
        self.server(lambda connection: time.sleep(0.2))
        with self.assertRaises(ContractError):
            invoke(self.path, self.request(), expected_uid=os.getuid(),
                   timeout_seconds=0.05)

    def test_provider_exception_does_not_leave_over_ipc(self):
        canary = "private-core-secret-canary"

        def fail(_request):
            raise RuntimeError(canary)

        self.server(lambda connection: serve_once(connection, fail,
                                                expected_uid=os.getuid(),
                                                timeout_seconds=1))
        with self.assertRaisesRegex(ContractError, "broker refused the request") as raised:
            invoke(self.path, self.request(), expected_uid=os.getuid(),
                   timeout_seconds=1)
        self.assertNotIn(canary, str(raised.exception))
        self.assertNotIn(canary, repr(raised.exception))

    def test_serve_once_rejects_non_callable_provider_before_reading(self):
        broker, caller = socket.socketpair()
        self.addCleanup(broker.close)
        self.addCleanup(caller.close)
        request = self.request().to_bytes()
        caller.sendall(struct.pack("!I", len(request)) + request)
        with self.assertRaisesRegex(ContractError, "provider is invalid"):
            serve_once(broker, None, expected_uid=os.getuid(), timeout_seconds=1)

    def test_serve_once_rejects_untyped_provider_result(self):
        def provider(_request):
            return SimpleNamespace(text="Not a broker result.", usage=None)

        self.server(lambda connection: serve_once(
            connection, provider, expected_uid=os.getuid(), timeout_seconds=1))
        with self.assertRaisesRegex(ContractError, "broker refused the request"):
            invoke(self.path, self.request(), expected_uid=os.getuid(),
                   timeout_seconds=1)

    def test_serve_once_refuses_a_provider_result_after_request_expiry(self):
        broker, caller = socket.socketpair()
        self.addCleanup(broker.close)
        self.addCleanup(caller.close)
        request = BrokerRequest("short-job", "c" * 64, 200, "Short request.")
        request_wire = request.to_bytes()
        caller.sendall(struct.pack("!I", len(request_wire)) + request_wire)
        with patch("geniusnew.llm_broker.time.time", side_effect=[100, 200]):
            accepted = serve_once(
                broker, lambda _request: ProviderResult("Late.", None),
                expected_uid=os.getuid(), timeout_seconds=1)
        self.assertFalse(accepted)

    def test_unrelated_environment_canary_does_not_enter_ipc(self):
        canary = "core-environment-secret-canary"
        captured = []

        def handler(connection):
            header = connection.recv(4)
            size = struct.unpack("!I", header)[0]
            body = bytearray()
            while len(body) < size:
                body.extend(connection.recv(size - len(body)))
            captured.append(bytes(body))
            response = BrokerResponse("job-123", "a" * 64, "Kurz.", None).to_bytes()
            connection.sendall(struct.pack("!I", len(response)) + response)

        self.server(handler)
        with patch.dict(os.environ, {"GENIUSNEW_CORE_SECRET_CANARY": canary}):
            invoke(self.path, self.request(), expected_uid=os.getuid(),
                   timeout_seconds=1)
        self.assertEqual(len(captured), 1)
        self.assertNotIn(canary.encode(), captured[0])


if __name__ == "__main__":
    unittest.main()
