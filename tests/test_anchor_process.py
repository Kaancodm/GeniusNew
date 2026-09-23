"""Roadmap step 8: the anchor in a process the writer cannot reach into."""

import io
import os
import unittest

from geniusnew import anchor_process
from geniusnew.anchor_process import AnchorProcess
from geniusnew.audit import AuditAuthority, event_from_handoff
from geniusnew.audit_chain import AuditAnchor, AuditChain, AuditHead, sign_head, verify
from geniusnew.contracts import ContractError, Grant, Policy, canonical, issue, validate

AUDIT_KEY = b'a-separate-audit-key-of-32-bytes!'
OTHER_KEY = b'yet-another-audit-key-of-32bytes!'
EMPTY = '0' * 64


class ChainFixture:
    """A five-record chain. Deliberately not a TestCase, so nothing runs twice."""

    def setUp(self):
        self.authority = AuditAuthority(audit_key=AUDIT_KEY)
        key = b'phase-2-test-integrity-key-32bytes'
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                      ('summarize',), 'isolated', False)
        policy = Policy('policy-v1', 'orchestrator-demo', 60,
                        ('summarize',), ('isolated',), (grant,))
        actor = self.authority.actor('gateway', 'gw-1')
        self.chain = AuditChain()
        for step in range(5):
            wire = issue({'text': f'job {step}'}, subject='subject-demo',
                         job_id=f'job-{step}', policy=policy, integrity_key=key, now=100)
            handoff = validate(wire, subject='subject-demo', job_id=f'job-{step}',
                               policy=policy, integrity_key=key, now=101)
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


class AnchorProcessTest(ChainFixture, unittest.TestCase):
    """Against a real child process. Kept few: each one starts an interpreter."""

    def setUp(self):
        super().setUp()
        self.anchor = AnchorProcess(audit_key=AUDIT_KEY)
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
        unused = AnchorProcess(audit_key=AUDIT_KEY)
        unused.close()
        with self.assertRaisesRegex(ContractError, 'anchor process is unreachable'):
            unused.committed
        self.assertIsNone(unused._process)

    def test_a_restarted_anchor_remembers_nothing_and_this_is_the_boundary(self):
        """Held open. Memory only, started by the service: a restart is a reset.

        `SECURITY.md` lists it. Closing it needs the anchor started under
        another operating-system user and persisted, which v0.1 leaves out.
        """
        self.anchor.commit(self.head, self.records, authority=self.authority)
        self.anchor.close()
        restarted = AnchorProcess(audit_key=AUDIT_KEY)
        self.addCleanup(restarted.close)
        shorter, resigned = self.truncated()
        self.assertEqual(verify(shorter, resigned, authority=self.authority,
                                anchor=restarted), 4)


class InProcessAnchorTest(ChainFixture, unittest.TestCase):
    def test_an_in_process_anchor_can_be_rewound_by_its_writer(self):
        """Held open: why the anchor had to leave the writer's process."""
        anchor = AuditAnchor()
        anchor.commit(self.head, self.records, authority=self.authority)
        vars(anchor).update(_count=0, _head_hash=EMPTY)
        shorter, resigned = self.truncated()
        self.assertEqual(verify(shorter, resigned, authority=self.authority, anchor=anchor), 4)

    def test_a_short_key_is_refused_before_a_process_starts(self):
        with self.assertRaisesRegex(ContractError, 'audit_key'):
            AnchorProcess(audit_key=b'short')


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
        return anchor_process._handle(message, self.anchor, self.authority)

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
            ('expected an init message', {'audit_key': AUDIT_KEY.hex(), 'kind': 'commit'}),
            ('must be hex', {'audit_key': 7, 'kind': 'init'}),
            ('at least 32 bytes', {'audit_key': 'zz', 'kind': 'init'}),
        ]
        for message, init in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ContractError, message):
                    anchor_process._authority_from(init)
        authority = anchor_process._authority_from({'audit_key': AUDIT_KEY.hex(),
                                                    'kind': 'init'})
        self.assertEqual(authority.audit_key, AUDIT_KEY)

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
        requests = (frame({'audit_key': AUDIT_KEY.hex(), 'kind': 'init'})
                    + frame(self.commit_message()) + frame({'kind': 'reset'})
                    + frame({'kind': 'committed'}) + b'\x00\x00')
        out = io.BytesIO()
        self.assertEqual(anchor_process._serve(io.BytesIO(requests), out), 0)
        answers = replies(out.getvalue())
        self.assertEqual([answer['kind'] for answer in answers],
                         ['ok', 'ok', 'refused', 'ok'])
        self.assertEqual(answers[0]['count'], 0)
        self.assertEqual(answers[3]['count'], 5)

    def test_the_serve_loop_will_not_start_without_a_key(self):
        for requests in (b'', frame({'kind': 'init'})):
            with self.subTest(requests=requests):
                out = io.BytesIO()
                self.assertEqual(anchor_process._serve(io.BytesIO(requests), out), 1)
                self.assertEqual(out.getvalue(), b'')


if __name__ == '__main__':
    unittest.main()
