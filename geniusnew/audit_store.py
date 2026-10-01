"""Gate B5: byte-exact records and their already signed heads in PostgreSQL.

The independent anchor remains a separate store. Snapshot reconstruction uses
only the public verifier: recovery cannot bless an unsigned database suffix.
An append can join a caller's transaction for B6; that caller must commit before
submitting the returned head and records to the independent anchor.
"""

from __future__ import annotations

from contextlib import contextmanager
import hmac
import json

import psycopg
from psycopg.pq import TransactionStatus

from .audit import AuditAuthority, AuditEvent, AuditVerifier, rehydrate_event
from . import anchor_process
from .audit_chain import (AuditChain, AuditHead, AuditRecord, _EMPTY_HASH,
                          _HEAD_VERSION, _MAX_COUNT, _record_hash, _verify_head,
                          sign_head, verify)
from .contracts import ContractError
from .database import connection_lock

_AUDIT_LOCK = 0x47454E4955534235
_MAX_EVENT_BYTES = 8192
_MAX_HEAD_VERSION_BYTES = 64
_MIN_HEAD_LINE_BYTES = len(anchor_process._head_line(
    AuditHead(_HEAD_VERSION, 0, "0" * 64, "0" * 128)))
_MAX_STORED_RECORDS = min(
    _MAX_COUNT, anchor_process._MAX_STATE_BYTES // _MIN_HEAD_LINE_BYTES)


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
        record_count, event_bytes, head_count = connection.execute(
            'SELECT (SELECT count(*) FROM public.audit_chain), '
            '(SELECT COALESCE(sum(octet_length(event)), 0) FROM public.audit_chain), '
            '(SELECT count(*) FROM public.audit_heads)').fetchone()
        if record_count > _MAX_STORED_RECORDS or head_count > _MAX_STORED_RECORDS:
            _fail("stored audit chain exceeds the supported record bound")
        if record_count != head_count:
            _fail("stored audit records and signed heads are not one-to-one")
        if event_bytes > anchor_process._MAX_REQUEST_BYTES:
            _fail("stored audit chain exceeds the supported byte bound")
        rows = connection.execute(
            'SELECT index, previous_hash, record_hash, '
            'CASE WHEN octet_length(event) <= %s THEN event ELSE NULL END '
            'FROM public.audit_chain ORDER BY index LIMIT %s',
            (_MAX_EVENT_BYTES, _MAX_STORED_RECORDS + 1,)).fetchall()
        heads = connection.execute(
            'SELECT count, '
            'CASE WHEN octet_length(version) <= %s THEN version ELSE NULL END, head_hash, '
            'CASE WHEN octet_length(signature) = 64 THEN signature ELSE NULL END, '
            'created_at '
            'FROM public.audit_heads ORDER BY count LIMIT %s',
            (_MAX_HEAD_VERSION_BYTES, _MAX_STORED_RECORDS + 1,)).fetchall()
        return _stored_snapshot(rows, heads, verifier=self._authority.verifier())

    def _snapshot(self):
        try:
            with self._lock, self._connection.transaction():
                self._connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                return self._read(self._connection)
        except psycopg.Error:
            raise ContractError("audit chain is unavailable") from None

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
