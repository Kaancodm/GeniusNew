"""An append-only audit chain, and an anchor for the one thing a chain cannot do.

Hash-linking each record to its predecessor detects modification and insertion:
change a record and its hash stops matching, splice one in and the link breaks.

It does **not** detect truncation. Drop the last records of a hash chain and what
remains is still a perfectly valid chain — every link intact, every hash correct,
simply shorter. `docs/ROADMAP-V01.md` step 8 requires deletion of the terminal
record to be caught, so the chain alone is not enough.

Three things close that gap, each covering the hole the one before it leaves.

**Position.** Every record carries its index, so a gap in the middle is visible.

**A signed head.** The record count together with the final hash is signed, so
shortening the log means producing a head that admits it. Forging a replacement
head needs the audit key.

**An external anchor.** The signed head alone only stops an attacker who cannot
sign. Whoever holds the audit key can shorten the log *and* sign a matching head,
and nothing inside the process can tell. `AuditAnchor` is the boundary where that
stops: a commitment to a chain, kept outside the trust domain of whatever writes
the log. A shortened chain cannot satisfy an anchor that already saw a longer one,
however freshly its head was signed.

The commitment is to a *chain*, not to a length. A counter that only moves
forward is satisfied by any longer chain, including one that shares no history
with what was committed — so an attacker holding the audit key could sign a
fabricated chain one record longer and take over the anchor, locking the real log
out for being too short. Both `AuditAnchor.commit` and `verify` therefore require
the records themselves and check that the record at the committed position still
hashes to the committed hash. Extension is proved, not inferred from a number.

## Separation of authority

The audit key is **not** the handoff integrity key. `CONSTITUTION-V1-DRAFT.md`
section 8 keeps Audit/Forensics logically separate from orchestration, policy and
execution; sharing one key would mean every component that issues or validates a
handoff could also forge the record of its own decisions. `AuditAuthority` holds
the audit key and is the only thing that can sign a head or mint an actor.

Heads are signed with Ed25519. Checking one needs only an `AuditVerifier`, the
public half, so the anchor, a verifier and a forensic reader can check heads
without being able to make one. With the HMAC of head version 1, anything that
could verify a head could also sign it; a head of that version is refused now.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
import hashlib
import hmac
import re
import sys
from threading import Lock
from typing import Any, Iterable

from .audit import AuditAuthority, AuditEvent, AuditVerifier
from .contracts import ContractError, canonical

_HEAD_VERSION = "geniusnew-audit-head-v2"
_EMPTY_HASH = "0" * 64
_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")
_SIGNATURE = re.compile(r"\A[0-9a-f]{128}\Z")

# A record count is what bounds how much of an untrusted iterable is read, so it
# needs a ceiling of its own. Without one, a head signed for 2**64 records passes
# verification and then overflows `islice` — a ValueError escaping the
# ContractError boundary.
#
# The first version of this bound was 2**32, which fixed the overflow and missed
# the point: a legitimately signed head could still licence materializing four
# billion records from a hostile iterable, and on a 32-bit build 2**32 is itself
# past `sys.maxsize` and reinstates the ValueError. The number has to be what an
# in-memory chain could actually hold, not what an integer can express. A
# million records at roughly a kilobyte each is already the outer edge of
# "process-local"; `docs/ROADMAP-V01.md` leaves durable storage to a later phase,
# and raising this belongs to that phase rather than to this module.
_MAX_COUNT = min(1_000_000, sys.maxsize - 1)


def _fail(message: str) -> None:
    raise ContractError(message)


def _authority(value: Any) -> AuditAuthority:
    if not isinstance(value, AuditAuthority):
        _fail("authority must be an AuditAuthority")
    return value


def _verifier(value: Any) -> AuditVerifier:
    """What checking a head needs: the public half, or an authority's own."""
    if isinstance(value, AuditAuthority):
        return value.verifier()
    if not isinstance(value, AuditVerifier):
        _fail("authority must be an AuditAuthority or an AuditVerifier")
    return value


def _digest(value: Any, field: str) -> str:
    if type(value) is not str or not _DIGEST.match(value):
        _fail(f"{field} must be a lowercase SHA-256 digest")
    return value


def _count(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        _fail(f"{field} must be a non-negative integer")
    if value > _MAX_COUNT:
        _fail(f"{field} must be at most {_MAX_COUNT}")
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


def sign_head(*, count: int, head_hash: str, authority: AuditAuthority) -> AuditHead:
    """Commit to a chain length and ending hash, outside the chain itself."""
    signer = _authority(authority)
    body = {"count": _count(count, "count"),
            "head_hash": _digest(head_hash, "head_hash"),
            "version": _HEAD_VERSION}
    return AuditHead(_HEAD_VERSION, body["count"], body["head_hash"],
                     signer.sign(canonical(body)).hex())


def _bounded_chain(records: Any, limit: int) -> tuple[Any, ...]:
    """Read at most `limit` records, refusing anything that is not iterable.

    The count comes from an authenticated and bounded head, so this bounds how
    much of an untrusted iterable is materialized. Passing a non-iterable
    straight to `islice` would raise `TypeError` and escape the `ContractError`
    boundary that callers catch to mean "do not trust this log".

    An exception raised *while* iterating is deliberately left to propagate.
    `ContractError` means the log was read and is not to be trusted; a storage
    layer failing mid-read is a different fact, and reporting it as tampering
    would be a worse answer than letting it through unchanged.

    Which is why the iteration protocol is checked rather than the attempt.
    Catching `TypeError` around `iter()` did not distinguish "this is not an
    iterable" from "this iterable's startup failed with a TypeError" — a
    storage adapter failing to decode raises exactly that, and was being
    reported as tampering. Asking the type whether it can be iterated separates
    the two before anything runs.
    """
    kind = type(records)
    if not hasattr(kind, "__iter__") and not hasattr(kind, "__getitem__"):
        _fail("records must be an iterable of AuditRecord")
    return tuple(islice(records, limit))


class AuditAnchor:
    """A continuity commitment kept outside the log writer's trust domain.

    The signed head stops an attacker who cannot sign. This stops one who can:
    a log shortened and re-signed still fails against what was committed here.

    Monotonicity alone is not enough, and believing otherwise was a real hole in
    an earlier version of this class. An anchor holding chain A at five records
    would accept *any* signed head claiming six — including one over a wholly
    unrelated chain B, which then became the anchored history while the genuine
    chain A was locked out for being "too short". A larger number is not proof
    of descent. So a commit must present the records it is committing, and a
    forward move must show that the record at the previously anchored position
    still hashes to the anchored hash.

    Keeping it in the same process as the chain defeats its purpose. It is
    modelled here so the boundary is explicit and testable; the wired service
    runs it in a process of its own, reached through `anchor_process.AnchorClient`.
    """

    def __init__(self) -> None:
        self._count = 0
        self._head_hash = _EMPTY_HASH
        self._lock = Lock()

    @property
    def committed(self) -> tuple[int, str]:
        with self._lock:
            return self._count, self._head_hash

    def commit(self, head: AuditHead, records: Iterable[AuditRecord], *,
               authority: AuditAuthority | AuditVerifier) -> tuple[int, str]:
        """Record a head. Extending the committed chain is allowed; nothing else is.

        `records` must be the chain the head was signed over. It is verified in
        full before anything moves, and then used for the one check a count
        cannot make: that this chain actually contains the committed one.

        Three ways forward are refused. A shorter chain is the obvious attack.
        A different chain at the same length would let the anchor quietly
        re-point at a rival. A longer chain that does not descend from the
        committed hash is the same rewrite wearing a bigger number.
        """
        _verify_head(head, authority=authority)

        # The head is authenticated first, so it bounds how much of an untrusted
        # iterable is read. `verify` rejects a chain that does not match it.
        chain = _bounded_chain(records, head.count + 1)
        verify(chain, head, authority=authority)

        with self._lock:
            if head.count < self._count:
                _fail(f"anchor already committed {self._count} records; head claims {head.count}")
            if head.count == self._count and not hmac.compare_digest(head.head_hash, self._head_hash):
                _fail(f"anchor already committed a different chain at {self._count} records")
            if self._count and head.count > self._count:
                # `verify` established that the chain holds exactly head.count
                # well-formed, correctly linked records, so this position exists.
                continued = chain[self._count - 1].record_hash
                if not hmac.compare_digest(continued, self._head_hash):
                    _fail(f"chain does not extend the {self._count} records already "
                          f"committed; record {self._count - 1} ends elsewhere")
            self._count, self._head_hash = head.count, head.head_hash
            return self._count, self._head_hash


class AuditChain:
    """Append-only in memory. Durable storage is a separate, later decision."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._lock = Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        with self._lock:
            return tuple(self._records)

    @property
    def head_hash(self) -> str:
        with self._lock:
            return self._records[-1].record_hash if self._records else _EMPTY_HASH

    def append(self, event: AuditEvent) -> AuditRecord:
        """Read the position, link and store as one operation.

        Concurrent server requests append to the same chain. Computing the index
        and predecessor outside the lock would let two threads claim the same
        position and produce a chain that fails verification with nobody having
        tampered with anything.
        """
        if not isinstance(event, AuditEvent):
            _fail("event is invalid")
        with self._lock:
            index = len(self._records)
            previous_hash = self._records[-1].record_hash if self._records else _EMPTY_HASH
            record = AuditRecord(index, event, previous_hash,
                                 _record_hash(index=index, event=event,
                                              previous_hash=previous_hash))
            self._records.append(record)
            return record

    def head(self, authority: AuditAuthority) -> AuditHead:
        with self._lock:
            count = len(self._records)
            head_hash = self._records[-1].record_hash if self._records else _EMPTY_HASH
        return sign_head(count=count, head_hash=head_hash, authority=authority)


def _verify_head(head: Any, *, authority: AuditAuthority | AuditVerifier) -> AuditHead:
    verifier = _verifier(authority)
    if not isinstance(head, AuditHead):
        _fail("head is invalid")
    if type(head.version) is not str or head.version != _HEAD_VERSION:
        _fail("head version is not recognised")
    _count(head.count, "head.count")
    _digest(head.head_hash, "head.head_hash")
    if (type(head.signature) is not str or not _SIGNATURE.match(head.signature)
            or not verifier.verifies(canonical(head.body()), bytes.fromhex(head.signature))):
        _fail("head signature does not verify")
    return head


def _checked_record(value: Any, position: int) -> AuditRecord:
    """Type-check before hashing or comparing.

    A record reconstructed from tampered storage can carry anything. Passing it
    straight to `compare_digest` or the hasher would raise `TypeError` or
    `AttributeError` and escape the `ContractError` boundary that callers catch
    to mean "do not trust this log".
    """
    if not isinstance(value, AuditRecord):
        _fail(f"record at position {position} is not an AuditRecord")
    if type(value.index) is not int:
        _fail(f"record at position {position} has a non-integer index")
    if not isinstance(value.event, AuditEvent):
        _fail(f"record at position {position} does not carry an AuditEvent")
    _digest(value.previous_hash, f"record {position} previous_hash")
    _digest(value.record_hash, f"record {position} record_hash")
    return value


def verify(records: Iterable[AuditRecord], head: AuditHead, *,
           authority: AuditAuthority | AuditVerifier,
           anchor: AuditAnchor | None = None) -> int:
    """Check the chain against its signed head, and optionally against an anchor.

    Raises `ContractError` on the first problem found, so a caller that treats
    any refusal as "do not trust this log" needs no further interpretation.

    Without an `anchor`, this establishes that the log matches a head signed by
    the audit authority — which whoever holds the audit key could have produced
    for a shortened log. With one, a chain shorter than the highest committed
    count is refused however recently its head was signed.
    """
    head = _verify_head(head, authority=authority)

    # The head is authenticated before anything is read, so it can bound how much
    # of an untrusted iterable is consumed. One extra record is taken so a longer
    # chain is reported rather than silently truncated.
    chain = _bounded_chain(records, head.count + 1)
    if len(chain) > head.count:
        _fail(f"chain holds more than the {head.count} records the signed head claims")
    if len(chain) != head.count:
        _fail(f"chain holds {len(chain)} records but the signed head claims {head.count}")

    for position, value in enumerate(chain):
        record = _checked_record(value, position)
        if record.index != position:
            _fail(f"record at position {position} claims index {record.index}")
        expected_previous = chain[position - 1].record_hash if position else _EMPTY_HASH
        if not hmac.compare_digest(record.previous_hash, expected_previous):
            _fail(f"record {position} does not link to its predecessor")
        recomputed = _record_hash(index=record.index, event=record.event,
                                  previous_hash=record.previous_hash)
        if not hmac.compare_digest(record.record_hash, recomputed):
            _fail(f"record {position} has been altered")

    actual_head = chain[-1].record_hash if chain else _EMPTY_HASH
    if not hmac.compare_digest(actual_head, head.head_hash):
        _fail("chain does not end where the signed head says it does")

    if anchor is not None:
        if not isinstance(anchor, AuditAnchor):
            _fail("anchor must be an AuditAnchor")
        committed_count, committed_hash = anchor.committed
        if head.count < committed_count:
            _fail(f"anchor committed {committed_count} records; this chain has {head.count}")
        if head.count == committed_count and not hmac.compare_digest(head.head_hash, committed_hash):
            _fail("chain ends differently from what the anchor committed at this length")
        if committed_count and head.count > committed_count:
            # Same reason `commit` requires it: being longer than the anchor is
            # not the same as containing it. Without this, a rival chain built
            # to one record more than the anchor would verify against it.
            if not hmac.compare_digest(chain[committed_count - 1].record_hash, committed_hash):
                _fail(f"chain does not contain the {committed_count} records the "
                      f"anchor committed")
    return len(chain)
