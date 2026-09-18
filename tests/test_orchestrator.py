import unittest
from unittest import mock

from geniusnew import orchestrator as orchestrator_module
from geniusnew.approvals import ApprovalStore, create_scope
from geniusnew.audit import AuditAuthority, event_from_handoff
from geniusnew.contracts import ContractError, Grant, Policy, issue, validate
from geniusnew.gateway import Gateway
from geniusnew.keys import derive_keys
from geniusnew.orchestrator import (ACTIONS, DENIAL_REASONS, Decision, Denied,
                                    Dispatch, Orchestrator, WorkerEntry)
from geniusnew.results import WorkerAuthority, accept
from geniusnew.workers import DeterministicSummarizer, WorkerRunner

# Zero-entropy and self-describing. Its job is to be unmistakable if it ever
# turns up somewhere a dispatcher's exception text must not reach.
CANARY = 'DISPATCH-CANARY-MUST-NOT-REACH-A-REFUSAL'

ROOT_SECRET = b'an-orchestrator-test-root-secret!!!!'

# The helpers below take a sentinel rather than None. Defaulting on falsiness is
# how four tests in `test_results.py` ended up passing for the wrong reason: a
# `policy=None` case silently became the valid policy and asserted nothing.
DEFAULT = object()


class Fixture:
    """Shared setup. A mixin, not a TestCase: subclassing one re-runs its suite."""

    def setUp(self):
        self.keys = derive_keys(ROOT_SECRET)
        self.store = ApprovalStore()
        self.gateway = Gateway(gateway_id='gateway-1',
                               integrity_key=self.keys.integrity_key,
                               approval_store=self.store)
        self.worker_authority = WorkerAuthority(result_key=self.keys.result_key,
                                                integrity_key=self.keys.integrity_key)
        self.runner = WorkerRunner(DeterministicSummarizer(),
                                   authority=self.worker_authority)
        self.policy = self.policy_for()
        self.orchestrator = self.orchestrator_for()

    def policy_for(self, *, requires_approval=False, tools=('summarize',),
                   allowed=('summarize', 'translate'), worker_agent_id='worker-demo',
                   orchestrator_id='orchestrator-1'):
        grant = Grant('subject-demo', 'user-demo', worker_agent_id, 'basic',
                      tools, 'isolated', requires_approval)
        return Policy('policy-v1', orchestrator_id, 60, allowed, ('isolated',), (grant,))

    def entry(self, *, worker_id='worker-demo', tool='summarize', dispatch=DEFAULT):
        return WorkerEntry(worker_id, tool,
                           self.runner.execute if dispatch is DEFAULT else dispatch)

    def orchestrator_for(self, *, workers=DEFAULT, gateway=DEFAULT,
                         orchestrator_id='orchestrator-1'):
        return Orchestrator(orchestrator_id=orchestrator_id,
                            integrity_key=self.keys.integrity_key,
                            gateway=self.gateway if gateway is DEFAULT else gateway,
                            workers=(self.entry(),) if workers is DEFAULT else workers)

    def submit(self, orchestrator=DEFAULT, *, request=DEFAULT, subject='subject-demo',
               job_id='job-1', tool='summarize', policy=DEFAULT, now=1000,
               approval_token=None):
        return (self.orchestrator if orchestrator is DEFAULT else orchestrator).submit(
            {'text': 'the quick brown fox'} if request is DEFAULT else request,
            subject=subject, job_id=job_id, tool=tool,
            policy=self.policy if policy is DEFAULT else policy,
            now=now, approval_token=approval_token)

    def denied(self, callable_, *args, **kwargs):
        """Run something expected to be denied and hand back its decision."""
        with self.assertRaises(Denied) as caught:
            callable_(*args, **kwargs)
        return caught.exception.decision


class Counter:
    """A dispatcher that records every call, and optionally misbehaves."""

    def __init__(self, *, wire=b'not-a-real-result', raises=None):
        self.calls = []
        self.wire = wire
        self.raises = raises

    def __call__(self, permit, *, now):
        self.calls.append((permit, now))
        if self.raises is not None:
            raise self.raises
        return self.wire


class OrchestratorTest(Fixture, unittest.TestCase):
    """Roadmap step 13: admission, assignment, dispatch."""

    # --- the path it exists for ---------------------------------------------

    def test_a_submitted_job_runs_and_comes_back_signed(self):
        dispatch = self.submit()
        self.assertIsInstance(dispatch, Dispatch)
        handoff = validate(dispatch.handoff_wire, subject='subject-demo',
                           job_id='job-1', policy=self.policy,
                           integrity_key=self.keys.integrity_key, now=1001)
        taken = accept(dispatch.result_wire, handoff=handoff,
                       authority=self.worker_authority, now=1002)
        self.assertTrue(taken.succeeded)
        self.assertEqual(taken.reason_code, 'WORK_COMPLETED')
        self.assertEqual(dispatch.worker_id, 'worker-demo')

    def test_it_does_not_judge_the_result_it_collected(self):
        """A FAILED result comes back as evidence, not as a verdict.

        The orchestrator holds no result key and never calls `accept`, so it has
        no way to declare its own job a success. Constitution section 8 keeps
        that a separate role, and step 14 is where it lands.
        """
        class Exploding(DeterministicSummarizer):
            def run(self, payload):
                raise RuntimeError('boom')

        runner = WorkerRunner(Exploding(), authority=self.worker_authority)
        orchestrator = self.orchestrator_for(
            workers=(self.entry(dispatch=runner.execute),))
        dispatch = self.submit(orchestrator)

        for name in ('succeeded', 'status', 'ok', 'accepted'):
            self.assertFalse(hasattr(dispatch, name), name)
        handoff = validate(dispatch.handoff_wire, subject='subject-demo',
                           job_id='job-1', policy=self.policy,
                           integrity_key=self.keys.integrity_key, now=1001)
        taken = accept(dispatch.result_wire, handoff=handoff,
                       authority=self.worker_authority, now=1002)
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, 'WORKER_FAILED')

    def test_it_holds_no_other_role_authority_in_its_own_state(self):
        """No shared mutable authority: not the approvals, not the signing keys.

        In one process nothing is truly out of reach — the dispatcher is a bound
        method and Python offers no memory boundary. What is checkable is that
        this component keeps no usable handle of another role's authority, and
        has no API through which to mint a permit or accept a result.
        """
        for value in vars(self.orchestrator).values():
            self.assertNotIsInstance(value, (ApprovalStore, WorkerAuthority,
                                             AuditAuthority))

    # --- assignment is a decision -------------------------------------------

    def test_a_worker_the_grant_does_not_name_is_refused(self):
        """The grant names the agent, and this is the only layer that checks it.

        The worker boundary only verifies that its tool is in the handoff's tool
        list, and every tool in the grant passes that. So dispatching `summarize`
        to some other registered worker would reach an agent the policy never
        named, and every layer after this one would agree.
        """
        other = Counter()
        orchestrator = self.orchestrator_for(
            workers=(self.entry(worker_id='worker-elsewhere', dispatch=other),))
        decision = self.denied(self.submit, orchestrator)
        self.assertEqual(decision.reason_code, 'WORKER_NOT_GRANTED')
        self.assertEqual(other.calls, [])

    def test_assignment_does_not_depend_on_registration_order(self):
        second = WorkerEntry('worker-demo', 'translate', Counter())
        first = self.entry()
        forwards = self.orchestrator_for(workers=(first, second))
        backwards = self.orchestrator_for(workers=(second, first))
        for tool in ('summarize', 'translate'):
            with self.subTest(tool=tool):
                self.assertEqual(
                    forwards.assign(subject='subject-demo', tool=tool,
                                    policy=self.policy_for(tools=('summarize', 'translate')),
                                    now=1000),
                    backwards.assign(subject='subject-demo', tool=tool,
                                     policy=self.policy_for(tools=('summarize', 'translate')),
                                     now=1000))
        self.assertEqual(forwards.tools, backwards.tools)

    def test_two_workers_for_one_tool_are_refused_at_construction(self):
        """Two would make the assignment a choice, and a choice nobody can pin down."""
        with self.assertRaisesRegex(ContractError, 'same tool'):
            self.orchestrator_for(workers=(self.entry(),
                                           self.entry(worker_id='worker-other')))

    def test_assignment_is_deterministic_across_instances(self):
        """Same request, same bytes — no clock and no entropy anywhere in the path."""
        first = self.submit(self.orchestrator_for(), job_id='job-a')
        second = self.submit(self.orchestrator_for(), job_id='job-a')
        self.assertEqual(first.handoff_wire, second.handoff_wire)
        self.assertEqual(first.result_wire, second.result_wire)
        self.assertEqual(first.decisions, second.decisions)

    # --- every refusal, by the reason it claims ------------------------------

    def test_each_admission_refusal_names_its_own_reason(self):
        """Refused is not enough: refused by the check under test.

        Several of these inputs would be refused by a later check too — an
        ungranted tool with no worker registered for it reaches
        NO_WORKER_FOR_TOOL — so asserting only that a refusal happened would
        survive deleting the earlier one.
        """
        two_tools = self.policy_for(tools=('summarize', 'translate'))
        cases = [
            ('POLICY_NOT_FOR_THIS_ORCHESTRATOR',
             dict(policy=self.policy_for(orchestrator_id='orchestrator-other'))),
            ('SUBJECT_NOT_AUTHORIZED', dict(subject='subject-unknown')),
            ('TOOL_NOT_IN_POLICY', dict(tool='exfiltrate')),
            ('TOOL_NOT_GRANTED', dict(tool='translate')),
            ('NO_WORKER_FOR_TOOL', dict(tool='translate', policy=two_tools)),
        ]
        for reason, arguments in cases:
            with self.subTest(reason=reason):
                decision = self.denied(self.submit, **arguments)
                self.assertEqual(decision.reason_code, reason)
                self.assertEqual(decision.action, 'HANDOFF_REJECTED')
                self.assertEqual(decision.decision, 'DENIED')

    def test_a_refused_job_never_reaches_a_worker_or_burns_its_id(self):
        counter = Counter()
        orchestrator = self.orchestrator_for(workers=(self.entry(dispatch=counter),))
        self.denied(self.submit, orchestrator, tool='exfiltrate')
        self.assertEqual(counter.calls, [])
        self.assertEqual(orchestrator._jobs, set())

    def test_a_job_id_is_burned_once_and_stays_burned(self):
        """A retry needs a new id: releasing a burned one is a replay window."""
        counter = Counter(wire=b'x')
        orchestrator = self.orchestrator_for(workers=(self.entry(dispatch=counter),))
        self.submit(orchestrator, job_id='job-once')
        decision = self.denied(self.submit, orchestrator, job_id='job-once')
        self.assertEqual(decision.reason_code, 'JOB_ID_REUSED')
        self.assertEqual(len(counter.calls), 1)

    def test_a_failed_dispatch_does_not_release_the_job_id(self):
        counter = Counter(raises=ContractError('worker said no'))
        orchestrator = self.orchestrator_for(workers=(self.entry(dispatch=counter),))
        with self.assertRaises(ContractError):
            self.submit(orchestrator, job_id='job-once')
        decision = self.denied(self.submit, orchestrator, job_id='job-once')
        self.assertEqual(decision.reason_code, 'JOB_ID_REUSED')
        self.assertEqual(len(counter.calls), 1)

    def test_the_job_ledger_is_bounded(self):
        """An unbounded set a caller can grow is memory exhaustion with a receipt."""
        counter = Counter(wire=b'x')
        orchestrator = self.orchestrator_for(workers=(self.entry(dispatch=counter),))
        with mock.patch.object(orchestrator_module, '_MAX_JOBS', 1):
            self.submit(orchestrator, job_id='job-1')
            decision = self.denied(self.submit, orchestrator, job_id='job-2')
        self.assertEqual(decision.reason_code, 'JOB_LEDGER_FULL')
        self.assertEqual(len(counter.calls), 1)

    def test_an_oversized_job_id_is_refused_before_the_ledger_stores_it(self):
        with self.assertRaisesRegex(ContractError, 'at most 128 bytes'):
            self.submit(job_id='j' * 129)
        self.assertEqual(self.orchestrator._jobs, set())

    # --- it cannot admit its own job ----------------------------------------

    def test_the_gateway_revalidates_and_its_refusal_travels_unchanged(self):
        """A gateway with a different key refuses, and that stays the gateway's word.

        Relabelling another instance's refusal as an orchestrator decision would
        be the same error as confirming one's own.
        """
        stranger = Gateway(gateway_id='gateway-2',
                           integrity_key=b'a-different-integrity-key-32bytes',
                           approval_store=ApprovalStore())
        orchestrator = self.orchestrator_for(gateway=stranger)
        with self.assertRaises(ContractError) as caught:
            self.submit(orchestrator)
        self.assertNotIsInstance(caught.exception, Denied)

    def test_a_gateway_that_returns_something_else_is_refused(self):
        class Forged(Gateway):
            def admit(self, *args, **kwargs):
                return 'permit'

        forged = Forged(gateway_id='gateway-1',
                        integrity_key=self.keys.integrity_key,
                        approval_store=ApprovalStore())
        counter = Counter()
        orchestrator = self.orchestrator_for(
            gateway=forged, workers=(self.entry(dispatch=counter),))
        with self.assertRaisesRegex(ContractError, 'dispatch permit'):
            self.submit(orchestrator)
        self.assertEqual(counter.calls, [])

    def test_a_permit_for_another_handoff_is_refused(self):
        """The permit has to be for the job that was sent.

        A gateway returning a permit for a different handoff would have the
        worker run a job this orchestrator never issued, while the handoff wire
        it reports still describes the one it did — leaving the result verifier
        checking a result against the wrong contract.
        """
        elsewhere = issue({'text': 'another job entirely'}, subject='subject-demo',
                          job_id='job-other', policy=self.policy,
                          integrity_key=self.keys.integrity_key, now=1000)

        class Substituting(Gateway):
            def admit(inner, wire, **kwargs):
                return Gateway.admit(inner, elsewhere, **{**kwargs, 'job_id': 'job-other'})

        counter = Counter()
        orchestrator = self.orchestrator_for(
            gateway=Substituting(gateway_id='gateway-1',
                                 integrity_key=self.keys.integrity_key,
                                 approval_store=ApprovalStore()),
            workers=(self.entry(dispatch=counter),))
        with self.assertRaisesRegex(ContractError, 'different handoff'):
            self.submit(orchestrator)
        self.assertEqual(counter.calls, [])

    def test_the_approval_path_goes_through_the_gateway(self):
        """The orchestrator forwards the token; only the gateway may consume it.

        The two job ids are not cosmetic. A job id is burned on reservation, so
        the run that is refused for a missing token cannot be retried under the
        same id — and an approval is scoped to the exact wire, job id included,
        so a retry needs a fresh approval as well. That is the cost of not
        releasing a burned id, and it is the cheaper of the two mistakes.
        """
        policy = self.policy_for(requires_approval=True)
        request = {'text': 'the quick brown fox'}
        with self.assertRaisesRegex(ContractError, 'required'):
            self.submit(job_id='job-unapproved', policy=policy)

        wire = issue(request, subject='subject-demo', job_id='job-approved',
                     policy=policy, integrity_key=self.keys.integrity_key, now=1000)
        scope = create_scope(wire, subject='subject-demo', job_id='job-approved',
                             policy=policy, integrity_key=self.keys.integrity_key,
                             now=1000)
        granted = self.store.grant(scope, now=1000, ttl_seconds=30)
        dispatch = self.submit(job_id='job-approved', policy=policy,
                               approval_token=granted.token)
        # The orchestrator reproduced the exact bytes the approval was scoped to.
        self.assertEqual(dispatch.handoff_wire, wire)
        # And the token was consumed by the gateway, so it buys nothing twice.
        with self.assertRaises(ContractError):
            self.submit(job_id='job-approved-again', policy=policy,
                        approval_token=granted.token)

    # --- dispatch is one call, to one worker --------------------------------

    def test_a_refused_dispatch_is_not_retried_on_another_worker(self):
        """No failover. A second attempt is default retry wearing default deny."""
        refusing = Counter(raises=ContractError('no'))
        spare = Counter()
        orchestrator = self.orchestrator_for(workers=(
            self.entry(dispatch=refusing),
            WorkerEntry('worker-demo', 'translate', spare)))
        with self.assertRaises(ContractError):
            self.submit(orchestrator)
        self.assertEqual(len(refusing.calls), 1)
        self.assertEqual(spare.calls, [])

    def test_a_dispatcher_exception_text_never_reaches_the_refusal(self):
        counter = Counter(raises=RuntimeError(CANARY))
        orchestrator = self.orchestrator_for(workers=(self.entry(dispatch=counter),))
        with self.assertRaises(ContractError) as caught:
            self.submit(orchestrator)
        self.assertNotIn(CANARY, str(caught.exception))
        self.assertEqual(str(caught.exception), 'dispatch failed')

    def test_a_dispatcher_that_returns_no_wire_is_refused(self):
        for value in (None, '', b'', 'wire', 42, {}, bytearray(b'x')):
            with self.subTest(value=repr(value)):
                orchestrator = self.orchestrator_for(
                    workers=(self.entry(dispatch=Counter(wire=value)),))
                with self.assertRaisesRegex(ContractError, 'signed result wire'):
                    self.submit(orchestrator)

    # --- decisions have to be auditable -------------------------------------

    def test_every_action_it_records_is_in_the_audit_vocabulary(self):
        """A decision this component cannot audit is a decision nobody can see."""
        from geniusnew.audit import _ACTIONS, _DECISIONS, _REASON_CODE
        self.assertTrue(ACTIONS.issubset(_ACTIONS), ACTIONS - _ACTIONS)
        self.assertIn('DENIED', _DECISIONS)
        for reason in DENIAL_REASONS | {'POLICY_SATISFIED'}:
            with self.subTest(reason=reason):
                self.assertRegex(reason, _REASON_CODE)

    def test_a_decision_becomes_an_audit_event(self):
        dispatch = self.submit()
        handoff = validate(dispatch.handoff_wire, subject='subject-demo',
                           job_id='job-1', policy=self.policy,
                           integrity_key=self.keys.integrity_key, now=1001)
        authority = AuditAuthority(audit_key=self.keys.audit_key)
        actor = authority.actor('orchestrator', 'orchestrator-1')
        for decision in dispatch.decisions:
            event = event_from_handoff(handoff, trace_id='trace-1', actor=actor,
                                       action=decision.action,
                                       decision=decision.decision,
                                       reason_code=decision.reason_code,
                                       occurred_at=decision.occurred_at)
            self.assertEqual(event.action, decision.action)

    def test_the_recorded_decisions_are_its_own_and_in_order(self):
        dispatch = self.submit()
        self.assertEqual([decision.action for decision in dispatch.decisions],
                         ['HANDOFF_ISSUED', 'EXECUTION_DISPATCHED'])
        # HANDOFF_ADMITTED is absent on purpose: that is the gateway's decision.
        self.assertNotIn('HANDOFF_ADMITTED',
                         {decision.action for decision in dispatch.decisions})

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

    def test_a_denial_is_a_contract_error_carrying_a_closed_reason(self):
        decision = self.denied(self.submit, tool='exfiltrate')
        self.assertIn(decision.reason_code, DENIAL_REASONS)
        with self.assertRaises(ContractError):
            self.submit(tool='exfiltrate')

    # --- construction fails closed ------------------------------------------

    def test_construction_refuses_anything_it_cannot_rely_on(self):
        good = dict(orchestrator_id='orchestrator-1',
                    integrity_key=self.keys.integrity_key,
                    gateway=self.gateway, workers=(self.entry(),))
        cases = [
            ('orchestrator_id', 'Orchestrator-1'), ('orchestrator_id', ''),
            ('orchestrator_id', 'x' * 64), ('orchestrator_id', None),
            ('orchestrator_id', b'orchestrator-1'),
            ('integrity_key', b'too-short'), ('integrity_key', None),
            ('integrity_key', 'x' * 32),
            ('gateway', None), ('gateway', 'gateway'), ('gateway', object()),
            ('workers', ()), ('workers', ('worker',)), ('workers', (None,)),
            ('workers', (self.runner,)),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=repr(value)[:40]):
                with self.assertRaises(ContractError):
                    Orchestrator(**{**good, field: value})

    def test_a_worker_entry_refuses_anything_it_cannot_rely_on(self):
        for arguments in (('Worker-1', 'summarize', print), ('', 'summarize', print),
                          ('x' * 64, 'summarize', print), (None, 'summarize', print),
                          ('worker-demo', '', print), ('worker-demo', None, print),
                          ('worker-demo', b'summarize', print),
                          ('worker-demo', 'summarize', None),
                          ('worker-demo', 'summarize', 'execute')):
            with self.subTest(arguments=repr(arguments)[:50]):
                with self.assertRaises(ContractError):
                    WorkerEntry(*arguments)

    def test_submit_fails_closed_on_malformed_arguments(self):
        for now in (None, '1000', 1000.0, object()):
            with self.subTest(now=type(now)), self.assertRaises(ContractError):
                self.submit(now=now)
        for tool in (None, '', 42, b'summarize'):
            with self.subTest(tool=repr(tool)), self.assertRaises(ContractError):
                self.submit(tool=tool)
        for job_id in (None, '', 42, b'job-1'):
            with self.subTest(job_id=repr(job_id)), self.assertRaises(ContractError):
                self.submit(job_id=job_id)
        for policy in (None, 'policy', 42, object()):
            with self.subTest(policy=type(policy)), self.assertRaises(ContractError):
                self.submit(policy=policy)

    def test_assign_fails_closed_on_malformed_arguments(self):
        """Asserted on `assign` and by message, because `submit` hides both checks.

        Downstream of `submit`, `issue` refuses a non-integer `now` with the
        same sentence, and a non-string tool is refused by the allow-list
        comparison a few lines later. Either check could be deleted with the
        whole suite still green if it were only exercised through `submit` and
        only asserted as "a ContractError happened". `assign` is public and
        documented as a pure decision, so it is where they are visible.
        """
        good = dict(subject='subject-demo', tool='summarize', policy=self.policy,
                    now=1000)
        for now in (None, '1000', 1000.0, True, object()):
            with self.subTest(now=repr(now)):
                with self.assertRaisesRegex(ContractError, 'now must be an integer'):
                    self.orchestrator.assign(**{**good, 'now': now})
        for tool in (None, '', 42, b'summarize'):
            with self.subTest(tool=repr(tool)):
                with self.assertRaisesRegex(ContractError, 'tool must be a non-empty string'):
                    self.orchestrator.assign(**{**good, 'tool': tool})
        for policy in (None, 'policy', 42, object()):
            with self.subTest(policy=type(policy)):
                with self.assertRaisesRegex(ContractError, 'policy is invalid'):
                    self.orchestrator.assign(**{**good, 'policy': policy})


if __name__ == '__main__':
    unittest.main()
