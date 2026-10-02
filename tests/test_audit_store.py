"""Pure snapshot validation catches corruption even when the final signature is valid."""

from contextlib import nullcontext
from dataclasses import replace
from hashlib import sha256
import json
import threading
import unittest
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


class CoreAuditBindingsRefusalTest(unittest.TestCase):
    class _Cursor:
        def __init__(self, *, one=None, many=()):
            self._one = one
            self._many = tuple(many)

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._many

    class _Connection:
        def __init__(self, *, job_count=0, jobs=(),
                     approval_count=0, approval_rows=(),
                     acceptance_count=0, acceptance_rows=()):
            self.job_count = job_count
            self.jobs = tuple(jobs)
            self.approval_count = approval_count
            self.approval_rows = tuple(approval_rows)
            self.acceptance_count = acceptance_count
            self.acceptance_rows = tuple(acceptance_rows)

        def transaction(self):
            return nullcontext()

        def execute(self, query, parameters=None):
            if query.startswith("SET TRANSACTION ISOLATION LEVEL"):
                return CoreAuditBindingsRefusalTest._Cursor()
            if "SELECT count(*) FROM public.job_ledger" in query:
                return CoreAuditBindingsRefusalTest._Cursor(one=(self.job_count,))
            if "FROM public.job_ledger" in query:
                return CoreAuditBindingsRefusalTest._Cursor(many=self.jobs)
            if "SELECT count(*) FROM public.approval_records" in query:
                return CoreAuditBindingsRefusalTest._Cursor(one=(self.approval_count,))
            if "FROM public.approval_records" in query:
                return CoreAuditBindingsRefusalTest._Cursor(many=self.approval_rows)
            if "SELECT count(*) FROM public.acceptance_ledger" in query:
                return CoreAuditBindingsRefusalTest._Cursor(one=(self.acceptance_count,))
            if "FROM public.acceptance_ledger" in query:
                return CoreAuditBindingsRefusalTest._Cursor(many=self.acceptance_rows)
            raise AssertionError(f"unexpected query: {query}")

    def setUp(self):
        self.authority = AuditAuthority(audit_key=b"test-only-audit-key" * 3)
        subject = "subject-demo"
        self.job_id = "job-demo"
        self.digest = "a" * 64
        self.when = 100
        self.subject_digest = sha256(subject.encode("utf-8")).hexdigest()
        self.base_job = (self.job_id, subject, self.digest, "PENDING_APPROVAL", self.when, self.when)

    def _event(self, action, *, job_id=None, occurred_at=None, approval_record_hash=None):
        return AuditEvent(
            trace_id=f"trace-{action.lower()}",
            job_id=self.job_id if job_id is None else job_id,
            actor=self.authority.actor("gateway", "gateway-1"),
            subject="subject-demo",
            action=action,
            decision="ALLOWED",
            reason_code="TEST_ONLY",
            policy_version="p",
            constitution_version="c",
            handoff_sha256=self.digest,
            payload_sha256="b" * 64,
            occurred_at=self.when if occurred_at is None else occurred_at,
            approval_record_hash=approval_record_hash,
            event_version=2,
            api_subject_sha256=self.subject_digest,
        )

    def _chain(self, connection, records):
        chain = object.__new__(PostgresAuditChain)
        chain._lock = threading.Lock()
        chain._connection = connection
        chain._read = lambda _: (None, tuple(records))
        return chain

    def test_refuses_when_job_count_exceeds_audited_records(self):
        chain = self._chain(self._Connection(job_count=1), records=())
        with self.assertRaisesRegex(ContractError, "job ledger has no audit issuance"):
            chain.check_core_bindings()

    def test_refuses_job_without_issued_audit_event(self):
        unrelated_issuance = self._event("HANDOFF_ISSUED", job_id="job-other")
        chain = self._chain(
            self._Connection(job_count=1, jobs=(self.base_job,)),
            records=(type("R", (), {"event": unrelated_issuance})(),),
        )
        with self.assertRaisesRegex(ContractError, "job ledger has no audit issuance"):
            chain.check_core_bindings()

    def test_refuses_duplicate_admission_bindings_even_when_totals_match(self):
        issued = self._event("HANDOFF_ISSUED")
        admitted = self._event("HANDOFF_ADMITTED")
        duplicated_job = (self.job_id, "subject-demo", self.digest, "RESERVED", self.when, self.when)
        chain = self._chain(
            self._Connection(job_count=2, jobs=(duplicated_job, duplicated_job)),
            records=(type("R", (), {"event": issued})(),
                     type("R", (), {"event": admitted})(),
                     type("R", (), {"event": admitted})()),
        )
        with self.assertRaisesRegex(ContractError, "job ledger and audit admissions do not match"):
            chain.check_core_bindings()

    def test_refuses_when_approval_count_exceeds_audited_events(self):
        chain = self._chain(
            self._Connection(job_count=0, approval_count=1),
            records=(),
        )
        with self.assertRaisesRegex(ContractError, "approval records and audit events do not match"):
            chain.check_core_bindings()

    def test_refuses_invalid_approval_state_before_mapping(self):
        issued = self._event("HANDOFF_ISSUED")
        chain = self._chain(
            self._Connection(
                approval_count=1,
                approval_rows=(("c" * 64, "INVALID", self.when, b"{\"handoff_sha256\":\"a\"}"),),
            ),
            records=(type("R", (), {"event": issued})(),),
        )
        with self.assertRaisesRegex(ContractError, "approval record state is invalid"):
            chain.check_core_bindings()

    def test_refuses_when_acceptance_count_exceeds_audited_results(self):
        chain = self._chain(
            self._Connection(job_count=0, acceptance_count=1),
            records=(),
        )
        with self.assertRaisesRegex(ContractError, "acceptance ledger and audit result events do not match"):
            chain.check_core_bindings()
