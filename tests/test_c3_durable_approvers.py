"""C3 approver decisions remain role-bound and recoverable after restart."""

import unittest
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from geniusnew import database
from geniusnew.audit_chain import AuditAnchor
from geniusnew.audit_store import PostgresAuditChain
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority
from geniusnew.wiring import build
from geniusnew.workers import DeterministicSummarizer, WorkerRunner
from postgres_support import PostgresDatabase
from test_end_to_end import API_KEY, ROOT_SECRET, Fixture


APPROVER_KEY = b"APPROVER-KEY-CANARY-C3-DURABLE-ROUTE"
SELF_KEY = b"SELF-APPROVER-KEY-CANARY-C3-DURABLE"


class DurableApproverTest(Fixture, unittest.TestCase):
    def setUp(self):
        self.db = PostgresDatabase()
        self.addCleanup(self.db.close)
        database.migrate(self.db.owner_dsn)
        self.clock = [1_700_000_000]
        self.anchor = AuditAnchor()

    def start(self):
        connection = self.db.connect(runtime=True)
        self.addCleanup(connection.close)
        keys = derive_keys(ROOT_SECRET)
        authority = WorkerAuthority(result_key=keys.result_key,
                                    integrity_key=keys.integrity_key)
        service = build(
            root_secret=ROOT_SECRET, policy=self.policy_for(requires_approval=True),
            api_keys={API_KEY: "subject-demo", APPROVER_KEY: "subject-approver",
                      SELF_KEY: "subject-self"},
            approvers={"subject-approver": "user-approver", "subject-self": "user-demo"},
            workers=(DeterministicSummarizer(),), anchor=self.anchor,
            clock=lambda: self.clock[0],
            job_ledger=database.PostgresJobLedger(connection),
            acceptance_ledger=database.PostgresAcceptanceLedger(connection),
            database_connection=connection,
            audit_chain_factory=lambda audit: PostgresAuditChain(connection, authority=audit),
            runner_factory=lambda worker: WorkerRunner(worker, authority=authority))
        self.addCleanup(service.close)
        return service

    @staticmethod
    def post(service, path, key, body=b"{}", token=None):
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + key.decode()}
        if token is not None:
            headers["X-Approval-Token"] = token
        return service.entry.handle(method="POST", path=path, headers=headers, body=body)

    def test_active_grant_is_unique_across_restart_and_expired_grant_can_be_replaced(self):
        service = self.start()
        submitted = self.post(service, "/jobs", API_KEY, b'{"text":"recover grant"}')
        self.assertEqual(submitted.status, 202)
        job_id = submitted.body["job_id"]
        path = f"/approvals/{job_id}/grant"
        first = self.post(service, path, APPROVER_KEY)
        self.assertEqual(first.status, 202)
        self.assertEqual(self.post(service, path, APPROVER_KEY).status, 409)
        service.close()

        service = self.start()
        self.assertEqual(self.post(service, path, APPROVER_KEY).status, 409)
        self.clock[0] += 31
        replacement = self.post(service, path, APPROVER_KEY)
        self.assertEqual(replacement.status, 202)
        self.assertNotEqual(replacement.body["approval_token"], first.body["approval_token"])
        completion = f"/jobs/{job_id}/approve"
        self.assertEqual(self.post(service, completion, API_KEY,
                                   token=first.body["approval_token"]).status, 409)
        self.assertEqual(self.post(service, completion, API_KEY,
                                   token=replacement.body["approval_token"]).status, 202)

    def test_only_another_configured_identity_can_grant(self):
        service = self.start()
        submitted = self.post(service, "/jobs", API_KEY, b'{"text":"role check"}')
        self.assertEqual(submitted.status, 202)
        path = f"/approvals/{submitted.body['job_id']}/grant"
        self.assertEqual(self.post(service, path, API_KEY).status, 409)
        self.assertEqual(self.post(service, path, SELF_KEY).status, 409)
        self.assertEqual(self.post(service, path, APPROVER_KEY).status, 202)
        grants = [record.event for record in service.chain.records
                  if record.event.action == "APPROVAL_GRANTED"]
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].api_subject_sha256,
                         hashlib.sha256(b"subject-approver").hexdigest())

    def test_two_instances_can_commit_only_one_active_grant(self):
        first_service = self.start()
        submitted = self.post(first_service, "/jobs", API_KEY,
                              b'{"text":"concurrent approval"}')
        self.assertEqual(submitted.status, 202)
        second_service = self.start()
        path = f"/approvals/{submitted.body['job_id']}/grant"
        barrier = Barrier(2)

        def grant(service):
            barrier.wait()
            return self.post(service, path, APPROVER_KEY).status

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(grant, (first_service, second_service)))
        self.assertEqual(sorted(statuses), [202, 409])
        with self.db.connect() as owner:
            self.assertEqual(owner.execute(
                "SELECT count(*) FROM public.approval_records "
                "WHERE state='GRANTED'").fetchone(), (1,))


if __name__ == "__main__":
    unittest.main()
