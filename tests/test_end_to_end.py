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
import threading
import unittest
import urllib.error
import urllib.request

from geniusnew.audit_chain import sign_head, verify
from geniusnew.contracts import ContractError, Grant, Policy
from geniusnew.http_entry import serve
from geniusnew.isolation import IsolatedWorkerRunner
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


class Fixture:
    def setUp(self):
        self.clock = [1_700_000_000]
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
            runner_factory=factory)

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

    def test_the_chain_records_each_instance_and_verifies_against_the_anchor(self):
        self.post()
        records = self.service.chain.records
        self.assertEqual(
            [record.event.action for record in records],
            ['HANDOFF_ISSUED', 'HANDOFF_ADMITTED',
             'EXECUTION_DISPATCHED', 'RESULT_ACCEPTED'])
        self.assertEqual(
            [record.event.actor.component for record in records],
            ['orchestrator', 'gateway', 'orchestrator', 'monitor'])
        head = self.service.head()
        self.assertEqual(
            verify(records, head, authority=self.service.audit,
                   anchor=self.service.anchor),
            len(records))

    def test_build_uses_process_isolation_by_default(self):
        endpoint = self.service.orchestrator._workers['worker-demo']
        self.assertIsInstance(endpoint.runner, IsolatedWorkerRunner)

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
                           integrity_key=self.service.keys.integrity_key,
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

    def test_two_requests_are_two_jobs_and_six_entries(self):
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
            runner_factory=self._slow_runner(takes=2.0))
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
        self.assertEqual(service.chain.records, ())

    def test_an_approval_bound_job_is_refused_with_the_gap_named(self):
        """The entrance has no field for an approval token, and says so."""
        service = build(
            root_secret=ROOT_SECRET,
            policy=self.policy_for(requires_approval=True),
            api_keys={API_KEY: 'subject-demo'},
            workers=(DeterministicSummarizer(),), clock=lambda: self.clock[0])
        with self.assertRaisesRegex(ContractError, 'through the entrance'):
            service.entry._submit(subject='subject-demo', job_id='job-x',
                                  payload={'text': REQUEST})

    def test_the_wiring_refuses_a_worker_no_grant_names(self):
        class Ungranted(Worker):
            tool = 'translate'

            def run(self, payload):
                return {'text': 'ok'}

        with self.assertRaisesRegex(ContractError, 'no grant in this policy'):
            build(root_secret=ROOT_SECRET, policy=self.policy_for(),
                  api_keys={API_KEY: 'subject-demo'}, workers=(Ungranted(),))

    def test_build_refuses_what_it_cannot_rely_on(self):
        for policy in (None, 'policy', 42, {}):
            with self.subTest(policy=type(policy)):
                with self.assertRaisesRegex(ContractError, 'policy is invalid'):
                    build(root_secret=ROOT_SECRET, policy=policy,
                          api_keys={API_KEY: 'subject-demo'},
                          workers=(DeterministicSummarizer(),))
        for clock in ('clock', 42, []):
            with self.subTest(clock=type(clock)):
                with self.assertRaisesRegex(ContractError, 'clock must be callable'):
                    build(root_secret=ROOT_SECRET, policy=self.policy_for(),
                          api_keys={API_KEY: 'subject-demo'},
                          workers=(DeterministicSummarizer(),), clock=clock)
        with self.assertRaisesRegex(ContractError, 'WorkerRunner'):
            build(root_secret=ROOT_SECRET, policy=self.policy_for(),
                  api_keys={API_KEY: 'subject-demo'},
                  workers=(DeterministicSummarizer(),),
                  runner_factory=lambda worker: 'not-a-runner')

    def _slow_runner(self, *, takes):
        keys = self.service.keys

        def factory(worker):
            runner = SlowRunner(worker, authority=WorkerAuthority(
                result_key=keys.result_key, integrity_key=keys.integrity_key))
            runner.takes = takes
            return runner

        return factory


if __name__ == '__main__':
    unittest.main()
