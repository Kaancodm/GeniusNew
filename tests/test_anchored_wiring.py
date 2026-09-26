"""Anchoring is part of each HTTP decision, including refusals and approvals."""

import tempfile
import threading
import unittest
from unittest.mock import patch

from geniusnew.audit_chain import AuditAnchor
from geniusnew.contracts import ContractError
from geniusnew.keys import derive_keys
from geniusnew.isolation import IsolatedWorkerRunner
from geniusnew.results import WorkerAuthority
from geniusnew.wiring import build
from geniusnew.workers import DeterministicSummarizer, WorkerRunner
from tests.test_end_to_end import API_KEY, ROOT_SECRET, Fixture


class CountingRunner(WorkerRunner):
    calls = 0
    entered = None
    release = None

    def execute(self, permit, *, now):
        type(self).calls += 1
        if self.entered is not None:
            self.entered.set()
            if not self.release.wait(3):
                raise ContractError('workers were serialized')
        return super().execute(permit, now=now)


class IntermittentAnchor(AuditAnchor):
    def __init__(self):
        super().__init__()
        self.fail_on = None
        self.bad_ack = False
        self.commits = []

    def commit(self, head, records, *, authority):
        records = tuple(records)
        action = records[-1].event.action if records else None
        if action == self.fail_on:
            self.fail_on = None
            raise ContractError('anchor unavailable')
        result = super().commit(head, records, authority=authority)
        self.commits.append(result)
        if self.bad_ack:
            self.bad_ack = False
            return (result[0], 'f' * 64)
        return result


class AnchoredSocketTest(Fixture, unittest.TestCase):
    def test_success_is_committed_before_http_response_without_head_call(self):
        status, body = self.post()
        self.assertEqual((status, body['status']), (202, 'SUCCEEDED'))
        records = self.service.chain.records
        self.assertEqual(self.service.anchor.committed,
                         (len(records), records[-1].record_hash))
        self.assertEqual(len(records), 4)

    def test_closed_default_anchor_refuses_before_a_worker_runs(self):
        self.service.anchor.close()
        with patch.object(IsolatedWorkerRunner, 'execute',
                          side_effect=AssertionError('worker ran')) as execute:
            self.assertEqual(self.post(), (409, {'error': 'REJECTED'}))
            execute.assert_not_called()
        self.assertEqual(len(self.service.chain.records), 1)

    def test_persisted_head_rejects_a_fresh_empty_chain_without_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            state = directory + '/anchor.state'
            options = dict(root_secret=ROOT_SECRET, policy=self.policy_for(),
                           api_keys={API_KEY: 'subject-demo'},
                           workers=(DeterministicSummarizer(),),
                           clock=lambda: self.clock[0], anchor_state=state)
            first = build(**options)
            self.addCleanup(first.close)
            self.assertEqual(first.entry.handle(method='POST', path='/jobs',
                headers={'Content-Type': 'application/json',
                         'Authorization': 'Bearer ' + API_KEY.decode()},
                body=b'{"text":"first"}').status, 202)
            committed = first.anchor.committed
            first.close()
            rebuilt = build(**options)
            self.addCleanup(rebuilt.close)
            self.assertEqual(rebuilt.entry.handle(method='POST', path='/jobs',
                headers={'Content-Type': 'application/json',
                         'Authorization': 'Bearer ' + API_KEY.decode()},
                body=b'{"text":"second"}').status, 409)
            self.assertEqual(rebuilt.anchor.committed, committed)


class ControlledAnchorTest(Fixture, unittest.TestCase):
    def service_for(self, *, policy=None, **unused):
        self.test_anchor = IntermittentAnchor()
        self.runner_type = type('ThisTestRunner', (CountingRunner,), {'calls': 0})
        keys = derive_keys(ROOT_SECRET)
        authority = WorkerAuthority(result_key=keys.result_key,
                                    integrity_key=keys.integrity_key)
        self.next_id = 'job-fixed'
        return build(root_secret=ROOT_SECRET,
                     policy=policy or self.policy_for(
                         requires_approval=any(word in self._testMethodName for word in ('approval', 'consumption', 'wrong_token'))),
                     api_keys={API_KEY: 'subject-demo'},
                     workers=(DeterministicSummarizer(),),
                     clock=lambda: self.clock[0], anchor=self.test_anchor,
                     job_ids=lambda: self.next_id,
                     runner_factory=lambda worker: self.runner_type(worker, authority=authority))

    def _committed(self, actions):
        records = self.service.chain.records
        self.assertEqual([r.event.action for r in records], actions)
        self.assertEqual(self.test_anchor.committed,
                         (len(records), records[-1].record_hash))

    def test_admission_commit_failure_prevents_worker_and_later_commit_covers_suffix(self):
        self.test_anchor.fail_on = 'HANDOFF_ADMITTED'
        self.assertEqual(self.post(), (409, {'error': 'REJECTED'}))
        self.assertEqual(self.runner_type.calls, 0)
        self.assertEqual(self.test_anchor.committed[0], 1)
        self.next_id = 'job-other'
        self.assertEqual(self.post()[0], 202)
        self._committed(['HANDOFF_ISSUED', 'HANDOFF_ADMITTED',
                         'HANDOFF_ISSUED', 'HANDOFF_ADMITTED',
                         'EXECUTION_DISPATCHED', 'RESULT_ACCEPTED'])

    def test_post_execution_failure_refuses_and_same_job_id_does_not_run_twice(self):
        self.test_anchor.fail_on = 'RESULT_ACCEPTED'
        self.assertEqual(self.post(), (409, {'error': 'REJECTED'}))
        self.assertEqual(self.runner_type.calls, 1)
        self.assertEqual(self.post(), (409, {'error': 'REJECTED'}))
        self.assertEqual(self.runner_type.calls, 1)
        self.assertEqual(self.test_anchor.committed,
                         (len(self.service.chain.records),
                          self.service.chain.records[-1].record_hash))

    def test_mismatched_acknowledgement_is_refused(self):
        self.test_anchor.bad_ack = True
        self.assertEqual(self.post(), (409, {'error': 'REJECTED'}))
        self.assertEqual(self.runner_type.calls, 0)

    def test_approval_is_anchored_through_final_response(self):
        self.assertEqual(self.post()[1]['status'], 'PENDING_APPROVAL')
        self._committed(['HANDOFF_ISSUED'])
        token = self.service.approve('job-fixed')
        self._committed(['HANDOFF_ISSUED', 'APPROVAL_GRANTED'])
        self.assertEqual(self.service.entry.handle(method='POST',
            path='/jobs/job-fixed/approve',
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + API_KEY.decode(),
                     'X-Approval-Token': token.hex()}, body=b'{}').status, 202)
        self._committed(['HANDOFF_ISSUED', 'APPROVAL_GRANTED',
                         'HANDOFF_ADMITTED', 'EXECUTION_DISPATCHED', 'RESULT_ACCEPTED'])
        self.assertEqual(self.runner_type.calls, 1)

    def test_failed_approval_grant_returns_no_token_and_runs_nothing(self):
        self.assertEqual(self.post()[0], 202)
        self.test_anchor.fail_on = 'APPROVAL_GRANTED'
        with self.assertRaises(ContractError):
            self.service.approve('job-fixed')
        self.assertEqual(self.runner_type.calls, 0)
        self.assertEqual(self.test_anchor.committed[0], 1)

    def test_failed_admission_after_consumption_never_reuses_pending_job(self):
        self.assertEqual(self.post()[0], 202)
        token = self.service.approve('job-fixed')
        self.test_anchor.fail_on = 'HANDOFF_ADMITTED'
        arguments = dict(method='POST', path='/jobs/job-fixed/approve', body=b'{}',
                         headers={'Content-Type': 'application/json',
                                  'Authorization': 'Bearer ' + API_KEY.decode(),
                                  'X-Approval-Token': token.hex()})
        self.assertEqual(self.service.entry.handle(**arguments).status, 409)
        self.assertEqual(self.service.entry.handle(**arguments).status, 409)
        self.assertEqual(self.runner_type.calls, 0)

    def test_wrong_token_refusal_is_anchored_then_correct_token_runs(self):
        self.assertEqual(self.post()[0], 202)
        token = self.service.approve('job-fixed')
        arguments = dict(method='POST', path='/jobs/job-fixed/approve', body=b'{}',
                         headers={'Content-Type': 'application/json',
                                  'Authorization': 'Bearer ' + API_KEY.decode(),
                                  'X-Approval-Token': ('e' * 64)})
        self.assertEqual(self.service.entry.handle(**arguments).status, 409)
        self._committed(['HANDOFF_ISSUED', 'APPROVAL_GRANTED', 'HANDOFF_REJECTED'])
        arguments['headers']['X-Approval-Token'] = token.hex()
        self.assertEqual(self.service.entry.handle(**arguments).status, 202)
        self.assertEqual(self.runner_type.calls, 1)

    def test_concurrent_http_workers_overlap_and_every_commit_matches(self):
        entered = threading.Event()
        release = threading.Event()
        runner = self.runner_type
        runner.entered, runner.release = entered, release
        self.addCleanup(setattr, runner, 'entered', None)
        self.addCleanup(setattr, runner, 'release', None)
        outcome = []
        ids = iter(('job-a', 'job-b'))
        self.service.entry._job_ids = lambda: next(ids)
        first = threading.Thread(target=lambda: outcome.append(self.post()))
        second = threading.Thread(target=lambda: outcome.append(self.post()))
        first.start()
        self.assertTrue(entered.wait(3))
        second.start()
        try:
            # A second runner must enter while the first is still waiting.
            for _ in range(200):
                if runner.calls == 2:
                    break
                threading.Event().wait(.005)
            self.assertEqual(runner.calls, 2)
            self.service.head()
        finally:
            release.set()
            first.join(5)
            second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual([status for status, _ in outcome], [202, 202])
        self.assertEqual([count for count, _ in self.test_anchor.commits],
                         sorted(count for count, _ in self.test_anchor.commits))
        records = self.service.chain.records
        self.assertEqual(len(records), 8)
        expected = ['HANDOFF_ISSUED', 'HANDOFF_ADMITTED',
                    'EXECUTION_DISPATCHED', 'RESULT_ACCEPTED']
        for job_id in ('job-a', 'job-b'):
            self.assertEqual([r.event.action for r in records
                              if r.event.job_id == job_id], expected)
        self.assertEqual(self.test_anchor.committed,
                         (len(records), records[-1].record_hash))
