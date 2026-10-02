"""Gate B4: durable pending wires and one-way approval histories on PostgreSQL."""

import hashlib
import unittest
from dataclasses import replace
from threading import Barrier, Thread
from unittest.mock import patch

import psycopg

from geniusnew import database
from geniusnew.approvals import ApprovalScope, ApprovalStore, _record_hash
from geniusnew.audit_chain import AuditAnchor
from geniusnew.audit_store import PostgresAuditChain
from geniusnew.contracts import ContractError, canonical, validate_pending
from geniusnew.wiring import _Waiting, build
from geniusnew.workers import DeterministicSummarizer
from postgres_support import PostgresDatabase
from test_approvals import ApprovalFixture
from test_end_to_end import API_KEY, ROOT_SECRET


class DurablePendingTest(ApprovalFixture, unittest.TestCase):
    def setUp(self):
        ApprovalFixture.setUp(self)
        self.db = PostgresDatabase()
        self.addCleanup(self.db.close)
        database.migrate(self.db.owner_dsn)
        self.connection = self.db.connect(runtime=True)
        self.addCleanup(self.connection.close)

    def stores(self, connection=None):
        connection = connection or self.connection
        pending = database.PostgresPendingJobs(
            connection, policy=self.policy, verifier=self.key.verifier())
        approvals = database.PostgresApprovalStore(connection)
        return pending, approvals

    def add(self, pending, *, job_id="job-demo", wire=None):
        wire = self.wire if wire is None else wire
        handoff = validate_pending(wire, subject="subject-demo", job_id=job_id,
                                   policy=self.policy, verifier=self.key.verifier(), now=100)
        waiting = _Waiting("subject-demo", wire, handoff,
                           "trace-" + hashlib.sha256(wire).hexdigest()[:16])
        pending.add(job_id, waiting, now=100)
        return waiting

    def rows(self):
        return self.connection.execute(
            "SELECT state, reserved_at FROM public.job_ledger").fetchall()

    def test_restart_preserves_exact_pending_wire_and_consumed_token(self):
        pending, approvals = self.stores()
        waiting = self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        with self.db.connect(runtime=True) as restarted:
            other_pending, other_approvals = self.stores(restarted)
            self.assertEqual(other_pending.peek("job-demo"), waiting)
            receipt = other_approvals.consume(grant.token, self.scope(), now=102, subject="subject-demo")
            self.assertEqual(receipt.state, "CONSUMED")
        self.assertEqual(self.rows(), [("RESERVED", 102)])
        self.assertEqual(self.connection.execute("SELECT * FROM pending_jobs").fetchall(), [])
        with self.db.connect(runtime=True) as restarted:
            _, other_approvals = self.stores(restarted)
            with self.assertRaisesRegex(ContractError, "not granted"):
                other_approvals.consume(grant.token, self.scope(), now=103, subject="subject-demo")
        self.assertEqual(self.connection.execute(
            "SELECT state FROM approval_records ORDER BY changed_at").fetchall(),
            [("GRANTED",), ("CONSUMED",)])

    def test_durable_wiring_requires_the_same_job_ledger_connection(self):
        with self.db.connect(runtime=True) as other_connection:
            for ledger in (None, database.PostgresJobLedger(other_connection)):
                with self.subTest(ledger=ledger), self.assertRaisesRegex(
                        ContractError, "job ledger.*same connection"):
                    build(root_secret=ROOT_SECRET, policy=self.policy,
                          api_keys={API_KEY: "subject-demo"},
                          workers=(DeterministicSummarizer(),), anchor=AuditAnchor(),
                          database_connection=self.connection, job_ledger=ledger)

    def test_durable_wiring_requires_the_same_acceptance_ledger_connection(self):
        with self.db.connect(runtime=True) as other_connection:
            for ledger in (None, database.PostgresAcceptanceLedger(other_connection)):
                with self.subTest(ledger=ledger), self.assertRaisesRegex(
                        ContractError, "acceptance storage needs the audit database connection"):
                    build(root_secret=ROOT_SECRET, policy=self.policy,
                          api_keys={API_KEY: "subject-demo"},
                          workers=(DeterministicSummarizer(),), anchor=AuditAnchor(),
                          database_connection=self.connection,
                          job_ledger=database.PostgresJobLedger(self.connection),
                          acceptance_ledger=ledger)

    def test_durable_wiring_requires_the_same_postgres_audit_chain_connection(self):
        with self.db.connect(runtime=True) as other_connection:
            with self.assertRaisesRegex(
                    ContractError, "PostgreSQL audit chain on the same connection"):
                build(root_secret=ROOT_SECRET, policy=self.policy,
                      api_keys={API_KEY: "subject-demo"},
                      workers=(DeterministicSummarizer(),), anchor=AuditAnchor(),
                      database_connection=self.connection,
                      job_ledger=database.PostgresJobLedger(self.connection),
                      acceptance_ledger=database.PostgresAcceptanceLedger(self.connection),
                      audit_chain_factory=lambda audit: PostgresAuditChain(
                          other_connection, authority=audit))

    def test_consume_refuses_an_outer_uncommitted_transaction(self):
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        with self.connection.transaction(force_rollback=True):
            with self.assertRaisesRegex(ContractError, "existing transaction"):
                approvals.consume(grant.token, self.scope(), now=102,
                                  subject="subject-demo")
        self.assertEqual(self.rows(), [("PENDING_APPROVAL", None)])
        self.assertEqual(self.connection.execute(
            "SELECT state FROM approval_records").fetchall(), [("GRANTED",)])

    def test_database_trigger_rejects_a_consumption_at_token_expiry(self):
        pending, approvals = self.stores()
        self.add(pending)
        scope = self.scope()
        grant = approvals.grant(scope, now=101, ttl_seconds=60)
        digest = hashlib.sha256(grant.token).hexdigest()
        record_hash = _record_hash(
            token_digest=bytes.fromhex(digest), scope=scope,
            issued_at=grant.issued_at, expires_at=grant.expires_at,
            state="CONSUMED", changed_at=grant.expires_at,
            previous_hash=grant.record_hash)
        with self.db.connect() as owner:
            with self.assertRaises(psycopg.errors.CheckViolation):
                owner.execute(
                    "INSERT INTO approval_records "
                    "(token_digest,record_hash,scope,issued_at,expires_at,state,"
                    "changed_at,previous_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (digest, record_hash, canonical(scope.to_dict()), grant.issued_at,
                     grant.expires_at, "CONSUMED", grant.expires_at,
                     grant.record_hash))

    def test_forged_consumed_tip_without_reservation_refuses_recovery(self):
        pending, approvals = self.stores()
        self.add(pending)
        scope = self.scope()
        grant = approvals.grant(scope, now=101, ttl_seconds=60)
        digest = hashlib.sha256(grant.token).hexdigest()
        record_hash = _record_hash(
            token_digest=bytes.fromhex(digest), scope=scope,
            issued_at=grant.issued_at, expires_at=grant.expires_at,
            state="CONSUMED", changed_at=102,
            previous_hash=grant.record_hash)
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.approval_records "
                "(token_digest,record_hash,scope,issued_at,expires_at,state,"
                "changed_at,previous_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (digest, record_hash, canonical(scope.to_dict()), grant.issued_at,
                 grant.expires_at, "CONSUMED", 102, grant.record_hash))
            owner.execute("UPDATE public.approval_tokens SET current_record_hash=%s "
                          "WHERE token_digest=%s", (record_hash, digest))
        with self.assertRaisesRegex(ContractError, "consumed approval.*reserved job"):
            database.PostgresApprovalStore(self.connection)

    def test_two_consumed_receipts_for_one_job_refuse_recovery(self):
        pending, approvals = self.stores()
        self.add(pending)
        scope = self.scope()
        first = approvals.grant(scope, now=101, ttl_seconds=60)
        second = approvals.grant(scope, now=101, ttl_seconds=60)
        approvals.consume(first.token, scope, now=102, subject="subject-demo")
        digest = hashlib.sha256(second.token).hexdigest()
        record_hash = _record_hash(
            token_digest=bytes.fromhex(digest), scope=scope,
            issued_at=second.issued_at, expires_at=second.expires_at,
            state="CONSUMED", changed_at=102,
            previous_hash=second.record_hash)
        with self.db.connect() as owner:
            owner.execute(
                "INSERT INTO public.approval_records "
                "(token_digest,record_hash,scope,issued_at,expires_at,state,"
                "changed_at,previous_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (digest, record_hash, canonical(scope.to_dict()), second.issued_at,
                 second.expires_at, "CONSUMED", 102, second.record_hash))
            owner.execute("UPDATE public.approval_tokens SET current_record_hash=%s "
                          "WHERE token_digest=%s", (record_hash, digest))
        with self.assertRaisesRegex(ContractError, "consumed approval.*reserved job"):
            database.PostgresApprovalStore(self.connection)

    def test_consumed_receipt_without_its_job_refuses_recovery(self):
        pending, approvals = self.stores()
        self.add(pending)
        scope = self.scope()
        grant = approvals.grant(scope, now=101, ttl_seconds=60)
        approvals.consume(grant.token, scope, now=102, subject="subject-demo")
        with self.db.connect() as owner, owner.transaction():
            owner.execute("SET LOCAL session_replication_role = replica")
            owner.execute("DELETE FROM public.job_ledger WHERE job_id='job-demo'")
        with self.assertRaisesRegex(ContractError, "consumed approval.*reserved job"):
            database.PostgresApprovalStore(self.connection)

    def test_grant_rechecks_that_its_job_is_still_pending(self):
        pending, approvals = self.stores()
        self.add(pending)
        pending.refuse("job-demo", "subject-demo", now=101)
        with self.assertRaisesRegex(ContractError, "pending job"):
            approvals.grant(self.scope(), now=102, ttl_seconds=30)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM public.approval_records").fetchone(), (0,))

    def test_grant_rejects_scope_fields_changed_after_creation(self):
        pending, approvals = self.stores()
        self.add(pending)
        scope = self.scope()
        for field, value in (("job_id", "foreign-job"),
                             ("user_id", "foreign-user"),
                             ("worker_agent_id", "foreign-worker"),
                             ("risk_tier", "foreign-tier"),
                             ("policy_version", "foreign-policy")):
            with self.subTest(field=field), self.assertRaisesRegex(
                    ContractError, "approval grant needs its exact pending job"):
                approvals.grant(replace(scope, **{field: value}), now=101, ttl_seconds=60)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM public.approval_records").fetchone(), (0,))

    def test_wrong_token_and_wrong_scope_leave_both_rows_untouched(self):
        pending, approvals = self.stores()
        waiting = self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        for token, scope in ((b"x" * 32, self.scope()),
                             (grant.token, replace(self.scope(), job_id="other"))):
            with self.subTest(token=token[:1]), self.assertRaises(ContractError):
                approvals.consume(token, scope, now=102, subject="subject-demo")
            self.assertEqual(pending.peek("job-demo"), waiting)
            self.assertEqual(self.rows(), [("PENDING_APPROVAL", None)])
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM approval_records").fetchone(), (1,))

    def test_changed_scope_field_cannot_consume_the_bound_pending_job(self):
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        altered = replace(self.scope(), risk_tier="other")
        with self.assertRaisesRegex(ContractError, "approval scope does not match"):
            approvals.consume(grant.token, altered, now=102, subject="subject-demo")
        self.assertEqual(self.rows(), [("PENDING_APPROVAL", None)])

    def test_approval_transition_rejects_a_boolean_time_before_sql(self):
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        with self.assertRaisesRegex(ContractError, "approval time is invalid"):
            approvals.revoke(grant.token, self.scope(), now=True)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM approval_records").fetchone(), (1,))

    def test_a_duplicate_generated_token_is_a_collision(self):
        pending, _ = self.stores()
        self.add(pending)
        approvals = database.PostgresApprovalStore(
            self.connection, token_source=lambda: b"approval-collision-test-token-00")
        approvals.grant(self.scope(), now=101, ttl_seconds=60)
        with self.assertRaisesRegex(ContractError, "approval token collision"):
            approvals.grant(self.scope(), now=101, ttl_seconds=60)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM approval_records").fetchone(), (1,))

    def test_pending_queue_bound_is_checked_before_insert(self):
        pending, _ = self.stores()
        with patch("geniusnew.database._MAX_PENDING", 0):
            with self.assertRaisesRegex(ContractError, "too many jobs"):
                self.add(pending)
        self.assertEqual(self.rows(), [])

    def test_pending_wire_must_bind_the_original_issuance_time(self):
        pending, _ = self.stores()
        self.add(pending)
        row = self.connection.execute(
            "SELECT p.job_id,p.subject,p.wire,p.trace_id,p.expires_at,j.state,j.subject,"
            "j.handoff_sha256,j.created_at,j.expires_at FROM public.pending_jobs p "
            "JOIN public.job_ledger j ON j.job_id=p.job_id").fetchone()
        changed = list(row)
        changed[8] += 1
        with self.assertRaisesRegex(ContractError, "pending wire does not bind its issuance"):
            pending._waiting(tuple(changed))

    def assert_refused(self, now):
        pending, _ = self.stores()
        self.add(pending)
        pending.refuse("job-demo", "subject-demo", now=now)
        self.assertEqual(self.rows(), [("REFUSED", None)])
        with self.assertRaises(ContractError):
            pending.peek("job-demo")
        with self.assertRaises(ContractError):
            self.add(pending)

    def test_expiry_atomically_removes_pending_and_burns_id(self):
        self.assert_refused(160)

    def test_final_refusal_atomically_removes_pending_and_burns_id(self):
        self.assert_refused(102)

    def test_refusal_rejects_invalid_times_before_changing_pending_state(self):
        pending, _ = self.stores()
        waiting = self.add(pending)
        for now in (0, True, 99, 4102444801):
            with self.subTest(now=now):
                with self.assertRaisesRegex(ContractError, 'refusal time'):
                    pending.refuse('job-demo', 'subject-demo', now=now)
                self.assertEqual(pending.peek('job-demo'), waiting)
                self.assertEqual(self.rows(), [('PENDING_APPROVAL', None)])

    def test_pending_subject_and_duplicate_id_are_refused(self):
        pending, _ = self.stores()
        self.add(pending)
        with self.assertRaises(ContractError):
            pending.take("job-demo", "other-subject")
        with self.assertRaises(ContractError):
            self.add(pending)
        with self.assertRaises(ContractError):
            pending.peek("missing")

    def test_partial_consume_rolls_back_token_pointer_pending_and_ledger(self):
        pending, approvals = self.stores()
        waiting = self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        with self.db.connect() as owner:
            owner.execute("CREATE FUNCTION fail_pending_delete() RETURNS trigger "
                          "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test'; END; $$")
            owner.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON pending_jobs "
                          "FOR EACH ROW EXECUTE FUNCTION fail_pending_delete()")
        with self.assertRaisesRegex(ContractError, "unavailable"):
            approvals.consume(grant.token, self.scope(), now=102, subject="subject-demo")
        self.assertEqual(self.rows(), [("PENDING_APPROVAL", None)])
        self.assertEqual(pending.peek("job-demo"), waiting)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM approval_records").fetchone(), (1,))

    def test_disconnected_database_fails_closed_without_memory_fallback(self):
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        self.connection.close()
        for operation in (lambda: pending.peek("job-demo"),
                          lambda: approvals.consume(grant.token, self.scope(), now=102, subject="subject-demo")):
            with self.assertRaisesRegex(ContractError, "unavailable"):
                operation()

    def test_start_refuses_changed_wire_and_bidirectional_pending_mismatch(self):
        pending, _ = self.stores()
        self.add(pending)
        with self.db.connect() as owner:
            owner.execute("UPDATE pending_jobs SET wire=%s", (self.wire.replace(b"Requires", b"Attacker"),))
        with self.assertRaises(ContractError):
            self.stores()
        with self.db.connect() as owner:
            owner.execute("UPDATE pending_jobs SET wire=%s", (self.wire,))
            owner.execute("DELETE FROM pending_jobs")
        with self.assertRaisesRegex(ContractError, "pending"):
            self.stores()

    def test_start_refuses_noncanonical_wire_and_metadata_changes(self):
        pending, _ = self.stores()
        self.add(pending)
        with self.db.connect() as owner:
            for column, value in (("wire", b" " + self.wire),
                                  ("subject", "foreign"), ("expires_at", 161),
                                  ("trace_id", "foreign")):
                original = owner.execute("SELECT " + column + " FROM pending_jobs").fetchone()[0]
                owner.execute("UPDATE pending_jobs SET " + column + "=%s", (value,))
                with self.assertRaises(ContractError):
                    self.stores()
                owner.execute("UPDATE pending_jobs SET " + column + "=%s", (original,))

    def test_database_rejects_oversized_pending_and_approval_bytes(self):
        pending, _ = self.stores()
        self.add(pending)
        with self.db.connect() as owner:
            owner.execute("DELETE FROM public.pending_jobs")
            for column, value in (("wire", b"x" * 16385),
                                  ("trace_id", "x" * 65)):
                with self.subTest(column=column), self.assertRaises(psycopg.errors.CheckViolation):
                    with owner.transaction():
                        owner.execute(
                            "INSERT INTO public.pending_jobs "
                            "(job_id,subject,wire,trace_id,expires_at) "
                            "VALUES (%s,%s,%s,%s,%s)",
                            ("job-demo", "subject-demo",
                             value if column == "wire" else self.wire,
                             value if column == "trace_id" else "trace-demo", 160))
        with self.db.connect() as owner:
            with self.assertRaises(psycopg.errors.CheckViolation):
                owner.execute(
                    "INSERT INTO public.approval_records "
                    "(token_digest,record_hash,scope,issued_at,expires_at,state,changed_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    ("a" * 64, "b" * 64, b"x" * 16385, 101, 160, "GRANTED", 101))

    def test_owner_oversized_bytes_refuse_before_python_fetch(self):
        pending, approvals = self.stores()
        self.add(pending)
        approvals.grant(self.scope(), now=101, ttl_seconds=60)
        with self.db.connect() as owner:
            owner.execute("ALTER TABLE public.pending_jobs DROP CONSTRAINT pending_jobs_wire_check")
            owner.execute("UPDATE public.pending_jobs SET wire=%s", (b"x" * 16385,))
        with self.assertRaisesRegex(ContractError, "stored pending wire exceeds"):
            database.PostgresPendingJobs(
                self.connection, policy=self.policy, verifier=self.key.verifier())
        with self.db.connect() as owner:
            owner.execute("ALTER TABLE public.approval_records "
                          "DROP CONSTRAINT approval_records_scope_check")
            owner.execute("UPDATE public.approval_records SET scope=%s", (b"x" * 16385,))
        with self.assertRaisesRegex(ContractError, "stored approval scope exceeds"):
            database.PostgresApprovalStore(self.connection)

    def test_start_refuses_stale_pending_for_reserved_job(self):
        pending, _ = self.stores()
        self.add(pending)
        with self.db.connect() as owner:
            owner.execute("UPDATE job_ledger SET state='RESERVED', reserved_at=101, updated_at=101")
        with self.assertRaisesRegex(ContractError, "pending"):
            self.stores()

    def test_parallel_consumption_across_connections_has_one_winner(self):
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        barrier, outcomes = Barrier(2), []
        def consume():
            with self.db.connect(runtime=True) as connection:
                store = database.PostgresApprovalStore(connection)
                barrier.wait()
                try:
                    outcomes.append(store.consume(grant.token, self.scope(), now=102, subject="subject-demo").state)
                except ContractError:
                    outcomes.append("REFUSED")
        threads = [Thread(target=consume), Thread(target=consume)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ["CONSUMED", "REFUSED"])

    def test_revoke_survives_restart_without_consuming_pending(self):
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        approvals.revoke(grant.token, self.scope(), now=102)
        self.assertEqual(self.rows(), [("PENDING_APPROVAL", None)])
        _, restarted = self.stores()
        with self.assertRaisesRegex(ContractError, "not granted"):
            restarted.consume(grant.token, self.scope(), now=103, subject="subject-demo")

    def test_start_refuses_changed_approval_hash_scope_or_pointer(self):
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        approvals.revoke(grant.token, self.scope(), now=102)
        with self.db.connect() as owner:
            owner.execute("ALTER TABLE approval_records DISABLE TRIGGER ALL")
            owner.execute("UPDATE approval_records SET scope=scope || %s WHERE state='GRANTED'", (b" ",))
        with self.assertRaises(ContractError):
            database.PostgresApprovalStore(self.connection)

    def test_runtime_records_are_append_only_and_pointers_cannot_rewind(self):
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        approvals.revoke(grant.token, self.scope(), now=102)
        for query in ("UPDATE approval_records SET changed_at=changed_at",
                      "DELETE FROM approval_records", "DELETE FROM approval_tokens",
                      "UPDATE pending_jobs SET subject='other'",
                      "TRUNCATE approval_records", "ALTER TABLE approval_records DISABLE TRIGGER ALL"):
            with self.subTest(query=query), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.connection.execute(query)
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute("UPDATE approval_tokens SET current_record_hash=%s", (grant.record_hash,))

    def test_database_rejects_second_root_fork_mutable_scope_and_terminal_successor(self):
        from geniusnew.approvals import _record_hash, _Record
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        digest = hashlib.sha256(grant.token).digest()
        root = _Record(digest, self.scope(), 101, 160, "GRANTED", 101, None, grant.record_hash)
        def attempt(state, previous, *, scope=None, changed=102, issued=101, expires=160):
            scope = scope or root.scope
            hashed = _record_hash(token_digest=digest, scope=scope, issued_at=issued,
                                  expires_at=expires, state=state, changed_at=changed,
                                  previous_hash=previous)
            with self.connection.transaction(force_rollback=True):
                approvals._insert(self.connection, _Record(digest, scope, issued, expires,
                                                            state, changed, previous, hashed))
        for state, previous, modifications, error in (
                ("CONSUMED", None, {}, psycopg.errors.CheckViolation),
                ("GRANTED", None, {"changed": 102}, psycopg.errors.UniqueViolation),
                ("GRANTED", root.record_hash, {}, psycopg.errors.CheckViolation),
                ("CONSUMED", root.record_hash, {"scope": replace(root.scope, user_id="other")},
                 psycopg.errors.CheckViolation),
                ("CONSUMED", root.record_hash, {"issued": 102}, psycopg.errors.CheckViolation),
                ("CONSUMED", root.record_hash, {"expires": 159}, psycopg.errors.CheckViolation),
                ("CONSUMED", root.record_hash, {"changed": 100}, psycopg.errors.CheckViolation)):
            with self.subTest(state=state, modifications=modifications), self.assertRaises(error):
                attempt(state, previous, **modifications)
        revoked = approvals.revoke(grant.token, self.scope(), now=102)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            attempt("CONSUMED", root.record_hash, changed=103)
        with self.assertRaises(psycopg.errors.CheckViolation):
            attempt("CONSUMED", revoked.record_hash, changed=103)

    def test_initial_pointer_and_composite_keys_cannot_cross_tokens(self):
        from geniusnew.approvals import _record_hash, _Record
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        revoked = approvals.revoke(grant.token, self.scope(), now=102)
        digest = hashlib.sha256(grant.token).hexdigest()
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute("INSERT INTO approval_tokens VALUES (%s,%s)", (digest, revoked.record_hash))
        other_digest = "a" * 64 if digest != "a" * 64 else "b" * 64
        hashed = _record_hash(token_digest=bytes.fromhex(other_digest), scope=self.scope(),
                              issued_at=101, expires_at=160, state="CONSUMED", changed_at=103,
                              previous_hash=grant.record_hash)
        with self.assertRaises(psycopg.errors.CheckViolation):
            approvals._insert(self.connection, _Record(bytes.fromhex(other_digest), self.scope(),
                                                       101, 160, "CONSUMED", 103, grant.record_hash, hashed))
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute("UPDATE approval_tokens SET token_digest=%s", (other_digest,))

    def test_start_refuses_pointer_rewind_and_orphan_records(self):
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        revoked = approvals.revoke(grant.token, self.scope(), now=102)
        with self.db.connect() as owner:
            owner.execute("ALTER TABLE approval_tokens DISABLE TRIGGER ALL")
            owner.execute("UPDATE approval_tokens SET current_record_hash=%s", (grant.record_hash,))
        with self.assertRaisesRegex(ContractError, "history tip"):
            database.PostgresApprovalStore(self.connection)
        with self.db.connect() as owner:
            owner.execute("UPDATE approval_tokens SET current_record_hash=%s", (revoked.record_hash,))
            owner.execute("DELETE FROM approval_tokens")
        with self.assertRaisesRegex(ContractError, "pointers do not match"):
            database.PostgresApprovalStore(self.connection)

    def test_partial_pending_insert_rolls_back_ledger(self):
        pending, _ = self.stores()
        with self.db.connect() as owner:
            owner.execute("CREATE FUNCTION fail_insert_pending() RETURNS trigger "
                          "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test'; END; $$")
            owner.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON pending_jobs "
                          "FOR EACH ROW EXECUTE FUNCTION fail_insert_pending()")
        with self.assertRaisesRegex(ContractError, "unavailable"):
            self.add(pending)
        self.assertEqual(self.rows(), [])

    def test_unexpired_handoff_can_get_new_token_after_only_token_expiry(self):
        pending, approvals = self.stores()
        waiting = self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=1)
        with self.assertRaisesRegex(ContractError, "not currently valid"):
            approvals.consume(grant.token, self.scope(), now=102, subject="subject-demo")
        self.assertEqual(pending.peek("job-demo"), waiting)
        self.assertEqual(self.rows(), [("PENDING_APPROVAL", None)])

    def test_foreign_subject_cannot_consume_or_refuse_an_exact_scope(self):
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        with self.assertRaisesRegex(ContractError, "subject"):
            approvals.consume(grant.token, self.scope(), now=102, subject="foreign")
        with self.assertRaisesRegex(ContractError, "subject"):
            pending.refuse("job-demo", "foreign", now=102)
        self.assertEqual(self.rows(), [("PENDING_APPROVAL", None)])

    def test_store_configuration_and_malformed_inputs_fail_closed(self):
        for connection in (None, object()):
            with self.assertRaisesRegex(ContractError, "autocommit"):
                database.PostgresApprovalStore(connection)
        with self.db.connect(runtime=True) as connection:
            connection.autocommit = False
            with self.assertRaisesRegex(ContractError, "autocommit"):
                database.PostgresApprovalStore(connection)
        for policy, verifier in ((None, self.key.verifier()), (self.policy, self.key)):
            with self.assertRaisesRegex(ContractError, "public handoff verifier"):
                database.PostgresPendingJobs(self.connection, policy=policy, verifier=verifier)
        pending, approvals = self.stores()
        self.add(pending)
        grant = approvals.grant(self.scope(), now=101, ttl_seconds=60)
        for token, scope, now in ((None, self.scope(), 102), (b"short", self.scope(), 102),
                                  (grant.token, None, 102), (grant.token, self.scope(), True),
                                  (grant.token, self.scope(), 0)):
            with self.assertRaises(ContractError):
                approvals.consume(token, scope, now=now, subject="subject-demo")
        with self.assertRaisesRegex(ContractError, "waiting signed handoff"):
            pending.add("other", None, now=100)
        with self.assertRaisesRegex(ContractError, "metadata"):
            waiting = pending.peek("job-demo")
            pending.add("job-demo", replace(waiting, trace_id="other"), now=100)
