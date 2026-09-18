import unittest
from threading import Barrier, Thread

from geniusnew.approvals import ApprovalScope, ApprovalStore, create_scope
from geniusnew.contracts import ContractError, Grant, Policy, issue


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.key = b'phase-2-test-integrity-key-32bytes'
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'high',
                      ('summarize',), 'isolated', True)
        self.policy = Policy('policy-v1', 'orchestrator-demo', 60,
                             ('summarize',), ('isolated',), (grant,))
        self.wire = issue({'text': 'Requires approval'}, subject='subject-demo',
                          job_id='job-demo', policy=self.policy,
                          integrity_key=self.key, now=100)

    def scope(self, wire=None, **kw):
        args = dict(subject='subject-demo', job_id='job-demo', policy=self.policy,
                    integrity_key=self.key, now=101)
        args.update(kw)
        return create_scope(self.wire if wire is None else wire, **args)

    def test_scope_binds_pending_handoff_facts(self):
        scope = self.scope()
        self.assertEqual(scope.job_id, 'job-demo')
        self.assertEqual(scope.user_id, 'user-demo')
        self.assertEqual(scope.action, 'EXECUTE_HANDOFF')
        self.assertEqual(scope.policy_version, 'policy-v1')
        self.assertEqual(scope.risk_tier, 'high')

    def test_scope_rejects_non_pending_and_tampered_handoffs(self):
        no_approval = Policy('policy-v1', 'orchestrator-demo', 60, ('summarize',),
                             ('isolated',), (Grant('subject-demo', 'user-demo', 'worker-demo',
                                                    'high', ('summarize',), 'isolated', False),))
        wire = issue({'text': 'No approval'}, subject='subject-demo', job_id='job-demo',
                     policy=no_approval, integrity_key=self.key, now=100)
        with self.assertRaises(ContractError):
            create_scope(wire, subject='subject-demo', job_id='job-demo', policy=no_approval,
                         integrity_key=self.key, now=101)
        with self.assertRaises(ContractError):
            self.scope(self.wire.replace(b'Requires', b'Attacker'))

    def test_grant_and_consume_are_exactly_bound_and_single_use(self):
        store = ApprovalStore(token_source=lambda: b'a' * 32)
        scope = self.scope()
        grant = store.grant(scope, now=101, ttl_seconds=60)
        receipt = store.consume(grant.token, scope, now=159)
        self.assertEqual(receipt.scope, scope)
        self.assertEqual(receipt.state, 'CONSUMED')
        self.assertNotEqual(receipt.record_hash, grant.record_hash)
        with self.assertRaises(ContractError):
            store.consume(grant.token, scope, now=159)
        other_scope = ApprovalScope(scope.handoff_sha256, scope.handoff_expires_at, 'other-job', scope.user_id,
                                    scope.worker_agent_id, scope.risk_tier,
                                    scope.policy_version, scope.action)
        with self.assertRaises(ContractError):
            store.consume(b'a' * 32, other_scope, now=102)

    def test_grant_requires_a_scope_created_from_a_signed_handoff(self):
        verified = self.scope()
        forged = ApprovalScope('0' * 64, verified.handoff_expires_at, 'job-demo',
                               'victim', verified.worker_agent_id, verified.risk_tier,
                               verified.policy_version, verified.action)
        with self.assertRaises(ContractError):
            ApprovalStore().grant(forged, now=101, ttl_seconds=60)
        rehydrated = ApprovalScope(verified.handoff_sha256, verified.handoff_expires_at,
                                   verified.job_id, verified.user_id, verified.worker_agent_id,
                                   verified.risk_tier, verified.policy_version, verified.action)
        self.assertEqual(rehydrated, verified)
        store = ApprovalStore()
        grant = store.grant(verified, now=101, ttl_seconds=60)
        self.assertEqual(store.consume(grant.token, rehydrated, now=102).state, 'CONSUMED')

    def test_expiry_ttl_and_token_fail_closed(self):
        scope = self.scope()
        for ttl in (0, 601, True, 1.0):
            with self.subTest(ttl=ttl), self.assertRaises(ContractError):
                ApprovalStore(token_source=lambda: b'b' * 32).grant(scope, now=101,
                                                                       ttl_seconds=ttl)
        store = ApprovalStore(token_source=lambda: b'b' * 32)
        grant = store.grant(scope, now=101, ttl_seconds=1)
        with self.assertRaises(ContractError):
            store.consume(grant.token, scope, now=102)
        with self.assertRaises(ContractError):
            store.consume(b'c' * 32, scope, now=101)
        with self.assertRaises(ContractError):
            ApprovalStore(token_source=lambda: b'short').grant(scope, now=101,
                                                                 ttl_seconds=60)

    def test_token_expiry_cannot_outlive_handoff(self):
        scope = self.scope()
        store = ApprovalStore(token_source=lambda: b'e' * 32)
        grant = store.grant(scope, now=101, ttl_seconds=600)
        self.assertEqual(grant.expires_at, scope.handoff_expires_at)
        with self.assertRaises(ContractError):
            store.consume(grant.token, scope, now=scope.handoff_expires_at)

    def test_revoke_prevents_consumption_and_rehashes_record(self):
        store = ApprovalStore(token_source=lambda: b'd' * 32)
        scope = self.scope()
        grant = store.grant(scope, now=101, ttl_seconds=60)
        revoked = store.revoke(grant.token, scope, now=102)
        self.assertEqual(revoked.state, 'REVOKED')
        self.assertNotEqual(revoked.record_hash, grant.record_hash)
        with self.assertRaises(ContractError):
            store.consume(grant.token, scope, now=102)

    def test_parallel_consumption_has_exactly_one_winner(self):
        store = ApprovalStore(token_source=lambda: b'f' * 32)
        scope = self.scope()
        grant = store.grant(scope, now=101, ttl_seconds=60)
        barrier = Barrier(2)
        outcomes = []

        def consume():
            barrier.wait()
            try:
                outcomes.append(store.consume(grant.token, scope, now=102).state)
            except ContractError:
                outcomes.append('REJECTED')

        threads = [Thread(target=consume), Thread(target=consume)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ['CONSUMED', 'REJECTED'])

    def test_grant_representation_does_not_include_token(self):
        grant = ApprovalStore(token_source=lambda: b'g' * 32).grant(
            self.scope(), now=101, ttl_seconds=60
        )
        self.assertNotIn("b'g'", repr(grant))


class UncoveredApprovalRefusalsTest(ApprovalTest):
    """One test per refusal that `scripts/refusals.py` found nothing covering."""

    def raw_scope(self, **over):
        fields = dict(handoff_sha256='a' * 64, handoff_expires_at=160, job_id='job-demo',
                      user_id='user-demo', worker_agent_id='worker-demo', risk_tier='high',
                      policy_version='policy-v1', action='EXECUTE_HANDOFF')
        fields.update(over)
        return ApprovalScope(**fields)

    def test_a_scope_field_may_not_be_empty(self):
        for field in ('handoff_sha256', 'job_id', 'user_id', 'worker_agent_id',
                      'risk_tier', 'policy_version', 'action'):
            for value in ('', None, 42):
                with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                    self.raw_scope(**{field: value})

    def test_the_handoff_expiry_in_a_scope_must_be_positive(self):
        for expires_at in (0, -1, -10 ** 6):
            with self.subTest(expires_at=expires_at), self.assertRaises(ContractError):
                self.raw_scope(handoff_expires_at=expires_at)

    def test_the_scope_digest_must_be_a_digest(self):
        for digest in ('z' * 64, 'A' * 64, 'abc', 'a' * 63, 'a' * 65):
            with self.subTest(digest=digest[:8]), self.assertRaises(ContractError):
                self.raw_scope(handoff_sha256=digest)

    def test_a_scope_may_only_authorize_the_one_action(self):
        for action in ('EXECUTE', 'execute_handoff', 'DELETE_EVERYTHING', 'ANY'):
            with self.subTest(action=action), self.assertRaises(ContractError):
                self.raw_scope(action=action)

    def test_granting_needs_a_scope_object_at_all(self):
        """Without the guard this reaches `scope.origin` and escapes as AttributeError."""
        store = ApprovalStore()
        for scope in (None, 'scope', 42, {}, self.scope().to_dict()):
            with self.subTest(scope=type(scope)), self.assertRaises(ContractError):
                store.grant(scope, now=101, ttl_seconds=60)

    def test_an_expired_handoff_cannot_be_granted_an_approval(self):
        scope = self.scope()
        store = ApprovalStore()
        for now in (scope.handoff_expires_at, scope.handoff_expires_at + 1):
            with self.subTest(now=now), self.assertRaises(ContractError):
                store.grant(scope, now=now, ttl_seconds=60)
        self.assertTrue(store.grant(scope, now=scope.handoff_expires_at - 1,
                                    ttl_seconds=60).token)

    def test_a_repeated_token_is_refused_rather_than_overwriting_a_record(self):
        """A token source that repeats itself must not silently replace a grant."""
        fixed = b'the-same-token-bytes-every-time!!'
        store = ApprovalStore(token_source=lambda: fixed)
        store.grant(self.scope(), now=101, ttl_seconds=60)
        with self.assertRaisesRegex(ContractError, 'collision'):
            store.grant(self.scope(), now=101, ttl_seconds=60)

    def test_consuming_fails_closed_on_a_malformed_token_or_scope(self):
        scope = self.scope()
        store = ApprovalStore()
        grant = store.grant(scope, now=101, ttl_seconds=60)
        for token in (None, 'token', 42, b'', b'too-short'):
            with self.subTest(token=repr(token)[:20]), self.assertRaises(ContractError):
                store.consume(token, scope, now=102)
        for bad_scope in (None, 'scope', 42, {}, scope.to_dict()):
            with self.subTest(scope=type(bad_scope)), self.assertRaises(ContractError):
                store.consume(grant.token, bad_scope, now=102)
        self.assertTrue(store.consume(grant.token, scope, now=102))

    def test_a_token_cannot_be_consumed_against_a_different_scope(self):
        """The binding that stops an approval for one handoff authorizing another."""
        store = ApprovalStore()
        grant = store.grant(self.scope(), now=101, ttl_seconds=60)
        other_wire = issue({'text': 'A different job'}, subject='subject-demo',
                           job_id='job-other', policy=self.policy,
                           integrity_key=self.key, now=100)
        other_scope = self.scope(wire=other_wire, job_id='job-other')
        self.assertNotEqual(other_scope.handoff_sha256, self.scope().handoff_sha256)
        with self.assertRaisesRegex(ContractError, 'scope'):
            store.consume(grant.token, other_scope, now=102)
        self.assertTrue(store.consume(grant.token, self.scope(), now=102))

    def test_a_token_cannot_be_consumed_before_it_was_issued(self):
        scope = self.scope()
        store = ApprovalStore()
        grant = store.grant(scope, now=110, ttl_seconds=60)
        for now in (109, 0, -1):
            with self.subTest(now=now), self.assertRaises(ContractError):
                store.consume(grant.token, scope, now=now)
        self.assertTrue(store.consume(grant.token, scope, now=110))


if __name__ == '__main__':
    unittest.main()
