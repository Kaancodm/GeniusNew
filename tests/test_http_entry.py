"""Roadmap step 16: identity comes from the key, never from the request."""

import json
import threading
import unittest
import urllib.error
import urllib.request

from geniusnew.contracts import ContractError
from geniusnew.http_entry import (HttpEntry, Principal, PrincipalRegistry,
                                  REASONS, Response, serve)

# Zero-entropy and self-describing, so no scanner mistakes them for real
# credentials and finding one in a response is unambiguous.
API_KEY = b'API-KEY-CANARY-MUST-NOT-BE-DISCLOSED'
OTHER_KEY = b'SECOND-API-KEY-CANARY-NOT-DISCLOSED'
PAYLOAD_CANARY = 'PAYLOAD-CANARY-MUST-NOT-LEAVE-THE-BOUNDARY'

DEFAULT = object()


class Recorder:
    """Stands in for the orchestrator, and records exactly what it was given."""

    def __init__(self, *, raises=None, returns=DEFAULT):
        self.calls = []
        self.raises = raises
        self.returns = {'status': 'SUCCEEDED'} if returns is DEFAULT else returns

    def __call__(self, **arguments):
        self.calls.append(arguments)
        if self.raises is not None:
            raise self.raises
        return self.returns


class Fixture:
    """Shared setup. A mixin, not a TestCase: subclassing one re-runs its suite."""

    def setUp(self):
        self.registry = PrincipalRegistry.from_api_keys({
            API_KEY: 'subject-demo',
            OTHER_KEY: 'subject-other',
        })
        self.submit = Recorder()
        self.ids = iter(f'job-{index:04d}' for index in range(1000))
        self.entry = self.entry_for()

    def entry_for(self, *, registry=DEFAULT, submit=DEFAULT, job_ids=DEFAULT, **rest):
        return HttpEntry(
            registry=self.registry if registry is DEFAULT else registry,
            submit=self.submit if submit is DEFAULT else submit,
            job_ids=(lambda: next(self.ids)) if job_ids is DEFAULT else job_ids,
            **rest)

    def post(self, body=DEFAULT, *, entry=DEFAULT, key=API_KEY, method='POST',
             path='/jobs', content_type='application/json', headers=DEFAULT):
        if headers is DEFAULT:
            headers = {}
            if content_type is not None:
                headers['Content-Type'] = content_type
            if key is not None:
                headers['Authorization'] = 'Bearer ' + key.decode()
        if body is DEFAULT:
            body = json.dumps({'text': 'the quick brown fox'}).encode()
        return (self.entry if entry is DEFAULT else entry).handle(
            method=method, path=path, headers=headers, body=body)


class HttpEntranceTest(Fixture, unittest.TestCase):
    """The only door a stranger can reach."""

    # --- the mapping ---------------------------------------------------------

    def test_a_valid_key_becomes_a_server_side_subject(self):
        response = self.post()
        self.assertEqual(response.status, 202)
        self.assertEqual(response.reason, 'ACCEPTED')
        self.assertEqual(self.submit.calls, [
            {'subject': 'subject-demo', 'job_id': 'job-0000',
             'payload': {'text': 'the quick brown fox'}}])

    def test_a_different_key_is_a_different_subject(self):
        self.post(key=API_KEY)
        self.post(key=OTHER_KEY)
        self.assertEqual([call['subject'] for call in self.submit.calls],
                         ['subject-demo', 'subject-other'])

    def test_identity_and_rights_are_never_read_from_the_request(self):
        """The rule step 16 states as a prohibition.

        A field accepted here becomes trusted at every layer after it, because
        those layers check against the policy grant this decision selected. So
        the body is a closed shape: an extra field is refused, not dropped.
        """
        for extra in ({'text': 'x', 'tier': 'admin'},
                      {'text': 'x', 'subject': 'subject-other'},
                      {'text': 'x', 'user_id': 'root'},
                      {'text': 'x', 'tools': ['exfiltrate']},
                      {'text': 'x', 'job_id': 'job-chosen'},
                      {'subject': 'subject-other'},
                      {'text': 'x', 'payload': {'text': 'y'}}):
            with self.subTest(body=sorted(extra)):
                response = self.post(json.dumps(extra).encode())
                self.assertEqual(response.status, 400)
                self.assertEqual(response.reason, 'MALFORMED_REQUEST')
        self.assertEqual(self.submit.calls, [], 'nothing reached the orchestrator')

    def test_the_job_id_is_minted_here_and_not_by_the_caller(self):
        """A caller choosing job ids chooses which job id to collide with."""
        self.post(json.dumps({'text': 'x'}).encode())
        self.assertEqual(self.submit.calls[0]['job_id'], 'job-0000')
        self.post(json.dumps({'text': 'x'}).encode())
        self.assertEqual(self.submit.calls[1]['job_id'], 'job-0001')

    def test_the_default_job_ids_are_unpredictable_and_distinct(self):
        entry = HttpEntry(registry=self.registry, submit=self.submit)
        for _ in range(8):
            self.post(entry=entry)
        minted = [call['job_id'] for call in self.submit.calls]
        self.assertEqual(len(set(minted)), 8)
        for job_id in minted:
            self.assertRegex(job_id, r'\Ajob-[0-9a-f]{32}\Z')

    # --- refusals ------------------------------------------------------------

    def test_an_unknown_key_and_a_missing_one_are_indistinguishable(self):
        """No oracle: a client cannot learn whether a key exists."""
        seen = set()
        for key, headers in ((None, DEFAULT),
                             (b'THIS-KEY-IS-NOT-IN-THE-REGISTRY-AT-ALL', DEFAULT),
                             (b'short', DEFAULT),
                             (None, {'Content-Type': 'application/json',
                                     'Authorization': 'Basic abc'}),
                             (None, {'Content-Type': 'application/json',
                                     'Authorization': 'Bearer '})):
            with self.subTest(key=key):
                response = self.post(key=key, headers=headers)
                self.assertEqual(response.status, 401)
                seen.add((response.status, response.reason, json.dumps(response.body)))
        self.assertEqual(len(seen), 1, seen)
        self.assertEqual(self.submit.calls, [])

    def test_the_wrong_door_is_refused_before_anything_else(self):
        cases = [
            (dict(path='/'), 404, 'NOT_FOUND'),
            (dict(path='/jobs/../etc'), 404, 'NOT_FOUND'),
            (dict(path='/JOBS'), 404, 'NOT_FOUND'),
            (dict(method='GET'), 405, 'METHOD_NOT_ALLOWED'),
            (dict(method='DELETE'), 405, 'METHOD_NOT_ALLOWED'),
            (dict(content_type='text/plain'), 415, 'UNSUPPORTED_MEDIA_TYPE'),
            (dict(content_type=None), 415, 'UNSUPPORTED_MEDIA_TYPE'),
        ]
        for arguments, status, reason in cases:
            with self.subTest(**arguments):
                response = self.post(**arguments)
                self.assertEqual((response.status, response.reason), (status, reason))
        self.assertEqual(self.submit.calls, [])

    def test_an_oversized_body_is_refused_without_being_parsed(self):
        entry = self.entry_for(max_body_bytes=64)
        response = self.post(json.dumps({'text': 'x' * 200}).encode(), entry=entry)
        self.assertEqual((response.status, response.reason), (413, 'PAYLOAD_TOO_LARGE'))
        self.assertEqual(self.submit.calls, [])

    def test_malformed_bodies_never_reach_the_orchestrator(self):
        for body in (b'', b'{', b'null', b'[]', b'"text"', b'42',
                     b'{"text": ""}', b'{"text": null}', b'{"text": 42}',
                     b'{"text": {"text": "x"}}', b'\xff\xfe',
                     json.dumps({'text': 'x'}).encode() + b'trailing'):
            with self.subTest(body=body[:24]):
                response = self.post(body)
                self.assertEqual(response.status, 400, body)
        self.assertEqual(self.submit.calls, [])

    def test_a_deeply_nested_body_is_refused_rather_than_crashing(self):
        body = ('{"text":' * 400) + '"x"' + ('}' * 400)
        response = self.post(body.encode())
        self.assertEqual(response.status, 400)

    def test_nothing_but_the_expected_types_is_accepted_at_all(self):
        """The outermost boundary cannot assume its caller is well behaved.

        An adapter other than the one in this file — a different server, a test
        harness, a future ASGI shim — is exactly the caller that hands `handle`
        a path that is bytes or a body that is a string.
        """
        for arguments in ({'method': 42}, {'method': None}, {'method': b'POST'},
                          {'path': 42}, {'path': None}, {'path': b'/jobs'}):
            with self.subTest(**{key: repr(value) for key, value in arguments.items()}):
                response = self.entry.handle(
                    method=arguments.get('method', 'POST'),
                    path=arguments.get('path', '/jobs'),
                    headers={'Content-Type': 'application/json'}, body=b'{}')
                self.assertEqual((response.status, response.reason),
                                 (400, 'MALFORMED_REQUEST'))
        for body in ('{"text": "x"}', None, 42, bytearray(b'{}'), {'text': 'x'}):
            with self.subTest(body=repr(body)[:24]):
                self.assertEqual(self.post(body).status, 400)
        self.assertEqual(self.submit.calls, [])

    def test_nothing_but_a_mapping_is_accepted_as_headers(self):
        for headers in (None, 'Authorization: Bearer x', 42, [('a', 'b')]):
            with self.subTest(headers=type(headers)):
                self.assertEqual(self.post(headers=headers).status, 400)

    def test_a_refused_job_says_that_and_nothing_more(self):
        """Refusal sentences name policy fields. A stranger gets the fact only."""
        entry = self.entry_for(
            submit=Recorder(raises=ContractError(
                'grant exceeds policy allow-lists for subject-demo')))
        response = self.post(entry=entry)
        self.assertEqual((response.status, response.reason), (409, 'REJECTED'))
        self.assertEqual(response.body, {'error': 'REJECTED'})
        self.assertNotIn('policy', json.dumps(response.body))

    def test_an_orchestrator_returning_nonsense_is_not_reported_as_success(self):
        for returns in (None, 'ok', 42, [], object()):
            with self.subTest(returns=type(returns)):
                entry = self.entry_for(submit=Recorder(returns=returns))
                self.assertEqual(self.post(entry=entry).status, 500)

    def test_a_broken_job_id_source_does_not_produce_a_job(self):
        for bad in (lambda: None, lambda: 42, lambda: ''):
            with self.subTest(source=bad()):
                entry = self.entry_for(job_ids=bad)
                self.assertEqual(self.post(entry=entry).status, 500)
        self.assertEqual(self.submit.calls, [])

    # --- credentials ---------------------------------------------------------

    def test_the_registry_keeps_no_plaintext_key(self):
        """A dump of this object must disclose no credential."""
        dumped = repr(vars(self.registry)) + repr(self.registry.__dict__)
        for key in (API_KEY, OTHER_KEY):
            self.assertNotIn(key.decode(), dumped)
            self.assertNotIn(key.decode()[:12], dumped)

    def test_no_response_ever_carries_the_key_or_the_payload(self):
        bodies = []
        for response in (self.post(),
                         self.post(key=b'THIS-KEY-IS-NOT-IN-THE-REGISTRY-AT-ALL'),
                         self.post(json.dumps({'text': PAYLOAD_CANARY}).encode()),
                         self.post(json.dumps({'text': 'x', 'tier': 'admin'}).encode()),
                         self.post(method='GET')):
            bodies.append(json.dumps(response.body))
            bodies.append(response.to_bytes().decode())
        joined = '\n'.join(bodies)
        for fragment in (API_KEY.decode(), OTHER_KEY.decode(), API_KEY.decode()[:12],
                         PAYLOAD_CANARY, 'tier', 'admin'):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, joined)

    def test_resolving_is_a_lookup_and_never_raises(self):
        for presented in (b'', b'short', b'x' * 300, 'string', None, 42,
                          b'THIS-KEY-IS-NOT-IN-THE-REGISTRY-AT-ALL'):
            with self.subTest(presented=repr(presented)[:24]):
                self.assertIsNone(self.registry.resolve(presented))
        self.assertEqual(self.registry.resolve(API_KEY).subject, 'subject-demo')

    # --- construction fails closed -------------------------------------------

    def test_the_registry_refuses_anything_it_cannot_rely_on(self):
        for principals in (None, {}, [], 'table', {'short': 'subject'},
                           {'z' * 64: 'subject'}, {42: 'subject'},
                           {'a' * 64: ''}, {'a' * 64: None}):
            with self.subTest(principals=repr(principals)[:30]):
                with self.assertRaises(ContractError):
                    PrincipalRegistry(principals)
        for api_keys in (None, {}, 'keys', {b'short': 'subject'},
                         {'not-bytes-and-long-enough': 'subject'},
                         {b'x' * 300: 'subject'}):
            with self.subTest(api_keys=repr(api_keys)[:30]):
                with self.assertRaises(ContractError):
                    PrincipalRegistry.from_api_keys(api_keys)

    def test_the_entrance_refuses_anything_it_cannot_rely_on(self):
        for registry in (None, 'registry', 42, {}):
            with self.subTest(registry=type(registry)):
                with self.assertRaisesRegex(ContractError, 'registry'):
                    self.entry_for(registry=registry)
        for submit in (None, 'submit', 42):
            with self.subTest(submit=type(submit)):
                with self.assertRaisesRegex(ContractError, 'submit'):
                    self.entry_for(submit=submit)
        for job_ids in ('ids', 42, []):
            with self.subTest(job_ids=type(job_ids)):
                with self.assertRaisesRegex(ContractError, 'job_ids'):
                    self.entry_for(job_ids=job_ids)
        for size in (0, -1, 10 ** 9, None, 16.0):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ContractError, 'max_body_bytes'):
                    self.entry_for(max_body_bytes=size)

    def test_the_socket_layer_refuses_what_it_cannot_serve(self):
        """`serve` and `make_handler` are the last place a typo is cheap."""
        from geniusnew.http_entry import make_handler

        for entry in (None, 'entry', 42, self.registry):
            with self.subTest(entry=type(entry)):
                with self.assertRaisesRegex(ContractError, 'HttpEntry'):
                    make_handler(entry)
        for host in ('', None, 42, b'127.0.0.1'):
            with self.subTest(host=repr(host)):
                with self.assertRaisesRegex(ContractError, 'host'):
                    serve(self.entry, host=host)
        for port in (-1, 65536, None, '8080', 80.0):
            with self.subTest(port=repr(port)):
                with self.assertRaisesRegex(ContractError, 'port'):
                    serve(self.entry, port=port)

    def test_a_principal_carries_nothing_but_a_subject(self):
        """Tier and tools live in the policy grant, not in a second place."""
        self.assertEqual(set(vars(Principal('subject-demo'))), {'subject'})
        for subject in ('', None, 42, b'subject', 'x' * 200):
            with self.subTest(subject=repr(subject)[:20]):
                with self.assertRaises(ContractError):
                    Principal(subject)

    def test_a_response_outside_the_closed_reason_set_is_refused(self):
        for status, reason in ((202, 'SOMETHING_ELSE'), (99, 'ACCEPTED'),
                               (600, 'ACCEPTED'), ('202', 'ACCEPTED')):
            with self.subTest(status=status, reason=reason):
                with self.assertRaises(ContractError):
                    Response(status=status, reason=reason, body={})
        self.assertIn('ACCEPTED', REASONS)


class HttpSocketTest(Fixture, unittest.TestCase):
    """The socket half — thin on purpose, but it has to actually serve."""

    def setUp(self):
        super().setUp()
        self.server = serve(self.entry)
        # A short poll interval only so `shutdown()` returns promptly: the
        # default of half a second is waited out once per test, and
        # `scripts/refusals.py` runs this suite once per refusal — 175 times
        # today, which turned two seconds of teardown into six minutes of CI.
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={'poll_interval': 0.01},
            daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.url = f'http://{host}:{port}/jobs'
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def request(self, body=b'{"text": "the quick brown fox"}', *, key=API_KEY,
                url=None, method='POST'):
        headers = {'Content-Type': 'application/json'}
        if key is not None:
            headers['Authorization'] = 'Bearer ' + key.decode()
        request = urllib.request.Request(url or self.url, data=body, method=method,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read()), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read()), dict(error.headers)

    def test_a_real_request_reaches_the_orchestrator(self):
        status, body, _ = self.request()
        self.assertEqual(status, 202)
        self.assertEqual(body, {'job_id': 'job-0000', 'status': 'SUCCEEDED'})
        self.assertEqual(self.submit.calls[0]['subject'], 'subject-demo')

    def test_a_real_request_without_a_key_is_refused(self):
        status, body, _ = self.request(key=None)
        self.assertEqual((status, body), (401, {'error': 'UNAUTHENTICATED'}))
        self.assertEqual(self.submit.calls, [])

    def test_the_server_does_not_announce_its_interpreter(self):
        """A version header nobody needed is a version to target."""
        _, _, headers = self.request()
        joined = ' '.join(f'{name}: {value}' for name, value in headers.items())
        self.assertNotIn('Python', joined)
        self.assertNotIn('BaseHTTP', joined)

    def test_a_chunked_body_is_refused_and_the_connection_is_not_reused(self):
        """An unread body is where the next request gets parsed from.

        Nothing here reads a chunked body, so leaving the connection open
        would have whatever the client sent next parsed as a request line. The
        refusal closes it instead.
        """
        import http.client

        host, port = self.server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=10)
        connection.putrequest('POST', '/jobs', skip_accept_encoding=True)
        connection.putheader('Content-Type', 'application/json')
        connection.putheader('Authorization', 'Bearer ' + API_KEY.decode())
        connection.putheader('Transfer-Encoding', 'chunked')
        connection.endheaders()
        connection.send(b'10\r\n{"text": "xxxxx"}\r\n0\r\n\r\n')
        response = connection.getresponse()
        body = response.read()
        self.assertEqual(response.status, 413)
        self.assertEqual(json.loads(body), {'error': 'PAYLOAD_TOO_LARGE'})
        self.assertEqual(response.getheader('Connection'), 'close')
        self.assertEqual(self.submit.calls, [])
        connection.close()

    def test_a_head_request_gets_headers_and_no_body(self):
        """Announcing a length and then writing it anyway desynchronises."""
        import http.client

        host, port = self.server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=10)
        connection.request('HEAD', '/jobs', headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + API_KEY.decode()})
        response = connection.getresponse()
        self.assertEqual(response.status, 405)
        self.assertEqual(response.read(), b'')
        self.assertNotEqual(response.getheader('Content-Length'), '0')
        connection.close()

    def test_an_oversized_content_length_is_refused_without_reading_it(self):
        status, body, _ = self.request(b'x' * 100)  # not JSON, but small
        self.assertEqual(status, 400)
        # And a declared length beyond the ceiling never gets read at all.
        request = urllib.request.Request(
            self.url, data=b'{}', method='POST',
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + API_KEY.decode(),
                     'Content-Length': str(64 * 1024)})
        request.add_unredirected_header('Content-Length', str(64 * 1024))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 413)


if __name__ == '__main__':
    unittest.main()
