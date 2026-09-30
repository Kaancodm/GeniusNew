import hashlib
import itertools
import os
import select
import signal
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

import psycopg

from geniusnew import orchestrator as orchestrator_module
from geniusnew.database import PostgresJobLedger, migrate
from geniusnew.approvals import ApprovalStore, create_scope
from geniusnew.audit import AuditAuthority, event_from_handoff
from geniusnew.contracts import ContractError, Grant, HandoffSigner, Policy, validate
from geniusnew.gateway import DispatchPermit, Gateway
from geniusnew.orchestrator import (ACTIONS, DENIALS, Admission, Decision, Denied,
                                    Dispatch, DispatchAttempted, JobLedger, Orchestrator,
                                    ProcessLocalJobLedger, Reservation, WorkerEndpoint)
from geniusnew.results import WorkerAuthority, accept
from geniusnew.workers import DeterministicSummarizer, Worker, WorkerRunner
from postgres_support import PostgresDatabase

# Zero-entropy and self-describing. Its job is to be unmistakable if it ever
# turns up where an execution-side exception text must not reach.
CANARY = 'DISPATCH-CANARY-MUST-NOT-REACH-A-REFUSAL'

# The helpers below take a sentinel rather than None. Defaulting on falsiness is
# how four tests in `test_results.py` came to pass for the wrong reason: a
# `policy=None` case silently became the valid policy and asserted nothing.
DEFAULT = object()


class OtherToolWorker(Worker):
    tool = 'translate'

    def run(self, payload):
        return {'text': payload['text']}


class CountingRunner(WorkerRunner):
    """A runner that records every dispatch, and optionally misbehaves.

    It is a real `WorkerRunner` subclass because `WorkerEndpoint` accepts
    nothing else — an arbitrary callable in the dispatch path is exactly what
    the endpoint exists to prevent.
    """

    def __init__(self, *, authority, wire=DEFAULT, raises=None):
        super().__init__(DeterministicSummarizer(), authority=authority)
        self.calls = []
        self.wire = b'not-a-real-result' if wire is DEFAULT else wire
        self.raises = raises

    def execute(self, permit, *, now):
        self.calls.append((permit, now))
        if self.raises is not None:
            raise self.raises
        return self.wire


class Fixture:
    """Shared setup. A mixin, not a TestCase: subclassing one re-runs its suite."""

    def setUp(self):
        self.key = HandoffSigner(integrity_key=b'phase-2-test-integrity-key-32bytes')
        tokens = itertools.count()
        self.store = ApprovalStore(
            token_source=lambda: b'demo-approval-token-for-tests-%012d' % next(tokens))
        self.gateway = Gateway(gateway_id='gateway-test', handoff_verifier=self.key.verifier(),
                               approval_store=self.store)
        self.result_authority = WorkerAuthority(
            result_key=b'a-separate-result-key-of-32bytes!')
        self.endpoint = WorkerEndpoint(
            'worker-demo',
            WorkerRunner(DeterministicSummarizer(), authority=self.result_authority))
        self.orchestrator = self.orchestrator_for()
        self.policy = self.policy_for()

    def policy_for(self, *, worker_agent_id='worker-demo', tools=('summarize',),
                   requires_approval=False, orchestrator_id='orchestrator-demo'):
        grant = Grant('subject-demo', 'user-demo', worker_agent_id, 'basic',
                      tools, 'isolated', requires_approval)
        return Policy('policy-v1', orchestrator_id, 60,
                      ('summarize', 'translate'), ('isolated',), (grant,))

    def orchestrator_for(self, *, workers=DEFAULT, gateway=DEFAULT,
                         orchestrator_id='orchestrator-demo', signer=DEFAULT,
                         on_admitted=None, job_ledger=None):
        return Orchestrator(
            orchestrator_id=orchestrator_id,
            signer=self.key if signer is DEFAULT else signer,
            gateway=self.gateway if gateway is DEFAULT else gateway,
            workers=(self.endpoint,) if workers is DEFAULT else workers,
            on_admitted=on_admitted, job_ledger=job_ledger)

    def counting(self, **arguments):
        return CountingRunner(authority=self.result_authority, **arguments)

    def admit(self, *, orchestrator=DEFAULT, policy=DEFAULT, job_id='job-demo',
              now=100, request=DEFAULT, subject='subject-demo'):
        return (self.orchestrator if orchestrator is DEFAULT else orchestrator).admit(
            {'text': 'the quick brown fox'} if request is DEFAULT else request,
            subject=subject, job_id=job_id,
            policy=self.policy if policy is DEFAULT else policy, now=now)

    def wire(self, **arguments):
        return self.admit(**arguments).wire

    def dispatch(self, wire, *, orchestrator=DEFAULT, policy=DEFAULT,
                 job_id='job-demo', now=110, subject='subject-demo',
                 approval_token=None):
        return (self.orchestrator if orchestrator is DEFAULT else orchestrator).dispatch(
            wire, subject=subject, job_id=job_id,
            policy=self.policy if policy is DEFAULT else policy, now=now,
            approval_token=approval_token)

    def submit(self, *, orchestrator=DEFAULT, policy=DEFAULT, job_id='job-demo',
               now=100, request=DEFAULT, subject='subject-demo',
               approval_token=None):
        return (self.orchestrator if orchestrator is DEFAULT else orchestrator).submit(
            {'text': 'the quick brown fox'} if request is DEFAULT else request,
            subject=subject, job_id=job_id,
            policy=self.policy if policy is DEFAULT else policy, now=now,
            approval_token=approval_token)

    def denied(self, callable_, *args, **kwargs):
        """Run something expected to be denied and hand back its decision."""
        with self.assertRaises(Denied) as caught:
            callable_(*args, **kwargs)
        return caught.exception.decision

    def taken(self, dispatched, *, policy=DEFAULT, job_id='job-demo', now=120):
        """Accept a result the way an independent verifier would have to."""
        handoff = validate(dispatched.handoff_wire, subject='subject-demo',
                           job_id=job_id,
                           policy=self.policy if policy is DEFAULT else policy,
                           verifier=self.key, now=now)
        return accept(dispatched.result_wire, handoff=handoff,
                      verifier=self.result_authority, now=now)


class OrchestratorTest(Fixture, unittest.TestCase):
    """Roadmap step 13: admission, routing, dispatch."""

    # --- the path it exists for ---------------------------------------------

    def test_admission_derives_identity_and_capabilities_only_from_policy(self):
        handoff = validate(self.wire(), subject='subject-demo', job_id='job-demo',
                           policy=self.policy, verifier=self.key, now=101)
        self.assertEqual(handoff.user_id, 'user-demo')
        self.assertEqual(handoff.worker_agent_id, 'worker-demo')
        self.assertEqual(handoff.tools, ('summarize',))
        self.assertEqual(handoff.orchestrator_id, 'orchestrator-demo')

    def test_non_approval_job_runs_through_gateway_and_selected_worker(self):
        dispatched = self.dispatch(self.wire())
        self.assertIsInstance(dispatched, Dispatch)
        result = self.taken(dispatched)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.reason_code, 'WORK_COMPLETED')
        self.assertEqual(dispatched.worker_agent_id, 'worker-demo')

    def test_submit_is_admission_and_dispatch_in_one_call(self):
        dispatched = self.submit()
        self.assertTrue(self.taken(dispatched).succeeded)
        self.assertEqual([decision.action for decision in dispatched.decisions],
                         ['HANDOFF_ISSUED', 'EXECUTION_DISPATCHED'])

    def test_it_does_not_judge_the_result_it_collected(self):
        """A FAILED result comes back as evidence, not as a verdict.

        The orchestrator holds no result key and never calls `accept`, so it has
        no way to declare its own job a success. Constitution section 8 keeps
        that a separate role, and step 14 is where it lands.
        """
        class Exploding(DeterministicSummarizer):
            def run(self, payload):
                raise RuntimeError('boom')

        endpoint = WorkerEndpoint('worker-demo', WorkerRunner(
            Exploding(), authority=self.result_authority))
        dispatched = self.submit(orchestrator=self.orchestrator_for(
            workers=(endpoint,)))
        for name in ('succeeded', 'status', 'ok', 'accepted'):
            self.assertFalse(hasattr(dispatched, name), name)
        result = self.taken(dispatched)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.reason_code, 'WORKER_FAILED')

    def test_it_holds_no_other_role_authority_in_its_own_state(self):
        """No shared mutable authority: not the approvals, not the signing keys.

        In one process nothing is truly out of reach — the endpoint holds a
        runner and Python offers no memory boundary. What is checkable is that
        this component keeps no usable handle on another role's authority, and
        has no API through which to mint a permit or accept a result.
        """
        for value in vars(self.orchestrator).values():
            self.assertNotIsInstance(value, (ApprovalStore, WorkerAuthority,
                                             AuditAuthority))

    def test_it_signs_with_its_own_key_not_the_gateway_s(self):
        """The issuer does not reach into the verifier for a key to sign with.

        Handoff v1 is HMAC, so the bytes are the same today. Taking the key as
        its own input is what keeps separate keys possible at all, and keeps
        rotation from being a decision two roles have to make together.
        """
        stranger = Gateway(gateway_id='gateway-other',
                           handoff_verifier=HandoffSigner(integrity_key=b'a-different-integrity-key-32bytes').verifier(),
                           approval_store=ApprovalStore())
        orchestrator = self.orchestrator_for(gateway=stranger)
        wire = self.wire(orchestrator=orchestrator)
        validate(wire, subject='subject-demo', job_id='job-demo',
                 policy=self.policy, verifier=self.key, now=101)
        # And the gateway with the other key refuses it, as an independent
        # verifier must — that refusal stays the gateway's word, not a Denied.
        with self.assertRaises(ContractError) as caught:
            self.dispatch(wire, orchestrator=orchestrator)
        self.assertNotIsInstance(caught.exception, Denied)

    # --- routing is a fact of the grant -------------------------------------

    def test_routing_is_exactly_by_trusted_worker_id(self):
        missing = self.policy_for(worker_agent_id='worker-missing')
        wire = self.wire(policy=missing)
        decision = self.denied(self.dispatch, wire, policy=missing)
        self.assertEqual(decision.reason_code, 'WORKER_NOT_CONFIGURED')

    def test_misconfigured_endpoint_tool_is_refused_before_gateway(self):
        endpoint = WorkerEndpoint('worker-demo', WorkerRunner(
            OtherToolWorker(), authority=self.result_authority))
        orchestrator = self.orchestrator_for(workers=(endpoint,))
        wire = self.wire(orchestrator=orchestrator)
        decision = self.denied(self.dispatch, wire, orchestrator=orchestrator)
        self.assertEqual(decision.reason_code, 'TOOL_NOT_GRANTED')

    def test_routing_does_not_depend_on_configuration_order(self):
        other = WorkerEndpoint('worker-other', WorkerRunner(
            OtherToolWorker(), authority=self.result_authority))
        forwards = self.orchestrator_for(workers=(self.endpoint, other))
        backwards = self.orchestrator_for(workers=(other, self.endpoint))
        self.assertEqual(forwards.route(subject='subject-demo', policy=self.policy, now=100),
                         backwards.route(subject='subject-demo', policy=self.policy, now=100))
        self.assertEqual(forwards.worker_ids, backwards.worker_ids)

    def test_worker_registry_is_copied_and_deterministic(self):
        workers = [self.endpoint]
        orchestrator = self.orchestrator_for(workers=workers)
        workers.clear()
        self.assertEqual(orchestrator.worker_ids, ('worker-demo',))
        self.assertIsInstance(self.submit(orchestrator=orchestrator), Dispatch)

    def test_the_same_request_produces_the_same_bytes(self):
        """No clock and no entropy anywhere in the path."""
        first = self.submit(orchestrator=self.orchestrator_for())
        second = self.submit(orchestrator=self.orchestrator_for())
        self.assertEqual(first.handoff_wire, second.handoff_wire)
        self.assertEqual(first.result_wire, second.result_wire)
        self.assertEqual(first.decisions, second.decisions)

    # --- every admission refusal, by the reason it claims --------------------

    def test_each_admission_refusal_names_its_own_reason(self):
        """Refused is not enough: refused by the check under test."""
        cases = [
            ('POLICY_NOT_FOR_THIS_ORCHESTRATOR',
             dict(policy=self.policy_for(orchestrator_id='orchestrator-other'))),
            ('SUBJECT_NOT_AUTHORIZED', dict(subject='subject-unknown')),
            ('WORKER_NOT_CONFIGURED',
             dict(policy=self.policy_for(worker_agent_id='worker-missing'))),
        ]
        for reason, arguments in cases:
            with self.subTest(reason=reason):
                decision = self.denied(self.submit, **arguments)
                self.assertEqual(decision.reason_code, reason)
                self.assertEqual(decision.action, 'HANDOFF_REJECTED')
                self.assertEqual(decision.decision, 'DENIED')

    def test_a_refused_job_reaches_no_worker_and_leaves_no_artifact(self):
        runner = self.counting()
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),))
        self.denied(self.submit, orchestrator=orchestrator,
                    policy=self.policy_for(worker_agent_id='worker-missing'))
        self.assertEqual(runner.calls, [])
        self.assertEqual(orchestrator._ledger.job_ids(), set())

    def test_misrouting_is_refused_before_an_approval_can_be_burned(self):
        """A one-time approval must not pay for a job that was never routable."""
        policy = self.policy_for(worker_agent_id='worker-missing',
                                 requires_approval=True)
        wire = self.wire(policy=policy)
        scope = create_scope(wire, subject='subject-demo', job_id='job-demo',
                             policy=policy, verifier=self.key, now=101)
        granted = self.store.grant(scope, now=101, ttl_seconds=30)
        decision = self.denied(self.dispatch, wire, policy=policy, now=102,
                               approval_token=granted.token)
        self.assertEqual(decision.reason_code, 'WORKER_NOT_CONFIGURED')
        self.assertEqual(self.store.consume(granted.token, scope, now=102).state,
                         'CONSUMED', 'routing failure must not burn the approval')

    # --- the job ledger ------------------------------------------------------

    def test_a_job_id_is_burned_once_and_stays_burned(self):
        """A retry needs a new id: releasing a burned one is a replay window."""
        runner = self.counting(wire=b'x')
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),))
        self.submit(orchestrator=orchestrator)
        decision = self.denied(self.submit, orchestrator=orchestrator)
        self.assertEqual(decision.reason_code, 'JOB_ID_REUSED')
        self.assertEqual(len(runner.calls), 1)

    def test_a_failed_dispatch_does_not_release_the_job_id(self):
        runner = self.counting(raises=ContractError('worker said no'))
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),))
        with self.assertRaises(ContractError):
            self.submit(orchestrator=orchestrator)
        decision = self.denied(self.submit, orchestrator=orchestrator)
        self.assertEqual(decision.reason_code, 'JOB_ID_REUSED')
        self.assertEqual(len(runner.calls), 1)

    def test_a_job_the_gateway_refused_keeps_its_id(self):
        """The ledger records what ran, not what was attempted.

        Burning the id before the gateway had spoken would cost a caller their
        job id for a refusal they can fix — a missing approval token, most
        obviously, where the approval is scoped to a wire that carries that very
        id and so cannot simply be reissued under a new one.
        """
        policy = self.policy_for(requires_approval=True)
        wire = self.wire(policy=policy)
        with self.assertRaisesRegex(ContractError, 'required'):
            self.dispatch(wire, policy=policy, now=102)
        self.assertEqual(self.orchestrator._ledger.job_ids(), set())

        scope = create_scope(wire, subject='subject-demo', job_id='job-demo',
                             policy=policy, verifier=self.key, now=101)
        granted = self.store.grant(scope, now=101, ttl_seconds=30)
        # The same id goes through once the approval is there. The result is not
        # accepted here: an approval-bound wire stays PENDING_APPROVAL, which
        # `validate` refuses outright, so step 14's verifier will need the
        # pending path for these — one more reason acceptance is its own role.
        dispatched = self.dispatch(wire, policy=policy, now=102,
                                   approval_token=granted.token)
        self.assertIsInstance(dispatched.result_wire, bytes)
        self.assertEqual(self.orchestrator._ledger.job_ids(), {'job-demo'})

    def test_the_gateway_receipt_travels_with_the_dispatch(self):
        """The result verifier refuses an approval-bound job without it.

        An approval-bound wire keeps PENDING_APPROVAL for ever, so the verifier
        cannot tell an approved job from an unapproved one and requires the
        gateway's receipt hash. Dropping it here made the two impossible to wire
        together — found by wiring them.
        """
        policy = self.policy_for(requires_approval=True)
        wire = self.wire(policy=policy)
        scope = create_scope(wire, subject='subject-demo', job_id='job-demo',
                             policy=policy, verifier=self.key, now=101)
        granted = self.store.grant(scope, now=101, ttl_seconds=30)
        dispatched = self.dispatch(wire, policy=policy, now=102,
                                   approval_token=granted.token)
        self.assertRegex(dispatched.approval_record_hash, r'\A[0-9a-f]{64}\Z')
        # And a job that needed no approval carries no receipt to claim one.
        self.assertIsNone(self.submit(job_id='job-plain').approval_record_hash)

    def test_the_approval_is_consumed_by_the_gateway_and_only_once(self):
        policy = self.policy_for(requires_approval=True)
        wire = self.wire(policy=policy)
        scope = create_scope(wire, subject='subject-demo', job_id='job-demo',
                             policy=policy, verifier=self.key, now=101)
        granted = self.store.grant(scope, now=101, ttl_seconds=30)
        self.dispatch(wire, policy=policy, now=102, approval_token=granted.token)
        with self.assertRaises(ContractError):
            self.dispatch(wire, policy=policy, job_id='job-second', now=103,
                          approval_token=granted.token)

    def test_the_job_ledger_is_bounded(self):
        """An unbounded set a caller can grow is memory exhaustion with a receipt."""
        runner = self.counting(wire=b'x')
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),))
        with mock.patch.object(orchestrator_module, '_MAX_JOBS', 1):
            self.submit(orchestrator=orchestrator, job_id='job-1')
            decision = self.denied(self.submit, orchestrator=orchestrator,
                                   job_id='job-2')
        self.assertEqual(decision.reason_code, 'JOB_LEDGER_FULL')
        self.assertEqual(len(runner.calls), 1)

    def test_reservation_rechecks_capacity_after_an_earlier_preflight(self):
        ledger = ProcessLocalJobLedger()
        orchestrator = self.orchestrator_for(job_ledger=ledger)
        with mock.patch.object(orchestrator_module, '_MAX_JOBS', 1):
            self.assertTrue(ledger.reserve(reservation(job_id='job-first')))
            decision = self.denied(orchestrator._reserve,
                                   reservation(job_id='job-second'), now=110)
        self.assertEqual(decision.reason_code, 'JOB_LEDGER_FULL')
        self.assertEqual(ledger.job_ids(), frozenset({'job-first'}))

    def test_an_oversized_job_id_is_refused_before_the_ledger_stores_it(self):
        for call in (lambda: self.wire(job_id='j' * 129),
                     lambda: self.submit(job_id='j' * 129),
                     lambda: self.dispatch(b'wire', job_id='j' * 129)):
            with self.subTest(call=call):
                with self.assertRaisesRegex(ContractError, 'at most 128 bytes'):
                    call()
        self.assertEqual(self.orchestrator._ledger.job_ids(), set())

    # --- it cannot admit its own job ----------------------------------------

    def test_the_gateway_still_rejects_a_tampered_wire(self):
        wire = self.wire()
        with self.assertRaises(ContractError):
            self.dispatch(wire.replace(b'quick', b'QUICK'))

    def test_a_gateway_that_returns_something_else_is_refused(self):
        class Forged(Gateway):
            def admit(self, *args, **kwargs):
                return 'permit'

        runner = self.counting()
        orchestrator = self.orchestrator_for(
            gateway=Forged(gateway_id='gateway-test', handoff_verifier=self.key.verifier(),
                           approval_store=ApprovalStore()),
            workers=(WorkerEndpoint('worker-demo', runner),))
        with self.assertRaisesRegex(ContractError, 'dispatch permit'):
            self.submit(orchestrator=orchestrator)
        self.assertEqual(runner.calls, [])

    def test_a_permit_for_another_handoff_is_refused(self):
        """The permit has to be for the job that was submitted.

        A gateway returning a permit for a different handoff would have the
        worker run a contract this orchestrator never sent, while the wire it
        reports still described the one it did — leaving the result verifier
        checking a result against the wrong contract.
        """
        elsewhere = self.wire(job_id='job-other')

        class Substituting(Gateway):
            def admit(inner, wire, **kwargs):
                return Gateway.admit(inner, elsewhere,
                                     **{**kwargs, 'job_id': 'job-other'})

        runner = self.counting()
        orchestrator = self.orchestrator_for(
            gateway=Substituting(gateway_id='gateway-test', handoff_verifier=self.key.verifier(),
                                 approval_store=ApprovalStore()),
            workers=(WorkerEndpoint('worker-demo', runner),))
        with self.assertRaisesRegex(ContractError, 'different handoff'):
            self.submit(orchestrator=orchestrator)
        self.assertEqual(runner.calls, [])

    # --- dispatch is one call, to one worker --------------------------------

    def test_a_refused_dispatch_is_not_retried_on_another_worker(self):
        """No failover. A second attempt is default retry wearing default deny."""
        refusing = self.counting(raises=ContractError('no'))
        spare = self.counting()
        orchestrator = self.orchestrator_for(workers=(
            WorkerEndpoint('worker-demo', refusing),
            WorkerEndpoint('worker-other', spare)))
        with self.assertRaises(ContractError):
            self.submit(orchestrator=orchestrator)
        self.assertEqual(len(refusing.calls), 1)
        self.assertEqual(spare.calls, [])

    def test_an_execution_exception_text_never_reaches_the_refusal(self):
        runner = self.counting(raises=RuntimeError(CANARY))
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),))
        with self.assertRaises(ContractError) as caught:
            self.submit(orchestrator=orchestrator)
        self.assertNotIn(CANARY, str(caught.exception))
        self.assertEqual(str(caught.exception), 'dispatch failed')

    def test_a_runner_that_returns_no_wire_is_refused(self):
        for value in (None, '', b'', 'wire', 42, {}, bytearray(b'x')):
            with self.subTest(value=repr(value)):
                orchestrator = self.orchestrator_for(workers=(
                    WorkerEndpoint('worker-demo', self.counting(wire=value)),))
                with self.assertRaisesRegex(ContractError, 'signed result wire'):
                    self.submit(orchestrator=orchestrator)

    # --- decisions have to be auditable -------------------------------------

    def test_every_action_it_records_is_in_the_audit_vocabulary(self):
        """A decision this component cannot audit is a decision nobody can see."""
        from geniusnew.audit import _ACTIONS, _DECISIONS, _REASON_CODE
        self.assertTrue(ACTIONS.issubset(_ACTIONS), ACTIONS - _ACTIONS)
        self.assertIn('DENIED', _DECISIONS)
        for reason in set(DENIALS) | {'POLICY_SATISFIED'}:
            with self.subTest(reason=reason):
                self.assertRegex(reason, _REASON_CODE)

    def test_a_decision_becomes_an_audit_event(self):
        dispatched = self.submit()
        handoff = validate(dispatched.handoff_wire, subject='subject-demo',
                           job_id='job-demo', policy=self.policy,
                           verifier=self.key, now=101)
        authority = AuditAuthority(audit_key=b'an-audit-key-of-thirty-two-bytes')
        actor = authority.actor('orchestrator', 'orchestrator-demo')
        for decision in dispatched.decisions:
            event = event_from_handoff(handoff, trace_id='trace-1', actor=actor,
                                       action=decision.action,
                                       decision=decision.decision,
                                       reason_code=decision.reason_code,
                                       occurred_at=decision.occurred_at)
            self.assertEqual(event.action, decision.action)

    def test_it_records_its_own_decisions_and_not_the_gateway_s(self):
        dispatched = self.submit()
        actions = {decision.action for decision in dispatched.decisions}
        self.assertNotIn('HANDOFF_ADMITTED', actions)

    def test_a_denial_message_is_the_fixed_sentence_for_its_code(self):
        decision = self.denied(self.submit,
                               policy=self.policy_for(worker_agent_id='worker-missing'))
        self.assertIn(decision.reason_code, DENIALS)
        with self.assertRaisesRegex(ContractError, 'unconfigured worker'):
            self.submit(policy=self.policy_for(worker_agent_id='worker-missing'))

    def test_a_decision_outside_the_closed_sets_is_refused(self):
        valid = dict(action='HANDOFF_ISSUED', decision='ALLOWED',
                     reason_code='POLICY_SATISFIED', occurred_at=1)
        for field, value in (('action', 'HANDOFF_ADMITTED'), ('action', 'x'),
                             ('decision', 'MAYBE'), ('decision', 'allowed'),
                             ('reason_code', 'SECRET_LEAKED_HERE'),
                             ('reason_code', 'POLICY_SATISFIED '),
                             ('occurred_at', '1'), ('occurred_at', 1.0),
                             ('occurred_at', True)):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ContractError):
                    Decision(**{**valid, field: value})

    # --- review findings, each with the evidence that it was real ------------

    def test_a_failed_execution_still_yields_the_dispatch_decision(self):
        """The attempt happened: the id is burned and the permit is consumed.

        Without the decision on the failure path, a security-relevant dispatch
        leaves the caller nothing to audit — the job ran (or was reached) and
        the record of deciding to run it was dropped with the exception.
        """
        runner = self.counting(raises=ContractError('worker said no'))
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),))
        with self.assertRaises(DispatchAttempted) as caught:
            self.submit(orchestrator=orchestrator)
        self.assertEqual(str(caught.exception), 'worker said no')
        decision = caught.exception.decision
        self.assertEqual(decision.action, 'EXECUTION_DISPATCHED')
        self.assertEqual(decision.decision, 'ALLOWED')
        self.assertIsInstance(caught.exception, ContractError)
        self.assertEqual(len(runner.calls), 1)

    def test_an_unavailable_job_id_does_not_burn_an_approval(self):
        """The gateway consumes the token; asking it first spends one for nothing."""
        policy = self.policy_for(requires_approval=True)
        first = self.wire(policy=policy, job_id='job-taken')
        scope = create_scope(first, subject='subject-demo', job_id='job-taken',
                             policy=policy, verifier=self.key, now=101)
        self.dispatch(first, policy=policy, job_id='job-taken', now=102,
                      approval_token=self.store.grant(scope, now=101,
                                                      ttl_seconds=30).token)

        second = self.store.grant(scope, now=102, ttl_seconds=30)
        decision = self.denied(self.dispatch, first, policy=policy,
                               job_id='job-taken', now=103,
                               approval_token=second.token)
        self.assertEqual(decision.reason_code, 'JOB_ID_REUSED')
        # Still spendable, which is the whole point of refusing early.
        self.assertEqual(self.store.consume(second.token, scope, now=103).state,
                         'CONSUMED')

    def test_a_full_ledger_does_not_burn_an_approval_either(self):
        policy = self.policy_for(requires_approval=True)
        wire = self.wire(policy=policy)
        scope = create_scope(wire, subject='subject-demo', job_id='job-demo',
                             policy=policy, verifier=self.key, now=101)
        granted = self.store.grant(scope, now=101, ttl_seconds=30)
        with mock.patch.object(orchestrator_module, '_MAX_JOBS', 0):
            decision = self.denied(self.dispatch, wire, policy=policy, now=102,
                                   approval_token=granted.token)
        self.assertEqual(decision.reason_code, 'JOB_LEDGER_FULL')
        self.assertEqual(self.store.consume(granted.token, scope, now=102).state,
                         'CONSUMED')

    def test_admission_hands_back_the_decision_that_issued_the_wire(self):
        """The approval flow is necessarily two steps, and step one is a decision.

        A wire has to exist before an approval can be scoped to it, so callers
        that need approval cannot use `submit`. Returning only bytes left that
        path with a signed authorization artifact and nothing to record.
        """
        admission = self.admit()
        self.assertIsInstance(admission, Admission)
        self.assertEqual(admission.decision.action, 'HANDOFF_ISSUED')
        self.assertEqual(admission.decision.decision, 'ALLOWED')
        self.assertEqual(admission.decision.occurred_at, 100)
        dispatched = self.dispatch(admission.wire)
        self.assertEqual((admission.decision,) + dispatched.decisions,
                         self.submit(job_id='job-two').decisions[:1]
                         + dispatched.decisions)

    def test_a_decision_whose_fields_contradict_each_other_is_refused(self):
        """Independent membership checks accept records that are lies.

        `HANDOFF_REJECTED/ALLOWED/POLICY_SATISFIED` passed all three, and
        handing it to `Denied` raised a bare `KeyError` — a refusal type
        escaping as something no caller catches.
        """
        for action, decision, reason in (
                ('HANDOFF_REJECTED', 'ALLOWED', 'POLICY_SATISFIED'),
                ('HANDOFF_ISSUED', 'DENIED', 'JOB_ID_REUSED'),
                ('EXECUTION_DISPATCHED', 'DENIED', 'JOB_ID_REUSED'),
                ('HANDOFF_REJECTED', 'DENIED', 'POLICY_SATISFIED'),
                ('HANDOFF_ISSUED', 'ALLOWED', 'JOB_ID_REUSED')):
            with self.subTest(action=action, decision=decision, reason=reason):
                with self.assertRaises(ContractError):
                    Decision(action=action, decision=decision,
                             reason_code=reason, occurred_at=1)

    def test_a_decision_field_that_is_not_a_string_is_refused_as_a_contract_error(self):
        """An unhashable field raised a bare TypeError out of the set lookup."""
        valid = dict(action='HANDOFF_ISSUED', decision='ALLOWED',
                     reason_code='POLICY_SATISFIED', occurred_at=1)
        for field in ('action', 'decision', 'reason_code'):
            for value in (['x'], {'x': 1}, {'x'}, None, 42, b'HANDOFF_ISSUED'):
                with self.subTest(field=field, value=repr(value)):
                    with self.assertRaisesRegex(ContractError, 'must be a string'):
                        Decision(**{**valid, field: value})

    def test_a_decision_the_audit_layer_could_not_record_is_refused(self):
        """`Decision` is documented as audit-shaped, so it has to be one.

        `AuditEvent` accepts 1..4102444800. A decision outside that window is a
        record nobody can write, produced by the component whose whole job is to
        produce records.
        """
        from geniusnew.audit import _MAX_OCCURRED_AT
        self.assertEqual(orchestrator_module._MAX_OCCURRED_AT, _MAX_OCCURRED_AT)
        valid = dict(action='HANDOFF_ISSUED', decision='ALLOWED',
                     reason_code='POLICY_SATISFIED')
        for occurred_at in (0, -1, _MAX_OCCURRED_AT + 1, 2 ** 64):
            with self.subTest(occurred_at=occurred_at):
                with self.assertRaises(ContractError):
                    Decision(**valid, occurred_at=occurred_at)
        Decision(**valid, occurred_at=_MAX_OCCURRED_AT)

    def test_a_clock_the_audit_layer_could_not_record_is_refused_at_the_door(self):
        """Refused where `now` enters, so a denial is always constructible.

        Checking it only inside `Decision` would mean a request at `now=0` is
        refused by the record of its own refusal, which is not a refusal anyone
        can audit either.
        """
        for now in (0, -1, orchestrator_module._MAX_OCCURRED_AT + 1):
            with self.subTest(now=now):
                for call in (lambda: self.orchestrator.route(
                                 subject='subject-demo', policy=self.policy, now=now),
                             lambda: self.wire(now=now),
                             lambda: self.submit(now=now)):
                    with self.assertRaisesRegex(ContractError, 'audit contract accepts'):
                        call()

    # --- the gateway's admission is passed on when it exists ------------------
    # Review finding LOW-1 (26.09.2026): the composition root recorded the
    # admission only after `dispatch` returned, so a lost race for the job id
    # left a minted permit with no admission on the in-memory chain.

    def test_the_admission_is_passed_on_before_the_worker_runs(self):
        seen = []
        runner = self.counting(wire=b'x')
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),),
            on_admitted=lambda permit: seen.append((permit, len(runner.calls))))
        self.dispatch(self.wire(), orchestrator=orchestrator, now=110)
        [(permit, calls_before)] = seen
        self.assertIsInstance(permit, DispatchPermit)
        self.assertEqual(calls_before, 0)
        self.assertIs(runner.calls[0][0], permit)
        self.assertEqual((permit.gateway_id, permit.admitted_at), ('gateway-test', 110))

    def test_a_lost_race_for_the_job_id_comes_after_the_admission(self):
        """Both callers pass the early check; the reservation decides.

        The approval is spent by then, so the admission it bought has to have
        been passed on before the denial, not instead of it.
        """
        policy = self.policy_for(requires_approval=True)
        wire = self.wire(policy=policy)
        scope = create_scope(wire, subject='subject-demo', job_id='job-demo',
                             policy=policy, verifier=self.key, now=101)
        granted = self.store.grant(scope, now=101, ttl_seconds=30)
        events = []
        runner = self.counting(wire=b'x')

        def rival_wins(permit):
            events.append(('admitted', permit.approval_record_hash))
            # The other caller reserves the same id in this window.
            orchestrator._ledger.reserve(Reservation(
                job_id='job-demo', subject='subject-demo', handoff_sha256='a' * 64,
                expires_at=1000, reserved_at=102))

        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),), on_admitted=rival_wins)
        decision = self.denied(self.dispatch, wire, orchestrator=orchestrator,
                               policy=policy, now=102, approval_token=granted.token)
        self.assertEqual(decision.reason_code, 'JOB_ID_REUSED')
        [(event, receipt)] = events
        self.assertEqual(event, 'admitted')
        self.assertRegex(receipt, r'\A[0-9a-f]{64}\Z')
        self.assertEqual(runner.calls, [])

    def test_an_admission_that_cannot_be_passed_on_runs_nothing(self):
        """No execution without its admission on record, and the id stays free."""
        runner = self.counting(wire=b'x')

        def recorder_down(permit):
            raise ContractError('audit is unavailable')

        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),), on_admitted=recorder_down)
        with self.assertRaisesRegex(ContractError, 'audit is unavailable'):
            self.dispatch(self.wire(), orchestrator=orchestrator)
        self.assertEqual(runner.calls, [])
        self.assertEqual(orchestrator._ledger.job_ids(), set())

    def test_the_sanitized_refusal_keeps_no_handle_on_the_original(self):
        """`from exc` puts the text back in __cause__ and in every traceback.

        Sanitizing only the message is not sanitizing. The cost is the lost
        stack trace, which is the right trade when the alternative is a worker's
        exception text crossing the execution boundary.
        """
        import traceback
        runner = self.counting(raises=RuntimeError(CANARY))
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),))
        with self.assertRaises(ContractError) as caught:
            self.submit(orchestrator=orchestrator)
        # The whole chain, not just the message: `from None` suppresses the
        # traceback line but leaves `__context__` populated, so the text would
        # still be one attribute away for any logger that walks it.
        seen, chain = set(), []
        exception = caught.exception
        while exception is not None and id(exception) not in seen:
            seen.add(id(exception))
            chain.append(exception)
            exception = exception.__cause__ or exception.__context__
        for link in chain:
            with self.subTest(link=type(link).__name__):
                self.assertNotIn(CANARY, str(link))
                self.assertNotIn(CANARY, repr(link.args))
        formatted = ''.join(traceback.format_exception(
            type(caught.exception), caught.exception, caught.exception.__traceback__))
        self.assertNotIn(CANARY, formatted)

    # --- construction fails closed ------------------------------------------

    def test_constructor_configuration_fails_closed(self):
        for orchestrator_id in ('', 'UPPER', 'with space', None, 42, 'x' * 64):
            with self.subTest(orchestrator_id=orchestrator_id):
                with self.assertRaises(ContractError):
                    self.orchestrator_for(orchestrator_id=orchestrator_id)
        # Raw key bytes and the public half are both refused: only the signer
        # can issue, and the orchestrator is the one place that must be able to.
        for signer in (b'phase-2-test-integrity-key-32bytes', None, 'x' * 32, 42,
                       self.key.verifier()):
            with self.subTest(signer=repr(signer)[:20]):
                with self.assertRaisesRegex(ContractError, 'signer'):
                    self.orchestrator_for(signer=signer)
        for gateway in (None, 'gateway', 42, {}):
            with self.subTest(gateway=type(gateway)):
                with self.assertRaisesRegex(ContractError, 'gateway'):
                    self.orchestrator_for(gateway=gateway)
        for workers in (None, 42):
            with self.subTest(workers=workers):
                with self.assertRaisesRegex(ContractError, 'iterable'):
                    self.orchestrator_for(workers=workers)
        with self.assertRaisesRegex(ContractError, 'must not be empty'):
            self.orchestrator_for(workers=())
        with self.assertRaisesRegex(ContractError, 'WorkerEndpoint'):
            self.orchestrator_for(workers=(self.endpoint, 'not-endpoint'))
        with self.assertRaisesRegex(ContractError, 'unique'):
            self.orchestrator_for(workers=(self.endpoint, self.endpoint))
        for on_admitted in ('record', 42, object()):
            with self.subTest(on_admitted=type(on_admitted)):
                with self.assertRaisesRegex(ContractError, 'on_admitted must be callable'):
                    self.orchestrator_for(on_admitted=on_admitted)

    def test_worker_endpoint_configuration_fails_closed(self):
        for worker_id in ('', 'UPPER', 'with space', None, 42, 'x' * 64):
            with self.subTest(worker_id=worker_id), self.assertRaises(ContractError):
                WorkerEndpoint(worker_id, self.endpoint.runner)
        for runner in (None, 'runner', 42, {}, self.endpoint):
            with self.subTest(runner=type(runner)):
                with self.assertRaisesRegex(ContractError, 'WorkerRunner'):
                    WorkerEndpoint('worker-demo', runner)

    def test_the_public_calls_fail_closed_on_malformed_arguments(self):
        for policy in (None, 'policy', 42, {}):
            with self.subTest(policy=type(policy)):
                with self.assertRaisesRegex(ContractError, 'policy is invalid'):
                    self.wire(policy=policy)
                with self.assertRaisesRegex(ContractError, 'policy is invalid'):
                    self.submit(policy=policy)
        for job_id in (None, '', 42, b'job-demo'):
            with self.subTest(job_id=repr(job_id)):
                with self.assertRaisesRegex(ContractError, 'job_id must be'):
                    self.wire(job_id=job_id)
                with self.assertRaisesRegex(ContractError, 'job_id must be'):
                    self.dispatch(b'wire', job_id=job_id)
        for now in (None, '100', 100.0, True, object()):
            with self.subTest(now=repr(now)):
                with self.assertRaises(ContractError):
                    self.submit(now=now)

    def test_route_fails_closed_on_malformed_arguments(self):
        """Asserted on `route` and by message, because `submit` hides both checks.

        Downstream, `issue` refuses a non-integer `now` with the same sentence a
        few frames later. Either check could be deleted with the whole suite
        still green if it were only exercised through `submit` and only asserted
        as "a ContractError happened". `route` is public and documented as a pure
        decision, so it is where they are visible.
        """
        good = dict(subject='subject-demo', policy=self.policy, now=100)
        for now in (None, '100', 100.0, True, object()):
            with self.subTest(now=repr(now)):
                with self.assertRaisesRegex(ContractError, 'now must be an integer'):
                    self.orchestrator.route(**{**good, 'now': now})
        for policy in (None, 'policy', 42, {}):
            with self.subTest(policy=type(policy)):
                with self.assertRaisesRegex(ContractError, 'policy is invalid'):
                    self.orchestrator.route(**{**good, 'policy': policy})
        wrong = self.policy_for(orchestrator_id='orchestrator-other')
        with self.assertRaisesRegex(ContractError, 'does not name this orchestrator'):
            self.orchestrator.route(**{**good, 'policy': wrong})


def reservation(**changes):
    fields = dict(job_id='job-demo', subject='subject-demo', handoff_sha256='a' * 64,
                  expires_at=160, reserved_at=110)
    fields.update(changes)
    return Reservation(**fields)


class RacingLedger(ProcessLocalJobLedger):
    """Another instance burns the id between this one's check and its insert.

    `is_burned` answers from before that moment and `reserve` from after it,
    which is exactly what two orchestrators on one database can observe.
    """

    def is_burned(self, job_id):
        return False

    def reserve(self, reservation):
        return False


class JobLedgerContractTest(Fixture, unittest.TestCase):
    """The ledger contract, independent of where the ledger keeps its rows."""

    def test_a_reservation_refuses_what_the_start_check_would_refuse(self):
        for changes, message in (
                (dict(job_id=''), 'job_id must be a non-empty string'),
                (dict(job_id=7), 'job_id must be a non-empty string'),
                (dict(subject=''), 'subject must be a non-empty string'),
                (dict(job_id='j' * 129), 'job_id must be at most 128 bytes'),
                (dict(handoff_sha256='A' * 64), 'lowercase SHA-256 digest'),
                (dict(handoff_sha256='a' * 63), 'lowercase SHA-256 digest'),
                (dict(handoff_sha256=None), 'lowercase SHA-256 digest'),
                (dict(expires_at=0, reserved_at=0), 'outside the range'),
                (dict(reserved_at=True), 'outside the range'),
                (dict(expires_at=4102444801), 'outside the range'),
                (dict(expires_at=110), 'before its handoff expires'),
                (dict(expires_at=109), 'before its handoff expires')):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ContractError, message):
                    reservation(**changes)
        self.assertEqual(reservation().job_id, 'job-demo')

    def test_the_process_local_ledger_moves_each_job_forward_once(self):
        ledger = ProcessLocalJobLedger()
        with self.assertRaisesRegex(ContractError, 'only a reserved job'):
            ledger.commit_execution(reservation(), now=110)
        self.assertTrue(ledger.reserve(reservation()))
        self.assertFalse(ledger.reserve(reservation()))
        ledger.commit_execution(reservation(), now=110)
        with self.assertRaisesRegex(ContractError, 'only a reserved job'):
            ledger.commit_execution(reservation(), now=111)
        self.assertTrue(ledger.is_burned('job-demo'))

    def test_shared_process_local_ledger_cannot_overfill_concurrently(self):
        ledger = ProcessLocalJobLedger()
        barrier = threading.Barrier(3)
        outcomes = []

        def reserve_job(job_id):
            barrier.wait()
            outcomes.append(ledger.reserve(reservation(job_id=job_id)))

        with mock.patch.object(orchestrator_module, '_MAX_JOBS', 1):
            threads = [threading.Thread(target=reserve_job, args=(job_id,))
                       for job_id in ('job-first', 'job-second')]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(5)
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(len(ledger.job_ids()), 1)

    def test_the_ledger_is_part_of_the_orchestrator_s_configuration(self):
        for ledger in (object(), set(), ProcessLocalJobLedger):
            with self.subTest(ledger=ledger):
                with self.assertRaisesRegex(ContractError, 'job_ledger must be a JobLedger'):
                    self.orchestrator_for(job_ledger=ledger)
        with self.assertRaises(TypeError):
            JobLedger()

    def test_a_lost_race_inside_the_ledger_is_a_reused_id_and_runs_nothing(self):
        runner = self.counting(wire=b'x')
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),), job_ledger=RacingLedger())
        decision = self.denied(self.dispatch, self.wire(), orchestrator=orchestrator)
        self.assertEqual(decision.reason_code, 'JOB_ID_REUSED')
        self.assertEqual(runner.calls, [])

    def test_the_job_is_committed_to_execution_before_the_worker_sees_it(self):
        ledger = ProcessLocalJobLedger()
        seen = []

        class Observing(CountingRunner):
            def execute(self, permit, *, now):
                seen.append(dict(ledger._states))
                return super().execute(permit, now=now)

        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', Observing(
                authority=self.result_authority, wire=b'x')),),
            job_ledger=ledger)
        self.dispatch(self.wire(), orchestrator=orchestrator)
        self.assertEqual(seen, [{'job-demo': 'EXECUTION_COMMITTED'}])


# Runs in its own process so that it can be killed where no cleanup can run:
# after the ledger committed the job to execution, while the worker is running.
_CRASH_CHILD = r'''
import os, sys, time
import psycopg
from geniusnew.approvals import ApprovalStore
from geniusnew.contracts import Grant, HandoffSigner, Policy
from geniusnew.database import PostgresJobLedger
from geniusnew.gateway import Gateway
from geniusnew.orchestrator import Orchestrator, WorkerEndpoint
from geniusnew.results import WorkerAuthority
from geniusnew.workers import DeterministicSummarizer, WorkerRunner


class Stuck(WorkerRunner):
    def execute(self, permit, *, now):
        print('RUNNING', flush=True)
        time.sleep(60)


key = HandoffSigner(integrity_key=b'phase-2-test-integrity-key-32bytes')
grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic', ('summarize',),
              'isolated', False)
policy = Policy('policy-v1', 'orchestrator-demo', 60, ('summarize', 'translate'),
                ('isolated',), (grant,))
gateway = Gateway(gateway_id='gateway-test', handoff_verifier=key.verifier(),
                  approval_store=ApprovalStore())
authority = WorkerAuthority(result_key=b'a-separate-result-key-of-32bytes!')
connection = psycopg.connect(os.environ['GENIUSNEW_CRASH_DSN'], autocommit=True)
orchestrator = Orchestrator(
    orchestrator_id='orchestrator-demo', signer=key, gateway=gateway,
    workers=(WorkerEndpoint('worker-demo', Stuck(DeterministicSummarizer(),
                                                 authority=authority)),),
    job_ledger=PostgresJobLedger(connection))
orchestrator.submit({'text': 'the quick brown fox'}, subject='subject-demo',
                    job_id='job-crash', policy=policy, now=100)
'''


class PersistentLedgerTest(Fixture, unittest.TestCase):
    """Gate B2: the job ledger lives in PostgreSQL and outlives the process."""

    def setUp(self):
        super().setUp()
        self.db = PostgresDatabase()
        self.addCleanup(self.db.close)
        migrate(self.db.owner_dsn)

    def ledger(self):
        """A ledger on its own runtime connection, as a restarted process has."""
        connection = self.db.connect(runtime=True)
        self.addCleanup(connection.close)
        return PostgresJobLedger(connection)

    def rows(self):
        with self.db.connect() as owner:
            return owner.execute(
                "SELECT job_id, subject, handoff_sha256, state, created_at, reserved_at, "
                "updated_at, expires_at FROM job_ledger ORDER BY job_id").fetchall()

    def test_a_restarted_orchestrator_does_not_dispatch_a_burned_job_id(self):
        """Reversed from `test_the_ledger_is_process_local_and_this_is_the_boundary`.

        The ledger used to be a set in one process, so a restart or a second
        replica dispatched the same unexpired handoff again: the gateway keeps
        no handoff ledger and mints a fresh permit each time. With the ledger in
        the database, a fresh orchestrator on a fresh connection refuses the id
        before the gateway is asked, and its worker never runs.
        """
        wire = self.wire()
        self.dispatch(wire, orchestrator=self.orchestrator_for(job_ledger=self.ledger()))
        runner = self.counting(wire=b'x')
        restarted = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),), job_ledger=self.ledger())
        self.assertEqual(self.denied(self.dispatch, wire, orchestrator=restarted).reason_code,
                         'JOB_ID_REUSED')
        self.assertEqual(runner.calls, [])

    def test_a_dispatch_leaves_exactly_its_committed_row(self):
        wire = self.wire()
        dispatched = self.dispatch(wire, orchestrator=self.orchestrator_for(
            job_ledger=self.ledger()), now=110)
        self.assertEqual(self.rows(), [(
            'job-demo', 'subject-demo', dispatched.handoff_sha256,
            'EXECUTION_COMMITTED', 110, 110, 110, 160)])

    def test_two_instances_racing_for_one_job_id_run_it_exactly_once(self):
        """Both pass the early check and both get a permit; the database decides."""
        wire = self.wire()
        barrier = threading.Barrier(2, timeout=10)
        runner = self.counting(wire=b'x')
        instances = [self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),),
            on_admitted=lambda permit: barrier.wait(), job_ledger=self.ledger())
            for _ in range(2)]
        outcomes = []

        def race(orchestrator):
            try:
                outcomes.append(self.dispatch(wire, orchestrator=orchestrator))
            except Denied as denied:
                outcomes.append(denied.decision.reason_code)

        threads = [threading.Thread(target=race, args=(instance,)) for instance in instances]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes.count('JOB_ID_REUSED'), 1)
        self.assertEqual(sum(isinstance(outcome, Dispatch) for outcome in outcomes), 1)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual([row[3] for row in self.rows()], ['EXECUTION_COMMITTED'])

    def test_the_job_is_committed_in_the_database_before_the_worker_sees_it(self):
        observed = []
        db = self.db

        class Observing(CountingRunner):
            def execute(self, permit, *, now):
                with db.connect() as owner:
                    observed.append(owner.execute(
                        "SELECT state FROM job_ledger WHERE job_id = 'job-demo'").fetchone())
                return super().execute(permit, now=now)

        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', Observing(
                authority=self.result_authority, wire=b'x')),),
            job_ledger=self.ledger())
        self.dispatch(self.wire(), orchestrator=orchestrator)
        self.assertEqual(observed, [('EXECUTION_COMMITTED',)])

    def test_a_crash_at_the_effect_boundary_is_never_run_again(self):
        """Killed while the worker runs: the row stays committed, nothing retries it."""
        root = Path(__file__).resolve().parent.parent
        child = subprocess.Popen(
            [sys.executable, '-c', _CRASH_CHILD], cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            env={**os.environ, 'PYTHONPATH': str(root),
                 'GENIUSNEW_CRASH_DSN': self.db.runtime_dsn})
        try:
            ready, _, _ = select.select([child.stdout], [], [], 30)
            self.assertTrue(ready, 'the child never reached its worker')
            self.assertEqual(child.stdout.readline(), b'RUNNING\n')
        finally:
            child.kill()
            child.wait(10)
            child.stdout.close()
        self.assertEqual(child.returncode, -signal.SIGKILL)
        wire = self.wire(job_id='job-crash', now=100)
        [row] = self.rows()
        self.assertEqual((row[0], row[3], row[5]), ('job-crash', 'EXECUTION_COMMITTED', 100))
        self.assertEqual(row[2], hashlib.sha256(wire).hexdigest())
        runner = self.counting(wire=b'x')
        restarted = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),), job_ledger=self.ledger())
        decision = self.denied(self.dispatch, wire, orchestrator=restarted,
                               job_id='job-crash', now=110)
        self.assertEqual(decision.reason_code, 'JOB_ID_REUSED')
        self.assertEqual(runner.calls, [])

    def test_a_commit_that_cannot_be_recorded_runs_nothing_and_keeps_the_id(self):
        """The database goes away between reservation and commit."""
        connection = self.db.connect(runtime=True)
        self.addCleanup(connection.close)

        class Failing(PostgresJobLedger):
            def commit_execution(self, reservation, *, now):
                connection.close()
                super().commit_execution(reservation, now=now)

        runner = self.counting(wire=b'x')
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),),
            job_ledger=Failing(connection))
        wire = self.wire()
        with self.assertRaisesRegex(ContractError, 'job ledger is unavailable') as caught:
            self.dispatch(wire, orchestrator=orchestrator)
        self.assertNotIsInstance(caught.exception, DispatchAttempted)
        self.assertEqual(runner.calls, [])
        self.assertEqual([row[3] for row in self.rows()], ['RESERVED'])
        restarted = self.orchestrator_for(job_ledger=self.ledger())
        self.assertEqual(self.denied(self.dispatch, wire, orchestrator=restarted).reason_code,
                         'JOB_ID_REUSED')

    def test_a_refused_execution_keeps_its_id_burned(self):
        runner = self.counting(raises=ContractError('worker refused'))
        orchestrator = self.orchestrator_for(
            workers=(WorkerEndpoint('worker-demo', runner),), job_ledger=self.ledger())
        wire = self.wire()
        with self.assertRaises(DispatchAttempted):
            self.dispatch(wire, orchestrator=orchestrator)
        self.assertEqual([row[3] for row in self.rows()], ['EXECUTION_COMMITTED'])
        self.assertEqual(self.denied(self.dispatch, wire, orchestrator=orchestrator).reason_code,
                         'JOB_ID_REUSED')
        self.assertEqual(len(runner.calls), 1)

    def test_the_runtime_cannot_release_or_rewind_a_burned_id(self):
        self.dispatch(self.wire(), orchestrator=self.orchestrator_for(job_ledger=self.ledger()))
        before = self.rows()
        with self.db.connect(runtime=True) as runtime:
            for query, error in (
                    ("DELETE FROM job_ledger", psycopg.errors.InsufficientPrivilege),
                    ("UPDATE job_ledger SET state = 'RESERVED'", psycopg.errors.CheckViolation),
                    ("UPDATE job_ledger SET job_id = 'job-other'", psycopg.errors.CheckViolation),
                    ("UPDATE job_ledger SET state = 'REFUSED'", psycopg.errors.CheckViolation)):
                with self.subTest(query=query), self.assertRaises(error):
                    runtime.execute(query)
        self.assertEqual(self.rows(), before)

    def test_a_ledger_needs_an_autocommit_psycopg_connection(self):
        with self.assertRaisesRegex(ContractError, 'needs a psycopg connection'):
            PostgresJobLedger(object())
        connection = psycopg.connect(self.db.runtime_dsn, connect_timeout=5)
        self.addCleanup(connection.close)
        with self.assertRaisesRegex(ContractError, 'autocommit mode'):
            PostgresJobLedger(connection)

    def test_a_ledger_does_not_reserve_inside_an_outer_transaction(self):
        connection = self.db.connect(runtime=True)
        self.addCleanup(connection.close)
        ledger = PostgresJobLedger(connection)
        with connection.transaction(force_rollback=True):
            with self.assertRaisesRegex(ContractError, 'existing transaction'):
                ledger.reserve(reservation())
        self.assertEqual(self.rows(), [])

    def test_the_ledger_refuses_anything_but_a_reservation(self):
        ledger = self.ledger()
        with self.assertRaisesRegex(ContractError, 'reserves only a Reservation'):
            ledger.reserve(object())
        with self.assertRaisesRegex(ContractError, 'commits only a Reservation'):
            ledger.commit_execution(object(), now=110)
        self.assertEqual(self.rows(), [])

    def test_only_a_reserved_row_can_be_committed_and_only_once(self):
        ledger = self.ledger()
        with self.assertRaisesRegex(ContractError, 'could not be committed'):
            ledger.commit_execution(reservation(), now=110)
        self.assertTrue(ledger.reserve(reservation()))
        with self.assertRaisesRegex(ContractError, 'could not be committed'):
            ledger.commit_execution(reservation(handoff_sha256='b' * 64), now=110)
        ledger.commit_execution(reservation(), now=110)
        with self.assertRaisesRegex(ContractError, 'could not be committed'):
            self.ledger().commit_execution(reservation(), now=111)
        self.assertFalse(self.ledger().reserve(reservation()))
        self.assertEqual([row[3] for row in self.rows()], ['EXECUTION_COMMITTED'])


if __name__ == '__main__':
    unittest.main()
