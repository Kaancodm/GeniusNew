"""The audit anchor in a process of its own. `docs/ROADMAP-V01.md` step 8.

`audit_chain.AuditAnchor` says of itself that keeping it in the same process as
the chain defeats its purpose: whoever writes the log could reach into the
anchor's memory and move it backwards. Here the anchor's state lives in a child
interpreter, and the writer holds nothing but two pipes. Across them it can ask
two things — commit this signed head over these records, and what is committed —
and there is no message that resets, rewinds or overwrites anything.

The child runs the existing `AuditAnchor` unchanged. It receives records as
data, rebuilds them with its own `AuditAuthority`, and applies the same
verification and the same extension rule the in-process anchor always applied,
so this module adds a boundary and no new chain logic.

## What the boundary is, and is not

It is a memory boundary: the anchored count and hash are not objects the writer
can reach. It is **not** a lifecycle boundary. The service starts this process,
so it can also end it, and the commitments live in memory only — a restarted
anchor starts at zero. Keeping the anchor alive independently of the writer
means running it under a different operating-system user and, for restarts,
persistence; both are deployment decisions `docs/ROADMAP-V01.md` leaves past
v0.1, and `SECURITY.md` lists the gap.

The child holds the audit key, because verifying a head needs it and HMAC is
symmetric. That does not weaken the anchor: it never writes the log, and what
it protects against is a writer that already holds the same key.

## Failing closed

A child that is gone, silent past the deadline, or answering in a shape it
should not, is reported as `ContractError` and never as an anchored state.
`verify(..., anchor=...)` therefore refuses a chain rather than skipping the
anchor check when the anchor cannot be reached.
"""

from __future__ import annotations

from contextlib import suppress
import json
import os
import select
import subprocess
import sys
import time
from threading import Lock
from typing import Any, BinaryIO, Iterable
import weakref

from .audit import AuditAuthority, AuditEvent
from .audit_chain import (AuditAnchor, AuditHead, AuditRecord, _bounded_chain, _count,
                          _digest)
from .contracts import ContractError, canonical
from .isolation import _minimal_environment

# A commit carries the whole chain, so this bounds the chain an anchor process
# will take in one message. Roughly sixty thousand records at the size an audit
# event serializes to — far past what a v0.1 process holds.
_MAX_REQUEST_BYTES = 64 * 1024 * 1024
_MAX_REPLY_BYTES = 4096
_MAX_REFUSAL_CHARS = 512
_REPLY_SECONDS = 30.0

_HEAD_KEYS = frozenset({"count", "head_hash", "signature", "version"})
_RECORD_KEYS = frozenset({"event", "index", "previous_hash", "record_hash"})
_EVENT_KEYS = frozenset({
    "action", "actor", "constitution_version", "decision", "handoff_sha256",
    "job_id", "occurred_at", "payload_sha256", "policy_version", "reason_code",
    "subject", "trace_id",
})


def _fail(message: str) -> None:
    raise ContractError(message)


class _Unreachable(Exception):
    """The child did not answer as a well-behaved anchor process would."""


def _decode(data: bytes, noun: str) -> dict[str, Any]:
    """One exact object per frame.

    Requiring the canonical re-encoding to reproduce the bytes also refuses
    duplicate keys and non-finite numbers without a rule for each: either
    changes what re-encoding produces.
    """
    try:
        value = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        value = None
    if not isinstance(value, dict) or canonical(value) != data:
        _fail(f"{noun} is not canonical JSON")
    return value


def _frame(message: dict[str, Any], limit: int) -> bytes:
    data = canonical(message)
    if len(data) > limit:
        _fail("anchor message is too large")
    return len(data).to_bytes(4, "big") + data


# --- the child ---------------------------------------------------------------

def _authority_from(init: dict[str, Any]) -> AuditAuthority:
    if set(init) != {"audit_key", "kind"} or init["kind"] != "init":
        _fail("anchor process expected an init message")
    if type(init["audit_key"]) is not str:
        _fail("anchor audit_key must be hex")
    try:
        key = bytes.fromhex(init["audit_key"])
    except ValueError:
        key = b""
    return AuditAuthority(audit_key=key)


def _head(value: Any) -> AuditHead:
    if not isinstance(value, dict) or set(value) != _HEAD_KEYS:
        _fail("anchor request carries a malformed head")
    return AuditHead(value["version"], value["count"], value["head_hash"], value["signature"])


def _record(value: Any, authority: AuditAuthority) -> AuditRecord:
    """Rebuild a record so the unchanged chain checks can run over it.

    The actor is re-minted by this process's own authority: an actor is proof
    that the audit authority stood behind an event, and a string off a pipe is
    not that proof until someone holding the key has said so.
    """
    if not isinstance(value, dict) or set(value) != _RECORD_KEYS:
        _fail("anchor request carries a malformed record")
    event = value["event"]
    if not isinstance(event, dict) or set(event) != _EVENT_KEYS:
        _fail("anchor request carries a malformed event")
    actor = event["actor"]
    if type(actor) is not str or actor.count(":") != 1:
        _fail("anchor request carries a malformed actor")
    component, instance_id = actor.split(":")
    fields = {name: event[name] for name in _EVENT_KEYS - {"actor"}}
    return AuditRecord(value["index"],
                       AuditEvent(actor=authority.actor(component, instance_id), **fields),
                       value["previous_hash"], value["record_hash"])


def _committed(state: tuple[int, str]) -> dict[str, Any]:
    count, head_hash = state
    return {"count": count, "head_hash": head_hash, "kind": "ok"}


def _handle(message: dict[str, Any], anchor: AuditAnchor,
            authority: AuditAuthority) -> dict[str, Any]:
    """Answer one request. There is deliberately no third kind."""
    if message.get("kind") == "committed" and set(message) == {"kind"}:
        return _committed(anchor.committed)
    if message.get("kind") != "commit" or set(message) != {"head", "kind", "records"}:
        _fail("anchor request is not recognised")
    head = _head(message["head"])
    if not isinstance(message["records"], list):
        _fail("anchor request records must be a list")
    records = [_record(value, authority) for value in message["records"]]
    return _committed(anchor.commit(head, records, authority=authority))


def _read_frame(stream: BinaryIO) -> bytes | None:
    header = stream.read(4)
    if len(header) != 4:
        return None
    size = int.from_bytes(header, "big")
    if size > _MAX_REQUEST_BYTES:
        return None
    data = stream.read(size)
    return data if len(data) == size else None


def _serve(requests: BinaryIO, replies: BinaryIO) -> int:
    """Serve until the writer closes the pipe. A torn frame ends the process."""
    data = _read_frame(requests)
    if data is None:
        return 1
    try:
        authority = _authority_from(_decode(data, "anchor init"))
    except ContractError:
        return 1
    anchor = AuditAnchor()
    replies.write(_frame(_committed(anchor.committed), _MAX_REPLY_BYTES))
    replies.flush()
    while (data := _read_frame(requests)) is not None:
        try:
            reply = _handle(_decode(data, "anchor request"), anchor, authority)
        except ContractError as refusal:
            reply = {"kind": "refused", "message": str(refusal)[:_MAX_REFUSAL_CHARS]}
        replies.write(_frame(reply, _MAX_REPLY_BYTES))
        replies.flush()
    return 0


# --- the writer's side -------------------------------------------------------

def _write_all(stream: BinaryIO, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(stream.fileno(), view)
        view = view[written:]


def _read_exact(stream: BinaryIO, size: int, deadline: float) -> bytes:
    fd = stream.fileno()
    chunks: list[bytes] = []
    while size:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select((fd,), (), (), remaining)[0]:
            raise _Unreachable()
        chunk = os.read(fd, size)
        if not chunk:
            raise _Unreachable()
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _stop(process: subprocess.Popen[bytes]) -> None:
    # Closing stdin is the shutdown message: the child's read returns short
    # and it exits. Killing is only for a child that does not.
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            with suppress(OSError):
                stream.close()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _state(reply: dict[str, Any]) -> tuple[int, str]:
    if (reply.get("kind") == "refused" and set(reply) == {"kind", "message"}
            and type(reply["message"]) is str):
        raise ContractError(reply["message"])
    if reply.get("kind") != "ok" or set(reply) != {"count", "head_hash", "kind"}:
        _fail("anchor process sent an invalid reply")
    return _count(reply["count"], "anchor count"), _digest(reply["head_hash"], "anchor head_hash")


class AnchorProcess(AuditAnchor):
    """An `AuditAnchor` whose state is in another interpreter.

    It is a subclass so that `audit_chain.verify` accepts it where it accepts
    any anchor, and it deliberately does not call the base constructor: there
    is no local count or hash to fall back on if the child cannot be asked.

    The child starts on first use. A fresh anchor holds nothing either way, and
    starting an interpreter costs about seventy milliseconds that a service
    which never commits a head should not pay. Once closed it does not start
    again: a second process would be a silent reset.
    """

    def __init__(self, *, audit_key: bytes) -> None:
        AuditAuthority(audit_key=audit_key)
        self._audit_key = audit_key
        self._lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._close = lambda: None

    def close(self) -> None:
        """End the anchor process. Everything it committed is gone with it."""
        with self._lock:
            self._closed = True
            self._close()

    @property
    def pid(self) -> int:
        self._exchange({"kind": "committed"})
        return self._process.pid

    def _started(self) -> subprocess.Popen[bytes]:
        """The child, started if this is the first request. Call under the lock."""
        if self._process is not None or self._closed:
            if self._process is None:
                raise _Unreachable()
            return self._process
        self._process = subprocess.Popen(
            [sys.executable, "-m", "geniusnew.anchor_process"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, close_fds=True, env=_minimal_environment(),
        )
        self._close = weakref.finalize(self, _stop, self._process)
        init = _frame({"audit_key": self._audit_key.hex(), "kind": "init"}, _MAX_REQUEST_BYTES)
        _state(_decode(_send(self._process, init), "anchor reply"))
        return self._process

    @property
    def committed(self) -> tuple[int, str]:
        return self._exchange({"kind": "committed"})

    def commit(self, head: AuditHead, records: Iterable[AuditRecord], *,
               authority: AuditAuthority) -> tuple[int, str]:
        """Send a head and its chain; the child verifies with its own authority.

        `authority` is accepted for the base signature and not sent: the key
        the child checks with is the one it was started with, not one the
        writer hands over per call.
        """
        if not isinstance(head, AuditHead):
            _fail("head is invalid")
        chain = _bounded_chain(records, _count(head.count, "head.count") + 1)
        if not all(isinstance(record, AuditRecord) and isinstance(record.event, AuditEvent)
                   for record in chain):
            _fail("records must be AuditRecord values carrying an AuditEvent")
        return self._exchange({
            "head": {"count": head.count, "head_hash": head.head_hash,
                     "signature": head.signature, "version": head.version},
            "kind": "commit",
            "records": [record.to_dict() for record in chain],
        })

    def _exchange(self, message: dict[str, Any]) -> tuple[int, str]:
        frame = _frame(message, _MAX_REQUEST_BYTES)
        with self._lock:
            try:
                reply = _send(self._started(), frame)
            except (_Unreachable, OSError, ValueError):
                # ValueError covers pipes already closed and, being its base
                # class, a ContractError from a malformed init reply. Either
                # way no further answer can be trusted, so the process is ended.
                self._close()
                reply = None
        if reply is None:
            _fail("anchor process is unreachable")
        return _state(_decode(reply, "anchor reply"))


def _send(process: subprocess.Popen[bytes], frame: bytes) -> bytes:
    _write_all(process.stdin, frame)
    deadline = time.monotonic() + _REPLY_SECONDS
    size = int.from_bytes(_read_exact(process.stdout, 4, deadline), "big")
    if size > _MAX_REPLY_BYTES:
        raise _Unreachable()
    return _read_exact(process.stdout, size, deadline)


if __name__ == "__main__":
    raise SystemExit(_serve(sys.stdin.buffer, sys.stdout.buffer))
