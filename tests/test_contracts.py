import hashlib
import hmac
import json
from pathlib import Path
import unittest

from geniusnew.contracts import (ContractError, Grant, HandoffSigner, HandoffVerifier, Policy,
                                 canonical, issue, validate)


class ContractsFixture:
    """Shared setup. Deliberately not a TestCase.

    Subclassing a TestCase to reuse its fixture re-runs every one of its tests
    inside the subclass, which silently doubles the suite and inflates any count
    taken from it.
    """

    def setUp(self):
        self.key = HandoffSigner(integrity_key=b'phase-1-test-integrity-key-32bytes')
        self.grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                           ('summarize',), 'isolated', False)
        self.policy = Policy('policy-v1', 'orchestrator-demo', 60,
                             ('summarize',), ('isolated',), (self.grant,))
        self.request = {'text': 'Example text'}

    def issue(self, request=None, policy=None):
        return issue(self.request if request is None else request,
                     subject='subject-demo', job_id='job-demo',
                     policy=policy or self.policy, signer=self.key, now=100)

    def check(self, wire, **kw):
        args = dict(subject='subject-demo', job_id='job-demo',
                    policy=self.policy, verifier=self.key, now=101)
        args.update(kw)
        return validate(wire, **args)


class ContractsTest(ContractsFixture, unittest.TestCase):
    def test_round_trip_and_determinism(self):
        wire = self.issue()
        self.assertEqual(wire, self.issue())
        result = self.check(wire)
        self.assertEqual(result.user_id, 'user-demo')
        self.assertEqual(result.tools, ('summarize',))
        self.assertEqual(result.to_bytes(), wire)

    def test_canonical_vector(self):
        self.assertEqual(canonical({'z': 1, 'a': 'ä'}), b'{"a":"\\u00e4","z":1}')
        body = json.loads(self.issue())
        self.assertEqual(body['payload_sha256'], hashlib.sha256(b'{"text":"Example text"}').hexdigest())

    def test_schema_describes_the_runtime_handoff(self):
        schema_path = Path(__file__).parents[1] / 'schemas' / 'handoff-v2.schema.json'
        schema = json.loads(schema_path.read_text(encoding='utf-8'))
        body = json.loads(self.issue())
        self.assertEqual(set(schema['required']), set(body))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['version']['const'], body['version'])

    def test_schema_identifier_does_not_depend_on_a_domain(self):
        schema_path = Path(__file__).parents[1] / 'schemas' / 'handoff-v2.schema.json'
        schema = json.loads(schema_path.read_text(encoding='utf-8'))
        self.assertEqual(schema['$id'], 'urn:geniusnew:schema:handoff:v2')
        self.assertFalse(schema['$id'].startswith(('http://', 'https://')))

    def test_noncanonical_json_is_rejected_even_with_a_valid_signature(self):
        body = json.loads(self.issue())
        noncanonical = json.dumps(body, sort_keys=True).encode()
        self.assertNotEqual(noncanonical, self.issue())
        with self.assertRaises(ContractError):
            self.check(noncanonical)

    def test_client_cannot_select_security_fields(self):
        for key in ('tier', 'tools', 'sandbox_profile', 'worker_agent_id',
                    'approval_state', 'user_id', 'metadata', 'policy_version'):
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.issue({**self.request, key: 'attacker'})

    def test_strict_request(self):
        for value in ({}, {'text': 1}, {'text': None}, {'text': ''}, [], None):
            with self.subTest(value=value), self.assertRaises(ContractError):
                issue(value, subject='subject-demo', job_id='job-demo', policy=self.policy,
                      signer=self.key, now=100)

    def test_all_required_fields_and_unknown_fields(self):
        body = json.loads(self.issue())
        for key in body:
            bad = dict(body)
            del bad[key]
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.check(json.dumps(bad).encode())
        with self.assertRaises(ContractError):
            self.check(json.dumps({**body, 'extra': True}).encode())

    def test_tampering(self):
        body = json.loads(self.issue())
        changes = dict(user_id='other', worker_agent_id='other', tier='admin',
                       tools=['shell'], sandbox_profile='host', approval_state='APPROVED',
                       policy_version='other', orchestrator_id='other', job_id='other',
                       version='v2', payload_sha256='0' * 64, signature='0' * 64, payload={'text': 'tampered'})
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.check(json.dumps({**body, key: value}).encode())

    def test_strict_wire_types(self):
        body = json.loads(self.issue())
        for key, values in {'issued_at': [True, 100.0, '100'],
                            'expires_at': [False, 160.0, '160'],
                            'tools': ['summarize', ['summarize', 'summarize']],
                            'user_id': [None, 1, ''], 'payload': [[], {'text': 3}]}.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ContractError):
                    self.check(json.dumps({**body, key: value}).encode())

    def test_time_boundaries(self):
        wire = self.issue()
        self.check(wire, now=100)
        self.check(wire, now=159)
        for now in (99, 160, 161, True, 101.0):
            with self.subTest(now=now), self.assertRaises(ContractError):
                self.check(wire, now=now)
        body = json.loads(wire)
        for issued, expires in ((100, 100), (100, 99), (100, 161)):
            with self.assertRaises(ContractError):
                self.check(json.dumps({**body, 'issued_at': issued, 'expires_at': expires}).encode())

    def test_unknown_subject_and_wrong_job(self):
        for args in ({'subject': 'unknown'}, {'job_id': 'other'}):
            with self.assertRaises(ContractError):
                self.check(self.issue(), **args)
        with self.assertRaises(ContractError):
            issue(self.request, subject='unknown', job_id='job-demo', policy=self.policy,
                  signer=self.key, now=100)

    def test_unicode_subject_is_exactly_bound(self):
        unicode_grant = Grant('subjekt-ü', 'user-ü', 'worker-ü', 'basic',
                              ('summarize',), 'isolated', False)
        policy = Policy('policy-v1', 'orchestrator-demo', 60,
                        ('summarize',), ('isolated',), (unicode_grant,))
        wire = issue(self.request, subject='subjekt-ü', job_id='job-demo',
                     policy=policy, signer=self.key, now=100)
        result = validate(wire, subject='subjekt-ü', job_id='job-demo',
                          policy=policy, verifier=self.key, now=101)
        self.assertEqual(result.user_id, 'user-ü')
        with self.assertRaises(ContractError):
            issue(self.request, subject='subjekt-u\u0308', job_id='job-demo',
                  policy=policy, signer=self.key, now=100)

    def test_unicode_grants_do_not_break_other_subjects(self):
        unicode_grant = Grant('subjekt-ü', 'user-ü', 'worker-ü', 'basic',
                              ('summarize',), 'isolated', False)
        policy = Policy('policy-v1', 'orchestrator-demo', 60,
                        ('summarize',), ('isolated',), (unicode_grant, self.grant))
        self.assertEqual(
            validate(self.issue(policy=policy), subject='subject-demo', job_id='job-demo',
                     policy=policy, verifier=self.key, now=101).user_id,
            'user-demo',
        )

    def test_invalid_unicode_subject_fails_closed(self):
        with self.assertRaises(ContractError):
            Grant('\ud800', 'user-demo', 'worker-demo', 'basic', ('summarize',), 'isolated', False)
        with self.assertRaises(ContractError):
            self.policy.grant_for('\ud800')

    def test_pending_cannot_cross_gateway(self):
        pending = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                        ('summarize',), 'isolated', True)
        policy = Policy('policy-v1', 'orchestrator-demo', 60, ('summarize',), ('isolated',), (pending,))
        wire = self.issue(policy=policy)
        self.assertEqual(json.loads(wire)['approval_state'], 'PENDING_APPROVAL')
        with self.assertRaises(ContractError):
            self.check(wire, policy=policy)
        body = json.loads(wire)
        body['approval_state'] = 'NOT_REQUIRED'
        with self.assertRaises(ContractError):
            self.check(json.dumps(body).encode(), policy=policy)

    def test_signature_binds_every_field(self):
        body = json.loads(self.issue())
        body['payload'] = {'text': 'attacker text'}
        body['payload_sha256'] = hashlib.sha256(canonical(body['payload'])).hexdigest()
        with self.assertRaises(ContractError):
            self.check(json.dumps(body).encode())
        with self.assertRaises(ContractError):
            self.check(self.issue(), verifier=HandoffSigner(integrity_key=b'x' * 32))

    def test_parser_rejects_ambiguous_or_nonserialized_input(self):
        for wire in (self.check(self.issue()), {}, 'text', b'{}', b'null', b'[]',
                     b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b'[' * 2000):
            with self.subTest(kind=type(wire)), self.assertRaises(ContractError):
                self.check(wire)

    def test_parser_rejects_deep_nesting_instead_of_crashing(self):
        for wire in (b'[' * 2000, b'[' * 2000 + b']' * 2000,
                     b'{"a":' * 500 + b'1' + b'}' * 500):
            with self.subTest(size=len(wire)), self.assertRaises(ContractError):
                self.check(wire)

    def test_bracket_characters_inside_payload_text_stay_valid(self):
        text = '[' * 100 + '{"nested": "value"}' + ']' * 100 + ' back\\slash'
        wire = self.issue({'text': text})
        self.assertEqual(self.check(wire).payload['text'], text)

    def test_policy_configuration_fails_closed(self):
        for tools, profiles, grants in (((), ('isolated',), (self.grant,)),
                                       (('summarize',), (), (self.grant,)),
                                       (('summarize',), ('isolated',), (self.grant, self.grant))):
            with self.assertRaises(ContractError):
                Policy('policy-v1', 'orchestrator-demo', 60, tools, profiles, grants)
        with self.assertRaises(ContractError):
            Grant('subject-demo', 'user-demo', 'worker-demo', 'admin', (), 'isolated', False)


class UncoveredRefusalsTest(ContractsFixture, unittest.TestCase):
    """One test per refusal that `scripts/refusals.py` found nothing covering.

    Each of these checks was doing its job — none was redundant. They simply
    had no test, so deleting any of them left the suite green. Several assert
    on the refusal message, because a later check would refuse the same input
    anyway and only the message distinguishes which one fired.
    """

    def test_a_grant_field_may_not_be_empty(self):
        for position in range(4):
            fields = ['subject-x', 'user-x', 'worker-x', 'basic']
            fields[position] = ''
            with self.subTest(position=position), self.assertRaises(ContractError):
                Grant(*fields, ('summarize',), 'isolated', False)
        with self.assertRaises(ContractError):
            Grant('subject-x', 'user-x', 'worker-x', 'basic', ('summarize',), '', False)

    def test_tools_may_not_repeat(self):
        with self.assertRaises(ContractError):
            Grant('subject-x', 'user-x', 'worker-x', 'basic',
                  ('summarize', 'summarize'), 'isolated', False)

    def test_a_short_integrity_key_is_refused(self):
        self.issue()  # sanity: the good key still works
        for key in (b'', b'short', b'x' * 31, 'x' * 32, None):
            with self.subTest(key=repr(key)[:12]), self.assertRaisesRegex(
                    ContractError, 'integrity_key'):
                HandoffSigner(integrity_key=key)

    def test_only_a_signer_can_issue(self):
        """Raw key bytes and the public half are both refused at issue time."""
        for signer in (b'phase-1-test-integrity-key-32bytes', self.key.verifier(), None):
            with self.subTest(signer=type(signer)), self.assertRaisesRegex(
                    ContractError, 'signer must be a HandoffSigner'):
                issue(self.request, subject='subject-demo', job_id='job-demo',
                      policy=self.policy, signer=signer, now=100)

    def test_the_public_half_verifies_and_cannot_sign(self):
        verifier = self.key.verifier()
        self.assertEqual(self.check(self.issue(), verifier=verifier).user_id, 'user-demo')
        self.assertFalse(hasattr(verifier, 'sign'))
        self.assertEqual(len(verifier.public_key), 32)
        self.assertNotIn(b'phase-1-test-integrity-key-32bytes', vars(verifier).values())
        for key in (b'', b'x' * 31, b'x' * 33, 'x' * 32, None):
            with self.subTest(key=repr(key)[:12]), self.assertRaisesRegex(
                    ContractError, 'public_key must be 32 bytes'):
                HandoffVerifier(public_key=key)
        for bad in (b'phase-1-test-integrity-key-32bytes', None, 'verifier'):
            with self.subTest(verifier=type(bad)), self.assertRaisesRegex(
                    ContractError, 'verifier must be a HandoffVerifier'):
                self.check(self.issue(), verifier=bad)

    def test_the_signer_signs_only_bytes(self):
        """A str or dict would be encoded some other way than the wire it claims."""
        self.assertEqual(len(self.key.sign(b'message')), 64)
        for message in ('message', {'text': 'x'}, None, bytearray(b'message')):
            with self.subTest(message=type(message)), self.assertRaisesRegex(
                    ContractError, 'message must be bytes'):
                self.key.sign(message)

    def test_a_handoff_signed_with_hmac_is_refused(self):
        """Version 1 was HMAC. A v1-shaped handoff, correctly MAC'd, is not a v2 one."""
        body = json.loads(self.issue())
        del body['signature']
        body['signature'] = hmac.new(b'phase-1-test-integrity-key-32bytes', canonical(body),
                                     hashlib.sha256).hexdigest()
        with self.assertRaisesRegex(ContractError, 'handoff signature is invalid'):
            self.check(canonical(body))
        body['version'] = 'geniusnew-handoff-v1'
        with self.assertRaises(ContractError):
            self.check(canonical(body))

    def test_a_signature_does_not_transfer_to_another_handoff(self):
        first = json.loads(self.issue())
        second = json.loads(issue({'text': 'other text'}, subject='subject-demo',
                                  job_id='job-demo', policy=self.policy,
                                  signer=self.key, now=100))
        second['signature'] = first['signature']
        with self.assertRaisesRegex(ContractError, 'handoff signature is invalid'):
            self.check(canonical(second))

    def test_a_malformed_signature_is_refused_before_it_is_decoded(self):
        body = json.loads(self.issue())
        for signature in ('0' * 64, 'zz' * 64, body['signature'].upper(), body['signature'][:-2],
                          body['signature'] + '00'):
            with self.subTest(signature=signature[:8]):
                body['signature'] = signature
                with self.assertRaisesRegex(ContractError, 'handoff signature is invalid'):
                    self.check(canonical(body))

    def test_requires_approval_must_be_a_boolean(self):
        for value in (1, 0, 'true', None, []):
            with self.subTest(value=value), self.assertRaises(ContractError):
                Grant('subject-x', 'user-x', 'worker-x', 'basic',
                      ('summarize',), 'isolated', value)

    def test_the_handoff_ttl_is_bounded_at_both_ends(self):
        for ttl in (0, -1, 301, 10 ** 6):
            with self.subTest(ttl=ttl), self.assertRaises(ContractError):
                Policy('policy-v1', 'orchestrator-demo', ttl,
                       ('summarize',), ('isolated',), (self.grant,))
        self.assertEqual(Policy('policy-v1', 'orchestrator-demo', 300, ('summarize',),
                                ('isolated',), (self.grant,)).handoff_ttl_seconds, 300)

    def test_a_policy_needs_real_grants(self):
        for grants in ((), [], None, 'grants', ('not-a-grant',), (self.grant, 'x')):
            with self.subTest(grants=repr(grants)[:30]), self.assertRaises(ContractError):
                Policy('policy-v1', 'orchestrator-demo', 60,
                       ('summarize',), ('isolated',), grants)

    def test_a_grant_may_not_exceed_the_policy_allow_lists(self):
        """The privilege-escalation check: a grant cannot hand out what policy withholds."""
        wider_tools = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                            ('summarize', 'exfiltrate'), 'isolated', False)
        wider_sandbox = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                              ('summarize',), 'wide-open', False)
        for grant in (wider_tools, wider_sandbox):
            with self.subTest(grant=grant.tools + (grant.sandbox_profile,)):
                with self.assertRaises(ContractError):
                    Policy('policy-v1', 'orchestrator-demo', 60,
                           ('summarize',), ('isolated',), (grant,))

    def test_nesting_past_the_limit_is_refused_by_the_depth_check(self):
        """Depth between the limit and the recursion limit — nothing else catches it.

        The existing nesting test uses wires so deep or so malformed that the
        JSON parser refuses them first, so the depth guard itself was never
        reached. This asserts the message to pin which refusal fired.
        """
        wire = b'{"a":' * 6 + b'1' + b'}' * 6
        with self.assertRaisesRegex(ContractError, 'nests too deeply'):
            self.check(wire)

    def test_duplicate_keys_are_refused_by_their_own_check(self):
        with self.assertRaisesRegex(ContractError, 'duplicate keys'):
            self.check(b'{"job_id":"a","job_id":"b"}')

    def test_a_payload_that_does_not_match_its_digest_is_refused(self):
        """A validly signed handoff whose payload_sha256 is simply wrong.

        Nothing downstream compares the payload to its digest, so without this
        check the handoff is accepted and everything built on `payload_sha256`
        — the audit entry, the result binding — describes something else.
        """
        from geniusnew.contracts import _signature
        body = json.loads(self.issue())
        body['payload_sha256'] = '0' * 64
        del body['signature']
        body['signature'] = _signature(body, self.key)
        with self.assertRaisesRegex(ContractError, 'payload hash'):
            self.check(canonical(body))

    def test_a_policy_argument_that_is_not_a_policy_fails_closed(self):
        """Without the guard this reaches `policy.grant_for` and escapes as AttributeError."""
        wire = self.issue()
        for policy in (None, 'policy', 42, {}, self.grant):
            with self.subTest(policy=type(policy)):
                with self.assertRaises(ContractError):
                    self.check(wire, policy=policy)
                with self.assertRaises(ContractError):
                    issue(self.request, subject='subject-demo', job_id='job-demo',
                          policy=policy, signer=self.key, now=100)


if __name__ == '__main__':
    unittest.main()
