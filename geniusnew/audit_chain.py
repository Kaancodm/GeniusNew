"""An append-only audit chain, and an anchor for the one thing a chain cannot do.

Hash-linking each record to its predecessor detects modification and insertion:
change a record and its hash stops matching, splice one in and the link breaks.

It does **not** detect truncation. Drop the last records of a hash chain and what
remains is still a perfectly valid chain — every link intact, every hash correct,
simply shorter. A `verify()` that only walks the links would call a censored log
sound. `docs/ROADMAP-V01.md` step 8 requires deletion of the terminal record to
be caught, so the chain alone is not enough.

Two things close that gap. Every record carries its position, so a gap is
visible; and the head — the record count together with the final hash — is
signed, so shortening the log means producing a head that says so.

## What this protects against, and what it does not

An attacker who can edit the log file cannot shorten it undetectably: the stored
head still names a count and a final hash that the shortened log cannot produce,
and forging a replacement head needs the signing key.

An attacker who **also holds the signing key** can rewrite the log and sign a
matching head. Nothing here prevents that. The protection is exactly as strong
as the separation between the log's storage and the key, which in this system
means a file on disk and a server-side runtime configuration. That is a real
boundary, not a proof, and it is worth stating plainly rather than implying the
chain is tamper-proof.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from typing import Any, Iterable, Sequence

from .audit import AuditEvent
from .contracts import ContractError, canonical

_HEAD_VERSION = "geniusnew-audit-head-v1"
_EMPTY_HASH = "0" * 64


def _fail(message: str) -> None:
    raise ContractError(message)


def _integrity_key(value: Any) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        _fail("integrity_key must be at least 32 bytes")
    return value


def _record_hash(*, index: int, event: AuditEvent, previous_hash: str) -> str:
    return hashlib.sha256(canonical({
        "event": event.to_dict(),
        "index": index,
        "previous_hash": previous_hash,
    })).hexdigest()


@dataclass(frozen=True)
class AuditRecord:
    """One event, fixed at a position and linked to the record before it."""

    index: int
    event: AuditEvent
    previous_hash: str
    record_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "index": self.index,
            "previous_hash": self.previous_hash,
            "record_hash": self.record_hash,
        }


@dataclass(frozen=True)
class AuditHead:
    """The signed claim about how long the chain is and where it ends."""

    version: str
    count: int
    head_hash: str
    signature: str

    def body(self) -> dict[str, Any]:
        return {"count": self.count, "head_hash": self.head_hash, "version": self.version}


def sign_head(*, count: int, head_hash: str, integrity_key: bytes) -> AuditHead:
    """Commit to a chain length and ending hash, outside the chain itself."""
    integrity_key = _integrity_key(integrity_key)
    if type(count) is not int or count < 0:
        _fail("count must be a non-negative integer")
    if type(head_hash) is not str or len(head_hash) != 64:
        _fail("head_hash must be a SHA-256 digest")
    body = {"count": count, "head_hash": head_hash, "version": _HEAD_VERSION}
    signature = hmac.new(integrity_key, canonical(body), hashlib.sha256).hexdigest()
    return AuditHead(_HEAD_VERSION, count, head_hash, signature)


class AuditChain:
    """Append-only in memory. Durable storage is a separate, later decision."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    @property
    def head_hash(self) -> str:
        return self._records[-1].record_hash if self._records else _EMPTY_HASH

    def append(self, event: AuditEvent) -> AuditRecord:
        if not isinstance(event, AuditEvent):
            _fail("event is invalid")
        index = len(self._records)
        previous_hash = self.head_hash
        record = AuditRecord(index, event, previous_hash,
                             _record_hash(index=index, event=event, previous_hash=previous_hash))
        self._records.append(record)
        return record

    def head(self, integrity_key: bytes) -> AuditHead:
        return sign_head(count=len(self._records), head_hash=self.head_hash,
                         integrity_key=integrity_key)


def verify(records: Iterable[AuditRecord], head: AuditHead, *, integrity_key: bytes) -> int:
    """Check the chain against its signed head. Returns the record count.

    Raises `ContractError` on the first problem found, so a caller that treats
    any refusal as "do not trust this log" needs no further interpretation.
    """
    integrity_key = _integrity_key(integrity_key)
    if not isinstance(head, AuditHead):
        _fail("head is invalid")
    if head.version != _HEAD_VERSION:
        _fail("head version is not recognised")

    expected = hmac.new(integrity_key, canonical(head.body()), hashlib.sha256).hexdigest()
    if type(head.signature) is not str or not hmac.compare_digest(head.signature, expected):
        _fail("head signature does not verify")

    chain: Sequence[AuditRecord] = tuple(records)
    for position, record in enumerate(chain):
        if not isinstance(record, AuditRecord):
            _fail("record is invalid")
        if record.index != position:
            _fail(f"record at position {position} claims index {record.index}")
        expected_previous = chain[position - 1].record_hash if position else _EMPTY_HASH
        if not hmac.compare_digest(record.previous_hash, expected_previous):
            _fail(f"record {position} does not link to its predecessor")
        recomputed = _record_hash(index=record.index, event=record.event,
                                  previous_hash=record.previous_hash)
        if not hmac.compare_digest(record.record_hash, recomputed):
            _fail(f"record {position} has been altered")

    if len(chain) != head.count:
        _fail(f"chain holds {len(chain)} records but the signed head claims {head.count}")
    actual_head = chain[-1].record_hash if chain else _EMPTY_HASH
    if not hmac.compare_digest(actual_head, head.head_hash):
        _fail("chain does not end where the signed head says it does")
    return len(chain)
