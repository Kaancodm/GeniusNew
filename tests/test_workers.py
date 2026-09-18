import hashlib
import unittest

from geniusnew.approvals import ApprovalStore
from geniusnew.contracts import ContractError, Grant, Policy, issue, validate
from geniusnew.gateway import Gateway
from geniusnew.results import WorkerAuthority, accept
from geniusnew.workers import (DeterministicSummarizer, Worker, WorkerRunner)

# Zero-entropy and self-describing. Its job is to be distinctive enough that
# finding it in a signed result would be unambiguous evidence of a leak.
CANARY = 'EXCEPTION-CANARY-MUST-NOT-REACH-A-SIGNED-RESULT'


class ExplodingWorker(Worker):
    tool = 'summarize'

    def __init__(self, exception):
        self.exception = exception
        self.seen = []

    def run(self, payload):
        self.seen.append(payload)
        raise self.exception


class ReturningWorker(Worker):
    tool = 'summarize'

    def __init__(self, value):
        self.value = value

    def run(self, payload):
        return self.value


class MutatingWorker(Worker):
    """Tries to change the handoff it was given, through the object it got."""

    tool = 'summarize'

    def run(self, payload):
        payload['text'] = 'mutated by the worker'
        return {'text': 'ok'}


class WorkerBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.key = b'phase-2-test-integrity-key-32bytes'
        self.authority = WorkerAuthority(result_key=b'a-separate-result-key-of-32bytes!')
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                      ('summarize',), 'isolated', False)
        self.policy = Policy('policy-v1', 'orchestrator-demo', 60,
                             ('summarize',), ('isolated',), (grant,))
        self.gateway = Gateway(gateway_id='gateway-test', integrity_key=self.key,
                               approval_store=ApprovalStore())
        self.handoff = self.issue_handoff()
        self.runner = WorkerRunner(DeterministicSummarizer(), authority=self.authority)

    def issue_handoff(self, text='the quick brown fox', job_id='job-demo', now=100):
        wire = issue({'text': text}, subject='subject-demo', job_id=job_id,
                     policy=self.policy, integrity_key=self.key, now=now)
        return validate(wire, subject='subject-demo', job_id=job_id,
                        policy=self.policy, integrity_key=self.key, now=now + 1)

    def permit_for(self, handoff=None, *, admitted_at=None):
        handoff = handoff or self.handoff
        when = handoff.issued_at + 1 if admitted_at is None else admitted_at
        return self.gateway.admit(handoff.to_bytes(), subject='subject-demo',
                                  job_id=handoff.job_id, policy=self.policy, now=when)

    def run_with(self, worker, *, handoff=None, now=110):
        handoff = handoff or self.handoff
        runner = WorkerRunner(worker, authority=self.authority)
        return runner.execute(self.permit_for(handoff), now=now)

    def taken(self, wire, *, handoff=None, now=120):
        return accept(wire, handoff=handoff or self.handoff,
                      authority=self.authority, now=now)

    # --- the reference worker ----------------------------------------------

    def test_the_reference_worker_produces_an_acceptable_result(self):
        taken = self.taken(self.runner.execute(self.permit_for(self.handoff), now=110))
        self.assertTrue(taken.succeeded)
        self.assertEqual(taken.reason_code, 'WORK_COMPLETED')
        digest = hashlib.sha256('the quick brown fox'.encode()).hexdigest()
        self.assertEqual(taken.output, {'text': f'4 words; sha256:{digest[:16]}'})

    def test_the_reference_worker_is_deterministic(self):
        """Same handoff, same bytes — no clock, no entropy, no ordering."""
        first = self.runner.execute(self.permit_for(self.handoff), now=110)
        second = self.runner.execute(self.permit_for(self.handoff), now=110)
        self.assertEqual(first, second)
        fresh = WorkerRunner(DeterministicSummarizer(), authority=self.authority)
        self.assertEqual(fresh.execute(self.permit_for(self.handoff), now=110), first)

    def test_different_input_gives_a_different_result(self):
        other = self.issue_handoff(text='a different job entirely')
        self.assertNotEqual(self.runner.execute(self.permit_for(other), now=110),
                            self.runner.execute(self.permit_for(self.handoff), now=110))

    # --- default deny at the boundary --------------------------------------

    def test_a_worker_whose_tool_is_not_granted_is_refused(self):
        """The boundary does not trust the gateway to have checked first."""
        class Ungranted(Worker):
            tool = 'exfiltrate'

            def run(self, payload):
                return {'text': CANARY}

        taken = self.taken(self.run_with(Ungranted()))
        self.assertFalse(taken.succeeded)
        self.assertEqual(taken.reason_code, 'TOOL_NOT_GRANTED')
        self.assertIsNone(taken.output)

    def test_a_refusal_is_still_signed_and_bound_to_the_handoff(self):
        class Ungranted(Worker):
            tool = 'other'

            def run(self, payload):
                return {'text': 'ok'}

        wire = self.run_with(Ungranted())
        taken = self.taken(wire)
        self.assertEqual(taken.job_id, self.handoff.job_id)
        other = self.issue_handoff(job_id='job-other')
        with self.assertRaises(ContractError):
            self.taken(wire, handoff=other)

    def test_an_ungranted_worker_never_runs(self):
        class Ungranted(Worker):
            tool = 'other'

            def __init__(self):
                self.ran = False

            def run(self, payload):
                self.ran = True
                return {'text': 'ok'}

        worker = Ungranted()
        self.run_with(worker)
        self.assertFalse(worker.ran)

    # --- failure is an outcome ---------------------------------------------

    def test_a_worker_that_raises_becomes_a_signed_failure(self):
        for exception in (RuntimeError('boom'), ValueError('bad'), KeyError('text'),
                          ZeroDivisionError(), TypeError(), AttributeError()):
            with self.subTest(exception=type(exception).__name__):
                taken = self.taken(self.run_with(ExplodingWorker(exception)))
                self.assertFalse(taken.succeeded)
                self.assertEqual(taken.reason_code, 'WORKER_FAILED')

    def test_an_exception_message_never_reaches_the_result(self):
        """A failure path that carries text is a leak with an error around it."""
        wire = self.run_with(ExplodingWorker(RuntimeError(CANARY)))
        self.assertNotIn(CANARY.encode(), wire)
        taken = self.taken(wire)
        for value in taken.to_dict().values():
            self.assertNotIn(CANARY, str(value))

    def test_a_worker_returning_the_wrong_shape_becomes_a_signed_failure(self):
        for value in ({'text': ''}, {'text': None}, {'text': 1}, {}, {'other': 'x'},
                      {'text': 'ok', 'extra': 'no'}, 'raw string', 42, None, [],
                      {'text': 'x' * (17 * 1024)}):
            with self.subTest(value=repr(value)[:40]):
                taken = self.taken(self.run_with(ReturningWorker(value)))
                self.assertFalse(taken.succeeded)
                self.assertEqual(taken.reason_code, 'OUTPUT_REJECTED')
                self.assertIsNone(taken.output)

    def test_a_worker_cannot_mutate_the_handoff_it_was_given(self):
        """The payload is copied, so the digest the result binds to survives."""
        taken = self.taken(self.run_with(MutatingWorker()))
        self.assertTrue(taken.succeeded)
        self.assertEqual(self.handoff.payload['text'], 'the quick brown fox')

    def test_a_worker_never_receives_the_key_or_the_handoff(self):
        worker = ExplodingWorker(RuntimeError('x'))
        self.run_with(worker)
        self.assertEqual(worker.seen, [{'text': 'the quick brown fox'}])
        self.assertNotIn(self.authority.result_key, repr(worker.seen).encode('utf-8', 'ignore'))

    # --- expiry has no signed answer ---------------------------------------

    def test_an_expired_handoff_cannot_be_executed_at_all(self):
        """Past expires_at nothing is signable, so a FAILED result is not a fallback."""
        with self.assertRaises(ContractError):
            self.runner.execute(self.permit_for(self.handoff), now=self.handoff.expires_at)
        with self.assertRaises(ContractError):
            self.runner.execute(self.permit_for(self.handoff), now=self.handoff.expires_at + 1)
        self.assertTrue(
            self.taken(self.runner.execute(self.permit_for(self.handoff), now=self.handoff.expires_at - 1),
                       now=self.handoff.expires_at - 1).succeeded)

    def test_an_expired_handoff_does_not_reach_the_worker(self):
        """The check has to be before execution, not only before signing.

        `produce` refuses to sign past `expires_at` too, so asserting only that
        `execute` raises passes with the runner's own check deleted — mutation
        testing caught exactly that. What the runner's check buys is that the
        work never runs: once a worker does more than compute, running it on a
        dead authorization is the whole problem, and an exception raised
        afterwards is too late.
        """
        for now in (self.handoff.expires_at, self.handoff.expires_at + 1,
                    self.handoff.issued_at - 1):
            worker = ExplodingWorker(RuntimeError('should never run'))
            with self.subTest(now=now):
                with self.assertRaises(ContractError):
                    self.run_with(worker, now=now)
                self.assertEqual(worker.seen, [])

    def test_dispatch_cannot_predate_gateway_admission(self):
        permit = self.permit_for(self.handoff, admitted_at=105)
        with self.assertRaisesRegex(ContractError, 'predates gateway admission'):
            self.runner.execute(permit, now=104)

    # --- construction and fail-closed --------------------------------------

    def test_the_runner_refuses_a_worker_without_a_declared_tool(self):
        class Nameless(Worker):
            def run(self, payload):
                return {'text': 'ok'}

        for worker in (Nameless(), 'worker', None, 42, DeterministicSummarizer):
            with self.subTest(worker=type(worker)), self.assertRaises(ContractError):
                WorkerRunner(worker, authority=self.authority)

    def test_the_runner_refuses_anything_but_a_worker_authority(self):
        for authority in (None, 'authority', 42, b'a-separate-result-key-of-32bytes!'):
            with self.subTest(authority=type(authority)), self.assertRaises(ContractError):
                WorkerRunner(DeterministicSummarizer(), authority=authority)

    def test_execution_fails_closed_on_malformed_input(self):
        for handoff in (None, 'handoff', 42, {}, self.handoff.to_bytes()):
            with self.subTest(handoff=type(handoff)), self.assertRaises(ContractError):
                self.runner.execute(handoff, now=110)
        for now in (None, '110', 110.0, object()):
            with self.subTest(now=type(now)), self.assertRaises(ContractError):
                self.runner.execute(self.permit_for(self.handoff), now=now)

    def test_a_hostile_worker_never_escapes_the_contract_error_boundary(self):
        class Hostile(Worker):
            tool = 'summarize'

            def __init__(self, behaviour):
                self.behaviour = behaviour

            def run(self, payload):
                return self.behaviour()

        class Evil:
            def __eq__(self, other): raise RuntimeError('evil __eq__')
            def __hash__(self): raise RuntimeError('evil __hash__')
            def __len__(self): raise RuntimeError('evil __len__')
            def __iter__(self): raise RuntimeError('evil __iter__')

        recursive = {}
        recursive['self'] = recursive

        def recurse():
            def deeper():
                return deeper()
            return deeper()

        behaviours = [
            lambda: {'text': Evil()},
            lambda: Evil(),
            lambda: recursive,
            lambda: {'text': 'ok', 'self': recursive},
            recurse,
        ]
        for index, behaviour in enumerate(behaviours):
            with self.subTest(behaviour=index):
                try:
                    wire = self.run_with(Hostile(behaviour))
                except ContractError:
                    continue
                except Exception as exc:  # noqa: BLE001 - that is the point
                    self.fail(f'{type(exc).__name__} escaped ContractError: {exc}')
                self.taken(wire)

    def test_a_base_exception_is_not_swallowed_as_a_failed_result(self):
        """`KeyboardInterrupt` and `SystemExit` are not worker outcomes.

        Turning them into a signed FAILED would make a shutdown look like a
        tool error in the audit trail, and would keep the process alive past
        the point its operator asked it to stop.
        """
        for exception in (KeyboardInterrupt(), SystemExit(1)):
            with self.subTest(exception=type(exception).__name__):
                with self.assertRaises(type(exception)):
                    self.run_with(ExplodingWorker(exception))


if __name__ == '__main__':
    unittest.main()
