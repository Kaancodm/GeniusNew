import hashlib
import json
from pathlib import Path
import unittest

from geniusnew.contracts import ContractError, Grant, Policy, canonical, issue, validate


class ContractsTest(unittest.TestCase):
    def setUp(self):
        self.key = b'phase-1-test-integrity-key-32bytes'
        self.grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                           ('summarize',), 'isolated', False)
        self.policy = Policy('policy-v1', 'orchestrator-demo', 60,
                             ('summarize',), ('isolated',), (self.grant,))
        self.request = {'text': 'Example text'}

    def issue(self, request=None, policy=None):
        return issue(self.request if request is None else request,
                     subject='subject-demo', job_id='job-demo',
                     policy=policy or self.policy, integrity_key=self.key, now=100)

    def check(self, wire, **kw):
        args = dict(subject='subject-demo', job_id='job-demo',
                    policy=self.policy, integrity_key=self.key, now=101)
        args.update(kw)
        return validate(wire, **args)

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
        schema_path = Path(__file__).parents[1] / 'schemas' / 'handoff-v1.schema.json'
        schema = json.loads(schema_path.read_text(encoding='utf-8'))
        body = json.loads(self.issue())
        self.assertEqual(set(schema['required']), set(body))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['version']['const'], body['version'])

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
                      integrity_key=self.key, now=100)

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
                  integrity_key=self.key, now=100)

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
            self.check(self.issue(), integrity_key=b'x' * 32)

    def test_parser_rejects_ambiguous_or_nonserialized_input(self):
        for wire in (self.check(self.issue()), {}, 'text', b'{}', b'null', b'[]',
                     b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b'[' * 2000):
            with self.subTest(kind=type(wire)), self.assertRaises(ContractError):
                self.check(wire)

    def test_policy_configuration_fails_closed(self):
        for tools, profiles, grants in (((), ('isolated',), (self.grant,)),
                                       (('summarize',), (), (self.grant,)),
                                       (('summarize',), ('isolated',), (self.grant, self.grant))):
            with self.assertRaises(ContractError):
                Policy('policy-v1', 'orchestrator-demo', 60, tools, profiles, grants)
        with self.assertRaises(ContractError):
            Grant('subject-demo', 'user-demo', 'worker-demo', 'admin', (), 'isolated', False)


if __name__ == '__main__':
    unittest.main()
