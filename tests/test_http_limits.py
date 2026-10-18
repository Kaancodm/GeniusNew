"""Gate C4: one caller cannot spend everyone's workers, and connections are capped."""

import io
import json
import socket
import threading
import time
import unittest
from http.client import HTTPMessage

from geniusnew.contracts import ContractError
from geniusnew.http_entry import HttpEntry, PrincipalRegistry, REASONS, make_handler, serve

API_KEY = b'API-KEY-CANARY-FOR-THE-LIMIT-TESTS'
OTHER_KEY = b'SECOND-KEY-CANARY-FOR-LIMIT-TESTS'
BODY = json.dumps({'text': 'the quick brown fox'}).encode()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, **arguments):
        self.calls.append(arguments)
        return {'status': 'SUCCEEDED'}


def registry():
    return PrincipalRegistry.from_api_keys({API_KEY: 'subject-a', OTHER_KEY: 'subject-b'})


def post(entry, key=API_KEY, body=BODY, path='/jobs', headers=None):
    headers = headers or {'Content-Type': 'application/json',
                          'Authorization': 'Bearer ' + key.decode()}
    return entry.handle(method='POST', path=path, headers=headers, body=body)


class RateLimitTest(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.submit = Recorder()
        self.entry = HttpEntry(registry=registry(), submit=self.submit,
                               rate_per_minute=60, burst=3, clock=self.clock)

    def test_the_burst_is_served_and_the_next_request_is_refused(self):
        statuses = [post(self.entry).status for _ in range(4)]
        self.assertEqual(statuses, [202, 202, 202, 429])
        self.assertEqual(len(self.submit.calls), 3)

    def test_a_refused_request_says_only_that_it_was_too_many(self):
        for _ in range(3):
            post(self.entry)
        refused = post(self.entry)
        self.assertEqual((refused.status, refused.reason), (429, 'TOO_MANY_REQUESTS'))
        self.assertEqual(refused.body, {'error': 'TOO_MANY_REQUESTS'})
        self.assertIn('TOO_MANY_REQUESTS', REASONS)

    def test_tokens_refill_with_time(self):
        for _ in range(3):
            post(self.entry)
        self.assertEqual(post(self.entry).status, 429)
        self.clock.now += 1.0  # 60 per minute is one per second
        self.assertEqual(post(self.entry).status, 202)
        self.assertEqual(post(self.entry).status, 429)

    def test_refill_never_exceeds_the_burst(self):
        self.clock.now += 3600
        statuses = [post(self.entry).status for _ in range(4)]
        self.assertEqual(statuses, [202, 202, 202, 429])

    def test_a_clock_stepping_back_mints_no_tokens(self):
        for _ in range(3):
            post(self.entry)
        self.clock.now -= 3600
        self.assertEqual(post(self.entry).status, 429)
        self.clock.now += 1.0
        self.assertEqual(post(self.entry).status, 429)

    def test_each_principal_has_its_own_bucket(self):
        for _ in range(3):
            post(self.entry, key=API_KEY)
        self.assertEqual(post(self.entry, key=API_KEY).status, 429)
        self.assertEqual(post(self.entry, key=OTHER_KEY).status, 202)

    def test_unauthenticated_requests_spend_nobody_s_budget(self):
        for _ in range(10):
            self.assertEqual(post(self.entry, key=b'UNKNOWN-KEY-NOT-CONFIGURED-ANYWHERE').status, 401)
        self.assertEqual(post(self.entry).status, 202)

    def test_a_malformed_request_still_spends_its_caller_s_budget(self):
        for _ in range(3):
            self.assertEqual(post(self.entry, body=b'{"text": 1}').status, 400)
        self.assertEqual(post(self.entry).status, 429)

    def test_the_approval_route_is_limited_too(self):
        completer = Recorder()
        entry = HttpEntry(registry=registry(), submit=self.submit, complete=completer,
                          rate_per_minute=60, burst=1, clock=self.clock)
        headers = {'Content-Type': 'application/json',
                   'Authorization': 'Bearer ' + API_KEY.decode(),
                   'X-Approval-Token': 'ab' * 32}
        first = post(entry, path='/jobs/job-1/approve', body=b'{}', headers=headers)
        second = post(entry, path='/jobs/job-1/approve', body=b'{}', headers=headers)
        self.assertEqual((first.status, second.status), (202, 429))
        self.assertEqual(len(completer.calls), 1)


class InFlightTest(unittest.TestCase):

    def test_a_job_beyond_the_in_flight_limit_is_refused_not_queued(self):
        release = threading.Event()
        started = threading.Event()

        def slow_submit(**arguments):
            started.set()
            release.wait(10)
            return {'status': 'SUCCEEDED'}

        entry = HttpEntry(registry=registry(), submit=slow_submit, max_in_flight=1)
        first = {}
        worker = threading.Thread(target=lambda: first.setdefault('r', post(entry)))
        worker.start()
        self.assertTrue(started.wait(10))
        busy = post(entry, key=OTHER_KEY)
        self.assertEqual((busy.status, busy.reason), (503, 'SERVICE_BUSY'))
        release.set()
        worker.join(10)
        self.assertEqual(first['r'].status, 202)
        self.assertEqual(post(entry, key=OTHER_KEY).status, 202)

    def test_a_refused_job_frees_its_slot(self):
        def refusing_submit(**arguments):
            raise ContractError('policy says no')

        entry = HttpEntry(registry=registry(), submit=refusing_submit, max_in_flight=1)
        self.assertEqual(post(entry).status, 409)
        self.assertEqual(post(entry).status, 409)

    def test_an_unexpected_error_frees_its_slot(self):
        calls = []

        def failing_submit(**arguments):
            calls.append(arguments)
            if len(calls) == 1:
                raise RuntimeError('worker crashed')
            return {'status': 'SUCCEEDED'}

        entry = HttpEntry(registry=registry(), submit=failing_submit, max_in_flight=1)
        with self.assertRaises(RuntimeError):
            post(entry)
        self.assertEqual(post(entry).status, 202)


class ConfigurationTest(unittest.TestCase):

    def refused(self, message, **arguments):
        with self.assertRaisesRegex(ContractError, message):
            HttpEntry(registry=registry(), submit=Recorder(), **arguments)

    def test_limits_must_be_positive_integers_below_their_ceiling(self):
        for name, ceiling in (('rate_per_minute', 6000), ('burst', 1000),
                              ('max_in_flight', 256)):
            for value in (0, -1, ceiling + 1, 1.5, True, '10'):
                with self.subTest(name=name, value=value):
                    self.refused(f'{name} must be an integer between 1 and {ceiling}',
                                 **{name: value})

    def test_the_ceilings_themselves_are_accepted(self):
        HttpEntry(registry=registry(), submit=Recorder(), rate_per_minute=6000,
                  burst=1000, max_in_flight=256)

    def test_the_clock_must_be_callable(self):
        self.refused('clock must be callable', clock=12.0)

    def test_the_connection_cap_must_be_bounded(self):
        entry = HttpEntry(registry=registry(), submit=Recorder())
        for value in (0, 4097, 2.0, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ContractError, 'max_connections must be'):
                    serve(entry, max_connections=value)


class ConnectionCapTest(unittest.TestCase):

    def setUp(self):
        entry = HttpEntry(registry=registry(), submit=Recorder())
        self.server = serve(entry, max_connections=2)
        self.port = self.server.server_address[1]
        self.loop = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.loop.start()
        self.sockets = []

    def tearDown(self):
        for sock in self.sockets:
            sock.close()
        self.server.shutdown()
        self.server.server_close()
        self.loop.join(10)

    def connect(self):
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        self.sockets.append(sock)
        return sock

    def is_closed_by_server(self, sock):
        try:
            return sock.recv(1) == b''
        except (ConnectionResetError, socket.timeout):
            return False

    def served(self, sock):
        request = (b'POST /jobs HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n'
                   b'Authorization: Bearer ' + API_KEY + b'\r\nContent-Length: '
                   + str(len(BODY)).encode() + b'\r\nConnection: close\r\n\r\n' + BODY)
        sock.sendall(request)
        return sock.recv(64).startswith(b'HTTP/1.1 202')

    def test_a_connection_past_the_cap_is_closed_unread(self):
        self.connect()
        self.connect()
        # Wait until both idle connections hold a slot before testing the third.
        deadline = time.monotonic() + 5
        while self.server._slots._value and time.monotonic() < deadline:
            time.sleep(0.01)
        third = self.connect()
        self.assertTrue(self.is_closed_by_server(third))

    def test_a_freed_slot_serves_the_next_connection(self):
        first = self.connect()
        self.connect()
        deadline = time.monotonic() + 5
        while self.server._slots._value and time.monotonic() < deadline:
            time.sleep(0.01)
        first.close()
        deadline = time.monotonic() + 5
        while not self.server._slots._value and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.served(self.connect()))


class DuplicateHeaderTest(unittest.TestCase):
    CASES = (
        ('Content-Length', str(len(BODY)), str(len(BODY) + 10)),
        ('Transfer-Encoding', 'chunked', 'gzip'),
        ('Host', 'core.invalid', 'other.invalid'),
        ('Authorization', 'Bearer ' + API_KEY.decode(), 'Bearer ' + OTHER_KEY.decode()),
    )

    def wire(self, name, first, second, *, second_name=None, method='POST',
             expect=False, include_body=True):
        headers = [
            ('Host', 'core.invalid'),
            ('Content-Type', 'application/json'),
            ('Authorization', 'Bearer ' + API_KEY.decode()),
            ('Content-Length', str(len(BODY))),
            ('Connection', 'keep-alive'),
        ]
        headers = [(key, value) for key, value in headers if key.lower() != name.lower()]
        headers.extend(((name, first), (second_name or name, second)))
        if expect:
            headers.append(('Expect', '100-continue'))
        head = method + ' /jobs HTTP/1.1\r\n'
        head += ''.join(key + ': ' + value + '\r\n' for key, value in headers)
        return (head + '\r\n').encode() + (BODY if include_body else b'')

    def assert_refused(self, wire, *, head=False):
        reads, mappings, handled = [], [], []
        submit = Recorder()

        class BodyReader(io.BytesIO):
            def read(self, *arguments):
                reads.append(arguments)
                return super().read(*arguments)

        class RawHeaders(HTTPMessage):
            def items(self):
                mappings.append(True)
                return super().items()

        class TrackedEntry(HttpEntry):
            def handle(self, **arguments):
                handled.append(arguments)
                return super().handle(**arguments)

        entry = TrackedEntry(registry=registry(), submit=submit)
        handler_type = make_handler(entry)
        handler_type.MessageClass = RawHeaders
        # Use the real request parser, with only its streams instrumented.
        with BodyReader(wire) as reader, io.BytesIO() as writer:
            handler = handler_type.__new__(handler_type)
            handler.rfile, handler.wfile = reader, writer
            handler.handle_one_request()
            response = writer.getvalue()
        self.assertEqual(reads, [], 'duplicate headers must be refused before a body read')
        self.assertEqual(mappings, [], 'raw header multiplicity must survive until refusal')
        self.assertEqual(handled, [], 'the entrance must not receive ambiguous headers')
        self.assertEqual(submit.calls, [])
        self.assertTrue(handler.close_connection)
        headers, body = response.split(b'\r\n\r\n', 1)
        self.assertTrue(headers.startswith(b'HTTP/1.1 400 '), response)
        self.assertIn(b'\r\nConnection: close', headers)
        if head:
            self.assertEqual(body, b'')
        else:
            self.assertEqual(json.loads(body), {'error': 'MALFORMED_REQUEST'})

    def check_header(self, name, first, other):
        for second in (first, other):
            for second_name in (name, name.swapcase()):
                with self.subTest(name=name, first=first, second=second,
                                  second_name=second_name):
                    self.assert_refused(self.wire(name, first, second, second_name=second_name))

    def test_duplicate_content_length_is_refused_even_when_values_match(self):
        self.check_header(*self.CASES[0])
        self.check_header('Content-Length', '5', '50')

    def test_duplicate_transfer_encoding_is_malformed_before_the_chunked_refusal(self):
        self.check_header(*self.CASES[1])

    def test_duplicate_host_is_refused_before_mapping_or_body_read(self):
        self.check_header(*self.CASES[2])

    def test_duplicate_authorization_is_refused_before_mapping_or_body_read(self):
        self.check_header(*self.CASES[3])

    def test_head_refuses_duplicates_without_a_response_body(self):
        for name, first, _ in self.CASES:
            with self.subTest(name=name):
                self.assert_refused(self.wire(
                    name, first, first, second_name=name.swapcase(), method='HEAD'), head=True)

    def test_duplicates_are_refused_before_100_continue(self):
        for name, first, _ in self.CASES:
            with self.subTest(name=name):
                self.assert_refused(self.wire(
                    name, first, first, second_name=name.swapcase(), expect=True))

    def test_the_server_closes_duplicate_requests_without_waiting_for_a_body(self):
        submit = Recorder()
        server = serve(HttpEntry(registry=registry(), submit=submit))
        loop = threading.Thread(target=server.serve_forever,
                                kwargs={'poll_interval': 0.01}, daemon=True)
        loop.start()
        try:
            for name, first, _ in self.CASES:
                with self.subTest(name=name):
                    with socket.create_connection(server.server_address, timeout=2) as sock:
                        sock.sendall(self.wire(
                            name, first, first, second_name=name.swapcase(), include_body=False))
                        response = bytearray()
                        while chunk := sock.recv(4096):
                            response.extend(chunk)
                    headers, body = bytes(response).split(b'\r\n\r\n', 1)
                    self.assertTrue(headers.startswith(b'HTTP/1.1 400 '), response)
                    self.assertIn(b'\r\nConnection: close', headers)
                    self.assertEqual(json.loads(body), {'error': 'MALFORMED_REQUEST'})
            self.assertEqual(submit.calls, [])
        finally:
            server.shutdown()
            server.server_close()
            loop.join(5)


if __name__ == '__main__':
    unittest.main()
