"""Roadmap step 8: the anchor in a process the writer cannot reach into."""

import errno
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from geniusnew import anchor_process
from geniusnew.anchor_process import AnchorClient, AnchorProcess
from geniusnew.audit import AuditAuthority, event_from_handoff
from geniusnew.audit_chain import AuditAnchor, AuditChain, AuditHead, sign_head, verify
from geniusnew.contracts import ContractError, Grant, HandoffSigner, Policy, canonical, issue, validate

AUDIT_KEY = b'a-separate-audit-key-of-32-bytes!'
OTHER_KEY = b'yet-another-audit-key-of-32bytes!'
EMPTY = '0' * 64


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

    def state_path(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return os.path.join(directory.name, 'anchor.state')

    def write_state(self, path, *heads, tail=b''):
        with open(path, 'wb') as stream:
            stream.write(b''.join(anchor_process._head_line(head) for head in heads) + tail)


class AnchorProcessTest(ChainFixture, unittest.TestCase):
    """Against a real child process. Kept few: each one starts an interpreter."""

    def setUp(self):
        super().setUp()
        self.anchor = AnchorProcess(verifier=self.authority.verifier())
        self.addCleanup(self.anchor.close)

    def test_a_commit_is_held_by_the_child_and_verified_against(self):
        committed = self.anchor.commit(self.head, self.records, authority=self.authority)
        self.assertEqual(committed, (5, self.head.head_hash))
        self.assertEqual(self.anchor.committed, committed)
        self.assertEqual(verify(self.records, self.head, authority=self.authority,
                                anchor=self.anchor), 5)
        self.assertNotEqual(self.anchor.pid, os.getpid())
        self.assertNotIn('_count', vars(self.anchor))

    def test_a_truncated_and_re_signed_chain_is_refused_by_the_child(self):
        self.anchor.commit(self.head, self.records, authority=self.authority)
        shorter, resigned = self.truncated()
        with self.assertRaisesRegex(ContractError, 'anchor committed 5 records; this chain has 4'):
            verify(shorter, resigned, authority=self.authority, anchor=self.anchor)
        with self.assertRaisesRegex(ContractError, 'anchor already committed 5 records'):
            self.anchor.commit(resigned, shorter, authority=self.authority)

    def test_the_child_is_given_the_public_key_and_nothing_else(self):
        """With HMAC the anchor had to hold the key that signs. Now it cannot."""
        sent = []
        real_send = anchor_process._send

        def spy(process, frame_bytes):
            sent.append(frame_bytes[4:])
            return real_send(process, frame_bytes)

        with unittest.mock.patch.object(anchor_process, '_send', side_effect=spy):
            self.anchor.committed
        init = json.loads(sent[0])
        self.assertEqual(init, {'kind': 'init',
                                'public_key': self.authority.verifier().public_key.hex()})
        self.assertFalse(any(isinstance(value, AuditAuthority)
                             for value in vars(self.anchor).values()))

    def test_the_writer_cannot_rewind_it_from_its_own_memory(self):
        self.anchor.commit(self.head, self.records, authority=self.authority)
        vars(self.anchor).update(_count=0, _head_hash=EMPTY)
        shorter, resigned = self.truncated()
        with self.assertRaisesRegex(ContractError, 'anchor committed 5 records'):
            verify(shorter, resigned, authority=self.authority, anchor=self.anchor)

    def test_a_head_signed_with_another_key_is_refused_by_the_child(self):
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
        self.anchor._process.kill()
        self.anchor._process.wait()
        with self.assertRaisesRegex(ContractError, 'anchor process is unreachable'):
            self.anchor.committed
        with self.assertRaisesRegex(ContractError, 'anchor process is unreachable'):
            verify(self.records, self.head, authority=self.authority, anchor=self.anchor)
        with self.assertRaisesRegex(ContractError, 'anchor process is unreachable'):
            self.anchor.commit(self.head, self.records, authority=self.authority)

    def test_a_closed_anchor_does_not_start_again(self):
        """Starting on first use must not become starting on any use: that is a reset."""
        unused = AnchorProcess(verifier=self.authority.verifier())
        unused.close()
        with self.assertRaisesRegex(ContractError, 'anchor process is unreachable'):
            unused.committed
        self.assertIsNone(unused._process)

    def test_without_a_state_file_a_restart_remembers_nothing(self):
        """Memory only is still what an anchor without `state_path` is."""
        self.anchor.commit(self.head, self.records, authority=self.authority)
        self.anchor.close()
        restarted = AnchorProcess(verifier=self.authority.verifier())
        self.addCleanup(restarted.close)
        shorter, resigned = self.truncated()
        self.assertEqual(verify(shorter, resigned, authority=self.authority,
                                anchor=restarted), 4)

    def test_a_restarted_anchor_resumes_from_its_state_file(self):
        """Until v0.1 a restart was a reset. With a state file it is not."""
        path = self.state_path()
        first = AnchorProcess(verifier=self.authority.verifier(), state_path=path)
        self.addCleanup(first.close)
        first.commit(self.head, self.records, authority=self.authority)
        first.close()
        restarted = AnchorProcess(verifier=self.authority.verifier(), state_path=path)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.committed, (5, self.head.head_hash))
        shorter, resigned = self.truncated()
        with self.assertRaisesRegex(ContractError, 'anchor committed 5 records'):
            verify(shorter, resigned, authority=self.authority, anchor=restarted)

    def test_two_child_anchors_cannot_write_the_same_state_file(self):
        path = self.state_path()
        first = AnchorProcess(verifier=self.authority.verifier(), state_path=path)
        second = AnchorProcess(verifier=self.authority.verifier(), state_path=path)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.assertEqual(first.committed, (0, EMPTY))
        with self.assertRaisesRegex(ContractError, 'already in use'):
            second.committed
        first.commit(self.head, self.records, authority=self.authority)
        first.close()
        restarted = AnchorProcess(verifier=self.authority.verifier(), state_path=path)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.committed, (5, self.head.head_hash))

    def test_an_unavailable_state_lease_refuses_before_loading(self):
        path = self.state_path()
        with unittest.mock.patch.object(anchor_process.socket, 'socket',
                                        side_effect=OSError(errno.EMFILE, 'test')):
            with self.assertRaisesRegex(ContractError, 'state lease cannot be acquired'):
                with anchor_process._state_lock(path):
                    self.fail('entered without a lease')

        class UnbindableLease:
            def bind(self, address):
                raise OSError(errno.EACCES, 'test')

            def close(self):
                pass

        with unittest.mock.patch.object(anchor_process.socket, 'socket',
                                        return_value=UnbindableLease()):
            with self.assertRaisesRegex(ContractError, 'state lease cannot be acquired'):
                with anchor_process._state_lock(path):
                    self.fail('entered without a lease')

    def test_a_file_rolled_back_to_an_older_signed_head_is_accepted_and_this_is_the_boundary(self):
        """Held open. Every line is a genuine signed head, so cutting the file back
        to an earlier one resumes from there.

        `SECURITY.md` lists it: whoever can write the file as the anchor's user
        can do this. `anchor_process serve` narrows *who* that is — run under its
        own operating-system user (`docs/ANCHOR-SERVICE.md`), the service's user
        can no longer write the file — but not *what* is possible: the anchor's
        user, root and a restored backup still can, and the served anchor
        resumes through the same `_load`. Which users can write the file is a
        property of the installation that this suite, running as one user,
        cannot prove, so the boundary stays held open rather than being
        declared closed by a test that could only pretend to separate users.
        """
        path = self.state_path()
        first = AnchorProcess(verifier=self.authority.verifier(), state_path=path)
        self.addCleanup(first.close)
        shorter, resigned = self.truncated()
        first.commit(resigned, shorter, authority=self.authority)
        first.commit(self.head, self.records, authority=self.authority)
        first.close()
        with open(path, 'rb') as stream:
            lines = stream.read().splitlines(keepends=True)
        self.assertEqual(len(lines), 2)
        with open(path, 'wb') as stream:
            stream.write(lines[0])
        restarted = AnchorProcess(verifier=self.authority.verifier(), state_path=path)
        self.addCleanup(restarted.close)
        self.assertEqual(verify(shorter, resigned, authority=self.authority,
                                anchor=restarted), 4)

    def test_an_unusable_state_file_stops_it_from_starting(self):
        """Refused with its reason. Starting at zero instead would be the reset."""
        path = self.state_path()
        self.write_state(path, self.head, tail=b'{"torn')
        anchor = AnchorProcess(verifier=self.authority.verifier(), state_path=path)
        self.addCleanup(anchor.close)
        with self.assertRaisesRegex(ContractError, 'refused to start: .*torn line'):
            anchor.committed
        with self.assertRaisesRegex(ContractError, 'unreachable'):
            anchor.committed

    def test_the_state_path_must_be_absolute(self):
        for path in ('anchor.state', '', 42, b'/tmp/anchor.state'):
            with self.subTest(path=path), self.assertRaisesRegex(
                    ContractError, 'state_path must be an absolute path'):
                AnchorProcess(verifier=self.authority.verifier(), state_path=path)
        # A path-like is fine; nothing starts until the first request.
        from pathlib import Path
        AnchorProcess(verifier=self.authority.verifier(),
                      state_path=Path(self.state_path())).close()


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
                    AnchorProcess(verifier=wrong)


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

    def test_the_writer_reads_only_the_two_reply_shapes(self):
        self.assertEqual(anchor_process._state({'count': 1, 'head_hash': EMPTY, 'kind': 'ok'}),
                         (1, EMPTY))
        with self.assertRaisesRegex(ContractError, 'anchor already committed'):
            anchor_process._state({'kind': 'refused', 'message': 'anchor already committed'})
        for reply in ({'kind': 'refused', 'message': 7}, {'kind': 'ok'},
                      {'kind': 'ok', 'count': 1, 'head_hash': EMPTY, 'extra': 1}):
            with self.subTest(reply=sorted(reply)):
                with self.assertRaisesRegex(ContractError, 'invalid reply'):
                    anchor_process._state(reply)

    def test_the_serve_loop_answers_each_frame_and_stops_at_a_torn_one(self):
        requests = (frame({'kind': 'init', 'public_key': self.public_hex()})
                    + frame(self.commit_message()) + frame({'kind': 'reset'})
                    + frame({'kind': 'committed'}) + b'\x00\x00')
        out = io.BytesIO()
        self.assertEqual(anchor_process._serve(io.BytesIO(requests), out), 0)
        answers = replies(out.getvalue())
        self.assertEqual([answer['kind'] for answer in answers],
                         ['ok', 'ok', 'refused', 'ok'])
        self.assertEqual(answers[0]['count'], 0)
        self.assertEqual(answers[3]['count'], 5)

    # --- the state file ------------------------------------------------------

    def load(self, path):
        return anchor_process._load(path, self.authority.verifier())

    def test_no_file_or_an_empty_one_is_a_fresh_anchor(self):
        path = self.state_path()
        self.assertEqual(self.load(None).committed, (0, EMPTY))
        self.assertEqual(self.load(path).committed, (0, EMPTY))
        self.write_state(path)
        self.assertEqual(self.load(path).committed, (0, EMPTY))

    def test_the_last_head_of_a_consistent_file_is_resumed(self):
        path = self.state_path()
        _, shorter_head = self.truncated()
        self.write_state(path, shorter_head, self.head)
        self.assertEqual(self.load(path).committed, (5, self.head.head_hash))

    def test_a_file_that_does_not_hold_together_is_refused(self):
        _, shorter_head = self.truncated()
        other = AuditAuthority(audit_key=OTHER_KEY)
        forged = sign_head(count=5, head_hash=self.head.head_hash, authority=other)
        cases = [
            ('torn line', (self.head,), b'{"count"'),
            ('head signature does not verify', (forged,), b''),
            ('head signature does not verify', (shorter_head, forged), b''),
            ('rise strictly', (self.head, shorter_head), b''),
            ('rise strictly', (self.head, self.head), b''),
            ('not valid|canonical|anchor state line', (), b'\n'),
            ('malformed head', (), b'{"count":5}\n'),
            ('not valid|canonical|anchor state line', (self.head,), b'not json\n'),
        ]
        for message, heads, tail in cases:
            with self.subTest(message=message, tail=tail[:10]):
                path = self.state_path()
                self.write_state(path, *heads, tail=tail)
                with self.assertRaisesRegex(ContractError, message):
                    self.load(path)

    def test_a_file_that_cannot_be_read_or_is_too_large_is_refused(self):
        with self.assertRaisesRegex(ContractError, 'cannot be read'):
            self.load(os.path.dirname(self.state_path()))
        path = self.state_path()
        self.write_state(path, self.head)
        with unittest.mock.patch.object(anchor_process, '_MAX_STATE_BYTES', 10):
            with self.assertRaisesRegex(ContractError, 'too large'):
                self.load(path)

    def test_the_serve_loop_appends_each_head_that_moves_it_and_nothing_else(self):
        path = self.state_path()
        shorter, shorter_head = self.truncated()
        requests = (frame({'kind': 'init', 'public_key': self.public_hex()})
                    + frame(self.commit_message(
                        head={'count': shorter_head.count, 'head_hash': shorter_head.head_hash,
                              'signature': shorter_head.signature,
                              'version': shorter_head.version},
                        records=[record.to_dict() for record in shorter]))
                    + frame(self.commit_message()) + frame(self.commit_message())
                    + frame({'kind': 'committed'}) + frame({'kind': 'reset'}))
        out = io.BytesIO()
        self.assertEqual(anchor_process._serve(io.BytesIO(requests), out, path), 0)
        self.assertEqual([answer['kind'] for answer in replies(out.getvalue())],
                         ['ok', 'ok', 'ok', 'ok', 'ok', 'refused'])
        with open(path, 'rb') as stream:
            self.assertEqual(stream.read(), anchor_process._head_line(shorter_head)
                             + anchor_process._head_line(self.head))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_the_serve_loop_refuses_to_start_from_a_bad_file(self):
        path = self.state_path()
        self.write_state(path, tail=b'torn')
        out = io.BytesIO()
        requests = frame({'kind': 'init', 'public_key': self.public_hex()})
        self.assertEqual(anchor_process._serve(io.BytesIO(requests), out, path), 1)
        self.assertEqual(replies(out.getvalue()),
                         [{'kind': 'refused', 'message': 'anchor state file ends in a torn line'}])

    def test_a_head_it_cannot_write_ends_the_child_unanswered(self):
        """Memory moved and the file did not: the file must stay the truth."""
        path = os.path.join(self.state_path(), 'missing-directory', 'anchor.state')
        requests = (frame({'kind': 'init', 'public_key': self.public_hex()})
                    + frame(self.commit_message()))
        out = io.BytesIO()
        self.assertEqual(anchor_process._serve(io.BytesIO(requests), out, path), 2)
        self.assertEqual([answer['kind'] for answer in replies(out.getvalue())], ['ok'])

    def test_resuming_verifies_the_head_first(self):
        other = AuditAuthority(audit_key=OTHER_KEY)
        with self.assertRaisesRegex(ContractError, 'head signature does not verify'):
            AuditAnchor.resumed(self.head, authority=other)
        with self.assertRaisesRegex(ContractError, 'head is invalid'):
            AuditAnchor.resumed('head', authority=self.authority)
        self.assertEqual(AuditAnchor.resumed(self.head, authority=self.authority).committed,
                         (5, self.head.head_hash))

    def test_the_serve_loop_will_not_start_without_a_key(self):
        for requests in (b'', frame({'kind': 'init'})):
            with self.subTest(requests=requests):
                out = io.BytesIO()
                self.assertEqual(anchor_process._serve(io.BytesIO(requests), out), 1)
                self.assertEqual(out.getvalue(), b'')


def short_directory(test):
    """A socket path must fit in 108 bytes, so the system temp dir, not a nested one."""
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    return directory.name


def envelope(request, nonce='ab' * 32):
    return {'nonce': nonce, 'request': request}


class ServedProtocolTest(ChainFixture, unittest.TestCase):
    """The served anchor's side of one connection, without a socket or a process."""

    def setUp(self):
        super().setUp()
        self.anchor = AuditAnchor()
        self.key = Ed25519PrivateKey.generate()
        self.path = self.state_path()

    def serve(self, *messages, raw=b''):
        requests = b''.join(frame(message) for message in messages) + raw
        out = io.BytesIO()
        code = anchor_process._serve_connection(
            io.BytesIO(requests), out, self.anchor, self.authority.verifier(), self.key,
            self.path)
        return code, replies(out.getvalue())

    def commit_request(self):
        return {'head': {'count': self.head.count, 'head_hash': self.head.head_hash,
                         'signature': self.head.signature, 'version': self.head.version},
                'kind': 'commit', 'records': [record.to_dict() for record in self.records]}

    def test_every_reply_is_signed_over_the_nonce_it_answers(self):
        code, answers = self.serve(envelope(self.commit_request(), 'aa' * 32),
                                   envelope({'kind': 'reset'}, 'bb' * 32))
        self.assertEqual(code, 0)
        self.assertEqual([answer['nonce'] for answer in answers], ['aa' * 32, 'bb' * 32])
        self.assertEqual([answer['reply']['kind'] for answer in answers], ['ok', 'refused'])
        public = self.key.public_key()
        for answer in answers:
            public.verify(bytes.fromhex(answer['signature']),
                          anchor_process._reply_message(answer['nonce'], answer['reply']))
        self.assertEqual(answers[0]['reply']['count'], 5)

    def test_a_head_is_written_before_it_is_answered(self):
        code, answers = self.serve(envelope(self.commit_request()))
        self.assertEqual((code, len(answers)), (0, 1))
        with open(self.path, 'rb') as stream:
            self.assertEqual(stream.read(), anchor_process._head_line(self.head))
        self.path = os.path.join(self.state_path(), 'missing-directory', 'anchor.state')
        self.anchor = AuditAnchor()
        self.assertEqual(self.serve(envelope(self.commit_request())), (2, []))

    def test_a_request_it_cannot_bind_a_reply_to_ends_the_connection_unanswered(self):
        committed = {'kind': 'committed'}
        for label, message in (
                ('extra key', {**envelope(committed), 'extra': 1}),
                ('no nonce', {'request': committed}),
                ('upper-case nonce', envelope(committed, 'AB' * 32)),
                ('short nonce', envelope(committed, 'ab' * 31)),
                ('numeric nonce', envelope(committed, 7)),
                ('request not an object', envelope('committed')),
                ('bare child request', committed)):
            with self.subTest(label):
                self.assertEqual(self.serve(message, envelope(committed)), (0, []))
        self.assertEqual(self.serve(raw=b'\x00\x00\x00\x02{}'), (0, []))


class ReplyKeyTest(unittest.TestCase):
    def path(self):
        return os.path.join(short_directory(self), 'anchor.key')

    def test_the_key_is_made_once_for_its_owner_and_kept(self):
        path = self.path()
        first = anchor_process._reply_key(path)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(anchor_process._public_bytes(anchor_process._reply_key(path)),
                         anchor_process._public_bytes(first))

    def test_a_key_file_others_could_read_or_of_the_wrong_size_is_refused(self):
        path = self.path()
        anchor_process._reply_key(path)
        os.chmod(path, 0o640)
        with self.assertRaisesRegex(ContractError, 'only its owner can read'):
            anchor_process._reply_key(path)
        for data in (b'', b'x' * 31, b'x' * 33):
            with self.subTest(size=len(data)):
                path = self.path()
                with open(path, 'wb') as stream:
                    stream.write(data)
                os.chmod(path, 0o600)
                with self.assertRaisesRegex(ContractError, 'exactly 32 bytes'):
                    anchor_process._reply_key(path)

    def test_a_key_path_that_is_a_link_or_unreachable_is_refused(self):
        path = self.path()
        target = path + '.target'
        with open(target, 'wb') as stream:
            stream.write(b'x' * 32)
        os.chmod(target, 0o600)
        os.symlink(target, path)
        with self.assertRaisesRegex(ContractError, 'cannot be read'):
            anchor_process._reply_key(path)
        with self.assertRaisesRegex(ContractError, 'cannot be created'):
            anchor_process._reply_key(os.path.join(path + '.missing', 'anchor.key'))


class ListenTest(unittest.TestCase):
    def test_it_binds_owner_and_group_only_and_never_takes_a_path_over(self):
        path = os.path.join(short_directory(self), 'anchor.sock')
        server = anchor_process._listen(path)
        self.addCleanup(server.close)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o660)
        with self.assertRaisesRegex(ContractError, 'already exists'):
            anchor_process._listen(path)

    def test_a_path_it_cannot_bind_is_refused(self):
        path = '/' + 'x' * 200 + '.sock'
        with self.assertRaisesRegex(ContractError, 'cannot be bound'):
            anchor_process._listen(path)

    def test_paths_must_be_absolute(self):
        for path in ('anchor.sock', '', None, 7):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ContractError, 'must be an absolute path'):
                    anchor_process._absolute(path, 'socket path')


class FakeAnchor:
    """Whatever sits at the socket path, answering as the test decides."""

    def __init__(self, test, answer):
        self.path = os.path.join(short_directory(test), 'anchor.sock')
        self.answer = answer
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(1)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
        test.addCleanup(self.thread.join, 5)
        test.addCleanup(self.server.close)

    def serve(self):
        with self.server.accept()[0] as connection:
            size = int.from_bytes(connection.recv(4, socket.MSG_WAITALL), 'big')
            request = json.loads(connection.recv(size, socket.MSG_WAITALL))
            connection.sendall(self.answer(request))


class AnchorClientTest(ChainFixture, unittest.TestCase):
    """The client refuses every answer the anchor did not sign for this request."""

    def setUp(self):
        super().setUp()
        self.key = Ed25519PrivateKey.generate()

    def client(self, path):
        return AnchorClient(socket_path=path,
                            reply_public_key=anchor_process._public_bytes(self.key))

    def signed(self, request, *, key=None, nonce=None, reply=None):
        nonce = request['nonce'] if nonce is None else nonce
        reply = {'count': 0, 'head_hash': EMPTY, 'kind': 'ok'} if reply is None else reply
        signature = (key or self.key).sign(anchor_process._reply_message(nonce, reply)).hex()
        return frame({'nonce': nonce, 'reply': reply, 'signature': signature})

    def test_a_reply_signed_for_this_request_is_believed(self):
        fake = FakeAnchor(self, self.signed)
        self.assertEqual(self.client(fake.path).committed, (0, EMPTY))

    def test_a_stand_in_at_the_socket_path_is_refused(self):
        other = Ed25519PrivateKey.generate()
        cases = [
            ('not signed by the anchor',
             lambda request: self.signed(request, key=other)),
            ('not signed by the anchor',
             lambda request: frame({'nonce': request['nonce'],
                                    'reply': {'count': 0, 'head_hash': EMPTY, 'kind': 'ok'},
                                    'signature': '00' * 64})),
            ('not signed by the anchor',
             lambda request: frame({'nonce': request['nonce'], 'reply': 'ok',
                                    'signature': 7})),
            ('answers another request',
             lambda request: self.signed(request, nonce='cd' * 32)),
            ('envelope is malformed',
             lambda request: frame({'count': 0, 'head_hash': EMPTY, 'kind': 'ok'})),
            ('unreachable',
             lambda request: (anchor_process._MAX_REPLY_BYTES + 1).to_bytes(4, 'big')),
            ('unreachable', lambda request: b'\x00\x00'),
        ]
        for message, answer in cases:
            with self.subTest(message=message):
                fake = FakeAnchor(self, answer)
                with self.assertRaisesRegex(ContractError, message):
                    self.client(fake.path).committed

    def test_an_anchor_that_is_not_there_fails_closed(self):
        path = os.path.join(short_directory(self), 'anchor.sock')
        with self.assertRaisesRegex(ContractError, 'anchor service is unreachable'):
            self.client(path).committed

    def test_what_is_sent_is_checked_before_it_is_sent(self):
        client = self.client(os.path.join(short_directory(self), 'anchor.sock'))
        with self.assertRaisesRegex(ContractError, 'head is invalid'):
            client.commit('head', self.records, authority=self.authority)

    def test_it_is_configured_with_an_absolute_path_and_a_public_key(self):
        public = anchor_process._public_bytes(self.key)
        with self.assertRaisesRegex(ContractError, 'absolute path'):
            AnchorClient(socket_path='anchor.sock', reply_public_key=public)
        for key in (public[:-1], public.hex(), None):
            with self.subTest(key=type(key)):
                with self.assertRaisesRegex(ContractError, '32 bytes'):
                    AnchorClient(socket_path='/run/anchor.sock', reply_public_key=key)


class ServedAnchorTest(ChainFixture, unittest.TestCase):
    """The operator's anchor: its own process, started and stopped without the service."""

    def setUp(self):
        super().setUp()
        directory = short_directory(self)
        self.socket = os.path.join(directory, 'anchor.sock')
        self.state = os.path.join(directory, 'anchor.state')
        self.key = os.path.join(directory, 'anchor.key')
        self.public = bytes.fromhex(self.command('public-key', '--key', self.key).stdout.strip())

    def command(self, *arguments):
        return subprocess.run([sys.executable, '-m', 'geniusnew.anchor_process', *arguments],
                              capture_output=True, text=True, timeout=30)

    def start(self):
        process = subprocess.Popen(
            [sys.executable, '-m', 'geniusnew.anchor_process', 'serve', '--socket', self.socket,
             '--state', self.state, '--key', self.key,
             '--audit-public-key', self.authority.verifier().public_key.hex()],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self.stop, process)
        deadline = time.monotonic() + 10
        while not os.path.exists(self.socket):
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail('the anchor did not start: ' + process.stderr.read().decode())
            time.sleep(0.02)
        return process

    def stop(self, process):
        if process.poll() is None:
            process.terminate()
            process.wait(10)
        process.stderr.close()

    def client(self):
        return AnchorClient(socket_path=self.socket, reply_public_key=self.public)

    def test_a_restarted_service_finds_the_anchor_where_it_left_it(self):
        self.start()
        self.assertEqual(self.client().commit(self.head, self.records, authority=self.authority),
                         (5, self.head.head_hash))
        shorter, shorter_head = self.truncated()
        with self.assertRaisesRegex(ContractError, 'anchor already committed'):
            self.client().commit(shorter_head, shorter, authority=self.authority)
        # A new client is all a restarted service has; the anchor is untouched.
        self.assertEqual(self.client().committed, (5, self.head.head_hash))
        self.assertEqual(verify(self.records, self.head, authority=self.authority,
                                anchor=self.client()), 5)

    def test_a_restarted_anchor_keeps_its_state_and_its_key(self):
        process = self.start()
        self.client().commit(self.head, self.records, authority=self.authority)
        self.stop(process)
        self.assertFalse(os.path.exists(self.socket))
        with self.assertRaisesRegex(ContractError, 'unreachable'):
            self.client().committed
        self.start()
        self.assertEqual(self.client().committed, (5, self.head.head_hash))

    def test_a_stop_waits_for_the_connection_in_hand(self):
        """A stop inside a connection could tear the head being appended."""
        import signal
        process = self.start()
        held = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(held.close)
        held.connect(self.socket)
        held.sendall(frame(envelope({'kind': 'committed'})))
        size = int.from_bytes(held.recv(4, socket.MSG_WAITALL), 'big')
        self.assertEqual(json.loads(held.recv(size, socket.MSG_WAITALL))['reply']['count'], 0)
        process.send_signal(signal.SIGTERM)
        time.sleep(0.3)
        self.assertIsNone(process.poll())
        held.close()
        self.assertEqual(process.wait(10), 0)
        self.assertFalse(os.path.exists(self.socket))

    def test_it_will_not_start_over_an_existing_path_or_without_its_inputs(self):
        with open(self.socket, 'w'):
            pass
        refused = self.command('serve', '--socket', self.socket, '--state', self.state,
                               '--key', self.key,
                               '--audit-public-key', self.authority.verifier().public_key.hex())
        self.assertEqual(refused.returncode, 1)
        self.assertIn('anchor socket path already exists', refused.stderr)
        os.unlink(self.socket)
        refused = self.command('serve', '--socket', self.socket, '--state', 'anchor.state',
                               '--key', self.key,
                               '--audit-public-key', self.authority.verifier().public_key.hex())
        self.assertEqual(refused.returncode, 1)
        self.assertIn('state path must be an absolute path', refused.stderr)
        self.assertFalse(os.path.exists(self.socket))


if __name__ == '__main__':
    unittest.main()
