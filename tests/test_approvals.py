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


if __name__ == '__main__':
    unittest.main()
