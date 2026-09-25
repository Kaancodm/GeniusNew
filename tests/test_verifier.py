import hashlib
import unittest
from unittest import mock

from geniusnew import verifier as verifier_module
from geniusnew.approvals import ApprovalStore, create_scope
from geniusnew.audit import AuditAuthority, event_from_handoff
from geniusnew.contracts import (ContractError, Grant, HandoffSigner, Policy, issue, validate,
                                 validate_pending)
from geniusnew.gateway import Gateway
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority, accept, produce
from geniusnew.verifier import ACTIONS, REJECTIONS, Acceptance, Rejected, ResultVerifier
from geniusnew.workers import DeterministicSummarizer, WorkerRunner

ROOT_SECRET = b'a-verifier-test-root-secret-32b!!!!!'

DEFAULT = object()


class Fixture:
    """Shared setup. A mixin, not a TestCase: subclassing one re-runs its suite."""

    def setUp(self):
        self.keys = derive_keys(ROOT_SECRET)
        self.signer = HandoffSigner(integrity_key=self.keys.integrity_key)
        self.worker_authority = WorkerAuthority(result_key=self.keys.result_key,
                                                integrity_key=self.keys.integrity_key)
        self.policy = self.policy_for()
        self.verifier = self.verifier_for()
        self.wire = self.issue()
        self.handoff = validate(self.wire, subject='subject-demo', job_id='job-demo',
                                policy=self.policy, verifier=self.signer,
                                now=101)

    def policy_for(self, *, requires_approval=False, tools=('summarize',),
                   worker_agent_id='worker-demo'):
        grant = Grant('subject-demo', 'user-demo', worker_agent_id, 'basic',
                      tools, 'isolated', requires_approval)
        return Policy('policy-v1', 'orchestrator-demo', 60,
                      ('summarize', 'translate'), ('isolated',), (grant,))

    def verifier_for(self, *, verifier_id='verifier-1', handoff_verifier=DEFAULT,
                     worker_verifier=DEFAULT):
        return ResultVerifier(
            verifier_id=verifier_id,
            handoff_verifier=(self.signer.verifier() if handoff_verifier is DEFAULT
                              else handoff_verifier),
            worker_verifier=(self.worker_authority.verifier() if worker_verifier is DEFAULT
                             else worker_verifier))

    def issue(self, *, policy=DEFAULT, job_id='job-demo', text='the quick brown fox',
              now=100):
        return issue({'text': text}, subject='subject-demo', job_id=job_id,
                     policy=self.policy if policy is DEFAULT else policy,
                     signer=self.signer, now=now)

    def result_for(self, handoff=DEFAULT, *, output=DEFAULT, status='SUCCEEDED',
                   reason_code='WORK_COMPLETED', now=110, authority=DEFAULT):
        return produce(
            {'text': 'a summary'} if output is DEFAULT else output,
            handoff=self.handoff if handoff is DEFAULT else handoff,
            status=status, reason_code=reason_code,
            authority=self.worker_authority if authority is DEFAULT else authority,
            now=now)

    def forge_result(self, handoff=DEFAULT, *, authority=DEFAULT, **overrides):
        """Sign a result body directly, past the checks `produce` applies.

        `produce` refuses to sign a result dated outside its handoff's life, so
        the only way to test that `accept` refuses one too is to build the wire
        the way an attacker with the key would.
        """
        from geniusnew.contracts import canonical
        from geniusnew.results import handoff_digest
        handoff = self.handoff if handoff is DEFAULT else handoff
        authority = self.worker_authority if authority is DEFAULT else authority
        body = {
            'version': 'geniusnew-result-v2',
            'job_id': handoff.job_id,
            'worker_agent_id': handoff.worker_agent_id,
            'handoff_sha256': handoff_digest(handoff),
            'status': 'SUCCEEDED',
            'reason_code': 'WORK_COMPLETED',
            'output': {'text': 'a summary'},
            'output_sha256': None,
            'produced_at': 110,
        }
        body.update(overrides)
        if body['output'] is not None and body['output_sha256'] is None:
            import hashlib as _hashlib
            body['output_sha256'] = _hashlib.sha256(
                canonical(body['output'])).hexdigest()
        body['signature'] = authority.sign(body)
        return canonical(body)

    def take(self, result_wire=DEFAULT, *, verifier=DEFAULT, handoff_wire=DEFAULT,
             subject='subject-demo', job_id='job-demo', policy=DEFAULT, now=120,
             approval_record_hash=None):
        return (self.verifier if verifier is DEFAULT else verifier).accept(
            self.result_for() if result_wire is DEFAULT else result_wire,
            handoff_wire=self.wire if handoff_wire is DEFAULT else handoff_wire,
            subject=subject, job_id=job_id,
            policy=self.policy if policy is DEFAULT else policy, now=now,
            approval_record_hash=approval_record_hash)

    def rejected(self, *args, **arguments):
        with self.assertRaises(Rejected) as caught:
            self.take(*args, **arguments)
        return caught.exception


class ResultVerifierTest(Fixture, unittest.TestCase):
    """Roadmap step 14: the instance that takes results and ran none of them."""

    # --- the path it exists for ---------------------------------------------

    def test_a_genuine_result_is_accepted_with_its_evidence(self):
        taken = self.take()
        self.assertIsInstance(taken, Acceptance)
        self.assertTrue(taken.succeeded)
        self.assertEqual(taken.job_id, 'job-demo')
        self.assertEqual(taken.worker_agent_id, 'worker-demo')
        self.assertEqual(taken.action, 'RESULT_ACCEPTED')
        self.assertEqual(taken.decision, 'ALLOWED')
        self.assertEqual(taken.reason_code, 'RESULT_VERIFIED')
        self.assertEqual(taken.occurred_at, 120)
        self.assertEqual(taken.result.output, {'text': 'a summary'})

    def test_the_whole_path_from_gateway_to_acceptance(self):
        """Gateway admits, worker runs, this instance takes it — no shared objects."""
        gateway = Gateway(gateway_id='gateway-1',
                          handoff_verifier=self.signer.verifier(),
                          approval_store=ApprovalStore())
        permit = gateway.admit(self.wire, subject='subject-demo', job_id='job-demo',
                               policy=self.policy, now=101)
        runner = WorkerRunner(DeterministicSummarizer(), authority=self.worker_authority)
        result_wire = runner.execute(permit, now=110)
        taken = self.take(result_wire)
        self.assertTrue(taken.succeeded)
        digest = hashlib.sha256('the quick brown fox'.encode()).hexdigest()
        self.assertEqual(taken.result.output,
                         {'text': f'4 words; sha256:{digest[:16]}'})

    def test_a_signed_failure_is_a_genuine_result_and_is_taken_as_one(self):
        """Accepting is about the artifact, not the outcome.

        Rejecting failures as invalid would let a worker bury its own by making
        them unacceptable, and the audit trail would show nothing at all.
        """
        taken = self.take(self.result_for(output=None, status='FAILED',
                                          reason_code='WORKER_FAILED'))
        self.assertEqual(taken.status, 'FAILED')
        self.assertFalse(taken.succeeded)
        self.assertIsNone(taken.result.output)

    # --- it takes wires, not decisions someone else already made ------------

    def test_an_in_memory_handoff_is_not_a_substitute_for_the_wire(self):
        """A validated Handoff is someone else's conclusion about those bytes."""
        for handoff_wire in (self.handoff, None, 42, 'wire', {}):
            with self.subTest(kind=type(handoff_wire).__name__):
                self.assertEqual(
                    self.rejected(handoff_wire=handoff_wire).reason_code,
                    'HANDOFF_NOT_VALID')

    def test_a_tampered_handoff_wire_is_refused_here_too(self):
        rejection = self.rejected(handoff_wire=self.wire.replace(b'quick', b'QUICK'))
        self.assertEqual(rejection.reason_code, 'HANDOFF_NOT_VALID')

    def test_a_handoff_for_another_policy_or_subject_is_refused(self):
        other = self.policy_for(worker_agent_id='worker-other')
        self.assertEqual(self.rejected(policy=other).reason_code, 'HANDOFF_NOT_VALID')
        self.assertEqual(self.rejected(job_id='job-other').reason_code,
                         'HANDOFF_NOT_VALID')
        # A subject the policy does not know is a verdict about the job, not a
        # complaint about the call, so it carries a code and can be audited.
        unknown = self.rejected(subject='subject-unknown')
        self.assertEqual(unknown.reason_code, 'HANDOFF_NOT_VALID')
        self.assertIn('not authorized', str(unknown))

    def test_a_result_that_does_not_answer_this_handoff_is_refused(self):
        other_wire = self.issue(job_id='job-other')
        other = validate(other_wire, subject='subject-demo', job_id='job-other',
                         policy=self.policy, verifier=self.signer,
                         now=101)
        rejection = self.rejected(self.result_for(other))
        self.assertEqual(rejection.reason_code, 'RESULT_NOT_VALID')
        self.assertIn('does not match', str(rejection))

    def test_a_forged_signature_is_refused(self):
        forged = WorkerAuthority(result_key=b'a-forged-result-key-of-32-bytes!')
        rejection = self.rejected(self.result_for(authority=forged))
        self.assertEqual(rejection.reason_code, 'RESULT_NOT_VALID')
        self.assertIn('signature', str(rejection))

    # --- exactly once --------------------------------------------------------

    def test_the_same_result_cannot_be_accepted_twice(self):
        """The gap `results.py` records: a contract holds no state, this does."""
        wire = self.result_for()
        self.assertTrue(self.take(wire).succeeded)
        rejection = self.rejected(wire)
        self.assertEqual(rejection.reason_code, 'RESULT_ALREADY_ACCEPTED')

    def test_the_contract_alone_would_take_it_twice(self):
        """Evidence that the state is what closes it, not the signature checks.

        `accept` is a pure function of its arguments, so it says yes as often as
        it is asked. Without this test the one-time rule above could be read as
        something the contract already provided.
        """
        wire = self.result_for()
        for _ in range(2):
            self.assertTrue(accept(wire, handoff=self.handoff,
                                   verifier=self.worker_authority, now=120).succeeded)

    def test_a_second_different_result_for_one_handoff_is_refused(self):
        self.take(self.result_for())
        rejection = self.rejected(self.result_for(output=None, status='FAILED',
                                                  reason_code='WORKER_FAILED'))
        self.assertEqual(rejection.reason_code, 'RESULT_ALREADY_ACCEPTED')

    def test_a_rejected_result_does_not_spend_the_one_acceptance(self):
        """Otherwise the anti-replay rule becomes a denial of service.

        Anyone who can reach this instance could present a forged result first
        and lock out the genuine one for good.
        """
        forged = WorkerAuthority(result_key=b'a-forged-result-key-of-32-bytes!')
        for attempt in (self.result_for(authority=forged),
                        self.result_for()[:-1],
                        b'{}'):
            with self.subTest(attempt=repr(attempt)[:30]):
                with self.assertRaises(Rejected):
                    self.take(attempt)
        self.assertTrue(self.take().succeeded)

    def test_a_handoff_rejected_before_the_result_spends_nothing_either(self):
        with self.assertRaises(Rejected):
            self.take(handoff_wire=self.wire.replace(b'quick', b'QUICK'))
        self.assertTrue(self.take().succeeded)

    def test_another_job_is_unaffected_by_one_acceptance(self):
        self.take()
        other_wire = self.issue(job_id='job-other')
        other = validate(other_wire, subject='subject-demo', job_id='job-other',
                         policy=self.policy, verifier=self.signer,
                         now=101)
        self.assertTrue(self.take(self.result_for(other), handoff_wire=other_wire,
                                  job_id='job-other').succeeded)

    def test_the_acceptance_ledger_is_bounded(self):
        with mock.patch.object(verifier_module, '_MAX_ACCEPTED', 0):
            rejection = self.rejected()
        self.assertEqual(rejection.reason_code, 'ACCEPTANCE_LEDGER_FULL')
        # And with room again, the same result is still takeable: a full ledger
        # refuses, it does not consume.
        self.assertTrue(self.take().succeeded)

    # --- TTL, the fourth control point --------------------------------------

    def test_a_handoff_that_expired_during_execution_has_its_result_refused(self):
        """The fourth TTL control point, reached one layer earlier than expected.

        `results.accept` has its own "handoff expired before its result was
        accepted" check, but through this API it is never the one that fires:
        revalidating the handoff wire refuses an expired handoff before the
        result is parsed at all. So the control point holds, and the code is
        HANDOFF_NOT_VALID — `accept`'s check remains as defence for callers
        that hold a Handoff some other way.
        """
        for now in (self.handoff.expires_at, self.handoff.expires_at + 1):
            with self.subTest(now=now):
                rejection = self.rejected(now=now)
                self.assertEqual(rejection.reason_code, 'HANDOFF_NOT_VALID')
                self.assertIn('not currently valid', str(rejection))
        self.assertTrue(self.take(now=self.handoff.expires_at - 1).succeeded)

    def test_a_result_dated_outside_its_handoff_is_refused(self):
        """Forged, because `produce` will not sign these — and that is the point.

        A worker that cannot sign such a result is not a worker that cannot
        send one: the signing key is all it takes.
        """
        # "produced after its handoff expired" is not among them: to reach it
        # the acceptance clock would have to be past `expires_at` too, and then
        # the handoff revalidation above refuses first. Through this API that
        # check of `accept` is unreachable, which is layering, not dead code.
        for produced_at, fragment in ((self.handoff.issued_at - 1, 'predates'),
                                      (121, 'future'),
                                      (0, 'produced_at must be between')):
            with self.subTest(produced_at=produced_at):
                rejection = self.rejected(
                    self.forge_result(produced_at=produced_at), now=120)
                self.assertEqual(rejection.reason_code, 'RESULT_NOT_VALID')
                self.assertIn(fragment, str(rejection))

    def test_a_forged_output_digest_is_refused(self):
        rejection = self.rejected(self.forge_result(output_sha256='c' * 64))
        self.assertEqual(rejection.reason_code, 'RESULT_NOT_VALID')
        self.assertIn('output hash', str(rejection))

    # --- approval-bound jobs -------------------------------------------------

    def test_an_approval_bound_job_needs_the_gateway_receipt(self):
        """This instance cannot tell an approved job from an unapproved one.

        The wire keeps PENDING_APPROVAL forever — consuming the approval does
        not rewrite it — so refusing without the receipt is what makes the
        evidence travel. Calling it verification would be reading UNKNOWN as
        PASS, and the store that could verify it belongs to the gateway.
        """
        policy = self.policy_for(requires_approval=True)
        wire = self.issue(policy=policy)
        handoff = validate_pending(wire, subject='subject-demo', job_id='job-demo',
                                   policy=policy, verifier=self.signer,
                                   now=101)
        result_wire = self.result_for(handoff)

        missing = self.rejected(result_wire, handoff_wire=wire, policy=policy)
        self.assertEqual(missing.reason_code, 'APPROVAL_NOT_EVIDENCED')
        self.assertIn('approval receipt', str(missing))
        for bad in ('not-a-digest', 'A' * 64, b'a' * 64, 42, ''):
            with self.subTest(hash=repr(bad)):
                rejection = self.rejected(result_wire, handoff_wire=wire,
                                          policy=policy, approval_record_hash=bad)
                self.assertEqual(rejection.reason_code, 'APPROVAL_NOT_EVIDENCED')

        receipt = self.receipt_for(wire, policy)
        taken = self.take(result_wire, handoff_wire=wire, policy=policy,
                          approval_record_hash=receipt)
        self.assertEqual(taken.approval_record_hash, receipt)

    def test_a_receipt_is_refused_where_no_approval_was_required(self):
        """The gateway refuses an unexpected token; this refuses an unexpected receipt."""
        rejection = self.rejected(approval_record_hash='b' * 64)
        self.assertEqual(rejection.reason_code, 'APPROVAL_NOT_EVIDENCED')
        self.assertIn('not allowed', str(rejection))

    def test_a_rejected_approval_spends_no_acceptance_either(self):
        self.rejected(approval_record_hash='b' * 64)
        self.assertTrue(self.take().succeeded)

    def test_the_result_digest_identifies_the_exact_wire_accepted(self):
        """Otherwise it names nothing an auditor could look up."""
        wire = self.result_for()
        self.assertEqual(self.take(wire).result_sha256,
                         hashlib.sha256(wire).hexdigest())

    def test_the_ledger_is_process_local_and_this_is_the_boundary(self):
        """Held open, the same limit the orchestrator's job ledger has.

        A restart, or a second verifier with the same identity, takes the same
        result again. Durable shared state is a persistence decision the roadmap
        places outside v0.1, so the claim is one acceptance per handoff **per
        instance** and a test pins it rather than the docs overstating it.
        """
        wire = self.result_for()
        self.take(wire)
        self.assertEqual(self.rejected(wire).reason_code, 'RESULT_ALREADY_ACCEPTED')
        self.assertTrue(self.take(wire, verifier=self.verifier_for()).succeeded)

    def receipt_for(self, wire, policy):
        store = ApprovalStore()
        gateway = Gateway(gateway_id='gateway-1',
                          handoff_verifier=self.signer.verifier(),
                          approval_store=store)
        scope = create_scope(wire, subject='subject-demo', job_id='job-demo',
                             policy=policy, verifier=self.signer,
                             now=101)
        granted = store.grant(scope, now=101, ttl_seconds=30)
        permit = gateway.admit(wire, subject='subject-demo', job_id='job-demo',
                               policy=policy, now=101,
                               approval_token=granted.token)
        return permit.approval_record_hash

    # --- what it cannot do ---------------------------------------------------

    def test_this_instance_cannot_sign_the_results_it_takes(self):
        """Closed after v0.1; until then a test pinned it open.

        With HMAC, holding the key to verify was holding the key to sign, so
        this instance could have forged the result it then accepted. It now
        holds the worker's public key only: no private key, no result key, no
        method that signs.
        """
        held = list(vars(self.verifier).values())
        self.assertFalse(any(isinstance(value, WorkerAuthority) for value in held))
        self.assertNotIn(self.keys.result_key, held)
        self.assertFalse(hasattr(self.verifier._worker_verifier, 'sign'))
        self.assertEqual(len(self.verifier._worker_verifier.public_key), 32)

    # --- decisions have to be auditable -------------------------------------

    def test_every_action_it_records_is_in_the_audit_vocabulary(self):
        from geniusnew.audit import _ACTIONS, _DECISIONS, _REASON_CODE
        self.assertTrue(ACTIONS.issubset(_ACTIONS), ACTIONS - _ACTIONS)
        self.assertIn('DENIED', _DECISIONS)
        for reason in REJECTIONS | {'RESULT_VERIFIED'}:
            with self.subTest(reason=reason):
                self.assertRegex(reason, _REASON_CODE)
        self.assertEqual(verifier_module._MAX_OCCURRED_AT,
                         __import__('geniusnew.audit', fromlist=['x'])._MAX_OCCURRED_AT)

    def test_an_acceptance_becomes_an_audit_event(self):
        taken = self.take()
        authority = AuditAuthority(audit_key=self.keys.audit_key)
        actor = authority.actor('monitor', 'verifier-1')
        event = event_from_handoff(self.handoff, trace_id='trace-1', actor=actor,
                                   action=taken.action, decision=taken.decision,
                                   reason_code=taken.reason_code,
                                   occurred_at=taken.occurred_at)
        self.assertEqual(event.action, 'RESULT_ACCEPTED')

    def test_a_rejection_carries_a_closed_code_and_a_fixed_shape(self):
        rejection = self.rejected(handoff_wire=b'{}')
        self.assertIn(rejection.reason_code, REJECTIONS)
        self.assertEqual(rejection.action, 'RESULT_REJECTED')
        self.assertEqual(rejection.decision, 'DENIED')
        self.assertEqual(rejection.occurred_at, 120)
        self.assertIsInstance(rejection, ContractError)

    def test_a_rejection_outside_the_closed_set_cannot_be_constructed(self):
        for reason in ('SOMETHING_ELSE', 'RESULT_VERIFIED', '', None, 42, ['x']):
            with self.subTest(reason=repr(reason)):
                with self.assertRaises(ContractError):
                    Rejected('why', reason_code=reason, occurred_at=1)
        for occurred_at in (None, '1', 1.0, True, object()):
            with self.subTest(occurred_at=repr(occurred_at)):
                with self.assertRaisesRegex(ContractError, 'occurred_at must be an integer'):
                    Rejected('why', reason_code='RESULT_NOT_VALID',
                             occurred_at=occurred_at)

    # --- construction and arguments fail closed -----------------------------

    def test_construction_refuses_anything_it_cannot_rely_on(self):
        for verifier_id in ('', 'UPPER', 'with space', None, 42, 'x' * 64):
            with self.subTest(verifier_id=verifier_id), self.assertRaises(ContractError):
                self.verifier_for(verifier_id=verifier_id)
        # The signer is refused as well: the verifier checks handoffs and must
        # not be able to issue one.
        for handoff_verifier in (b'x' * 32, None, 'x' * 32, 42, self.signer):
            with self.subTest(key=repr(handoff_verifier)[:20]):
                with self.assertRaisesRegex(ContractError, 'handoff_verifier'):
                    self.verifier_for(handoff_verifier=handoff_verifier)
        # So is the worker authority: holding it, the verifier could forge the
        # results it accepts.
        for worker_verifier in (b'x' * 32, None, 'x' * 32, 42, self.worker_authority):
            with self.subTest(key=repr(worker_verifier)[:20]):
                with self.assertRaisesRegex(ContractError, 'worker_verifier'):
                    self.verifier_for(worker_verifier=worker_verifier)

    def test_the_verifier_holds_nothing_that_can_issue_a_handoff(self):
        """A verifier holding the minting key could authorize the work it takes.

        With HMAC it had to hold that key to check a handoff at all. Now it holds
        the public half, and neither the signer nor the key it derives from is
        anywhere in it.
        """
        held = list(vars(self.verifier).values())
        self.assertFalse(any(isinstance(value, HandoffSigner) for value in held))
        self.assertNotIn(self.keys.integrity_key, held)
        self.assertFalse(hasattr(self.verifier._handoff_verifier, 'sign'))

    def test_acceptance_fails_closed_on_malformed_arguments(self):
        for now in (None, '120', 120.0, True, object()):
            with self.subTest(now=repr(now)):
                with self.assertRaisesRegex(ContractError, 'now must be an integer'):
                    self.take(now=now)
        for now in (0, -1, verifier_module._MAX_OCCURRED_AT + 1):
            with self.subTest(now=now):
                with self.assertRaisesRegex(ContractError, 'audit contract accepts'):
                    self.take(now=now)
        for policy in (None, 'policy', 42, {}):
            with self.subTest(policy=type(policy)):
                with self.assertRaisesRegex(ContractError, 'policy is invalid'):
                    self.take(policy=policy)


if __name__ == '__main__':
    unittest.main()
