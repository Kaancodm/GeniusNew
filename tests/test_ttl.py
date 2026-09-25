"""Roadmap step 15: the four TTL control points, and the one that was missing.

A handoff carries a deadline. The question this file answers is not whether the
deadline exists but *where* it is enforced, and whether each place is reached by
its own check rather than by the next one down the line.

The four, in the order a job meets them:

1. **Admission** — the gateway revalidates the wire and refuses an expired one.
2. **Before dispatch** — the worker boundary refuses to start work on a handoff
   that is already past `expires_at`, before the work function runs at all.
3. **Revalidation in the worker** — after the work returns, the boundary checks
   how long it actually took. This is the one step 15 warns about and the one
   that was missing: `now` is the dispatch clock and never advances, so a worker
   that ran past the deadline produced a result nothing could tell from a prompt
   one.
4. **Acceptance** — the result is refused if the deadline has passed by the time
   someone takes it.

Each test asserts the *message*, not just that something was refused. Several of
these inputs would be refused by a later point anyway, so "a ContractError
happened" would survive deleting the earlier one — the failure mode this
repository keeps meeting.

## Why nothing here sleeps

`scripts/refusals.py` runs this whole suite once per refusal in the guarded set,
today 175 times. One second of sleeping is three minutes of CI. `WorkerRunner`
takes its elapsed time from a `_monotonic` seam, so a test can make a worker
take an hour without taking an hour. The measurement in production is
`time.monotonic`; a probe with a real 1.5-second worker under a one-second TTL
is what found the gap in the first place.
"""

import unittest

from geniusnew.approvals import ApprovalStore
from geniusnew.contracts import ContractError, Grant, HandoffSigner, Policy, issue, validate
from geniusnew.gateway import DispatchPermit, Gateway
from geniusnew.isolation import IsolationLimits
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority, accept, produce
from geniusnew.workers import DeterministicSummarizer, Worker, WorkerRunner

ROOT_SECRET = b'a-ttl-test-root-secret-of-32-bytes!!'
T = 1_700_000_000

# Zero-entropy and self-describing. Its job is to be unmistakable if a worker's
# exception text ever reaches a refusal raised on its behalf.
CANARY = 'TTL-CANARY-MUST-NOT-REACH-A-REFUSAL'


class SlowRunner(WorkerRunner):
    """A runner whose work function appears to take `takes` seconds."""

    def __init__(self, *args, takes, **arguments):
        super().__init__(*args, **arguments)
        self.takes = takes
        self._ticks = iter((0.0, float(takes)))

    def _monotonic(self) -> float:
        return next(self._ticks)


class Fixture:
    """Shared setup. A mixin, not a TestCase: subclassing one re-runs its suite."""

    def setUp(self):
        self.keys = derive_keys(ROOT_SECRET)
        self.authority = WorkerAuthority(result_key=self.keys.result_key,
                                         integrity_key=self.keys.integrity_key)
        self.gateway = Gateway(gateway_id='gateway-1',
                               handoff_verifier=HandoffSigner(integrity_key=self.keys.integrity_key).verifier(),
                               approval_store=ApprovalStore())

    def policy_for(self, ttl=60):
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                      ('summarize',), 'isolated', False)
        return Policy('policy-v1', 'orchestrator-1', ttl,
                      ('summarize',), ('isolated',), (grant,))

    def issue(self, *, ttl=60, now=T):
        policy = self.policy_for(ttl)
        return policy, issue({'text': 'the quick brown fox'}, subject='subject-demo',
                             job_id='job-demo', policy=policy,
                             signer=HandoffSigner(integrity_key=self.keys.integrity_key), now=now)

    def admit(self, *, ttl=60, now=T, admitted_at=None):
        policy, wire = self.issue(ttl=ttl, now=now)
        return policy, wire, self.gateway.admit(
            wire, subject='subject-demo', job_id='job-demo', policy=policy,
            now=now if admitted_at is None else admitted_at)


class TTLControlPointTest(Fixture, unittest.TestCase):
    """Four places, four refusals, each reached by its own check."""

    # --- 1. admission --------------------------------------------------------

    def test_admission_refuses_a_handoff_past_its_deadline(self):
        policy, wire = self.issue(ttl=60)
        expires_at = T + 60
        for now in (expires_at, expires_at + 1):
            with self.subTest(now=now):
                with self.assertRaisesRegex(ContractError, 'not currently valid'):
                    self.gateway.admit(wire, subject='subject-demo', job_id='job-demo',
                                       policy=policy, now=now)
        # And one second earlier it is admitted, so the boundary is exact.
        self.assertIsInstance(
            self.gateway.admit(wire, subject='subject-demo', job_id='job-demo',
                               policy=policy, now=expires_at - 1),
            DispatchPermit)

    def test_admission_refuses_a_handoff_from_the_future(self):
        policy, wire = self.issue(now=T)
        with self.assertRaisesRegex(ContractError, 'not currently valid'):
            self.gateway.admit(wire, subject='subject-demo', job_id='job-demo',
                               policy=policy, now=T - 1)

    def test_a_permit_cannot_be_minted_outside_the_handoff_lifetime(self):
        """Defence behind admission: the capability carries the deadline too."""
        _, _, permit = self.admit(ttl=60)
        from dataclasses import replace
        for admitted_at in (T - 1, T + 60, T + 61):
            with self.subTest(admitted_at=admitted_at):
                with self.assertRaisesRegex(ContractError, 'outside the handoff lifetime'):
                    replace(permit, admitted_at=admitted_at)

    # --- 2. before dispatch --------------------------------------------------

    def test_the_worker_boundary_refuses_to_start_past_the_deadline(self):
        class NeverRuns(Worker):
            tool = 'summarize'

            def __init__(self):
                self.ran = False

            def run(self, payload):
                self.ran = True
                return {'text': 'ok'}

        worker = NeverRuns()
        _, _, permit = self.admit(ttl=60)
        with self.assertRaisesRegex(ContractError, 'expired before execution'):
            WorkerRunner(worker, authority=self.authority).execute(permit, now=T + 60)
        self.assertFalse(worker.ran, 'the work function must not have run')

    def test_an_expired_handoff_has_no_signed_answer_at_all(self):
        """Not even a FAILED: past the deadline there is nothing signable.

        `produce` refuses too, so the boundary's refusal is what stops the work
        from running — the exception is not a substitute for that.
        """
        policy, wire = self.issue(ttl=60)
        handoff = validate(wire, subject='subject-demo', job_id='job-demo',
                           policy=policy, verifier=HandoffSigner(integrity_key=self.keys.integrity_key),
                           now=T + 1)
        with self.assertRaisesRegex(ContractError, 'after its handoff expired'):
            produce(None, handoff=handoff, status='FAILED',
                    reason_code='WORKER_FAILED', authority=self.authority, now=T + 60)

    # --- 3. revalidation in the worker, after the work ----------------------

    def test_a_handoff_that_expires_while_the_worker_runs_is_refused(self):
        """The control point step 15 is actually about.

        Admission was valid, dispatch was valid, and the deadline passed while
        the work function was running. `now` never advances, so without this
        the result is signed and indistinguishable from a prompt one — measured
        with a real 1.5-second worker under a one-second TTL before this check
        existed.
        """
        _, _, permit = self.admit(ttl=1)
        runner = SlowRunner(DeterministicSummarizer(), authority=self.authority,
                            takes=1.5)
        with self.assertRaisesRegex(ContractError, 'expired while the worker was running'):
            runner.execute(permit, now=T)

    def test_a_worker_that_fails_late_gets_no_signed_failure_either(self):
        """Past the deadline there is no signable answer, success or failure.

        The first version of this check sat after the failure paths had already
        returned, so a worker that raised *and* overran still produced a signed
        FAILED for a dead authorization. That is the same lie as a signed
        SUCCEEDED, told in the other direction.
        """
        class Exploding(Worker):
            tool = 'summarize'

            def run(self, payload):
                raise RuntimeError(CANARY)

        _, _, permit = self.admit(ttl=1)
        runner = SlowRunner(Exploding(), authority=self.authority, takes=2.0)
        with self.assertRaisesRegex(ContractError, 'expired while the worker was running'):
            runner.execute(permit, now=T)

    def test_the_late_refusal_carries_no_trace_of_the_worker_exception(self):
        """Raised outside the handler, so nothing rides out on __context__.

        `from None` would not be enough — it only sets __suppress_context__ and
        leaves the text one attribute away.
        """
        import traceback

        class Exploding(Worker):
            tool = 'summarize'

            def run(self, payload):
                raise RuntimeError(CANARY)

        _, _, permit = self.admit(ttl=1)
        runner = SlowRunner(Exploding(), authority=self.authority, takes=2.0)
        with self.assertRaises(ContractError) as caught:
            runner.execute(permit, now=T)
        seen, exception = set(), caught.exception
        while exception is not None and id(exception) not in seen:
            seen.add(id(exception))
            with self.subTest(link=type(exception).__name__):
                self.assertNotIn(CANARY, str(exception))
            exception = exception.__cause__ or exception.__context__
        formatted = ''.join(traceback.format_exception(
            type(caught.exception), caught.exception, caught.exception.__traceback__))
        self.assertNotIn(CANARY, formatted)

    def test_the_work_still_runs_and_signs_when_it_finishes_in_time(self):
        _, _, permit = self.admit(ttl=60)
        runner = SlowRunner(DeterministicSummarizer(), authority=self.authority,
                            takes=59.0)
        self.assertIsInstance(runner.execute(permit, now=T), bytes)

    def test_the_elapsed_check_is_exact_at_the_deadline(self):
        for takes, refused in ((0.9, False), (1.0, True), (1.1, True)):
            with self.subTest(takes=takes):
                _, _, permit = self.admit(ttl=1)
                runner = SlowRunner(DeterministicSummarizer(),
                                    authority=self.authority, takes=takes)
                if refused:
                    with self.assertRaisesRegex(ContractError, 'while the worker was running'):
                        runner.execute(permit, now=T)
                else:
                    self.assertIsInstance(runner.execute(permit, now=T), bytes)

    def test_the_signed_artifact_does_not_depend_on_how_long_the_work_took(self):
        """Elapsed time decides whether to sign, never what is signed.

        Putting the finishing time into `produced_at` would make the same job
        produce different bytes on every run, and `scripts/demo.sh` prints those
        digests precisely so a reader can diff two runs.
        """
        wires = []
        for takes in (0.0, 10.0, 30.0):
            _, _, permit = self.admit(ttl=60)
            wires.append(SlowRunner(DeterministicSummarizer(),
                                    authority=self.authority,
                                    takes=takes).execute(permit, now=T))
        self.assertEqual(len(set(wires)), 1, 'the result bytes changed with elapsed time')

    def test_a_resource_time_limit_does_not_stand_in_for_the_deadline(self):
        """Step 15 says so explicitly, and the numbers make it unavoidable.

        A sandbox wall limit may be up to 30 seconds while a handoff TTL may be
        as short as one, so a worker can sit well inside its resource limit and
        still be minutes past its authorization. The limits answer different
        questions: one bounds what a worker may consume, the other how long the
        authorization to consume it lasts.
        """
        self.assertEqual(IsolationLimits(wall_seconds=30.0).wall_seconds, 30.0)
        with self.assertRaises(ContractError):
            IsolationLimits(wall_seconds=31.0)
        self.assertEqual(self.policy_for(ttl=1).handoff_ttl_seconds, 1)

        _, _, permit = self.admit(ttl=1)
        runner = SlowRunner(DeterministicSummarizer(), authority=self.authority,
                            takes=5.0)  # far inside any wall limit, far past the TTL
        with self.assertRaisesRegex(ContractError, 'while the worker was running'):
            runner.execute(permit, now=T)

    # --- 4. acceptance -------------------------------------------------------

    def test_acceptance_refuses_a_result_once_the_deadline_has_passed(self):
        policy, wire, permit = self.admit(ttl=60)
        handoff = permit.handoff
        result_wire = WorkerRunner(DeterministicSummarizer(),
                                   authority=self.authority).execute(permit, now=T)
        with self.assertRaisesRegex(ContractError, 'expired before its result was accepted'):
            accept(result_wire, handoff=handoff, authority=self.authority, now=T + 60)
        self.assertTrue(accept(result_wire, handoff=handoff,
                               authority=self.authority, now=T + 59).succeeded)

    def test_acceptance_refuses_a_result_dated_after_the_deadline(self):
        policy, wire, permit = self.admit(ttl=60)
        with self.assertRaisesRegex(ContractError, 'after its handoff expired'):
            produce({'text': 'late'}, handoff=permit.handoff, status='SUCCEEDED',
                    reason_code='WORK_COMPLETED', authority=self.authority,
                    now=T + 60)

    # --- the four are four ---------------------------------------------------

    def test_each_control_point_is_the_one_that_refuses_its_own_case(self):
        """One table, four distinct sentences. If two collapsed into one, the
        refusal that disappeared would still leave this test passing on the
        other's message — which is why each row asserts its own.
        """
        seen = {}

        policy, wire = self.issue(ttl=60)
        with self.assertRaises(ContractError) as caught:
            self.gateway.admit(wire, subject='subject-demo', job_id='job-demo',
                               policy=policy, now=T + 60)
        seen['admission'] = str(caught.exception)

        _, _, permit = self.admit(ttl=60)
        with self.assertRaises(ContractError) as caught:
            WorkerRunner(DeterministicSummarizer(),
                         authority=self.authority).execute(permit, now=T + 60)
        seen['before dispatch'] = str(caught.exception)

        _, _, permit = self.admit(ttl=1)
        with self.assertRaises(ContractError) as caught:
            SlowRunner(DeterministicSummarizer(), authority=self.authority,
                       takes=2.0).execute(permit, now=T)
        seen['in the worker'] = str(caught.exception)

        _, _, permit = self.admit(ttl=60)
        result_wire = WorkerRunner(DeterministicSummarizer(),
                                   authority=self.authority).execute(permit, now=T)
        with self.assertRaises(ContractError) as caught:
            accept(result_wire, handoff=permit.handoff, authority=self.authority,
                   now=T + 60)
        seen['acceptance'] = str(caught.exception)

        self.assertEqual(len(set(seen.values())), 4, seen)
        self.assertIn('not currently valid', seen['admission'])
        self.assertIn('expired before execution', seen['before dispatch'])
        self.assertIn('while the worker was running', seen['in the worker'])
        self.assertIn('before its result was accepted', seen['acceptance'])

    def test_the_whole_path_survives_a_job_that_finishes_in_time(self):
        """The four refusals must not cost a job that did everything right."""
        _, _, permit = self.admit(ttl=60)
        handoff = permit.handoff
        result_wire = WorkerRunner(DeterministicSummarizer(),
                                   authority=self.authority).execute(permit, now=T + 1)
        taken = accept(result_wire, handoff=handoff, authority=self.authority,
                       now=T + 2)
        self.assertTrue(taken.succeeded)


if __name__ == '__main__':
    unittest.main()
