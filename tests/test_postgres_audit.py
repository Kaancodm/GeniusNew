"""B5 uses a real PostgreSQL for atomic appends, recovery and append-only rights."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from unittest.mock import patch
import unittest

import psycopg

from geniusnew import database
from geniusnew import anchor_process
from geniusnew.audit import AuditAuthority
from geniusnew.audit_chain import AuditAnchor, _record_hash, verify
from geniusnew.audit_store import PostgresAuditChain, _AUDIT_LOCK
from geniusnew.contracts import ContractError
from geniusnew.wiring import _AnchoredAudit
from tests.postgres_support import PostgresDatabase
from tests import test_audit_store


class PostgresAuditTest(unittest.TestCase):
    def setUp(self):
        self.db = PostgresDatabase()
        self.addCleanup(self.db.close)
        database.migrate(self.db.owner_dsn)
        fixture = test_audit_store.StoredAuditSnapshotTest()
        fixture.setUp()
        self.authority = fixture.authority
        self.event = fixture.event
        self.connection = self.db.connect(runtime=True)
        self.addCleanup(self.connection.close)
        self.chain = PostgresAuditChain(self.connection, authority=self.authority)

    def owner(self, query, parameters=None):
        with self.db.connect() as connection:
            cursor = connection.execute(query, parameters)
            return cursor.fetchall() if cursor.description else None

    def counts(self):
        return self.owner("SELECT (SELECT count(*) FROM audit_chain), "
                          "(SELECT count(*) FROM audit_heads)")[0]

    def recorder(self, anchor=None):
        return _AnchoredAudit(self.authority, self.chain, anchor or AuditAnchor())

    def test_record_and_signed_head_survive_a_new_connection_byte_exactly(self):
        self.assertEqual((len(self.chain), self.chain.head_hash), (0, "0" * 64))
        record = self.chain.append(self.event)
        self.assertEqual((len(self.chain), self.chain.head_hash), (1, record.record_hash))
        stored = self.owner("SELECT event FROM audit_chain")
        self.assertEqual(stored, [(self.event.to_bytes(),)])
        with self.db.connect(runtime=True) as other:
            with patch.object(self.authority, "sign", side_effect=AssertionError("re-signed")):
                rebuilt = PostgresAuditChain(other, authority=self.authority)
                head, records = rebuilt.snapshot(self.authority)
        self.assertEqual(records, (record,))
        self.assertEqual(head.signature,
                         self.owner("SELECT signature FROM audit_heads")[0][0].hex())
        self.assertEqual(verify(records, head, authority=self.authority.verifier()), 1)

    def test_head_insert_failure_rolls_back_the_record_and_never_anchors(self):
        self.owner("CREATE FUNCTION public.reject_audit_head() RETURNS trigger "
                   "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test-only'; END; $$")
        self.owner("CREATE TRIGGER reject_audit_head BEFORE INSERT ON audit_heads "
                   "FOR EACH ROW EXECUTE FUNCTION public.reject_audit_head()")
        recorder = self.recorder()
        with self.assertRaises(ContractError):
            recorder.append(self.event)
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(recorder.anchor.committed[0], 0)

    def test_commit_failure_rolls_back_both_records_without_an_anchor_call(self):
        self.owner("CREATE FUNCTION public.reject_audit_commit() RETURNS trigger "
                   "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test-only'; END; $$")
        self.owner("CREATE CONSTRAINT TRIGGER reject_audit_commit AFTER INSERT ON audit_heads "
                   "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                   "EXECUTE FUNCTION public.reject_audit_commit()")
        recorder = self.recorder()
        with self.assertRaises(ContractError):
            recorder.append(self.event)
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(recorder.anchor.committed[0], 0)

    def test_sql_unsigned_suffix_is_a_start_refusal_not_a_recovery_signature(self):
        first = self.chain.append(self.event)
        digest = _record_hash(index=1, event=self.event, previous_hash=first.record_hash)
        self.owner("INSERT INTO audit_chain VALUES (%s,%s,%s,%s)",
                   (1, first.record_hash, digest, self.event.to_bytes()))
        with patch.object(self.authority, "sign", side_effect=AssertionError("re-signed")):
            with self.assertRaisesRegex(ContractError, "one-to-one"):
                PostgresAuditChain(self.connection, authority=self.authority)

    def test_oversized_event_is_rejected_by_the_database_before_storage(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute(
                "INSERT INTO public.audit_chain (index,previous_hash,record_hash,event) "
                "VALUES (0,%s,%s,%s)",
                ("0" * 64, "f" * 64, b"x" * 8193))
        self.assertEqual(self.counts(), (0, 0))

    def test_owner_inserted_oversized_event_refuses_before_fetching_its_bytes(self):
        self.owner("ALTER TABLE public.audit_chain DROP CONSTRAINT audit_chain_event_check")
        self.owner(
            "INSERT INTO public.audit_chain (index,previous_hash,record_hash,event) "
            "VALUES (0,%s,%s,%s)",
            ("0" * 64, "f" * 64, b"x" * 8193))
        with self.assertRaisesRegex(ContractError, "event exceeds the maximum size"):
            PostgresAuditChain(self.connection, authority=self.authority)

    def test_owner_inserted_oversized_head_version_refuses_before_fetching_it(self):
        self.chain.append(self.event)
        self.owner("UPDATE public.audit_heads SET version=%s WHERE count=1",
                   ("x" * 100_000,))
        with self.assertRaisesRegex(ContractError, "head version exceeds the maximum size"):
            PostgresAuditChain(self.connection, authority=self.authority)

    def test_recovery_reuses_the_precrash_signature_and_proves_the_anchor_prefix(self):
        self.chain.append(self.event)
        anchor = AuditAnchor()
        first = self.recorder(anchor).head()
        self.chain.append(self.event)
        saved = self.owner("SELECT signature FROM audit_heads ORDER BY count DESC LIMIT 1")[0][0]
        with patch.object(self.authority, "sign", side_effect=AssertionError("re-signed")):
            rebuilt = PostgresAuditChain(self.connection, authority=self.authority)
            recovered = _AnchoredAudit(self.authority, rebuilt, anchor).head()
        self.assertEqual(recovered.signature, saved.hex())
        self.assertEqual(anchor.committed, (2, recovered.head_hash))
        self.assertEqual(first.count, 1)

    def test_anchor_ahead_same_length_fork_and_longer_fork_are_refused(self):
        self.chain.append(self.event)
        head, records = self.chain.snapshot(self.authority)
        anchor = AuditAnchor.resumed(head, authority=self.authority.verifier())
        self.chain.append(self.event)
        other_head, other_records = self.chain.snapshot(self.authority)
        too_far = AuditAnchor.resumed(other_head, authority=self.authority.verifier())
        self.owner("DELETE FROM audit_chain WHERE index=1")
        self.owner("DELETE FROM audit_heads WHERE count=2")
        with self.assertRaises(ContractError):
            self.recorder(too_far).head()
        from geniusnew.audit_chain import sign_head
        fork = sign_head(count=1, head_hash="f" * 64, authority=self.authority)
        rival = AuditAnchor.resumed(fork, authority=self.authority.verifier())
        with self.assertRaises(ContractError):
            self.recorder(rival).head()
        self.chain.append(self.event)
        with self.assertRaises(ContractError):
            self.recorder(rival).head()
        self.assertEqual(anchor.committed, (head.count, head.head_hash))

    def test_altered_canonical_bytes_and_earlier_signature_are_refused(self):
        self.chain.append(self.event)
        self.chain.append(self.event)
        self.owner("UPDATE audit_chain SET event = %s WHERE index=0",
                   (b" " + self.event.to_bytes(),))
        with self.assertRaisesRegex(ContractError, "byte-exact"):
            PostgresAuditChain(self.connection, authority=self.authority)
        self.owner("UPDATE audit_chain SET event = %s WHERE index=0",
                   (self.event.to_bytes(),))
        self.owner("UPDATE audit_heads SET signature=%s WHERE count=1", (b"x" * 64,))
        with self.assertRaisesRegex(ContractError, "signature"):
            PostgresAuditChain(self.connection, authority=self.authority)

    def test_owner_changed_head_time_refuses_recovery(self):
        self.chain.append(self.event)
        self.owner("UPDATE public.audit_heads SET created_at=%s",
                   (self.event.occurred_at + 1,))
        with self.assertRaisesRegex(ContractError, "timestamp does not match"):
            PostgresAuditChain(self.connection, authority=self.authority)

    def test_runtime_cannot_update_delete_or_truncate_either_audit_table(self):
        self.chain.append(self.event)
        for query in ("UPDATE audit_chain SET event=event", "DELETE FROM audit_chain",
                      "TRUNCATE audit_chain", "UPDATE audit_heads SET created_at=1",
                      "DELETE FROM audit_heads", "TRUNCATE audit_heads"):
            with self.subTest(query=query):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    self.connection.execute(query)

    def test_two_instances_append_a_contiguous_shared_history(self):
        def append():
            with self.db.connect(runtime=True) as connection:
                chain = PostgresAuditChain(connection, authority=self.authority)
                return chain.append(self.event)
        with ThreadPoolExecutor(max_workers=4) as executor:
            records = list(executor.map(lambda _: append(), range(8)))
        self.assertEqual(sorted(record.index for record in records), list(range(8)))
        self.assertEqual(self.counts(), (8, 8))
        head, stored = self.chain.snapshot(self.authority)
        self.assertEqual(verify(stored, head, authority=self.authority.verifier()), 8)

    def test_oversized_anchor_commit_refuses_before_a_database_commit(self):
        with patch.object(anchor_process, "_MAX_REQUEST_BYTES", 600):
            with self.assertRaisesRegex(ContractError, "anchor message is too large"):
                self.recorder().append(self.event)
        self.assertEqual(self.counts(), (0, 0))
        self.recorder().append(self.event)
        self.assertEqual(self.counts(), (1, 1))

    def test_anchor_state_bound_refuses_before_a_database_commit(self):
        with patch.object(anchor_process, "_MAX_STATE_BYTES", 100):
            with self.assertRaisesRegex(ContractError, "anchor state would exceed"):
                self.recorder().append(self.event)
        self.assertEqual(self.counts(), (0, 0))

    def test_anchor_lock_refuses_an_outer_transaction_and_missing_release(self):
        with self.chain.transaction():
            with self.assertRaisesRegex(ContractError, "outside an existing transaction"):
                with self.chain.anchor_lock():
                    self.fail("entered anchor lock in an outer transaction")
        with self.assertRaisesRegex(ContractError, "anchor lock was not held"):
            with self.chain.anchor_lock():
                self.assertEqual(self.connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (_AUDIT_LOCK,)).fetchone(), (True,))

    def test_two_instances_keep_anchor_commit_inside_the_shared_lock(self):
        first_entered = Event()
        second_entered = Event()
        turn = Lock()

        class DelayedAnchor(AuditAnchor):
            def commit(self, head, records, *, authority):
                with turn:
                    first = not first_entered.is_set()
                    if first:
                        first_entered.set()
                if first:
                    second_entered.wait(1)
                else:
                    second_entered.set()
                return super().commit(head, records, authority=authority)

        anchor = DelayedAnchor()
        first = self.recorder(anchor)

        def second_append():
            with self.db.connect(runtime=True) as connection:
                chain = PostgresAuditChain(connection, authority=self.authority)
                _AnchoredAudit(self.authority, chain, anchor).append(self.event)

        with ThreadPoolExecutor(max_workers=2) as executor:
            earlier = executor.submit(first.append, self.event)
            self.assertTrue(first_entered.wait(3))
            later = executor.submit(second_append)
            earlier.result(timeout=5)
            later.result(timeout=5)
        self.assertEqual(self.counts(), (2, 2))
        self.assertEqual(anchor.committed, (2, self.chain.head_hash))

    def test_external_transaction_rolls_back_the_audit_and_other_mutation_together(self):
        with self.assertRaisesRegex(RuntimeError, "test-only rollback"):
            with self.chain.transaction() as connection:
                connection.execute("INSERT INTO job_ledger VALUES "
                                   "('job-outer','subject-test',%s,'RESERVED',100,100,100,200)",
                                   ("e" * 64,))
                self.chain.append(self.event, transaction=connection)
                self.assertEqual(connection.execute("SELECT count(*) FROM audit_chain").fetchone(),
                                 (1,))
                raise RuntimeError("test-only rollback")
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(self.owner("SELECT count(*) FROM job_ledger"), [(0,)])

    def test_external_transaction_appends_never_commit_before_the_caller(self):
        with self.chain.transaction() as connection:
            record = self.chain.append(self.event, transaction=connection)
            self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(self.chain.records, (record,))

    def test_external_transaction_requires_the_same_connection_and_active_transaction(self):
        with self.db.connect(runtime=True) as other:
            with self.assertRaisesRegex(ContractError, "own connection"):
                self.chain.append(self.event, transaction=other)
        with self.assertRaisesRegex(ContractError, "active external transaction"):
            self.chain.append(self.event, transaction=self.connection)

    def test_invalid_constructor_event_and_unavailable_database_are_refused(self):
        with self.assertRaisesRegex(ContractError, "psycopg connection"):
            PostgresAuditChain(object(), authority=self.authority)
        with self.assertRaisesRegex(ContractError, "AuditAuthority"):
            PostgresAuditChain(self.connection, authority=self.authority.verifier())
        with self.db.connect(runtime=True) as other:
            other.autocommit = False
            with self.assertRaisesRegex(ContractError, "autocommit"):
                PostgresAuditChain(other, authority=self.authority)
        with self.assertRaisesRegex(ContractError, "event is invalid"):
            self.chain.append(object())
        self.connection.close()
        for operation in (lambda: self.chain.records, lambda: self.chain.append(self.event)):
            with self.subTest(operation=operation):
                with self.assertRaises(ContractError):
                    operation()

    def test_driver_refusal_in_an_external_transaction_is_a_contract_refusal(self):
        self.owner("CREATE FUNCTION public.reject_external_head() RETURNS trigger "
                   "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test-only'; END; $$")
        self.owner("CREATE TRIGGER reject_external_head BEFORE INSERT ON audit_heads "
                   "FOR EACH ROW EXECUTE FUNCTION public.reject_external_head()")
        with self.assertRaisesRegex(ContractError, "append failed"):
            with self.chain.transaction() as connection:
                self.chain.append(self.event, transaction=connection)
        self.assertEqual(self.counts(), (0, 0))

    def test_ordinary_append_refuses_to_publish_a_record_inside_an_outer_transaction(self):
        with self.chain.transaction():
            with self.assertRaisesRegex(ContractError, "outside an existing transaction"):
                self.chain.append(self.event)
        self.assertEqual(self.counts(), (0, 0))
