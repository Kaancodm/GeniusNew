"""Pure snapshot validation catches corruption even when the final signature is valid."""

from dataclasses import replace
import hashlib
import json
import threading
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from geniusnew import anchor_process
from geniusnew.audit import AuditAuthority, AuditEvent
from geniusnew.audit_chain import AuditChain, _MAX_COUNT, sign_head
from geniusnew.audit_store import PostgresAuditChain, _event, _stored_snapshot
from geniusnew.contracts import ContractError


class StoredAuditSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.authority = AuditAuthority(audit_key=b"test-only-audit-key" * 3)
        self.chain = AuditChain()
        self.event = AuditEvent(
            trace_id="trace-test", job_id="job-test",
            actor=self.authority.actor("gateway", "gateway-1"), subject="subject-test",
            action="HANDOFF_ADMITTED", decision="ALLOWED", reason_code="ADMITTED",
            policy_version="p", constitution_version="c", handoff_sha256="a" * 64,
            payload_sha256="b" * 64, occurred_at=100)
        self.rows = []
        self.heads = []
        for _ in range(3):
            record = self.chain.append(self.event)
            head = self.chain.head(self.authority)
            self.rows.append((record.index, record.previous_hash, record.record_hash,
                              record.event.to_bytes()))
            self.heads.append((head.count, head.version, head.head_hash,
                               bytes.fromhex(head.signature), self.event.occurred_at))

    def load(self, rows=None, heads=None):
        return _stored_snapshot(self.rows if rows is None else rows,
                                self.heads if heads is None else heads,
                                verifier=self.authority.verifier())

    def test_load_preserves_the_highest_stored_signature_without_signing(self):
        with patch.object(self.authority, "sign", side_effect=AssertionError("re-signed")):
            head, records = self.load()
        self.assertEqual(head.signature, self.heads[-1][3].hex())
        self.assertEqual(records, self.chain.records)
        self.assertEqual(self.load(rows=[], heads=[]), (None, ()))

    def test_unsigned_sql_suffix_or_orphan_head_is_refused(self):
        for rows, heads in ((self.rows, self.heads[:-1]), (self.rows[:-1], self.heads)):
            with self.subTest(records=len(rows), heads=len(heads)):
                with self.assertRaisesRegex(ContractError, "one-to-one"):
                    self.load(rows=rows, heads=heads)

    def test_each_stored_head_is_verified_even_with_a_valid_highest_head(self):
        for offset, replacement, reason in (
                (0, 4, "contiguous"), (0, True, "contiguous"),
                (1, "unknown-head-version", "version"),
                (3, b"x" * 64, "signature"), (3, "x", "must be bytes"),
                (3, b"x", "signature"),
                (4, 0, "timestamp"), (4, True, "timestamp"),
                (4, 4102444801, "timestamp")):
            heads = list(self.heads)
            changed = list(heads[0])
            changed[offset] = replacement
            heads[0] = tuple(changed)
            with self.subTest(offset=offset, replacement=replacement):
                with self.assertRaisesRegex(ContractError, reason):
                    self.load(heads=heads)

    def test_an_earlier_head_must_bind_its_exact_prefix(self):
        other = sign_head(count=1, head_hash="f" * 64, authority=self.authority)
        heads = list(self.heads)
        heads[0] = (other.count, other.version, other.head_hash,
                    bytes.fromhex(other.signature), 100)
        with self.assertRaisesRegex(ContractError, "record prefix"):
            self.load(heads=heads)

    def test_changed_bytes_even_with_equivalent_json_are_refused(self):
        raw = self.event.to_bytes()
        variants = [b" " + raw, json.dumps(self.event.to_dict(), indent=2).encode(),
                    raw.replace(b'"action":', b'"action":"HANDOFF_ADMITTED","action":')]
        for altered in variants:
            with self.subTest(altered=altered[:20]):
                with self.assertRaisesRegex(ContractError, "byte-exact"):
                    _event(altered)

    def test_oversized_stored_event_refusal_keeps_its_specific_reason(self):
        with self.assertRaisesRegex(ContractError, "event exceeds the maximum size"):
            _event(None)

    def test_invalid_storage_bytes_or_event_are_contract_refusals(self):
        for raw in (None, "{}", b"\xff", b"{", b"[]", b"{}", b"[" * 1500):
            with self.subTest(raw_type=type(raw).__name__):
                with self.assertRaises(ContractError):
                    _event(raw)

    def test_record_hash_links_indices_and_event_are_reverified(self):
        for offset, replacement in ((0, -1), (0, True), (0, 7),
                                    (1, "c" * 64), (1, 2),
                                    (2, "d" * 64), (2, 3),
                                    (3, replace(self.event, reason_code="ALTERED").to_bytes())):
            rows = list(self.rows)
            changed = list(rows[1])
            changed[offset] = replacement
            rows[1] = tuple(changed)
            with self.subTest(offset=offset, replacement=replacement):
                with self.assertRaises(ContractError):
                    self.load(rows=rows)

    def test_the_supported_count_bounds_materialization(self):
        class Oversized:
            def __len__(self):
                return _MAX_COUNT + 1

            def __iter__(self):
                raise AssertionError("unbounded storage materialized")

        for rows, heads in ((Oversized(), []), ([], Oversized())):
            with self.subTest(rows=type(rows).__name__):
                with self.assertRaisesRegex(ContractError, "record bound"):
                    self.load(rows=rows, heads=heads)

    def test_postgres_preflight_refuses_resource_bounds_before_bulk_fetch(self):
        class Cursor:
            def __init__(self, stats=None):
                self.stats = stats

            def fetchone(self):
                return self.stats

            def fetchall(self):
                raise AssertionError("bulk audit rows were fetched before preflight")

        class Connection:
            def __init__(self, stats):
                self.stats = stats

            def execute(self, query, parameters=None):
                if "count(*)" in query:
                    return Cursor(self.stats)
                return Cursor()

        chain = object.__new__(PostgresAuditChain)
        chain._authority = self.authority
        cases = (
            ((_MAX_COUNT, 0, 0, _MAX_COUNT), "record bound"),
            ((1, anchor_process._MAX_REQUEST_BYTES + 1, 1, 1), "byte bound"),
            ((1, 8193, 8193, 0), "event exceeds the maximum size"),
            ((1, 1, 1, 0), "one-to-one"),
        )
        for stats, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ContractError, reason):
                    chain._read(Connection(stats))


class CoreBindingPreflightTest(unittest.TestCase):
    class Cursor:
        def __init__(self, one=None, all_rows=None):
            self._one = one
            self._all = all_rows

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._all

    class Connection:
        def __init__(self, responses):
            self._responses = {key: list(value) for key, value in responses.items()}

        @contextmanager
        def transaction(self):
            yield

        def execute(self, query, parameters=None):
            if "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in query:
                return CoreBindingPreflightTest.Cursor()
            for marker, prepared in self._responses.items():
                if marker in query:
                    if not prepared:
                        raise AssertionError(f"unexpected additional query: {query}")
                    one, all_rows = prepared.pop(0)
                    return CoreBindingPreflightTest.Cursor(one=one, all_rows=all_rows)
            raise AssertionError(f"unexpected query: {query}")

    @staticmethod
    def _record(*, action, job_id="job-test", digest="a" * 64, occurred_at=100,
                approval_record_hash=None, subject_hash="subject-hash",
                result_sha256="b" * 64):
        return SimpleNamespace(event=SimpleNamespace(
            action=action,
            job_id=job_id,
            handoff_sha256=digest,
            occurred_at=occurred_at,
            approval_record_hash=approval_record_hash,
            event_version=2,
            api_subject_sha256=subject_hash,
            result_sha256=result_sha256,
            subject="subject-hash",
            policy_version="policy-version",
            reason_code="POLICY_SATISFIED",
        ))

    def _chain(self, connection, records):
        chain = object.__new__(PostgresAuditChain)
        chain._lock = threading.Lock()
        chain._connection = connection
        chain._read = lambda _connection: (None, records)
        return chain

    def test_job_count_must_not_exceed_loaded_records(self):
        connection = self.Connection({
            "SELECT count(*) FROM public.job_ledger": [((1,), None)],
            "SELECT job_id,subject,handoff_sha256,state,reserved_at,updated_at":
                [(
                    None,
                    [("job-other", "subject-demo", "c" * 64, "PENDING_APPROVAL", None, 101)],
                )],
            "SELECT count(*) FROM public.approval_records": [((0,), None)],
            "SELECT record_hash,state,changed_at":
                [(None, [])],
            "SELECT count(*) FROM public.acceptance_ledger": [((0,), None)],
            "SELECT job_id,handoff_sha256,result_sha256,accepted_at":
                [(None, [])],
        })
        with self.assertRaisesRegex(ContractError, "job ledger has no audit issuance"):
            self._chain(connection, ()).check_core_bindings()

    def test_each_job_requires_an_issued_audit_event(self):
        records = (self._record(action="HANDOFF_ISSUED", job_id="job-other", digest="d" * 64),)
        connection = self.Connection({
            "SELECT count(*) FROM public.job_ledger": [((1,), None)],
            "SELECT job_id,subject,handoff_sha256,state,reserved_at,updated_at":
                [(
                    None,
                    [("job-missing", "subject-demo", "c" * 64, "PENDING_APPROVAL", None, 101)],
                )],
        })
        with self.assertRaisesRegex(ContractError, "job ledger has no audit issuance"):
            self._chain(connection, records).check_core_bindings()

    def test_duplicate_admissions_are_refused_even_when_counts_match_by_key(self):
        hashed_subject = hashlib.sha256(b"subject-demo").hexdigest()
        records = (
            self._record(action="HANDOFF_ISSUED", subject_hash=hashed_subject),
            self._record(action="HANDOFF_ADMITTED", subject_hash=hashed_subject),
            self._record(action="HANDOFF_ADMITTED", subject_hash=hashed_subject),
        )
        connection = self.Connection({
            "SELECT count(*) FROM public.job_ledger": [((2,), None)],
            "SELECT job_id,subject,handoff_sha256,state,reserved_at,updated_at":
                [(
                    None,
                    [("job-test", "subject-demo", "a" * 64, "RESERVED", 100, 100),
                     ("job-test", "subject-demo", "a" * 64, "RESERVED", 100, 100)],
                )],
        })
        with self.assertRaisesRegex(ContractError, "job ledger and audit admissions do not match"):
            self._chain(connection, records).check_core_bindings()

    def test_approval_count_preflight_rejects_impossible_snapshot(self):
        connection = self.Connection({
            "SELECT count(*) FROM public.job_ledger": [((0,), None)],
            "SELECT job_id,subject,handoff_sha256,state,reserved_at,updated_at":
                [(None, [])],
            "SELECT count(*) FROM public.approval_records": [((1,), None)],
            "SELECT record_hash,state,changed_at":
                [(None, [])],
            "SELECT count(*) FROM public.acceptance_ledger": [((0,), None)],
            "SELECT job_id,handoff_sha256,result_sha256,accepted_at":
                [(None, [])],
        })
        with self.assertRaisesRegex(ContractError, "approval records and audit events do not match"):
            self._chain(connection, ()).check_core_bindings()

    def test_acceptance_count_preflight_rejects_impossible_snapshot(self):
        connection = self.Connection({
            "SELECT count(*) FROM public.job_ledger": [((0,), None)],
            "SELECT job_id,subject,handoff_sha256,state,reserved_at,updated_at":
                [(None, [])],
            "SELECT count(*) FROM public.approval_records": [((0,), None)],
            "SELECT record_hash,state,changed_at":
                [(None, [])],
            "SELECT count(*) FROM public.acceptance_ledger": [((1,), None)],
            "SELECT job_id,handoff_sha256,result_sha256,accepted_at":
                [(None, [])],
        })
        with self.assertRaisesRegex(ContractError, "acceptance ledger and audit result events do not match"):
            self._chain(connection, ()).check_core_bindings()
