"""Gate B5: byte-exact records and their already signed heads in PostgreSQL.

The independent anchor remains a separate store. Snapshot reconstruction uses
only the public verifier: recovery cannot bless an unsigned database suffix.
An append can join a caller's transaction for B6; that caller must commit before
submitting the returned head and records to the independent anchor.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from hashlib import sha256
import hmac
import json

import psycopg
from psycopg.pq import TransactionStatus

from .audit import AuditAuthority, AuditEvent, AuditVerifier, _audit_safe, rehydrate_event
from . import anchor_process
from .audit_chain import (AuditChain, AuditHead, AuditRecord, _EMPTY_HASH,
                          _MAX_COUNT, _record_hash, _verify_head, sign_head, verify)
from .contracts import ContractError, _subject_bytes, decode_wire
from .database import _SCOPE_KEYS, _MAX_WIRE_BYTES, connection_lock

_AUDIT_LOCK = 0x47454E4955534235
_MAX_EVENT_BYTES = 8192
_MAX_HEAD_VERSION_BYTES = 64
_TERMINAL_PENDING_REFUSALS = frozenset({
    "PENDING_APPROVAL_EXPIRED",
    "PENDING_DISPATCH_REFUSED",
})


def _fail(message: str) -> None:
    raise ContractError(message)


def _event(raw: bytes) -> AuditEvent:
    if raw is None:
        _fail("stored audit event exceeds the maximum size")
    if type(raw) is not bytes:
        _fail("stored audit event must be bytes")
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeError, ValueError, RecursionError):
        raise ContractError("stored audit event is not valid JSON") from None
    event = rehydrate_event(value)
    if raw != event.to_bytes():
        _fail("stored audit event is not byte-exact canonical JSON")
    return event


def _stored_snapshot(rows, head_rows, *, verifier: AuditVerifier):
    """Validate every record and every previously signed prefix head."""
    if len(rows) > _MAX_COUNT or len(head_rows) > _MAX_COUNT:
        _fail("stored audit chain exceeds the supported record bound")
    records = tuple(AuditRecord(index, _event(raw), previous, digest)
                    for index, previous, digest, raw in rows)
    if len(head_rows) != len(records):
        _fail("stored audit records and signed heads are not one-to-one")
    heads = []
    for position, (count, version, digest, signature, created_at) in enumerate(head_rows, 1):
        if type(count) is not int or count != position:
            _fail("stored audit heads are not contiguous")
        if version is None:
            _fail("stored audit head version exceeds the maximum size")
        if type(signature) is not bytes:
            _fail("stored audit head signature must be bytes")
        if created_at != records[position - 1].event.occurred_at:
            _fail("stored audit head timestamp does not match its record")
        head = _verify_head(AuditHead(version, count, digest, signature.hex()),
                            authority=verifier)
        heads.append(head)
    if not records:
        return None, ()
    head = heads[-1]
    verify(records, head, authority=verifier)
    for prefix_head, record in zip(heads, records):
        if not hmac.compare_digest(prefix_head.head_hash, record.record_hash):
            _fail("stored audit head does not bind its record prefix")
    return head, records


class PostgresAuditChain(AuditChain):
    """Read committed storage on every call; never fall back to process memory.

    PostgreSQL's transaction advisory lock serializes appenders across service
    instances, including an initially empty chain. The process lock also keeps
    this shared connection's request threads from entering another transaction.
    """

    def __init__(self, connection, *, authority: AuditAuthority) -> None:
        if not isinstance(connection, psycopg.Connection):
            _fail("audit chain needs a psycopg connection")
        if not connection.autocommit:
            _fail("audit chain connection must be in autocommit mode")
        if not isinstance(authority, AuditAuthority):
            _fail("audit chain needs an AuditAuthority")
        self._connection = connection
        self._authority = authority
        self._lock = connection_lock(connection)
        # Startup validation is complete before the composition root constructs
        # any runner or opens its listener. No signature is created here.
        self._snapshot()

    def _read(self, connection):
        rows = connection.execute(
            'SELECT index, previous_hash, record_hash, '
            'CASE WHEN octet_length(event) <= %s THEN event ELSE NULL END '
            'FROM public.audit_chain ORDER BY index LIMIT %s',
            (_MAX_EVENT_BYTES, _MAX_COUNT + 1,)).fetchall()
        heads = connection.execute(
            'SELECT count, '
            'CASE WHEN octet_length(version) <= %s THEN version ELSE NULL END, head_hash, '
            'CASE WHEN octet_length(signature) = 64 THEN signature ELSE NULL END, '
            'created_at '
            'FROM public.audit_heads ORDER BY count LIMIT %s',
            (_MAX_HEAD_VERSION_BYTES, _MAX_COUNT + 1,)).fetchall()
        return _stored_snapshot(rows, heads, verifier=self._authority.verifier())

    def _snapshot(self):
        try:
            with self._lock, self._connection.transaction():
                self._connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return self._read(self._connection)
        except psycopg.Error:
            raise ContractError("audit chain is unavailable") from None

    def check_core_bindings(self) -> None:
        """Compare committed admission decisions with burned job ids at startup."""
        try:
            with self._lock, self._connection.transaction():
                self._connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                _, records = self._read(self._connection)
                if self._connection.execute(
                        "SELECT count(*) FROM public.job_ledger").fetchone()[0] > len(records):
                    _fail("job ledger has no audit issuance")
                jobs = self._connection.execute(
                    "SELECT job_id,subject,handoff_sha256,state,reserved_at,updated_at "
                    "FROM public.job_ledger").fetchall()
                issued = defaultdict(list)
                for record in records:
                    event = record.event
                    if event.action == "HANDOFF_ISSUED":
                        issued[(event.job_id, event.handoff_sha256)].append(event)
                for job_id, subject, digest, _, _, _ in jobs:
                    matching = issued.get((_audit_safe(job_id, "job_id"), digest), ())
                    if not matching:
                        _fail("job ledger has no audit issuance")
                    expected_subject = sha256(_subject_bytes(subject)).hexdigest()
                    if any(event.event_version != 2
                           or event.api_subject_sha256 != expected_subject
                           for event in matching):
                        _fail("job ledger subject has no signed audit binding")
                    if len(matching) != 1:
                        _fail("job ledger has duplicate audit issuance")
                admitted = Counter(
                    (record.event.job_id, record.event.handoff_sha256,
                     record.event.occurred_at,
                     record.event.approval_record_hash)
                    for record in records if record.event.action == "HANDOFF_ADMITTED")
                expected = Counter()
                for job_id, subject, digest, state, reserved_at, _ in jobs:
                    if state in ("RESERVED", "EXECUTION_COMMITTED", "COMPLETED"):
                        expected[(_audit_safe(job_id, "job_id"), digest, reserved_at, None)] += 1
                if any(count != 1 for count in admitted.values()) or any(
                        count != 1 for count in expected.values()):
                    _fail("job ledger and audit admissions do not match")
                # Approval-bound admissions carry a receipt hash in the event.
                # The remaining fields still bind the exact reserved job.
                actual = Counter(key[:3] for key in admitted)
                wanted = Counter(key[:3] for key in expected)
                if actual != wanted:
                    _fail("job ledger and audit admissions do not match")
                expected_executions = Counter(
                    (_audit_safe(job_id, "job_id"), digest, reserved_at)
                    for job_id, subject, digest, state, reserved_at, _ in jobs
                    if state in ("EXECUTION_COMMITTED", "COMPLETED"))
                actual_executions = Counter(
                    (event.job_id, event.handoff_sha256, event.occurred_at)
                    for event in (record.event for record in records)
                    if event.action == "EXECUTION_DISPATCHED")
                if actual_executions != expected_executions:
                    _fail("job ledger and audit execution events do not match")
                expected_refusals = Counter(
                    (_audit_safe(job_id, "job_id"), digest, updated_at)
                    for job_id, subject, digest, state, _, updated_at in jobs
                    if state == "REFUSED")
                actual_refusals = Counter(
                    (event.job_id, event.handoff_sha256, event.occurred_at)
                    for event in (record.event for record in records)
                    if event.action == "HANDOFF_REJECTED"
                    and event.reason_code in _TERMINAL_PENDING_REFUSALS)
                if actual_refusals != expected_refusals:
                    _fail("refused jobs and terminal audit events do not match")
                approval_actions = {
                    "GRANTED": "APPROVAL_GRANTED",
                    "CONSUMED": "HANDOFF_ADMITTED",
                    "REVOKED": "APPROVAL_REVOKED",
                }
                if self._connection.execute(
                        "SELECT count(*) FROM public.approval_records").fetchone()[0] > len(records):
                    _fail("approval records and audit events do not match")
                approval_rows = self._connection.execute(
                    "SELECT record_hash,state,changed_at,"
                    "CASE WHEN octet_length(scope) <= %s THEN scope ELSE NULL END "
                    "FROM public.approval_records", (_MAX_WIRE_BYTES,)).fetchall()
                expected_approvals = Counter()
                scopes = {}
                for record_hash, state, changed_at, raw in approval_rows:
                    if raw is None:
                        _fail("stored approval scope exceeds the maximum size")
                    if state not in approval_actions:
                        _fail("approval record state is invalid")
                    expected_approvals[(approval_actions[state], record_hash, changed_at)] += 1
                    scopes[record_hash] = decode_wire(raw, keys=_SCOPE_KEYS,
                                                       noun="approval scope")
                approval_events = tuple(
                    record.event for record in records
                    if record.event.action in ("APPROVAL_GRANTED", "APPROVAL_REVOKED")
                    or (record.event.action == "HANDOFF_ADMITTED"
                        and record.event.approval_record_hash is not None))
                actual_approvals = Counter(
                    (event.action, event.approval_record_hash, event.occurred_at)
                    for event in approval_events)
                if actual_approvals != expected_approvals:
                    _fail("approval records and audit events do not match")
                for event in approval_events:
                    scope = scopes[event.approval_record_hash]
                    if (event.job_id != _audit_safe(scope["job_id"], "job_id")
                            or event.handoff_sha256 != scope["handoff_sha256"]
                            or event.subject != _audit_safe(scope["user_id"], "subject")
                            or event.policy_version != _audit_safe(
                                scope["policy_version"], "policy_version")):
                        _fail("approval receipt does not bind the audited job")
                if self._connection.execute(
                        "SELECT count(*) FROM public.acceptance_ledger").fetchone()[0] > len(records):
                    _fail("acceptance ledger and audit result events do not match")
                acceptances = self._connection.execute(
                    "SELECT job_id,handoff_sha256,result_sha256,accepted_at "
                    "FROM public.acceptance_ledger").fetchall()
                expected_results = Counter(
                    (_audit_safe(job_id, "job_id"), digest, result_digest, accepted_at)
                    for job_id, digest, result_digest, accepted_at in acceptances)
                actual_results = Counter(
                    (event.job_id, event.handoff_sha256, event.result_sha256,
                     event.occurred_at)
                    for event in (record.event for record in records)
                    if event.action == "RESULT_ACCEPTED")
                if actual_results != expected_results:
                    _fail("acceptance ledger and audit result events do not match")
        except psycopg.Error:
            raise ContractError("core audit reconciliation is unavailable") from None

    def __len__(self) -> int:
        return len(self._snapshot()[1])

    @property
    def head_hash(self) -> str:
        head, _ = self._snapshot()
        return head.head_hash if head is not None else _EMPTY_HASH

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        return self._snapshot()[1]

    def snapshot(self, authority: AuditAuthority) -> tuple[AuditHead, tuple[AuditRecord, ...]]:
        head, records = self._snapshot()
        # Only a genuinely empty database needs a new empty head. Existing
        # records always use the verified signature stored with their append.
        if head is None:
            head = sign_head(count=0, head_hash=_EMPTY_HASH, authority=authority)
        return head, records

    def head(self, authority: AuditAuthority) -> AuditHead:
        return self.snapshot(authority)[0]

    @contextmanager
    def transaction(self):
        """Hold the shared connection for a B6 caller's complete mutation.

        External coordinators must likewise hold connection_lock(connection)
        throughout their transaction; the driver transaction alone is not a
        thread boundary for a shared connection.
        """
        try:
            with self._lock:
                if self._connection.info.transaction_status != TransactionStatus.IDLE:
                    _fail("audit transaction must begin outside an existing transaction")
                with self._connection.transaction():
                    yield self._connection
        except psycopg.Error:
            raise ContractError("audit transaction is unavailable") from None

    @contextmanager
    def anchor_lock(self):
        """Keep a committed snapshot ordered through its independent anchor ack."""
        with self._lock:
            if self._connection.info.transaction_status != TransactionStatus.IDLE:
                _fail("anchor lock must begin outside an existing transaction")
            acquired = False
            try:
                self._connection.execute("SELECT pg_advisory_lock(%s)", (_AUDIT_LOCK,))
                acquired = True
                yield
            except psycopg.Error:
                raise ContractError("audit anchor lock is unavailable") from None
            finally:
                if acquired:
                    try:
                        released = self._connection.execute(
                            "SELECT pg_advisory_unlock(%s)", (_AUDIT_LOCK,)).fetchone()
                    except psycopg.Error:
                        raise ContractError("audit anchor lock could not be released") from None
                    if released != (True,):
                        _fail("audit anchor lock was not held")

    def append(self, event: AuditEvent, *, transaction=None) -> AuditRecord:
        if not isinstance(event, AuditEvent):
            _fail("event is invalid")
        if transaction is not None:
            if transaction is not self._connection:
                _fail("audit append transaction must use its own connection")
            try:
                with self._lock:
                    if transaction.info.transaction_status != TransactionStatus.INTRANS:
                        _fail("audit append requires an active external transaction")
                    return self._append(transaction, event)
            except psycopg.Error:
                raise ContractError("audit chain append failed") from None
        try:
            with self.transaction() as connection:
                return self._append(connection, event)
        except psycopg.Error:
            raise ContractError("audit chain append failed") from None

    def _append(self, connection, event: AuditEvent) -> AuditRecord:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (_AUDIT_LOCK,))
        _, records = self._read(connection)
        index = len(records)
        previous_hash = records[-1].record_hash if records else _EMPTY_HASH
        record = AuditRecord(index, event, previous_hash,
                             _record_hash(index=index, event=event,
                                          previous_hash=previous_hash))
        head = sign_head(count=index + 1, head_hash=record.record_hash,
                         authority=self._authority)
        # A stored suffix must never outgrow the full-chain anchor protocol:
        # otherwise every restart would fail after this transaction commits.
        anchor_process._check_commit_size(head, records + (record,))
        connection.execute(
            'INSERT INTO public.audit_chain (index, previous_hash, record_hash, event) '
            'VALUES (%s, %s, %s, %s)',
            (index, previous_hash, record.record_hash, event.to_bytes()))
        connection.execute(
            'INSERT INTO public.audit_heads (count, version, head_hash, signature, created_at) '
            'VALUES (%s, %s, %s, %s, %s)',
            (head.count, head.version, head.head_hash, bytes.fromhex(head.signature),
             event.occurred_at))
        return record
