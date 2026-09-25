"""Roadmap step 8: the anchor in a process the writer cannot reach into."""

import gc
import io
import json
import os
import socket
import tempfile
import threading
import unittest
import unittest.mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from geniusnew import anchor_process
from geniusnew.anchor_process import AnchorClient, start
from geniusnew.audit import AuditAuthority, event_from_handoff
from geniusnew.audit_chain import AuditAnchor, AuditChain, AuditHead, sign_head, verify
from geniusnew.contracts import ContractError, Grant, HandoffSigner, Policy, canonical, issue, validate

AUDIT_KEY = b'a-separate-audit-key-of-32-bytes!'
OTHER_KEY = b'yet-another-audit-key-of-32bytes!'
EMPTY = '0' * 64
NONCE = 'ab' * 32
# Stands in for the key a real anchor process makes when it starts.
ANCHOR_KEY = Ed25519PrivateKey.from_private_bytes(b'anchor-key-for-tests-32-bytes!!!')
ANCHOR_PUBLIC = ANCHOR_KEY.public_key().public_bytes(serialization.Encoding.Raw,
                                                     serialization.PublicFormat.Raw)


def signed(reply, nonce=NONCE, key=ANCHOR_KEY):
    """A reply as the anchor sends it: with the nonce, signed over both."""
    reply = {**reply, 'nonce': nonce}
    return {**reply, 'signature': key.sign(canonical(reply)).hex()}


class ChainFixture:
    """A five-record chain. Deliberately not a TestCase, so nothing runs twice."""

    def setUp(self):
        self.authority = AuditAuthority(audit_key=AUDIT_KEY)
        key = HandoffSigner(integrity_key=b'phase-2-test-integrity-key-32bytes')
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                      ('summarize',), 'isolated', False)
        policy = Policy('policy-v1', 'orchestrator-demo', 60,
                        ('summarize',), ('isolated',), (grant,))
        actor = self.authority.actor('gateway', 'gw-1')
        self.chain = AuditChain()
        for step in range(5):
            wire = issue({'text': f'job {step}'}, subject='subject-demo',
                         job_id=f'job-{step}', policy=policy, signer=key, now=100)
            handoff = validate(wire, subject='subject-demo', job_id=f'job-{step}',
                               policy=policy, verifier=key, now=101)
            self.chain.append(event_from_handoff(
                handoff, trace_id=f'trace-{step}', actor=actor, action='HANDOFF_ADMITTED',
                decision='ALLOWED', reason_code='POLICY_SATISFIED', occurred_at=101 + step))
        self.records = self.chain.records
        self.head = self.chain.head(self.authority)

    def truncated(self):
        """The attack an anchor exists for: shorten the log and re-sign it."""
        shorter = self.records[:-1]
        return shorter, sign_head(count=len(shorter), head_hash=shorter[-1].record_hash,
                                  authority=self.authority)


def started_anchor(test, directory=None):
    """Start an anchor the way an operator would: outside any service."""
    if directory is None:
        holder = tempfile.TemporaryDirectory(prefix='geniusnew-anchor-')
        test.addCleanup(holder.cleanup)
        directory = holder.name
    verifier = AuditAuthority(audit_key=AUDIT_KEY).verifier()
    handle = start(verifier=verifier, socket_path=os.path.join(directory, 'anchor.sock'))
    test.addCleanup(handle.stop)
    return handle


class AnchorProcessTest(ChainFixture, unittest.TestCase):
    """Against a real anchor process. Kept few: each one starts an interpreter."""

    def setUp(self):
        super().setUp()
        self.handle = started_anchor(self)
        self.anchor = self.handle.client()

    def test_a_commit_is_held_by_the_anchor_and_verified_against(self):
        committed = self.anchor.commit(self.head, self.records, authority=self.authority)
        self.assertEqual(committed, (5, self.head.head_hash))
        self.assertEqual(self.anchor.committed, committed)
        self.assertEqual(verify(self.records, self.head, authority=self.authority,
                                anchor=self.anchor), 5)
        self.assertNotEqual(self.handle.pid, os.getpid())
        self.assertNotIn('_count', vars(self.anchor))

    def test_a_truncated_and_re_signed_chain_is_refused_by_the_anchor(self):
        self.anchor.commit(self.head, self.records, authority=self.authority)
        shorter, resigned = self.truncated()
        with self.assertRaisesRegex(ContractError, 'anchor committed 5 records; this chain has 4'):
            verify(shorter, resigned, authority=self.authority, anchor=self.anchor)
        with self.assertRaisesRegex(ContractError, 'anchor already committed 5 records'):
            self.anchor.commit(resigned, shorter, authority=self.authority)

    def test_the_anchor_is_given_the_public_key_and_nothing_else(self):
        """With HMAC the anchor had to hold the key that signs. Now it cannot."""
        sent = []
        real_write_all = anchor_process._write_all

        def spy(stream, data):
            sent.append(data[4:])
            return real_write_all(stream, data)

        with tempfile.TemporaryDirectory() as directory:
            with unittest.mock.patch.object(anchor_process, '_write_all', side_effect=spy):
                handle = start(verifier=self.authority.verifier(),
                               socket_path=os.path.join(directory, 'anchor.sock'))
            handle.stop()
        self.assertEqual([json.loads(data) for data in sent],
                         [{'kind': 'init',
                           'public_key': self.authority.verifier().public_key.hex()}])

    def test_the_writer_cannot_rewind_it_from_its_own_memory(self):
        self.anchor.commit(self.head, self.records, authority=self.authority)
        vars(self.anchor).update(_count=0, _head_hash=EMPTY)
        shorter, resigned = self.truncated()
        with self.assertRaisesRegex(ContractError, 'anchor committed 5 records'):
            verify(shorter, resigned, authority=self.authority, anchor=self.anchor)

    def test_a_head_signed_with_another_key_is_refused_by_the_anchor(self):
        other = AuditAuthority(audit_key=OTHER_KEY)
        with self.assertRaisesRegex(ContractError, 'head signature does not verify'):
            self.anchor.commit(self.chain.head(other), self.records, authority=other)
        self.assertEqual(self.anchor.committed, (0, EMPTY))

    def test_what_is_sent_is_checked_before_it_is_sent(self):
        with self.assertRaisesRegex(ContractError, 'head is invalid'):
            self.anchor.commit(None, self.records, authority=self.authority)
        with self.assertRaisesRegex(ContractError, 'AuditRecord'):
            self.anchor.commit(self.head, [object()] * 5, authority=self.authority)
        with self.assertRaisesRegex(ContractError, 'head.count'):
            self.anchor.commit(AuditHead('v', 2 ** 70, EMPTY, 'x'), self.records,
                               authority=self.authority)

    def test_an_anchor_that_is_gone_fails_closed(self):
        self.anchor.commit(self.head, self.records, authority=self.authority)
        self.handle._process.kill()
        self.handle._process.wait()
        with self.assertRaisesRegex(ContractError, 'anchor process is unreachable'):
            self.anchor.committed
        with self.assertRaisesRegex(ContractError, 'anchor process is unreachable'):
            verify(self.records, self.head, authority=self.authority, anchor=self.anchor)
        with self.assertRaisesRegex(ContractError, 'anchor process is unreachable'):
            self.anchor.commit(self.head, self.records, authority=self.authority)

    def test_a_restarted_writer_finds_its_commitments_still_there(self):
        """The lifecycle gap this module closed: a writer restart is not a reset.

        The client is all a service holds. Dropping it and connecting anew is
        what a restarted service does, and the anchor is still where it was.
        """
        self.anchor.commit(self.head, self.records, authority=self.authority)
        del self.anchor
        gc.collect()
        restarted = self.handle.client()
        self.assertEqual(restarted.committed, (5, self.head.head_hash))
        shorter, resigned = self.truncated()
        with self.assertRaisesRegex(ContractError, 'anchor committed 5 records; this chain has 4'):
            verify(shorter, resigned, authority=self.authority, anchor=restarted)

    def test_a_replaced_socket_is_refused_not_believed(self):
        """Codex review on #32: the writer moves the path to a stand-in of its own.

        Unlinking the path leaves the real anchor running and unreachable; the
        stand-in answers "nothing committed" in exactly the right shape. Before
        answers were signed, the shortened, re-signed chain then verified.
        """
        self.anchor.commit(self.head, self.records, authority=self.authority)
        os.unlink(self.handle.socket_path)
        stand_in = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(stand_in.close)
        stand_in.bind(self.handle.socket_path)
        stand_in.listen(4)

        def answer_nothing_committed():
            for _ in range(2):
                connection, _ = stand_in.accept()
                with connection, connection.makefile('rb') as requests:
                    nonce = json.loads(anchor_process._read_frame(requests))['nonce']
                    connection.sendall(frame({'count': 0, 'head_hash': EMPTY, 'kind': 'ok',
                                              'nonce': nonce, 'signature': '00' * 64}))

        server = threading.Thread(target=answer_nothing_committed, daemon=True)
        server.start()
        shorter, resigned = self.truncated()
        with self.assertRaisesRegex(ContractError, 'not signed by this anchor'):
            self.anchor.committed
        with self.assertRaisesRegex(ContractError, 'not signed by this anchor'):
            verify(shorter, resigned, authority=self.authority, anchor=self.anchor)
        server.join(5)

    def test_the_client_holds_nothing_that_could_stop_the_anchor(self):
        self.assertEqual(set(vars(self.anchor)), {'_anchor_key', '_socket_path'})
        for name in ('stop', 'close', 'pid', '_process'):
            with self.subTest(name=name):
                self.assertFalse(hasattr(self.anchor, name))

    def test_it_runs_in_a_session_of_its_own(self):
        """A Ctrl-C at the starter's terminal goes to the starter's group, not here."""
        self.assertEqual(os.getsid(self.handle.pid), self.handle.pid)
        self.assertNotEqual(os.getpgid(self.handle.pid), os.getpgid(0))

    def test_a_taken_socket_path_is_not_taken_over(self):
        """A second anchor on the same path would be a silent reset."""
        self.anchor.commit(self.head, self.records, authority=self.authority)
        with self.assertRaisesRegex(ContractError, 'anchor process did not start'):
            start(verifier=self.authority.verifier(), socket_path=self.handle.socket_path)
        self.assertEqual(self.anchor.committed, (5, self.head.head_hash))

    def test_stopping_it_removes_its_socket_and_the_client_fails_closed(self):
        self.handle.stop()
        self.assertFalse(os.path.exists(self.handle.socket_path))
        with self.assertRaisesRegex(ContractError, 'anchor process is unreachable'):
            self.anchor.committed

    def test_a_restarted_anchor_remembers_nothing_and_this_is_the_boundary(self):
        """Held open. Memory only: an anchor restart is a reset.

        `SECURITY.md` lists it, and `docs/ADR-002-anchor-persistence.md` is
        where persisting it is to be decided. The stop and the start are the
        operator's now, not the service's — but they still forget.
        """
        self.anchor.commit(self.head, self.records, authority=self.authority)
        self.handle.stop()
        restarted = started_anchor(self, directory=os.path.dirname(self.handle.socket_path))
        shorter, resigned = self.truncated()
        self.assertEqual(verify(shorter, resigned, authority=self.authority,
                                anchor=restarted.client()), 4)
        # A client still holding the first anchor's key notices the restart:
        # the new process signs with a key of its own.
        with self.assertRaisesRegex(ContractError, 'not signed by this anchor'):
            self.anchor.committed


class InProcessAnchorTest(ChainFixture, unittest.TestCase):
    def test_an_in_process_anchor_can_be_rewound_by_its_writer(self):
        """Held open: why the anchor had to leave the writer's process."""
        anchor = AuditAnchor()
        anchor.commit(self.head, self.records, authority=self.authority)
        vars(anchor).update(_count=0, _head_hash=EMPTY)
        shorter, resigned = self.truncated()
        self.assertEqual(verify(shorter, resigned, authority=self.authority, anchor=anchor), 4)

    def test_only_the_public_half_starts_an_anchor(self):
        """Handing it the authority would put the signing key back in its reach."""
        for wrong in (self.authority, AUDIT_KEY, None):
            with self.subTest(wrong=type(wrong).__name__):
                with self.assertRaisesRegex(ContractError, 'verifier must be an AuditVerifier'):
                    start(verifier=wrong, socket_path='/nonexistent/anchor.sock')

    def test_the_socket_path_must_be_absolute(self):
        for path in ('anchor.sock', None, b'/tmp/anchor.sock'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ContractError, 'absolute path'):
                    AnchorClient(path, anchor_key=ANCHOR_PUBLIC)
        with self.assertRaisesRegex(ContractError, 'absolute path'):
            start(verifier=self.authority.verifier(), socket_path='anchor.sock')


def frame(message):
    data = canonical(message)
    return len(data).to_bytes(4, 'big') + data


def replies(data):
    out, stream = [], io.BytesIO(data)
    while (header := stream.read(4)):
        out.append(anchor_process._decode(stream.read(int.from_bytes(header, 'big')), 'reply'))
    return out


class ChildProtocolTest(ChainFixture, unittest.TestCase):
    """The child's side, called directly. No process needed to test a refusal."""

    def setUp(self):
        super().setUp()
        self.anchor = AuditAnchor()

    def handle(self, message):
        return anchor_process._handle(message, self.anchor, self.authority.verifier())

    def commit_message(self, **overrides):
        message = {
            'head': {'count': self.head.count, 'head_hash': self.head.head_hash,
                     'signature': self.head.signature, 'version': self.head.version},
            'kind': 'commit',
            'records': [record.to_dict() for record in self.records],
        }
        message.update(overrides)
        return message

    def test_a_commit_rebuilt_from_data_is_the_same_commit(self):
        self.assertEqual(self.handle(self.commit_message()),
                         {'count': 5, 'head_hash': self.head.head_hash, 'kind': 'ok'})
        self.assertEqual(self.handle({'kind': 'committed'})['count'], 5)

    def test_there_is_no_request_but_commit_and_committed(self):
        for message in ({'kind': 'reset'}, {'kind': 'committed', 'count': 0},
                        {'kind': 'commit'}, {**self.commit_message(), 'extra': 1}):
            with self.subTest(message=sorted(message)):
                with self.assertRaisesRegex(ContractError, 'not recognised'):
                    self.handle(message)

    def test_a_malformed_head_is_refused(self):
        with self.assertRaisesRegex(ContractError, 'malformed head'):
            self.handle(self.commit_message(head={'count': 5}))

    def test_records_must_be_a_list(self):
        with self.assertRaisesRegex(ContractError, 'records must be a list'):
            self.handle(self.commit_message(records={'a': 1}))

    def test_a_malformed_record_event_or_actor_is_refused(self):
        good = self.records[0].to_dict()
        cases = [
            ('malformed record', {**good, 'extra': 1}),
            ('malformed event', {**good, 'event': {**good['event'], 'payload': 'x'}}),
            ('malformed actor', {**good, 'event': {**good['event'], 'actor': 'gateway'}}),
            ('malformed actor', {**good, 'event': {**good['event'], 'actor': 'a:b:c'}}),
            ('malformed actor', {**good, 'event': {**good['event'], 'actor': 7}}),
            ('constitution', {**good, 'event': {**good['event'], 'actor': 'root:gw-1'}}),
        ]
        for message, record in cases:
            with self.subTest(message=message, record=record['event']['actor']
                              if isinstance(record.get('event'), dict) else None):
                with self.assertRaisesRegex(ContractError, message):
                    self.handle(self.commit_message(records=[record]))

    def test_a_record_rebuilt_differently_does_not_verify(self):
        records = [record.to_dict() for record in self.records]
        records[2]['event']['occurred_at'] += 1
        with self.assertRaisesRegex(ContractError, 'record 2 has been altered'):
            self.handle(self.commit_message(records=records))

    def test_init_must_carry_a_usable_key(self):
        cases = [
            ('expected an init message', {'kind': 'init'}),
            ('expected an init message', {'audit_key': AUDIT_KEY.hex(), 'kind': 'init'}),
            ('expected an init message', {'kind': 'commit', 'public_key': self.public_hex()}),
            ('must be hex', {'kind': 'init', 'public_key': 7}),
            ('32 bytes', {'kind': 'init', 'public_key': 'zz'}),
            ('32 bytes', {'kind': 'init', 'public_key': self.public_hex()[:-2]}),
        ]
        for message, init in cases:
            with self.subTest(message=message, init=sorted(init)):
                with self.assertRaisesRegex(ContractError, message):
                    anchor_process._verifier_from(init)
        verifier = anchor_process._verifier_from({'kind': 'init',
                                                  'public_key': self.public_hex()})
        self.assertEqual(verifier.public_key, self.authority.verifier().public_key)

    def public_hex(self):
        return self.authority.verifier().public_key.hex()

    def test_only_exact_canonical_objects_are_read(self):
        for data in (b'{"kind": "committed"}', b'{"kind":"a","kind":"b"}',
                     b'{"n":NaN}', b'["kind"]', b'\xff', b''):
            with self.subTest(data=data):
                with self.assertRaisesRegex(ContractError, 'not canonical JSON'):
                    anchor_process._decode(data, 'request')

    def test_a_frame_past_its_limit_is_refused(self):
        with self.assertRaisesRegex(ContractError, 'too large'):
            anchor_process._frame({'kind': 'x' * 64}, 16)

    def state(self, reply, nonce=NONCE):
        return anchor_process._state(reply, nonce=nonce,
                                     key=anchor_process._anchor_key(ANCHOR_PUBLIC))

    def test_the_writer_reads_only_the_two_reply_shapes(self):
        self.assertEqual(self.state(signed({'count': 1, 'head_hash': EMPTY, 'kind': 'ok'})),
                         (1, EMPTY))
        with self.assertRaisesRegex(ContractError, 'anchor already committed'):
            self.state(signed({'kind': 'refused', 'message': 'anchor already committed'}))
        for reply in ({'kind': 'refused', 'message': 7}, {'kind': 'ok'},
                      {'kind': 'ok', 'count': 1, 'head_hash': EMPTY, 'extra': 1}):
            with self.subTest(reply=sorted(reply)):
                with self.assertRaisesRegex(ContractError, 'invalid reply'):
                    self.state(signed(reply))

    def test_a_reply_counts_only_if_this_anchor_signed_it(self):
        good = {'count': 1, 'head_hash': EMPTY, 'kind': 'ok'}
        other = Ed25519PrivateKey.from_private_bytes(b'another-anchor-key-of-32-bytes!!')
        forged = [
            {**good, 'nonce': NONCE},
            {**signed(good), 'signature': 7},
            {**signed(good), 'signature': 'zz'},
            {**signed(good), 'count': 0},
            signed(good, key=other),
        ]
        for reply in forged:
            with self.subTest(reply=sorted(reply)):
                with self.assertRaisesRegex(ContractError, 'not signed by this anchor'):
                    self.state(reply)

    def test_a_signed_reply_to_another_request_is_not_an_answer(self):
        """Replaying something the anchor once said is not the anchor saying it now."""
        for nonce in ('cd' * 32, None):
            with self.subTest(nonce=nonce):
                with self.assertRaisesRegex(ContractError, 'answers another request'):
                    self.state(signed({'count': 0, 'head_hash': EMPTY, 'kind': 'ok'},
                                      nonce=nonce))

    def test_the_client_needs_the_anchors_key(self):
        for key in (None, 'ab' * 32, ANCHOR_PUBLIC[:31]):
            with self.subTest(key=type(key).__name__):
                with self.assertRaisesRegex(ContractError, 'anchor_key must be 32 bytes'):
                    anchor_process.AnchorClient('/tmp/anchor.sock', anchor_key=key)

    def test_ready_must_name_the_anchors_key(self):
        cases = [
            ('did not report ready', {'kind': 'ok', 'anchor_key': ANCHOR_PUBLIC.hex()}),
            ('did not report ready', {'kind': 'ready'}),
            ('must be hex', {'kind': 'ready', 'anchor_key': 7}),
            ('32 bytes', {'kind': 'ready', 'anchor_key': 'zz'}),
        ]
        for message, ready in cases:
            with self.subTest(message=message, ready=sorted(ready)):
                with self.assertRaisesRegex(ContractError, message):
                    anchor_process._ready(ready)
        self.assertEqual(anchor_process._ready({'kind': 'ready',
                                                'anchor_key': ANCHOR_PUBLIC.hex()}),
                         ANCHOR_PUBLIC)

    def serve(self, data):
        """One connection, served as the anchor serves it. Returns what came back."""
        server, client = socket.socketpair()
        with server, client:
            client.sendall(data)
            client.shutdown(socket.SHUT_WR)
            anchor_process._serve_connection(server, self.anchor, self.authority.verifier(),
                                             ANCHOR_KEY)
            server.close()
            return client.makefile('rb').read()

    def test_each_connection_gets_one_signed_answer(self):
        self.assertEqual([answer['kind'] for answer in replies(
            self.serve(frame({**self.commit_message(), 'nonce': NONCE})))], ['ok'])
        self.assertEqual(replies(self.serve(frame({'kind': 'reset', 'nonce': NONCE})))[0]['kind'],
                         'refused')
        answers = replies(self.serve(frame({'kind': 'committed', 'nonce': NONCE})
                                     + frame({'kind': 'committed', 'nonce': NONCE})))
        self.assertEqual(answers, [signed({'count': 5, 'head_hash': self.head.head_hash,
                                           'kind': 'ok'})])
        self.assertEqual(self.state(answers[0]), (5, self.head.head_hash))

    def test_a_request_without_a_usable_nonce_is_refused(self):
        for nonce in (None, 7, 'AB' * 32, 'ab' * 31, 'zz' * 32):
            with self.subTest(nonce=nonce):
                request = {'kind': 'committed'} if nonce is None else {'kind': 'committed',
                                                                      'nonce': nonce}
                answer = replies(self.serve(frame(request)))[0]
                self.assertEqual((answer['kind'], answer['nonce']), ('refused', None))
                self.assertIn('nonce', answer['message'])

    def test_a_torn_or_oversized_frame_gets_no_answer(self):
        oversized = (anchor_process._MAX_REQUEST_BYTES + 1).to_bytes(4, 'big')
        for data in (b'', b'\x00\x00', frame({'kind': 'committed'})[:-1], oversized):
            with self.subTest(data=data[:8]):
                self.assertEqual(self.serve(data), b'')
        self.assertEqual(self.anchor.committed, (0, EMPTY))

    def test_the_anchor_will_not_start_without_a_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'anchor.sock')
            for requests in (b'', frame({'kind': 'init'})):
                with self.subTest(requests=requests):
                    out = io.BytesIO()
                    self.assertEqual(anchor_process._serve(io.BytesIO(requests), out, path), 1)
                    self.assertEqual(out.getvalue(), b'')
                    self.assertFalse(os.path.exists(path))

if __name__ == '__main__':
    unittest.main()
