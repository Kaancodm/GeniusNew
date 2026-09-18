import unittest
from unittest import mock

from geniusnew import orchestrator as orchestrator_module
from geniusnew.approvals import ApprovalStore, create_scope
from geniusnew.audit import AuditAuthority, event_from_handoff
from geniusnew.contracts import ContractError, Grant, Policy, validate
from geniusnew.gateway import Gateway
from geniusnew.orchestrator import (ACTIONS, DENIALS, Decision, Denied, Dispatch,
                                    Orchestrator, WorkerEndpoint)
from geniusnew.results import WorkerAuthority, accept
from geniusnew.workers import DeterministicSummarizer, Worker, WorkerRunner

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
        self.key = b'phase-2-test-integrity-key-32bytes'
        self.store = ApprovalStore(token_source=lambda: b'a' * 32)
        self.gateway = Gateway(gateway_id='gateway-test', integrity_key=self.key,
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
                         orchestrator_id='orchestrator-demo', integrity_key=DEFAULT):
        return Orchestrator(
            orchestrator_id=orchestrator_id,
            integrity_key=self.key if integrity_key is DEFAULT else integrity_key,
            gateway=self.gateway if gateway is DEFAULT else gateway,
            workers=(self.endpoint,) if workers is DEFAULT else workers)

    def counting(self, **arguments):
        return CountingRunner(authority=self.result_authority, **arguments)

    def admit(self, *, orchestrator=DEFAULT, policy=DEFAULT, job_id='job-demo',
              now=100, request=DEFAULT, subject='subject-demo'):
        return (self.orchestrator if orchestrator is DEFAULT else orchestrator).admit(
            {'text': 'the quick brown fox'} if request is DEFAULT else request,
            subject=subject, job_id=job_id,
            policy=self.policy if policy is DEFAULT else policy, now=now)

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
                           integrity_key=self.key, now=now)
        return accept(dispatched.result_wire, handoff=handoff,
                      authority=self.result_authority, now=now)


class OrchestratorTest(Fixture, unittest.TestCase):
    """Roadmap step 13: admission, routing, dispatch."""

    # --- the path it exists for ---------------------------------------------

    def test_admission_derives_identity_and_capabilities_only_from_policy(self):
        handoff = validate(self.admit(), subject='subject-demo', job_id='job-demo',
                           policy=self.policy, integrity_key=self.key, now=101)
        self.assertEqual(handoff.user_id, 'user-demo')
        self.assertEqual(handoff.worker_agent_id, 'worker-demo')
        self.assertEqual(handoff.tools, ('summarize',))
        self.assertEqual(handoff.orchestrator_id, 'orchestrator-demo')

    def test_non_approval_job_runs_through_gateway_and_selected_worker(self):
        dispatched = self.dispatch(self.admit())
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
                           integrity_key=b'a-different-integrity-key-32bytes',
                           approval_store=ApprovalStore())
        orchestrator = self.orchestrator_for(gateway=stranger)
        wire = self.admit(orchestrator=orchestrator)
        validate(wire, subject='subject-demo', job_id='job-demo',
                 policy=self.policy, integrity_key=self.key, now=101)
        # And the gateway with the other key refuses it, as an independent
        # verifier must — that refusal stays the gateway's word, not a Denied.
        with self.assertRaises(ContractError) as caught:
            self.dispatch(wire, orchestrator=orchestrator)
        self.assertNotIsInstance(caught.exception, Denied)

    # --- routing is a fact of the grant -------------------------------------

    def test_routing_is_exactly_by_trusted_worker_id(self):
        missing = self.policy_for(worker_agent_id='worker-missing')
        wire = self.admit(policy=missing)
        decision = self.denied(self.dispatch, wire, policy=missing)
        self.assertEqual(decision.reason_code, 'WORKER_NOT_CONFIGURED')

    def test_misconfigured_endpoint_tool_is_refused_before_gateway(self):
        endpoint = WorkerEndpoint('worker-demo', WorkerRunner(
            OtherToolWorker(), authority=self.result_authority))
        orchestrator = self.orchestrator_for(workers=(endpoint,))
        wire = self.admit(orchestrator=orchestrator)
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
        self.assertEqual(orchestrator._jobs, set())

    def test_misrouting_is_refused_before_an_approval_can_be_burned(self):
        """A one-time approval must not pay for a job that was never routable."""
        policy = self.policy_for(worker_agent_id='worker-missing',
                                 requires_approval=True)
        wire = self.admit(policy=policy)
        scope = create_scope(wire, subject='subject-demo', job_id='job-demo',
                             policy=policy, integrity_key=self.key, now=101)
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
        wire = self.admit(policy=policy)
        with self.assertRaisesRegex(ContractError, 'required'):
            self.dispatch(wire, policy=policy, now=102)
        self.assertEqual(self.orchestrator._jobs, set())

        scope = create_scope(wire, subject='subject-demo', job_id='job-demo',
                             policy=policy, integrity_key=self.key, now=101)
        granted = self.store.grant(scope, now=101, ttl_seconds=30)
        # The same id goes through once the approval is there. The result is not
        # accepted here: an approval-bound wire stays PENDING_APPROVAL, which
        # `validate` refuses outright, so step 14's verifier will need the
        # pending path for these — one more reason acceptance is its own role.
        dispatched = self.dispatch(wire, policy=policy, now=102,
                                   approval_token=granted.token)
        self.assertIsInstance(dispatched.result_wire, bytes)
        self.assertEqual(self.orchestrator._jobs, {'job-demo'})

    def test_the_approval_is_consumed_by_the_gateway_and_only_once(self):
        policy = self.policy_for(requires_approval=True)
        wire = self.admit(policy=policy)
        scope = create_scope(wire, subject='subject-demo', job_id='job-demo',
                             policy=policy, integrity_key=self.key, now=101)
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

    def test_an_oversized_job_id_is_refused_before_the_ledger_stores_it(self):
        for call in (lambda: self.admit(job_id='j' * 129),
                     lambda: self.submit(job_id='j' * 129),
                     lambda: self.dispatch(b'wire', job_id='j' * 129)):
            with self.subTest(call=call):
                with self.assertRaisesRegex(ContractError, 'at most 128 bytes'):
                    call()
        self.assertEqual(self.orchestrator._jobs, set())

    # --- it cannot admit its own job ----------------------------------------

    def test_the_gateway_still_rejects_a_tampered_wire(self):
        wire = self.admit()
        with self.assertRaises(ContractError):
            self.dispatch(wire.replace(b'quick', b'QUICK'))

    def test_a_gateway_that_returns_something_else_is_refused(self):
        class Forged(Gateway):
            def admit(self, *args, **kwargs):
                return 'permit'

        runner = self.counting()
        orchestrator = self.orchestrator_for(
            gateway=Forged(gateway_id='gateway-test', integrity_key=self.key,
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
        elsewhere = self.admit(job_id='job-other')

        class Substituting(Gateway):
            def admit(inner, wire, **kwargs):
                return Gateway.admit(inner, elsewhere,
                                     **{**kwargs, 'job_id': 'job-other'})

        runner = self.counting()
        orchestrator = self.orchestrator_for(
            gateway=Substituting(gateway_id='gateway-test', integrity_key=self.key,
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
                           integrity_key=self.key, now=101)
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

    # --- construction fails closed ------------------------------------------

    def test_constructor_configuration_fails_closed(self):
        for orchestrator_id in ('', 'UPPER', 'with space', None, 42, 'x' * 64):
            with self.subTest(orchestrator_id=orchestrator_id):
                with self.assertRaises(ContractError):
                    self.orchestrator_for(orchestrator_id=orchestrator_id)
        for integrity_key in (b'too-short', None, 'x' * 32, 42):
            with self.subTest(integrity_key=repr(integrity_key)[:20]):
                with self.assertRaisesRegex(ContractError, 'integrity_key'):
                    self.orchestrator_for(integrity_key=integrity_key)
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
                    self.admit(policy=policy)
                with self.assertRaisesRegex(ContractError, 'policy is invalid'):
                    self.submit(policy=policy)
        for job_id in (None, '', 42, b'job-demo'):
            with self.subTest(job_id=repr(job_id)):
                with self.assertRaisesRegex(ContractError, 'job_id must be'):
                    self.admit(job_id=job_id)
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


if __name__ == '__main__':
    unittest.main()
