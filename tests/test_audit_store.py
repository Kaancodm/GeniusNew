"""Pure snapshot validation catches corruption even when the final signature is valid."""

from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from geniusnew.audit import AuditAuthority, AuditEvent
from geniusnew.audit_chain import AuditChain, _MAX_COUNT, sign_head
from geniusnew.audit_store import _event, _stored_snapshot
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
