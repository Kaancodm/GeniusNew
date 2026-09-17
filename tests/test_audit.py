import unittest

from geniusnew.audit import CONSTITUTION_VERSION, AuditEvent, event_from_handoff
from geniusnew.contracts import ContractError, Grant, Policy, issue, validate

# A canary, deliberately shaped so no scanner mistakes it for a credential:
# readable, zero entropy, and self-describing. Its only job is to be
# distinctive enough that its absence from an audit entry is meaningful.
PAYLOAD_CANARY = 'PAYLOAD-CANARY-MUST-NOT-REACH-THE-AUDIT-LOG'


class AuditEventTest(unittest.TestCase):
    def setUp(self):
        self.key = b'phase-2-test-integrity-key-32bytes'
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                      ('summarize',), 'isolated', False)
        self.policy = Policy('policy-v1', 'orchestrator-demo', 60,
                             ('summarize',), ('isolated',), (grant,))
        self.handoff = self.issue_handoff({'text': f'Please summarise {PAYLOAD_CANARY}'})

    def issue_handoff(self, request):
        wire = issue(request, subject='subject-demo', job_id='job-demo',
                     policy=self.policy, integrity_key=self.key, now=100)
        return validate(wire, subject='subject-demo', job_id='job-demo',
                        policy=self.policy, integrity_key=self.key, now=101)

    def event(self, **kw):
        args = dict(trace_id='trace-demo', actor='gateway', action='HANDOFF_ADMITTED',
                    decision='ALLOWED', reason_code='POLICY_SATISFIED', occurred_at=101)
        args.update(kw)
        return event_from_handoff(self.handoff, **args)

    def test_payload_content_cannot_reach_an_event(self):
        self.assertIn(PAYLOAD_CANARY, self.handoff.payload['text'])
        event = self.event()
        self.assertNotIn(PAYLOAD_CANARY.encode(), event.to_bytes())
        for value in event.to_dict().values():
            self.assertNotIn(PAYLOAD_CANARY, str(value))
        self.assertEqual(event.payload_sha256, self.handoff.payload_sha256)

    def test_event_carries_what_the_constitution_requires(self):
        recorded = self.event().to_dict()
        self.assertEqual(recorded['job_id'], 'job-demo')
        self.assertEqual(recorded['trace_id'], 'trace-demo')
        self.assertEqual(recorded['action'], 'HANDOFF_ADMITTED')
        self.assertEqual(recorded['decision'], 'ALLOWED')
        self.assertEqual(recorded['reason_code'], 'POLICY_SATISFIED')
        self.assertEqual(recorded['policy_version'], 'policy-v1')
        self.assertEqual(recorded['constitution_version'], CONSTITUTION_VERSION)
        self.assertEqual(recorded['subject'], 'user-demo')
        self.assertEqual(recorded['occurred_at'], 101)
        self.assertEqual(len(recorded['handoff_sha256']), 64)
        self.assertEqual(len(recorded['payload_sha256']), 64)

    def test_free_text_cannot_be_smuggled_into_a_reason_code(self):
        for code in (f'LEAKED {PAYLOAD_CANARY}', PAYLOAD_CANARY, 'lowercase', 'HAS SPACE',
                     'TRAILING-DASH', 'A' * 65, '', 1, None):
            with self.subTest(code=code), self.assertRaises(ContractError):
                self.event(reason_code=code)

    def test_identifiers_are_bounded_and_carry_no_whitespace(self):
        for value in (f'trace {PAYLOAD_CANARY}', 'x' * 129, 'has\nnewline', '', 1, None):
            with self.subTest(value=value), self.assertRaises(ContractError):
                self.event(trace_id=value)
            with self.subTest(value=value), self.assertRaises(ContractError):
                self.event(actor=value)

    def test_action_and_decision_come_from_closed_sets(self):
        for action in ('PAYLOAD_DUMP', 'handoff_admitted', '', None):
            with self.subTest(action=action), self.assertRaises(ContractError):
                self.event(action=action)
        for decision in ('MAYBE', 'allowed', '', None):
            with self.subTest(decision=decision), self.assertRaises(ContractError):
                self.event(decision=decision)

    def test_integrity_fields_must_be_digests(self):
        recorded = self.event().to_dict()
        for bad in (PAYLOAD_CANARY, 'g' * 64, 'A' * 64, 'abc', '', None):
            for field in ('handoff_sha256', 'payload_sha256'):
                with self.subTest(field=field, bad=bad), self.assertRaises(ContractError):
                    AuditEvent(**{**recorded, field: bad})

    def test_time_reference_fails_closed(self):
        for occurred_at in (True, 0, -1, '101', 101.0, None):
            with self.subTest(occurred_at=occurred_at), self.assertRaises(ContractError):
                self.event(occurred_at=occurred_at)

    def test_an_entry_stays_small_whatever_a_caller_passes(self):
        largest = AuditEvent(
            trace_id='t' * 128, job_id='j' * 128, actor='a' * 128, subject='s' * 128,
            action='RESULT_REJECTED', decision='DENIED', reason_code='X' * 64,
            policy_version='p' * 128, constitution_version='c' * 128,
            handoff_sha256='0' * 64, payload_sha256='1' * 64, occurred_at=2 ** 31)
        self.assertLess(len(largest.to_bytes()), 2048)

    def test_identifier_fields_are_bounded_but_are_not_a_secret_filter(self):
        recorded = self.event(trace_id=PAYLOAD_CANARY).to_dict()
        self.assertEqual(recorded['trace_id'], PAYLOAD_CANARY)
        with self.assertRaises(ContractError):
            self.event(trace_id=PAYLOAD_CANARY * 4)

    def test_event_hash_is_deterministic_and_binds_every_field(self):
        self.assertEqual(self.event().event_sha256(), self.event().event_sha256())
        baseline = self.event().event_sha256()
        self.assertNotEqual(self.event(decision='DENIED').event_sha256(), baseline)
        self.assertNotEqual(self.event(occurred_at=102).event_sha256(), baseline)
        self.assertNotEqual(self.event(actor='monitor').event_sha256(), baseline)

    def test_two_handoffs_differing_only_in_payload_produce_different_events(self):
        other = self.handoff
        self.handoff = self.issue_handoff({'text': 'Please summarise something else'})
        changed = self.event()
        self.handoff = other
        self.assertNotEqual(changed.payload_sha256, self.event().payload_sha256)
        self.assertNotEqual(changed.handoff_sha256, self.event().handoff_sha256)


if __name__ == '__main__':
    unittest.main()
