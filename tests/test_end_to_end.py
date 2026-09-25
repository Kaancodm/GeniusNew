"""Roadmap step 17: the whole path, once, with the chain verified afterwards.

Every other test file checks one boundary while standing on the others. This
one starts at a socket with an API key and stops when the audit chain verifies
against a head the anchor is holding — which is the first time the parts are
asked whether they actually fit together.

They did not, at first. `Dispatch` dropped the gateway's approval receipt, and
the result verifier requires it for an approval-bound job, so those two
components could not be wired at all. Nothing short of wiring them would have
said so: both had full test suites and both were right about their own half.
"""

import json
import os
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request

from geniusnew import wiring

from geniusnew.anchor_process import AnchorClient, start
from geniusnew.audit import AuditAuthority
from geniusnew.audit_chain import AuditAnchor, sign_head, verify
from geniusnew.contracts import ContractError, Grant, Policy
from geniusnew.http_entry import serve
from geniusnew.isolation import IsolatedWorkerRunner
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority, accept
from geniusnew.verifier import Rejected
from geniusnew.wiring import build
from geniusnew.workers import DeterministicSummarizer, Worker, WorkerRunner

# Zero-entropy and self-describing, so no scanner mistakes either for a real
# credential. Every other test file here follows that rule and says so; this one
# did not, and GitGuardian was right to stop the pull request over it. A test
# constant that looks like a secret costs a reviewer the same attention a real
# one would.
ROOT_SECRET = b'NOT-A-SECRET-' + b'0' * 24
API_KEY = b'API-KEY-CANARY-MUST-NOT-BE-DISCLOSED'
REQUEST = 'Zero trust means never trust, always verify.'

DEFAULT = object()


class UngrantedWorker(Worker):
    tool = 'translate'

    def run(self, payload):
        return {'text': 'ok'}


class SlowRunner(WorkerRunner):
    """A runner whose work function appears to take `takes` seconds.

    Sleeping for real would be honest and cost three minutes of CI per second,
    because `scripts/refusals.py` runs this suite once per refusal.
    """

    takes = 0.0

    def _monotonic(self) -> float:
        value = getattr(self, '_tick', 0.0)
        self._tick = value + self.takes
        return value


class MalformedResultRunner(WorkerRunner):
    """Test seam: a compromised execution boundary returns an invalid wire."""

    def execute(self, permit, *, now):
        # Consume the gateway capability first so this is an execution attempt,
        # then return bytes the independent verifier must refuse.
        super().execute(permit, now=now)
        return b'{}'


class Fixture:
    # A real anchor costs an interpreter start per test, and
    # `scripts/refusals.py` runs this suite once per refusal. So only the
    # tests about the anchored path start one; the rest use the in-process
    # seam, which answers the same two questions.
    anchored = False

    def setUp(self):
        self.clock = [1_700_000_000]
        self.anchor = self.started_anchor() if self.anchored else None
        self.service = self.service_for()
        self.server = serve(self.service.entry)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={'poll_interval': 0.01},
            daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.url = f'http://{host}:{port}/jobs'
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def anchor_client(self):
        return self.anchor.client() if self.anchor else AuditAnchor()

    def started_anchor(self):
        """Started as an operator would, outside the service it will anchor."""
        directory = tempfile.TemporaryDirectory(prefix='geniusnew-anchor-')
        self.addCleanup(directory.cleanup)
        audit = AuditAuthority(audit_key=derive_keys(ROOT_SECRET).audit_key)
        handle = start(verifier=audit.verifier(),
                       socket_path=os.path.join(directory.name, 'anchor.sock'))
        self.addCleanup(handle.stop)
        return handle

    def policy_for(self, *, ttl=60, requires_approval=False):
        grant = Grant('subject-demo', 'user-demo', 'worker-demo', 'basic',
                      ('summarize',), 'isolated', requires_approval)
        return Policy('policy-v1', 'orchestrator-1', ttl,
                      ('summarize',), ('isolated',), (grant,))

    def service_for(self, *, policy=DEFAULT, takes=None, workers=DEFAULT):
        factory = None
        if takes is not None:
            def factory(worker, takes=takes):  # noqa: F811 - one or the other
                runner = SlowRunner(worker, authority=WorkerAuthority(
                    result_key=self.service.keys.result_key,
                    integrity_key=self.service.keys.integrity_key))
                runner.takes = takes
                return runner
        return build(
            root_secret=ROOT_SECRET,
            policy=self.policy_for() if policy is DEFAULT else policy,
            api_keys={API_KEY: 'subject-demo'},
            workers=(DeterministicSummarizer(),) if workers is DEFAULT else workers,
            clock=lambda: self.clock[0],
            runner_factory=factory,
            anchor=self.anchor_client())

    def post(self, text=REQUEST, *, key=API_KEY, body=DEFAULT, url=None):
        payload = json.dumps({'text': text}).encode() if body is DEFAULT else body
        headers = {'Content-Type': 'application/json'}
        if key is not None:
            headers['Authorization'] = 'Bearer ' + key.decode()
        request = urllib.request.Request(url or self.url, data=payload,
                                         method='POST', headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())


class EndToEndTest(Fixture, unittest.TestCase):
    """One command's worth of path, from a socket to an anchored head."""

    def test_a_job_goes_in_over_http_and_comes_back_verified(self):
        import hashlib

        status, body = self.post()
        self.assertEqual(status, 202)
        self.assertEqual(body['status'], 'SUCCEEDED')
        self.assertEqual(body['reason_code'], 'WORK_COMPLETED')
        digest = hashlib.sha256(REQUEST.encode()).hexdigest()
        self.assertEqual(body['output'], {'text': f'7 words; sha256:{digest[:16]}'})
        self.assertRegex(body['job_id'], r'\Ajob-[0-9a-f]{32}\Z')
        self.assertRegex(body['handoff_sha256'], r'\A[0-9a-f]{64}\Z')
        self.assertRegex(body['result_sha256'], r'\A[0-9a-f]{64}\Z')

    def test_default_composition_uses_process_isolation(self):
        runners = [endpoint.runner
                   for endpoint in self.service.orchestrator._workers.values()]
        self.assertTrue(runners)
        self.assertTrue(all(isinstance(runner, IsolatedWorkerRunner)
                            for runner in runners))

    def test_the_anchor_is_not_optional(self):
        """No default: a default would be the service starting its own anchor."""
        with self.assertRaisesRegex(TypeError, 'anchor'):
            build(root_secret=ROOT_SECRET, policy=self.policy_for(),
                  api_keys={API_KEY: 'subject-demo'},
                  workers=(DeterministicSummarizer(),))

    def test_gateway_refusal_is_audited_after_handoff_issue(self):
        ticks = iter((self.clock[0], self.clock[0] + 1))
        service = build(
            root_secret=ROOT_SECRET, policy=self.policy_for(ttl=1),
            api_keys={API_KEY: 'subject-demo'},
            workers=(DeterministicSummarizer(),), clock=lambda: next(ticks),
            anchor=AuditAnchor())
        server = serve(service.entry)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        host, port = server.server_address
        status, body = self.post(url=f'http://{host}:{port}/jobs')
        self.assertEqual((status, body), (409, {'error': 'REJECTED'}))
        records = service.chain.records
        self.assertEqual([record.event.action for record in records],
                         ['HANDOFF_ISSUED', 'HANDOFF_REJECTED'])
        self.assertEqual([record.event.actor.component for record in records],
                         ['orchestrator', 'gateway'])
        self.assertEqual(records[-1].event.reason_code, 'HANDOFF_NOT_VALID')

    def test_the_result_signature_is_what_acceptance_actually_checked(self):
        """A forged result does not become an accepted one.

        The response says SUCCEEDED because an instance that ran nothing said
        so. This is what that instance would have done with a different answer.
        """
        self.post()
        forged = WorkerAuthority(result_key=b'a-forged-result-key-of-32-bytes!')
        dispatched = self.service.orchestrator.submit(
            {'text': REQUEST}, subject='subject-demo', job_id='job-direct',
            policy=self.service.policy, now=self.clock[0])
        from geniusnew.results import produce
        from geniusnew.contracts import validate
        handoff = validate(dispatched.handoff_wire, subject='subject-demo',
                           job_id='job-direct', policy=self.service.policy,
                           verifier=self.service.handoff_verifier,
                           now=self.clock[0])
        lie = produce({'text': 'never ran'}, handoff=handoff, status='SUCCEEDED',
                      reason_code='WORK_COMPLETED', authority=forged,
                      now=self.clock[0])
        with self.assertRaises(Rejected) as caught:
            self.service.verifier.accept(
                lie, handoff_wire=dispatched.handoff_wire, subject='subject-demo',
                job_id='job-direct', policy=self.service.policy, now=self.clock[0])
        self.assertEqual(caught.exception.reason_code, 'RESULT_NOT_VALID')

    def test_one_handoff_still_has_exactly_one_accepted_result(self):
        self.post()
        dispatched = self.service.orchestrator.submit(
            {'text': REQUEST}, subject='subject-demo', job_id='job-twice',
            policy=self.service.policy, now=self.clock[0])
        arguments = dict(handoff_wire=dispatched.handoff_wire,
                         subject='subject-demo', job_id='job-twice',
                         policy=self.service.policy, now=self.clock[0])
        self.assertTrue(self.service.verifier.accept(
            dispatched.result_wire, **arguments).succeeded)
        with self.assertRaises(Rejected) as caught:
            self.service.verifier.accept(dispatched.result_wire, **arguments)
        self.assertEqual(caught.exception.reason_code, 'RESULT_ALREADY_ACCEPTED')

    def test_two_requests_are_two_jobs_and_eight_entries(self):
        first = self.post()[1]
        second = self.post()[1]
        self.assertNotEqual(first['job_id'], second['job_id'])
        # Same input, same work, different artifact: the result is bound to the
        # handoff and the handoff carries the job id, so two runs of identical
        # text are two results that cannot stand in for each other. Asserting
        # they were equal is what this test did first, and it was wrong in the
        # direction that matters.
        self.assertEqual(first['output'], second['output'])
        self.assertNotEqual(first['handoff_sha256'], second['handoff_sha256'])
        self.assertNotEqual(first['result_sha256'], second['result_sha256'])
        self.assertEqual(len(self.service.chain.records), 8)

    # --- what the path refuses ----------------------------------------------

    def test_an_unknown_key_reaches_nothing_and_records_nothing(self):
        status, body = self.post(key=b'THIS-KEY-IS-NOT-IN-THE-REGISTRY-AT-ALL')
        self.assertEqual((status, body), (401, {'error': 'UNAUTHENTICATED'}))
        self.assertEqual(self.service.chain.records, ())

    def test_a_request_that_tries_to_name_its_own_rights_is_refused(self):
        status, body = self.post(
            body=json.dumps({'text': REQUEST, 'tier': 'admin'}).encode())
        self.assertEqual((status, body), (400, {'error': 'MALFORMED_REQUEST'}))
        self.assertEqual(self.service.chain.records, ())

    def test_a_handoff_that_expires_during_execution_is_refused_end_to_end(self):
        """The roadmap's own example for step 15, over the real path.

        Admission was valid, dispatch was valid, and the deadline passed while
        the work ran. The client gets a refusal, and nothing is recorded as
        accepted.
        """
        service = build(
            root_secret=ROOT_SECRET, policy=self.policy_for(ttl=1),
            api_keys={API_KEY: 'subject-demo'},
            workers=(DeterministicSummarizer(),), clock=lambda: self.clock[0],
            runner_factory=self._slow_runner(takes=2.0), anchor=AuditAnchor())
        server = serve(service.entry)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        host, port = server.server_address
        status, body = self.post(url=f'http://{host}:{port}/jobs')
        self.assertEqual((status, body), (409, {'error': 'REJECTED'}))
        records = service.chain.records
        self.assertEqual([record.event.action for record in records],
                         ['HANDOFF_ISSUED', 'HANDOFF_ADMITTED',
                          'EXECUTION_DISPATCHED', 'HANDOFF_REJECTED'])
        self.assertEqual([record.event.actor.component for record in records],
                         ['orchestrator', 'gateway', 'orchestrator', 'worker'])
        self.assertNotIn('RESULT_ACCEPTED',
                         [record.event.action for record in records])

    def test_verifier_rejection_is_audited_after_dispatch(self):
        keys = self.service.keys

        def factory(worker):
            return MalformedResultRunner(worker, authority=WorkerAuthority(
                result_key=keys.result_key,
                integrity_key=keys.integrity_key))

        service = build(
            root_secret=ROOT_SECRET, policy=self.policy_for(),
            api_keys={API_KEY: 'subject-demo'},
            workers=(DeterministicSummarizer(),), clock=lambda: self.clock[0],
            runner_factory=factory, anchor=AuditAnchor())
        server = serve(service.entry)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        host, port = server.server_address
        status, body = self.post(url=f'http://{host}:{port}/jobs')
        self.assertEqual((status, body), (409, {'error': 'REJECTED'}))
        records = service.chain.records
        self.assertEqual([record.event.action for record in records],
                         ['HANDOFF_ISSUED', 'HANDOFF_ADMITTED',
                          'EXECUTION_DISPATCHED', 'RESULT_REJECTED'])
        self.assertEqual([record.event.actor.component for record in records],
                         ['orchestrator', 'gateway', 'orchestrator', 'monitor'])
        self.assertEqual(records[-1].event.reason_code, 'RESULT_NOT_VALID')

    def test_the_wiring_refuses_a_worker_no_grant_names(self):
        with self.assertRaisesRegex(ContractError, 'no grant in this policy'):
            build(root_secret=ROOT_SECRET, policy=self.policy_for(),
                  api_keys={API_KEY: 'subject-demo'}, workers=(UngrantedWorker(),),
                  anchor=AuditAnchor())

    def two_subject_policy(self, *, second_agent):
        grants = tuple(
            Grant(subject, 'user-demo', agent, 'basic', ('summarize',), 'isolated', False)
            for subject, agent in (('subject-a', 'worker-demo'), ('subject-b', second_agent)))
        return Policy('policy-v1', 'orchestrator-1', 60,
                      ('summarize',), ('isolated',), grants)

    def test_the_wiring_refuses_a_tool_granted_to_several_agents(self):
        with self.assertRaisesRegex(ContractError, 'more than one worker agent'):
            build(root_secret=ROOT_SECRET,
                  policy=self.two_subject_policy(second_agent='worker-other'),
                  api_keys={API_KEY: 'subject-a'}, workers=(DeterministicSummarizer(),),
                  anchor=AuditAnchor())

    def test_subjects_sharing_one_agent_are_still_wired(self):
        service = build(root_secret=ROOT_SECRET,
                        policy=self.two_subject_policy(second_agent='worker-demo'),
                        api_keys={API_KEY: 'subject-a'},
                        workers=(DeterministicSummarizer(),), anchor=AuditAnchor())
        self.assertIsNotNone(service)

    def test_build_refuses_what_it_cannot_rely_on(self):
        for policy in (None, 'policy', 42, {}):
            with self.subTest(policy=type(policy)):
                with self.assertRaisesRegex(ContractError, 'policy is invalid'):
                    build(root_secret=ROOT_SECRET, policy=policy,
                          api_keys={API_KEY: 'subject-demo'},
                          workers=(DeterministicSummarizer(),), anchor=AuditAnchor())
        for clock in ('clock', 42, []):
            with self.subTest(clock=type(clock)):
                with self.assertRaisesRegex(ContractError, 'clock must be callable'):
                    build(root_secret=ROOT_SECRET, policy=self.policy_for(),
                          api_keys={API_KEY: 'subject-demo'},
                          workers=(DeterministicSummarizer(),), clock=clock,
                          anchor=AuditAnchor())
        with self.assertRaisesRegex(ContractError, 'WorkerRunner'):
            build(root_secret=ROOT_SECRET, policy=self.policy_for(),
                  api_keys={API_KEY: 'subject-demo'},
                  workers=(DeterministicSummarizer(),),
                  runner_factory=lambda worker: 'not-a-runner', anchor=AuditAnchor())
        with self.assertRaisesRegex(ContractError, 'anchor must be an AuditAnchor'):
            build(root_secret=ROOT_SECRET, policy=self.policy_for(),
                  api_keys={API_KEY: 'subject-demo'},
                  workers=(DeterministicSummarizer(),), anchor='not-an-anchor')

    def _slow_runner(self, *, takes):
        keys = self.service.keys

        def factory(worker):
            runner = SlowRunner(worker, authority=WorkerAuthority(
                result_key=keys.result_key, integrity_key=keys.integrity_key))
            runner.takes = takes
            return runner

        return factory


class AnchoredPathTest(Fixture, unittest.TestCase):
    """From the socket to a head held by an anchor the service did not start."""

    anchored = True

    def test_the_chain_records_each_instance_and_verifies_against_the_anchor(self):
        self.post()
        records = self.service.chain.records
        self.assertEqual([record.event.action for record in records],
                         ['HANDOFF_ISSUED', 'HANDOFF_ADMITTED',
                          'EXECUTION_DISPATCHED', 'RESULT_ACCEPTED'])
        self.assertEqual([record.event.actor.component for record in records],
                         ['orchestrator', 'gateway', 'orchestrator', 'monitor'])
        head = self.service.head()
        self.assertEqual(
            verify(records, head, authority=self.service.audit,
                   anchor=self.service.anchor),
            len(records))

    def test_the_service_is_anchored_in_a_process_it_did_not_start(self):
        self.assertIsInstance(self.service.anchor, AnchorClient)
        self.assertNotEqual(self.anchor.pid, os.getpid())
        self.assertFalse(hasattr(self.service, 'close'))

    def test_a_truncated_chain_is_refused_even_re_signed(self):
        """The anchor's reason for existing, on the real chain this time."""
        self.post()
        records = self.service.chain.records
        head = self.service.head()
        with self.assertRaisesRegex(ContractError, 'signed head claims'):
            verify(records[:-1], head, authority=self.service.audit,
                   anchor=self.service.anchor)
        with self.assertRaisesRegex(ContractError, 'anchor committed'):
            verify(records[:-1],
                   sign_head(count=len(records) - 1,
                             head_hash=records[-2].record_hash,
                             authority=self.service.audit),
                   authority=self.service.audit, anchor=self.service.anchor)

    def test_a_restarted_service_is_still_held_to_what_it_committed(self):
        """Roadmap step 8, the lifecycle half: a service restart is not a reset.

        The restarted service has a fresh chain and the same keys — everything
        it needs to re-sign a shortened log. What it no longer has is a fresh
        anchor, because it never started the one it had.
        """
        self.post()
        records = self.service.chain.records
        self.service.head()
        restarted = self.service_for()
        with self.assertRaisesRegex(ContractError, 'anchor committed 4 records; this chain has 3'):
            verify(records[:-1],
                   sign_head(count=len(records) - 1, head_hash=records[-2].record_hash,
                             authority=restarted.audit),
                   authority=restarted.audit, anchor=restarted.anchor)
        with self.assertRaisesRegex(ContractError, 'anchor already committed 4 records'):
            restarted.head()


OTHER_KEY = b'SECOND-API-KEY-CANARY-NOT-DISCLOSED'


class ApprovalOverHttpTest(Fixture, unittest.TestCase):
    """An approval-bound job over the socket: wait, get approved, run once."""

    def service_for(self, **ignored):
        grants = tuple(
            Grant(subject, 'user-demo', 'worker-demo', 'basic', ('summarize',),
                  'isolated', True)
            for subject in ('subject-demo', 'subject-other'))
        policy = Policy('policy-v1', 'orchestrator-1', 60,
                        ('summarize',), ('isolated',), grants)
        return build(root_secret=ROOT_SECRET, policy=policy,
                     api_keys={API_KEY: 'subject-demo', OTHER_KEY: 'subject-other'},
                     workers=(DeterministicSummarizer(),),
                     clock=lambda: self.clock[0],
                     anchor=self.anchor_client())

    def approve(self, job_id, token, *, key=API_KEY, body=b'{}'):
        headers = {'Content-Type': 'application/json',
                   'Authorization': 'Bearer ' + key.decode()}
        if token is not None:
            headers['X-Approval-Token'] = token.hex()
        request = urllib.request.Request(f'{self.url}/{job_id}/approve', data=body,
                                         method='POST', headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def waiting_job(self, key=API_KEY):
        status, body = self.post(key=key)
        self.assertEqual((status, body['status']), (202, 'PENDING_APPROVAL'), body)
        return body['job_id']

    def actions(self):
        return [(record.event.actor.component, record.event.action)
                for record in self.service.chain.records]

    def test_a_job_waits_then_runs_with_its_token_and_the_chain_says_so(self):
        job_id = self.waiting_job()
        token = self.service.approve(job_id)
        status, body = self.approve(job_id, token)
        self.assertEqual(status, 202, body)
        self.assertEqual((body['job_id'], body['status'], body['reason_code']),
                         (job_id, 'SUCCEEDED', 'WORK_COMPLETED'))
        self.assertEqual(self.actions(), [
            ('orchestrator', 'HANDOFF_ISSUED'), ('gateway', 'APPROVAL_GRANTED'),
            ('gateway', 'HANDOFF_ADMITTED'), ('orchestrator', 'EXECUTION_DISPATCHED'),
            ('monitor', 'RESULT_ACCEPTED')])
        self.assertEqual(verify(self.service.chain.records, self.service.head(),
                                authority=self.service.audit,
                                anchor=self.service.anchor), 5)

    def test_the_token_runs_the_job_once(self):
        job_id = self.waiting_job()
        token = self.service.approve(job_id)
        self.assertEqual(self.approve(job_id, token)[0], 202)
        self.assertEqual(self.approve(job_id, token), (409, {'error': 'REJECTED'}))

    def test_a_wrong_token_is_audited_and_leaves_the_job_waiting(self):
        job_id = self.waiting_job()
        token = self.service.approve(job_id)
        self.assertEqual(self.approve(job_id, os.urandom(32)), (409, {'error': 'REJECTED'}))
        self.assertIn(('gateway', 'HANDOFF_REJECTED'), self.actions())
        status, body = self.approve(job_id, token)
        self.assertEqual((status, body['status']), (202, 'SUCCEEDED'))

    def test_another_jobs_token_is_refused_and_not_spent(self):
        first, second = self.waiting_job(), self.waiting_job()
        first_token = self.service.approve(first)
        second_token = self.service.approve(second)
        self.assertEqual(self.approve(first, second_token)[0], 409)
        self.assertEqual(self.approve(second, second_token)[1]['status'], 'SUCCEEDED')
        self.assertEqual(self.approve(first, first_token)[1]['status'], 'SUCCEEDED')

    def test_another_subject_cannot_complete_the_job(self):
        job_id = self.waiting_job()
        token = self.service.approve(job_id)
        self.assertEqual(self.approve(job_id, token, key=OTHER_KEY),
                         (409, {'error': 'REJECTED'}))
        self.assertEqual(self.approve(job_id, token)[1]['status'], 'SUCCEEDED')

    def test_unknown_unapproved_and_foreign_jobs_look_the_same(self):
        foreign = self.waiting_job(key=OTHER_KEY)
        unapproved = self.waiting_job()
        answers = {
            'unknown': self.approve('job-that-does-not-exist', os.urandom(32)),
            'unapproved': self.approve(unapproved, os.urandom(32)),
            'foreign': self.approve(foreign, self.service.approve(foreign)),
        }
        self.assertEqual(set(map(repr, answers.values())), {repr((409, {'error': 'REJECTED'}))})

    def test_a_malformed_approval_is_refused_before_anything_runs(self):
        job_id = self.waiting_job()
        before = len(self.service.chain.records)
        self.assertEqual(self.approve(job_id, None)[0], 400)
        self.assertEqual(self.approve(job_id, os.urandom(32), body=b'{"text":"x"}')[0], 400)
        self.assertEqual(len(self.service.chain.records), before)

    def test_only_a_waiting_job_can_be_approved(self):
        with self.assertRaisesRegex(ContractError, 'no job is waiting'):
            self.service.approve('job-that-does-not-exist')


class PendingJobsTest(unittest.TestCase):
    def waiting(self, expires_at=100):
        handoff = type('Handoff', (), {'expires_at': expires_at})()
        return wiring._Waiting('subject-demo', b'wire', handoff, 'trace-x')

    def test_a_job_id_waits_once(self):
        jobs = wiring.PendingJobs()
        jobs.add('job-1', self.waiting(), now=10)
        with self.assertRaisesRegex(ContractError, 'already waiting'):
            jobs.add('job-1', self.waiting(), now=10)

    def test_it_is_bounded_and_expired_entries_do_not_count(self):
        jobs = wiring.PendingJobs()
        with unittest.mock.patch.object(wiring, '_MAX_PENDING', 2):
            jobs.add('job-1', self.waiting(expires_at=20), now=10)
            jobs.add('job-2', self.waiting(), now=10)
            with self.assertRaisesRegex(ContractError, 'too many jobs'):
                jobs.add('job-3', self.waiting(), now=10)
            jobs.add('job-3', self.waiting(), now=20)
        with self.assertRaisesRegex(ContractError, 'no job is waiting'):
            jobs.peek('job-1')


if __name__ == '__main__':
    unittest.main()
