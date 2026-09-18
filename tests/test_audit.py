import unittest
from dataclasses import replace

from geniusnew.audit import (CONSTITUTION_VERSION, AuditAuthority, AuditEvent,
                             ComponentActor, event_from_handoff)
from geniusnew.contracts import ContractError, Grant, Policy, issue, validate

# A canary, deliberately shaped so no scanner mistakes it for a credential:
# readable, zero entropy, and self-describing. Its only job is to be
# distinctive enough that its absence from an audit entry is meaningful.
PAYLOAD_CANARY = 'PAYLOAD-CANARY-MUST-NOT-REACH-THE-AUDIT-LOG'


class EventFixture:
    """Shared setup, deliberately not a TestCase — see test_audit_chain.py."""

    def setUp(self):
        self.key = b'phase-2-test-integrity-key-32bytes'
        self.authority = AuditAuthority(audit_key=b'a-separate-audit-key-of-32-bytes!')
        self.actor = self.authority.actor('gateway', 'gw-1')
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                      ('summarize',), 'isolated', False)
        self.policy = Policy('policy-v1', 'orchestrator-demo', 60,
                             ('summarize',), ('isolated',), (grant,))
        self.handoff = self.issue_handoff({'text': f'Please summarise {PAYLOAD_CANARY}'})

    def issue_handoff(self, request, job_id='job-demo'):
        wire = issue(request, subject='subject-demo', job_id=job_id,
                     policy=self.policy, integrity_key=self.key, now=100)
        return validate(wire, subject='subject-demo', job_id=job_id,
                        policy=self.policy, integrity_key=self.key, now=101)

    def event(self, job_id_override=None, **kw):
        args = dict(trace_id='trace-demo', actor=self.actor, action='HANDOFF_ADMITTED',
                    decision='ALLOWED', reason_code='POLICY_SATISFIED', occurred_at=101)
        args.update(kw)
        return event_from_handoff(self.handoff, **args)


class AuditEventTest(EventFixture, unittest.TestCase):
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
        self.assertEqual(recorded['actor'], 'gateway:gw-1')
        self.assertEqual(recorded['occurred_at'], 101)
        self.assertEqual(len(recorded['handoff_sha256']), 64)
        self.assertEqual(len(recorded['payload_sha256']), 64)

    def test_free_text_cannot_be_smuggled_into_a_reason_code(self):
        for code in (f'LEAKED {PAYLOAD_CANARY}', PAYLOAD_CANARY, 'lowercase', 'HAS SPACE',
                     'TRAILING-DASH', 'A' * 65, '', 1, None):
            with self.subTest(code=code), self.assertRaises(ContractError):
                self.event(reason_code=code)

    def test_identifiers_are_bounded_and_carry_no_whitespace(self):
        for value in (f'trace {PAYLOAD_CANARY}', 'x' * 200, 'has\nnewline', '', 1, None):
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
        event = self.event()
        for bad in (PAYLOAD_CANARY, 'g' * 64, 'A' * 64, 'abc', '', None):
            for field in ('handoff_sha256', 'payload_sha256'):
                with self.subTest(field=field, bad=bad), self.assertRaises(ContractError):
                    replace(event, **{field: bad})

    def test_time_reference_fails_closed(self):
        for occurred_at in (True, 0, -1, '101', 101.0, None):
            with self.subTest(occurred_at=occurred_at), self.assertRaises(ContractError):
                self.event(occurred_at=occurred_at)

    def test_an_entry_stays_small_whatever_a_caller_passes(self):
        largest = replace(
            self.event(),
            trace_id='t' * 128,
            job_id='j' * 128,
            actor=self.authority.actor('orchestrator', 'o' * 63),  # the longest role
            subject='s' * 128,
            action='RESULT_REJECTED',
            decision='DENIED',
            reason_code='X' * 64,
            policy_version='p' * 128,
            constitution_version='c' * 128,
            handoff_sha256='0' * 64,
            payload_sha256='1' * 64,
            occurred_at=2 ** 31,
        )
        self.assertLess(len(largest.to_bytes()), 2048)

    def test_identifier_fields_are_bounded_but_are_not_a_secret_filter(self):
        recorded = self.event(trace_id=PAYLOAD_CANARY).to_dict()
        self.assertEqual(recorded['trace_id'], PAYLOAD_CANARY)
        with self.assertRaises(ContractError):
            self.event(trace_id=PAYLOAD_CANARY * 4)

    def test_non_ascii_identifiers_cannot_inflate_an_entry(self):
        with self.assertRaises(ContractError):
            self.event(trace_id='\U0001f600' * 128)
        self.assertLess(len(self.event(trace_id='\U0001f600' * 12).to_bytes()), 2048)

    def test_time_reference_has_an_upper_bound(self):
        with self.assertRaises(ContractError):
            self.event(occurred_at=10 ** 4000)
        with self.assertRaises(ContractError):
            self.event(occurred_at=4102444801)
        self.assertEqual(self.event(occurred_at=4102444800).occurred_at, 4102444800)

    def test_unhashable_action_or_decision_fails_as_a_contract_error(self):
        event = self.event()
        for bad in ([], {}, set()):
            for field in ('action', 'decision'):
                with self.subTest(field=field, bad=type(bad)), self.assertRaises(ContractError):
                    replace(event, **{field: bad})

    def test_a_mutated_payload_cannot_be_audited(self):
        self.handoff.payload['text'] = 'swapped after validation'
        with self.assertRaises(ContractError):
            self.event()

    def test_every_valid_handoff_stays_auditable(self):
        for job_id in ('j' * 200, 'job id with spaces', '\U0001f600' * 40):
            with self.subTest(job_id=job_id):
                self.handoff = self.issue_handoff({'text': 'ordinary'}, job_id=job_id)
                recorded = self.event(job_id_override=None).to_dict()
                self.assertTrue(recorded['job_id'].startswith('sha256:'))
                self.assertNotIn(' ', recorded['job_id'])
                self.assertLess(len(self.event(job_id_override=None).to_bytes()), 2048)

    def test_an_actor_cannot_be_claimed_without_the_audit_authority(self):
        for forged in ('gateway', 'gateway:gw-1', None, 42,
                       {'component': 'gateway', 'instance_id': 'gw-1'}):
            with self.subTest(forged=forged), self.assertRaises(ContractError):
                self.event(actor=forged)
        with self.assertRaises(ContractError):
            ComponentActor('gateway', 'gw-1')
        with self.assertRaises(ContractError):
            ComponentActor('gateway', 'gw-1', object())

    def test_an_event_cannot_replace_actor_capability_with_text(self):
        for forged in ('gateway:gw-1', 'orchestrator:root', 'admin:x', 'gateway',
                       '', None, 42, {'component': 'gateway', 'instance_id': 'gw-1'}):
            with self.subTest(forged=forged), self.assertRaises(ContractError):
                replace(self.event(), actor=forged)

    def test_the_recorded_actor_is_still_a_plain_string(self):
        """The field became a capability; the recorded entry must not follow.

        `to_dict()` is what gets hashed, chained and stored. If it emitted the
        actor object, every event hash would change and `canonical()` would be
        carrying a dataclass — so the type change has to stop at the boundary.
        """
        event = self.event()
        self.assertEqual(event.to_dict()['actor'], 'gateway:gw-1')
        self.assertIs(type(event.to_dict()['actor']), str)
        self.assertIn(b'"actor":"gateway:gw-1"', event.to_bytes())

    def test_an_authority_only_mints_constitutional_roles(self):
        for component in ('orchestrator', 'gateway', 'worker', 'monitor', 'forensics'):
            self.assertEqual(self.authority.actor(component, 'i-1').component, component)
        for component in ('admin', 'Gateway', '', None, []):
            with self.subTest(component=component), self.assertRaises(ContractError):
                self.authority.actor(component, 'i-1')
        for instance in ('UPPER', 'has space', 'x' * 64, '', None, 7):
            with self.subTest(instance=instance), self.assertRaises(ContractError):
                self.authority.actor('gateway', instance)

    def test_an_audit_authority_needs_a_real_key(self):
        for bad in (b'too-short', 'not-bytes', None, 32):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                AuditAuthority(audit_key=bad)

    def test_event_hash_is_deterministic_and_binds_every_field(self):
        self.assertEqual(self.event().event_sha256(), self.event().event_sha256())
        baseline = self.event().event_sha256()
        self.assertNotEqual(self.event(decision='DENIED').event_sha256(), baseline)
        self.assertNotEqual(self.event(occurred_at=102).event_sha256(), baseline)
        self.assertNotEqual(
            self.event(actor=self.authority.actor('monitor', 'mon-1')).event_sha256(), baseline)

    def test_two_handoffs_differing_only_in_payload_produce_different_events(self):
        other = self.handoff
        self.handoff = self.issue_handoff({'text': 'Please summarise something else'})
        changed = self.event()
        self.handoff = other
        self.assertNotEqual(changed.payload_sha256, self.event().payload_sha256)
        self.assertNotEqual(changed.handoff_sha256, self.event().handoff_sha256)


class UncoveredRefusalsTest(EventFixture, unittest.TestCase):
    """One test per refusal `scripts/refusals.py` found nothing covering."""

    def test_deriving_an_event_from_something_that_is_not_a_handoff_fails_closed(self):
        """Without the guard this reaches `handoff.payload` and escapes as AttributeError."""
        for handoff in (None, 'handoff', 42, {}, self.handoff.to_dict()
                        if hasattr(self.handoff, 'to_dict') else {'job_id': 'j'},
                        self.handoff.to_bytes()):
            with self.subTest(handoff=type(handoff)), self.assertRaises(ContractError):
                event_from_handoff(handoff, trace_id='trace-demo', actor=self.actor,
                                   action='HANDOFF_ADMITTED', decision='ALLOWED',
                                   reason_code='POLICY_SATISFIED', occurred_at=101)

    def test_a_non_string_handoff_identifier_fails_closed(self):
        """`_audit_safe` falls back to a digest, which needs a string to digest.

        The handoff contract keeps these as strings, so this is only reachable
        through a `Handoff` built directly. Without the guard the fallback path
        calls `.encode()` on whatever it was given and escapes as AttributeError
        — a refusal type the audit boundary is not supposed to emit.
        """
        from dataclasses import replace
        for field in ('job_id', 'user_id', 'policy_version'):
            for value in (42, None, b'bytes', ['list']):
                broken = replace(self.handoff, **{field: value})
                with self.subTest(field=field, value=type(value)):
                    with self.assertRaises(ContractError):
                        event_from_handoff(broken, trace_id='trace-demo', actor=self.actor,
                                           action='HANDOFF_ADMITTED', decision='ALLOWED',
                                           reason_code='POLICY_SATISFIED', occurred_at=101)


if __name__ == '__main__':
    unittest.main()
