"""B6 rejects durable authorization state without matching audit evidence."""

import unittest
from hashlib import sha256
from unittest import mock

from geniusnew import database
from geniusnew.approvals import ApprovalStore, create_scope
from geniusnew.audit import AuditAuthority, event_from_handoff
from geniusnew.audit_chain import AuditAnchor
from geniusnew.audit_store import PostgresAuditChain
from geniusnew.contracts import (ContractError, HandoffSigner, canonical, issue, validate,
                                 validate_pending)
from geniusnew.gateway import ADMISSION_REASON_CODE, Gateway
from geniusnew.keys import derive_keys
from geniusnew.orchestrator import Reservation
from geniusnew.results import WorkerAuthority, handoff_digest, produce
from geniusnew.wiring import _Waiting, build
from geniusnew.workers import DeterministicSummarizer, WorkerRunner
from postgres_support import PostgresDatabase
from test_end_to_end import API_KEY, ROOT_SECRET, Fixture


class LedgerAuditReconciliationTest(Fixture, unittest.TestCase):
    def setUp(self):
        self.db = PostgresDatabase()
        self.addCleanup(self.db.close)
        database.migrate(self.db.owner_dsn)
        self.connection = self.db.connect(runtime=True)
        self.addCleanup(self.connection.close)
        self.acceptance_connection = self.connection

    def start(self, *, requires_approval=False, anchor=None,
              acceptance_connection=None, audit_connection=None):
        keys = derive_keys(ROOT_SECRET)
        worker_authority = WorkerAuthority(result_key=keys.result_key,
                                           integrity_key=keys.integrity_key)
        acceptance_connection = (
            self.acceptance_connection if acceptance_connection is None
            else acceptance_connection)
        audit_connection = self.connection if audit_connection is None else audit_connection
        return build(
            root_secret=ROOT_SECRET,
            policy=self.policy_for(requires_approval=requires_approval),
            api_keys={API_KEY: "subject-demo"}, workers=(DeterministicSummarizer(),),
            anchor=AuditAnchor() if anchor is None else anchor, clock=lambda: 1_700_000_000,
            job_ledger=database.PostgresJobLedger(self.connection),
            acceptance_ledger=database.PostgresAcceptanceLedger(acceptance_connection),
            database_connection=self.connection,
            audit_chain_factory=lambda audit: PostgresAuditChain(
                audit_connection, authority=audit),
            runner_factory=lambda worker: WorkerRunner(worker, authority=worker_authority),
        )

    def append_issued(self, handoff, authority):
        event = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + handoff_digest(handoff)[:16],
            actor=authority.actor("orchestrator", "orchestrator-1"),
            action="HANDOFF_ISSUED", decision="ALLOWED",
            reason_code="POLICY_SATISFIED", occurred_at=handoff.issued_at)
        PostgresAuditChain(self.connection, authority=authority).append(event)

    def test_process_local_audit_cannot_claim_an_atomic_database_mutation(self):
        keys = derive_keys(ROOT_SECRET)
        worker_authority = WorkerAuthority(result_key=keys.result_key,
                                           integrity_key=keys.integrity_key)
        service = build(
            root_secret=ROOT_SECRET, policy=self.policy_for(),
            api_keys={API_KEY: "subject-demo"}, workers=(DeterministicSummarizer(),),
            anchor=AuditAnchor(),
            runner_factory=lambda worker: WorkerRunner(worker,
                                                        authority=worker_authority))
        self.addCleanup(service.close)
        with self.assertRaisesRegex(ContractError, "PostgreSQL chain"):
            service._recorder.atomic(lambda transaction: None, lambda result: None)

    def test_durable_acceptance_ledger_requires_the_audit_connection(self):
        foreign = self.db.connect(runtime=True)
        self.addCleanup(foreign.close)
        with self.assertRaisesRegex(ContractError, "durable acceptance storage"):
            self.start(acceptance_connection=foreign)

    def test_durable_audit_chain_requires_the_database_connection(self):
        foreign = self.db.connect(runtime=True)
        self.addCleanup(foreign.close)
        with self.assertRaisesRegex(ContractError, "durable service needs"):
            self.start(audit_connection=foreign)

    def test_atomic_admission_refuses_a_permit_from_another_gateway(self):
        service = self.start()
        self.addCleanup(service.close)
        admission = service.orchestrator.admit(
            {"text": "foreign gateway"}, subject="subject-demo",
            job_id="job-foreign-gateway", policy=service.policy, now=1_700_000_000)
        foreign_gateway = Gateway(
            gateway_id="gateway-other", handoff_verifier=service.handoff_verifier,
            approval_store=ApprovalStore())
        permit = foreign_gateway.admit(
            admission.wire, subject="subject-demo", job_id="job-foreign-gateway",
            policy=service.policy, now=1_700_000_000)

        with self.assertRaisesRegex(ContractError, "service did not wire"):
            service.orchestrator._atomic_admitted(
                lambda transaction: (permit, None), "subject-demo")

    def test_atomic_audit_rejects_missing_mutation_callback(self):
        service = self.start()
        self.addCleanup(service.close)
        with self.assertRaisesRegex(ContractError, "needs callbacks"):
            service._recorder.atomic(None, lambda result: None)

    def test_reservation_and_signed_admission_roll_back_together(self):
        service = self.start()
        self.addCleanup(service.close)
        signer = HandoffSigner(integrity_key=derive_keys(ROOT_SECRET).integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "atomic"}, subject="subject-demo", job_id="job-atomic",
                     policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-atomic",
                           policy=policy, verifier=signer.verifier(), now=100)
        digest = sha256(wire).hexdigest()
        service._recorder.append(event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + digest[:16],
            actor=service.audit.actor("orchestrator", "orchestrator-1"),
            action="HANDOFF_ISSUED", decision="ALLOWED",
            reason_code="POLICY_SATISFIED", occurred_at=100))
        reservation = Reservation("job-atomic", "subject-demo", digest,
                                  handoff.expires_at, 100)
        admitted = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + digest[:16],
            actor=service.audit.actor("gateway", "gateway-1"),
            action="HANDOFF_ADMITTED", decision="ALLOWED",
            reason_code=ADMISSION_REASON_CODE, occurred_at=100)

        def reserve(transaction):
            return service.orchestrator._ledger.reserve(
                reservation, transaction=transaction)

        def admission_event(inserted):
            if inserted is not True:
                raise ContractError("reservation was not inserted")
            return admitted

        with mock.patch("geniusnew.anchor_process._check_commit_size",
                        side_effect=ContractError("anchor commit is too large")):
            with self.assertRaisesRegex(ContractError, "anchor commit is too large"):
                service._recorder.atomic(reserve, admission_event)
        self.assertIsNone(self.connection.execute(
            "SELECT 1 FROM public.job_ledger WHERE job_id='job-atomic'").fetchone())
        self.assertEqual(len(service.chain), 1)

        self.assertTrue(service._recorder.atomic(reserve, admission_event))
        self.assertEqual(len(service.chain), 2)
        self.assertEqual(service.head().count, 2)
        with self.assertRaisesRegex(ContractError, "reservation was not inserted"):
            service._recorder.atomic(reserve, admission_event)
        self.assertEqual(len(service.chain), 2)

    def test_acceptance_and_signed_result_event_roll_back_together(self):
        service = self.start()
        self.addCleanup(service.close)
        keys = derive_keys(ROOT_SECRET)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        worker = WorkerAuthority(result_key=keys.result_key,
                                 integrity_key=keys.integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "accept atomically"}, subject="subject-demo",
                     job_id="job-accepted-atomic", policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-accepted-atomic",
                           policy=policy, verifier=signer.verifier(), now=100)
        result_wire = produce({"text": "accepted"}, handoff=handoff,
                              status="SUCCEEDED", reason_code="WORK_COMPLETED",
                              authority=worker, now=110)
        digest = sha256(wire).hexdigest()
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-accepted-atomic','subject-demo',%s,"
                "'EXECUTION_COMMITTED',100,100,100,160)", (digest,))
        for component, action, reason in (
                ("orchestrator", "HANDOFF_ISSUED", "POLICY_SATISFIED"),
                ("gateway", "HANDOFF_ADMITTED", ADMISSION_REASON_CODE),
                ("orchestrator", "EXECUTION_DISPATCHED", "POLICY_SATISFIED")):
            service._recorder.append(event_from_handoff(
                handoff, api_subject="subject-demo", trace_id="trace-" + digest[:16],
                actor=service.audit.actor(component, component + "-1"),
                action=action, decision="ALLOWED", reason_code=reason,
                occurred_at=100))
        accepted_event = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + digest[:16],
            result_sha256=sha256(result_wire).hexdigest(),
            actor=service.audit.actor("monitor", "verifier-1"),
            action="RESULT_ACCEPTED", decision="ALLOWED",
            reason_code="RESULT_VALID", occurred_at=120)
        ledger = database.PostgresAcceptanceLedger(self.connection)

        def accept_once(transaction):
            return ledger.reserve(job_id=handoff.job_id, handoff_wire=wire,
                                  result_wire=result_wire, now=120,
                                  transaction=transaction)

        def result_event(inserted):
            if inserted is not True:
                raise ContractError("result was not accepted")
            return accepted_event

        with mock.patch("geniusnew.anchor_process._check_commit_size",
                        side_effect=ContractError("anchor commit is too large")):
            with self.assertRaisesRegex(ContractError, "anchor commit is too large"):
                service._recorder.atomic(accept_once, result_event)
        self.assertIsNone(self.connection.execute(
            "SELECT 1 FROM public.acceptance_ledger WHERE job_id='job-accepted-atomic'"
        ).fetchone())
        self.assertEqual(self.connection.execute(
            "SELECT state FROM public.job_ledger WHERE job_id='job-accepted-atomic'"
        ).fetchone(), ("EXECUTION_COMMITTED",))
        self.assertTrue(service._recorder.atomic(accept_once, result_event))
        self.assertEqual(self.connection.execute(
            "SELECT state FROM public.job_ledger WHERE job_id='job-accepted-atomic'"
        ).fetchone(), ("COMPLETED",))
        self.start().close()

    def test_failed_anchor_acknowledgement_blocks_new_writes_until_reanchored(self):
        service = self.start()
        self.addCleanup(service.close)
        signer = HandoffSigner(integrity_key=derive_keys(ROOT_SECRET).integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "anchor"}, subject="subject-demo", job_id="job-anchor",
                     policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-anchor",
                           policy=policy, verifier=signer.verifier(), now=100)
        event = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + sha256(wire).hexdigest()[:16],
            actor=service.audit.actor("orchestrator", "orchestrator-1"),
            action="HANDOFF_ISSUED", decision="ALLOWED",
            reason_code="POLICY_SATISFIED", occurred_at=100)

        with mock.patch.object(service.anchor, "commit",
                               side_effect=ContractError("anchor unavailable")):
            with self.assertRaisesRegex(ContractError, "anchor unavailable"):
                service._recorder.append(event)
            self.assertEqual(len(service.chain), 1)
            with self.assertRaisesRegex(ContractError, "anchor unavailable"):
                service._recorder.append(event)
            self.assertEqual(len(service.chain), 1)

        service._recorder.append(event)
        self.assertEqual(len(service.chain), 2)
        records = service.chain.records
        self.assertEqual(service.anchor.committed,
                         (len(records), records[-1].record_hash))

    def test_restart_refuses_database_ahead_of_unavailable_anchor_until_reanchored(self):
        anchor = AuditAnchor()
        service = self.start(anchor=anchor)
        signer = HandoffSigner(integrity_key=derive_keys(ROOT_SECRET).integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "restart anchor"}, subject="subject-demo",
                     job_id="job-restart-anchor", policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-restart-anchor",
                           policy=policy, verifier=signer.verifier(), now=100)
        event = event_from_handoff(
            handoff, api_subject="subject-demo",
            trace_id="trace-" + sha256(wire).hexdigest()[:16],
            actor=service.audit.actor("orchestrator", "orchestrator-1"),
            action="HANDOFF_ISSUED", decision="ALLOWED",
            reason_code="POLICY_SATISFIED", occurred_at=100)
        with mock.patch.object(anchor, "commit",
                               side_effect=ContractError("anchor unavailable")):
            with self.assertRaisesRegex(ContractError, "anchor unavailable"):
                service._recorder.append(event)
        service.close()
        self.assertEqual(len(PostgresAuditChain(
            self.connection, authority=AuditAuthority(
                audit_key=derive_keys(ROOT_SECRET).audit_key))), 1)

        with mock.patch.object(anchor, "commit",
                               side_effect=ContractError("anchor unavailable")):
            with self.assertRaisesRegex(ContractError, "anchor unavailable"):
                self.start(anchor=anchor)

        restarted = self.start(anchor=anchor)
        self.addCleanup(restarted.close)
        records = restarted.chain.records
        self.assertEqual(anchor.committed, (len(records), records[-1].record_hash))

    def test_durable_stores_refuse_a_foreign_transaction_connection(self):
        service = self.start(requires_approval=True)
        self.addCleanup(service.close)
        signer = HandoffSigner(integrity_key=derive_keys(ROOT_SECRET).integrity_key)
        policy = self.policy_for(requires_approval=True)
        wire = issue({"text": "foreign transaction"}, subject="subject-demo",
                     job_id="job-foreign", policy=policy, signer=signer, now=100)
        handoff = validate_pending(wire, subject="subject-demo", job_id="job-foreign",
                                   policy=policy, verifier=signer.verifier(), now=100)
        digest = sha256(wire).hexdigest()
        waiting = _Waiting("subject-demo", wire, handoff, "trace-" + digest[:16])
        reservation = Reservation("job-foreign", "subject-demo", digest,
                                  handoff.expires_at, 100)
        with self.db.connect(runtime=True) as foreign:
            with foreign.transaction():
                with self.assertRaisesRegex(ContractError, "own active audit transaction"):
                    service.pending.add("job-foreign", waiting, now=100,
                                        transaction=foreign)
                with self.assertRaisesRegex(ContractError, "own active audit transaction"):
                    service.orchestrator._ledger.reserve(reservation,
                                                         transaction=foreign)
        self.assertIsNone(self.connection.execute(
            "SELECT 1 FROM public.job_ledger WHERE job_id='job-foreign'").fetchone())

    def test_sql_inserted_job_without_admission_event_refuses_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "admission"}, subject="subject-demo",
                     job_id="job-forged", policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-forged",
                           policy=policy, verifier=signer.verifier(), now=100)
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-forged','subject-demo',%s,'RESERVED',100,100,100,160)",
                (sha256(wire).hexdigest(),))
        self.append_issued(handoff, authority)
        with self.assertRaisesRegex(ContractError, "audit admissions"):
            self.start()

    def test_pending_job_without_issuance_event_refuses_start(self):
        keys = derive_keys(ROOT_SECRET)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for(requires_approval=True)
        job_id = "job-unaudited-pending"
        wire = issue({"text": "pending"}, subject="subject-demo",
                     job_id=job_id, policy=policy, signer=signer, now=100)
        handoff = validate_pending(wire, subject="subject-demo", job_id=job_id,
                                   policy=policy, verifier=signer.verifier(), now=100)
        waiting = _Waiting("subject-demo", wire, handoff,
                           "trace-" + sha256(wire).hexdigest()[:16])
        database.PostgresPendingJobs(
            self.connection, policy=policy, verifier=signer.verifier()).add(
                job_id, waiting, now=100)
        unrelated_wire = issue(
            {"text": "unrelated issuance"}, subject="subject-demo",
            job_id="job-unrelated-issuance", policy=policy, signer=signer, now=100)
        unrelated = validate_pending(
            unrelated_wire, subject="subject-demo", job_id="job-unrelated-issuance",
            policy=policy, verifier=signer.verifier(), now=100)
        self.append_issued(
            unrelated, AuditAuthority(audit_key=derive_keys(ROOT_SECRET).audit_key))
        with self.assertRaisesRegex(ContractError, "audit issuance"):
            self.start(requires_approval=True)

    def test_refused_job_without_issuance_event_refuses_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "unrelated issuance"}, subject="subject-demo",
                     job_id="job-unrelated-refused", policy=policy,
                     signer=signer, now=100)
        handoff = validate(
            wire, subject="subject-demo", job_id="job-unrelated-refused",
            policy=policy, verifier=signer.verifier(), now=100)
        self.append_issued(handoff, authority)
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-unaudited-refused','subject-demo',%s,'REFUSED',100,NULL,110,160)",
                ("e" * 64,))
        with self.assertRaisesRegex(ContractError, "matching audit issuance"):
            self.start()

    def test_job_count_exceeding_audit_records_refuses_at_the_count_bound(self):
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-without-record','subject-demo',%s,'REFUSED',100,NULL,110,160)",
                ("f" * 64,))
        with self.assertRaisesRegex(ContractError, "record-count bound"):
            self.start()

    def test_approval_count_exceeding_audit_records_refuses_at_the_count_bound(self):
        signer = HandoffSigner(integrity_key=derive_keys(ROOT_SECRET).integrity_key)
        policy = self.policy_for(requires_approval=True)
        wire = issue({"text": "orphan approval"}, subject="subject-demo",
                     job_id="job-orphan-approval", policy=policy, signer=signer, now=100)
        scope = create_scope(wire, subject="subject-demo", job_id="job-orphan-approval",
                             policy=policy, verifier=signer.verifier(), now=100)
        with self.db.connect() as owner:
            owner.execute("ALTER TABLE public.approval_records DISABLE TRIGGER ALL")
            owner.execute(
                "INSERT INTO public.approval_records "
                "(token_digest,record_hash,scope,issued_at,expires_at,state,changed_at) "
                "VALUES (%s,%s,%s,100,160,'GRANTED',100)",
                ("a" * 64, "b" * 64, canonical(scope.to_dict())))
        with self.assertRaisesRegex(ContractError, "approval records exceed"):
            self.start(requires_approval=True)

    def test_acceptance_count_exceeding_audit_records_refuses_at_the_count_bound(self):
        with self.db.connect() as owner:
            owner.execute("ALTER TABLE public.acceptance_ledger DISABLE TRIGGER ALL")
            owner.execute(
                "INSERT INTO public.acceptance_ledger "
                "(handoff_sha256,job_id,handoff_wire,result_sha256,result_wire,accepted_at) "
                "VALUES (%s,'job-without-record',%s,%s,%s,100)",
                ("a" * 64, b"wire", "b" * 64, b"result"))
        with self.assertRaisesRegex(ContractError, "acceptance ledger exceeds"):
            self.start()

    def test_invalid_approval_state_refuses_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        wire = issue({"text": "invalid approval state"}, subject="subject-demo",
                     job_id="job-invalid-approval-state", policy=self.policy_for(),
                     signer=signer, now=100)
        handoff = validate(
            wire, subject="subject-demo", job_id="job-invalid-approval-state",
            policy=self.policy_for(), verifier=signer.verifier(), now=100)
        self.append_issued(handoff, authority)
        with self.db.connect() as owner:
            owner.execute(
                "ALTER TABLE public.approval_records "
                "DROP CONSTRAINT approval_records_state_check")
            owner.execute("ALTER TABLE public.approval_records DISABLE TRIGGER ALL")
            owner.execute(
                "INSERT INTO public.approval_records "
                "(token_digest,record_hash,scope,issued_at,expires_at,state,changed_at) "
                "VALUES (%s,%s,%s,100,160,'INVALID',100)",
                ("c" * 64, "d" * 64, b"{}"))
        with self.assertRaisesRegex(ContractError, "approval record state is invalid"):
            self.start()

    def test_duplicate_admission_events_refuse_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "duplicate admission"}, subject="subject-demo",
                     job_id="job-duplicate-admission", policy=policy,
                     signer=signer, now=100)
        handoff = validate(
            wire, subject="subject-demo", job_id="job-duplicate-admission",
            policy=policy, verifier=signer.verifier(), now=100)
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-duplicate-admission','subject-demo',%s,'RESERVED',100,100,100,160)",
                (sha256(wire).hexdigest(),))
        self.append_issued(handoff, authority)
        admission = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + handoff_digest(handoff)[:16],
            actor=authority.actor("gateway", "gateway-1"),
            action="HANDOFF_ADMITTED", decision="ALLOWED",
            reason_code=ADMISSION_REASON_CODE, occurred_at=100)
        chain = PostgresAuditChain(self.connection, authority=authority)
        chain.append(admission)
        chain.append(admission)
        with self.assertRaisesRegex(ContractError, "audit admissions do not match"):
            self.start()

    def test_refused_job_needs_its_terminal_audit_event(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for(requires_approval=True)
        job_id = "job-refused-terminal"
        wire = issue({"text": "refused"}, subject="subject-demo",
                     job_id=job_id, policy=policy, signer=signer, now=100)
        handoff = validate_pending(
            wire, subject="subject-demo", job_id=job_id,
            policy=policy, verifier=signer.verifier(), now=100)
        waiting = _Waiting(
            "subject-demo", wire, handoff,
            "trace-" + sha256(wire).hexdigest()[:16])
        pending = database.PostgresPendingJobs(
            self.connection, policy=policy, verifier=signer.verifier())
        pending.add(job_id, waiting, now=100)
        self.append_issued(handoff, authority)
        pending.refuse(job_id, "subject-demo", now=110)

        with self.assertRaisesRegex(ContractError, "terminal audit events"):
            self.start(requires_approval=True)

        PostgresAuditChain(self.connection, authority=authority).append(
            event_from_handoff(
                handoff, api_subject="subject-demo",
                trace_id="trace-" + handoff_digest(handoff)[:16],
                actor=authority.actor("orchestrator", "orchestrator-1"),
                action="HANDOFF_REJECTED", decision="DENIED",
                reason_code="PENDING_DISPATCH_REFUSED", occurred_at=110))
        self.start(requires_approval=True).close()

    def test_changed_api_subject_without_a_signed_binding_refuses_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "subject binding"}, subject="subject-demo",
                     job_id="job-subject", policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-subject",
                           policy=policy, verifier=signer.verifier(), now=100)
        digest = sha256(wire).hexdigest()
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-subject','subject-attacker',%s,'RESERVED',100,100,100,160)",
                (digest,))
        event = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + digest[:16],
            actor=authority.actor("gateway", "gateway-1"),
            action="HANDOFF_ADMITTED", decision="ALLOWED",
            reason_code=ADMISSION_REASON_CODE, occurred_at=100)
        self.append_issued(handoff, authority)
        PostgresAuditChain(self.connection, authority=authority).append(event)
        with self.assertRaisesRegex(ContractError, "subject"):
            self.start()

    def test_legacy_job_events_without_api_principal_binding_refuse_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "legacy audit"}, subject="subject-demo",
                     job_id="job-legacy", policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-legacy",
                           policy=policy, verifier=signer.verifier(), now=100)
        digest = sha256(wire).hexdigest()
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-legacy','subject-demo',%s,'RESERVED',100,100,100,160)",
                (digest,))
        chain = PostgresAuditChain(self.connection, authority=authority)
        for component, action, reason in (
                ("orchestrator", "HANDOFF_ISSUED", "POLICY_SATISFIED"),
                ("gateway", "HANDOFF_ADMITTED", ADMISSION_REASON_CODE)):
            chain.append(event_from_handoff(
                handoff, trace_id="trace-" + digest[:16],
                actor=authority.actor(component, component + "-1"),
                action=action, decision="ALLOWED", reason_code=reason,
                occurred_at=100))
        with self.assertRaisesRegex(ContractError, "signed audit binding"):
            self.start()

    def test_oversized_approval_scope_refuses_before_python_fetch(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "oversized scope"}, subject="subject-demo",
                     job_id="job-scope", policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-scope",
                           policy=policy, verifier=signer.verifier(), now=100)
        self.append_issued(handoff, authority)
        with self.db.connect() as owner:
            owner.execute("ALTER TABLE public.approval_records "
                          "DROP CONSTRAINT approval_records_scope_check")
            owner.execute(
                "INSERT INTO public.approval_records "
                "(token_digest,record_hash,scope,issued_at,expires_at,state,changed_at) "
                "VALUES (%s,%s,%s,101,160,'GRANTED',101)",
                ("a" * 64, "b" * 64, b"x" * 16385))
        with self.assertRaisesRegex(ContractError, "stored approval scope exceeds"):
            self.start()

    def test_signed_admission_for_a_valid_unusual_job_id_starts(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for()
        job_id = "job with space"
        wire = issue({"text": "unusual id"}, subject="subject-demo",
                     job_id=job_id, policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id=job_id,
                           policy=policy, verifier=signer.verifier(), now=100)
        digest = sha256(wire).hexdigest()
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES (%s,'subject-demo',%s,'RESERVED',100,100,100,160)",
                (job_id, digest))
        event = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + digest[:16],
            actor=authority.actor("gateway", "gateway-1"),
            action="HANDOFF_ADMITTED", decision="ALLOWED",
            reason_code=ADMISSION_REASON_CODE, occurred_at=100)
        self.append_issued(handoff, authority)
        PostgresAuditChain(self.connection, authority=authority).append(event)
        self.start().close()

    def test_valid_approval_flow_reconciles_at_each_restart(self):
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + API_KEY.decode()}
        service = self.start(requires_approval=True)
        response = service.entry.handle(
            method="POST", path="/jobs", headers=headers,
            body=b'{"text":"approval flow"}')
        self.assertEqual(response.status, 202)
        self.assertEqual(response.body["status"], "PENDING_APPROVAL")
        job_id = response.body["job_id"]
        service.close()

        service = self.start(requires_approval=True)
        token = service.approve(job_id)
        service.close()

        service = self.start(requires_approval=True)
        response = service.entry.handle(
            method="POST", path=f"/jobs/{job_id}/approve",
            headers={**headers, "X-Approval-Token": token.hex()}, body=b"{}")
        self.assertEqual(response.status, 202)
        self.assertEqual(response.body["status"], "SUCCEEDED")
        service.close()
        self.start(requires_approval=True).close()

    def test_swapped_consumed_receipts_refuse_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for(requires_approval=True)
        pending = database.PostgresPendingJobs(
            self.connection, policy=policy, verifier=signer.verifier())
        approvals = database.PostgresApprovalStore(self.connection)
        chain = PostgresAuditChain(self.connection, authority=authority)
        jobs = []
        for job_id in ("job-receipt-a", "job-receipt-b"):
            wire = issue({"text": job_id}, subject="subject-demo", job_id=job_id,
                         policy=policy, signer=signer, now=100)
            handoff = validate_pending(wire, subject="subject-demo", job_id=job_id,
                                       policy=policy, verifier=signer.verifier(), now=100)
            digest = sha256(wire).hexdigest()
            pending.add(job_id, _Waiting("subject-demo", wire, handoff,
                                         "trace-" + digest[:16]), now=100)
            scope = create_scope(wire, subject="subject-demo", job_id=job_id,
                                 policy=policy, verifier=signer.verifier(), now=101)
            grant = approvals.grant(scope, now=101, ttl_seconds=10)
            receipt = approvals.consume(grant.token, scope, now=102,
                                        subject="subject-demo")
            jobs.append((handoff, grant, receipt))
        for handoff, grant, _ in jobs:
            self.append_issued(handoff, authority)
            chain.append(event_from_handoff(
                handoff, api_subject="subject-demo", trace_id="trace-" + handoff_digest(handoff)[:16],
                actor=authority.actor("gateway", "gateway-1"),
                action="APPROVAL_GRANTED", decision="ALLOWED",
                reason_code="OPERATOR_APPROVED", occurred_at=101,
                approval_record_hash=grant.record_hash))
        for index, (handoff, _, _) in enumerate(jobs):
            swapped = jobs[1 - index][2]
            chain.append(event_from_handoff(
                handoff, api_subject="subject-demo", trace_id="trace-" + handoff_digest(handoff)[:16],
                actor=authority.actor("gateway", "gateway-1"),
                action="HANDOFF_ADMITTED", decision="ALLOWED",
                reason_code=ADMISSION_REASON_CODE, occurred_at=102,
                approval_record_hash=swapped.record_hash))
        with self.assertRaisesRegex(ContractError, "approval receipt.*job"):
            self.start(requires_approval=True)

    def test_signed_admission_without_a_job_row_refuses_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        wire = issue({"text": "signed admission"}, subject="subject-demo",
                     job_id="job-absent", policy=self.policy_for(), signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-absent",
                           policy=self.policy_for(), verifier=signer.verifier(), now=100)
        event = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + handoff_digest(handoff)[:16],
            actor=authority.actor("gateway", "gateway-1"),
            action="HANDOFF_ADMITTED", decision="ALLOWED",
            reason_code=ADMISSION_REASON_CODE, occurred_at=100)
        self.append_issued(handoff, authority)
        PostgresAuditChain(self.connection, authority=authority).append(event)
        with self.assertRaisesRegex(ContractError, "audit"):
            self.start()

    def test_inserted_granted_record_without_approval_event_refuses_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for(requires_approval=True)
        wire = issue({"text": "pending approval"}, subject="subject-demo",
                     job_id="job-granted", policy=policy, signer=signer, now=100)
        handoff = validate_pending(wire, subject="subject-demo", job_id="job-granted",
                                   policy=policy, verifier=signer.verifier(), now=100)
        digest = sha256(wire).hexdigest()
        database.PostgresPendingJobs(
            self.connection, policy=policy, verifier=signer.verifier()).add(
                "job-granted", _Waiting("subject-demo", wire, handoff,
                                        "trace-" + digest[:16]), now=100)
        self.append_issued(handoff, authority)
        scope = create_scope(wire, subject="subject-demo", job_id="job-granted",
                             policy=policy, verifier=signer.verifier(), now=101)
        database.PostgresApprovalStore(self.connection).grant(scope, now=101,
                                                              ttl_seconds=10)
        with self.assertRaisesRegex(ContractError, "audit"):
            self.start()

    def test_committed_execution_without_execution_event_refuses_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "committed execution"}, subject="subject-demo",
                     job_id="job-committed", policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-committed",
                           policy=policy, verifier=signer.verifier(), now=100)
        digest = sha256(wire).hexdigest()
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-committed','subject-demo',%s,'EXECUTION_COMMITTED',100,100,100,160)",
                (digest,))
        admission = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + digest[:16],
            actor=authority.actor("gateway", "gateway-1"),
            action="HANDOFF_ADMITTED", decision="ALLOWED",
            reason_code=ADMISSION_REASON_CODE, occurred_at=100)
        self.append_issued(handoff, authority)
        PostgresAuditChain(self.connection, authority=authority).append(admission)
        with self.assertRaisesRegex(ContractError, "execution event"):
            self.start()

    def test_duplicate_valid_issuance_refuses_start(self):
        # B6 regression: two HANDOFF_ISSUED events for the same (job_id,
        # handoff_sha256) must be rejected even when every duplicate is
        # event_version 2 with the correct api_subject_sha256.  Previously the
        # reconciliation only checked "at least one" and "all valid", so a
        # replay or double-write of a structurally-valid issuance slipped
        # through and self.start() succeeded.
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "duplicate issuance"}, subject="subject-demo",
                     job_id="job-dup-issued", policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-dup-issued",
                           policy=policy, verifier=signer.verifier(), now=100)
        digest = sha256(wire).hexdigest()
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-dup-issued','subject-demo',%s,'RESERVED',100,100,100,160)",
                (digest,))
        # Append the same valid v2 HANDOFF_ISSUED event twice for the same job.
        chain = PostgresAuditChain(self.connection, authority=authority)
        for _ in range(2):
            chain.append(event_from_handoff(
                handoff, api_subject="subject-demo",
                trace_id="trace-" + handoff_digest(handoff)[:16],
                actor=authority.actor("orchestrator", "orchestrator-1"),
                action="HANDOFF_ISSUED", decision="ALLOWED",
                reason_code="POLICY_SATISFIED", occurred_at=100))
        with self.assertRaisesRegex(ContractError, "duplicate"):
            self.start()

    def test_accepted_result_without_result_event_refuses_start(self):
        keys = derive_keys(ROOT_SECRET)
        authority = AuditAuthority(audit_key=keys.audit_key)
        signer = HandoffSigner(integrity_key=keys.integrity_key)
        policy = self.policy_for()
        wire = issue({"text": "accepted result"}, subject="subject-demo",
                     job_id="job-accepted", policy=policy, signer=signer, now=100)
        handoff = validate(wire, subject="subject-demo", job_id="job-accepted",
                           policy=policy, verifier=signer.verifier(), now=100)
        worker = WorkerAuthority(result_key=keys.result_key,
                                 integrity_key=keys.integrity_key)
        result_wire = produce({"text": "summary"}, handoff=handoff, status="SUCCEEDED",
                              reason_code="WORK_COMPLETED", authority=worker, now=110)
        digest = sha256(wire).hexdigest()
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.job_ledger "
                "(job_id,subject,handoff_sha256,state,created_at,reserved_at,updated_at,expires_at) "
                "VALUES ('job-accepted','subject-demo',%s,'EXECUTION_COMMITTED',100,100,100,160)",
                (digest,))
        self.assertTrue(database.PostgresAcceptanceLedger(
            self.acceptance_connection).reserve(
                job_id="job-accepted", handoff_wire=wire,
                result_wire=result_wire, now=120))
        admission = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + digest[:16],
            actor=authority.actor("gateway", "gateway-1"),
            action="HANDOFF_ADMITTED", decision="ALLOWED",
            reason_code=ADMISSION_REASON_CODE, occurred_at=100)
        execution = event_from_handoff(
            handoff, api_subject="subject-demo", trace_id="trace-" + digest[:16],
            actor=authority.actor("orchestrator", "orchestrator-1"),
            action="EXECUTION_DISPATCHED", decision="ALLOWED",
            reason_code="POLICY_SATISFIED", occurred_at=100)
        chain = PostgresAuditChain(self.connection, authority=authority)
        self.append_issued(handoff, authority)
        chain.append(admission)
        chain.append(execution)
        with self.assertRaisesRegex(ContractError, "result event"):
            self.start()
