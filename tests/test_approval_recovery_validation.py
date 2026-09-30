"""Stored approval history stays invalid even when its digest is recomputed."""

from hashlib import sha256
from types import SimpleNamespace
import unittest

from geniusnew.approvals import _record_hash
from geniusnew.contracts import ContractError, canonical
from geniusnew.database import _approval_record, _check_approvals
from test_approvals import ApprovalFixture


class _StoredRows:
    def __init__(self, rows, pointer):
        self.rows = rows
        self.pointer = pointer

    def execute(self, query):
        if "FROM public.approval_records" in query:
            return iter(self.rows)
        return SimpleNamespace(fetchall=lambda: [self.pointer])


class ApprovalRecoveryValidationTest(ApprovalFixture, unittest.TestCase):
    def row(self, *, state="GRANTED", previous=None, changed=101,
            issued=101, expires=160):
        scope = self.scope()
        digest = sha256(b"approval-recovery-test-token" * 2).hexdigest()
        record_hash = _record_hash(
            token_digest=bytes.fromhex(digest), scope=scope,
            issued_at=issued, expires_at=expires, state=state,
            changed_at=changed, previous_hash=previous)
        return (digest, canonical(scope.to_dict()), issued, expires, state,
                changed, previous, record_hash)

    def check(self, rows, pointer):
        _check_approvals(_StoredRows(rows, (rows[0][0], pointer)))

    def test_recomputed_hash_does_not_make_an_invalid_time_valid(self):
        with self.assertRaisesRegex(ContractError, "approval record is invalid"):
            _approval_record(self.row(expires=101))

    def test_recomputed_hash_cannot_consume_at_token_expiry(self):
        with self.assertRaisesRegex(ContractError, "approval record is invalid"):
            _approval_record(self.row(state="CONSUMED", previous="f" * 64,
                                      changed=160, expires=160))

    def test_a_terminal_state_cannot_be_the_history_root(self):
        terminal = self.row(state="CONSUMED")
        with self.assertRaisesRegex(ContractError, "one granted root"):
            self.check([terminal], terminal[7])

    def test_a_successor_must_name_its_actual_predecessor(self):
        root = self.row()
        successor = self.row(state="CONSUMED", previous="f" * 64, changed=102)
        with self.assertRaisesRegex(ContractError, "one-way chain"):
            self.check([root, successor], successor[7])
