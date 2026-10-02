"""B6 startup reconciliation rejects durable ledger state without audit evidence."""

import unittest

from geniusnew import database
from geniusnew.audit_chain import AuditAnchor
from geniusnew.audit_store import PostgresAuditChain
from geniusnew.contracts import ContractError
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority
from geniusnew.wiring import build
from geniusnew.workers import DeterministicSummarizer, WorkerRunner
from postgres_support import PostgresDatabase
from test_end_to_end import API_KEY, ROOT_SECRET, Fixture


class LedgerAuditBoundaryTest(Fixture, unittest.TestCase):
    def test_a_job_row_without_an_audit_event_refuses_start(self):
        db = PostgresDatabase()
        self.addCleanup(db.close)
        database.migrate(db.owner_dsn)
        with db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-without-audit','subject-demo',%s,'RESERVED',100,100,100,200)",
                ("e" * 64,))
        jobs = db.connect(runtime=True)
        self.addCleanup(jobs.close)
        keys = derive_keys(ROOT_SECRET)
        worker_authority = WorkerAuthority(result_key=keys.result_key,
                                           integrity_key=keys.integrity_key)
        with self.assertRaisesRegex(ContractError, "job ledger has no audit issuance"):
            build(
                root_secret=ROOT_SECRET, policy=self.policy_for(),
                api_keys={API_KEY: "subject-demo"}, workers=(DeterministicSummarizer(),),
                anchor=AuditAnchor(), job_ledger=database.PostgresJobLedger(jobs),
                acceptance_ledger=database.PostgresAcceptanceLedger(jobs),
                database_connection=jobs,
                audit_chain_factory=lambda audit: PostgresAuditChain(jobs, authority=audit),
                runner_factory=lambda worker: WorkerRunner(worker, authority=worker_authority),
            )
