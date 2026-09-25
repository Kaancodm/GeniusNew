"""The audit anchor in a process of its own. `docs/ROADMAP-V01.md` step 8.

`audit_chain.AuditAnchor` says of itself that keeping it in the same process as
the chain defeats its purpose: whoever writes the log could reach into the
anchor's memory and move it backwards. Here the anchor's state lives in a child
interpreter, and the writer holds nothing but two pipes. Across them it can ask
two things — commit this signed head over these records, and what is committed —
and there is no message that resets, rewinds or overwrites anything.

The child runs the existing `AuditAnchor` unchanged. It receives records as
data, rebuilds them for checking, and applies the same verification and the
same extension rule the in-process anchor always applied, so this module adds a
boundary and no new chain logic.

## What the boundary is, and is not

It is a memory boundary: the anchored count and hash are not objects the writer
can reach. It is **not** a lifecycle boundary. The service starts this process,
so it can also end it.

## Surviving a restart

Given a `state_path`, the child appends every head that moves it forward to that
file — one canonical signed head per line, flushed and fsynced before it
answers — and a restarted child resumes from the last one. On start it checks
every line against the public key and requires the counts to rise strictly; a
forged, reordered or torn line stops it from starting at all, because starting
at zero instead would be the silent reset persistence is there to prevent. A
write that fails ends the child, so memory never runs ahead of the file.

What the file cannot stop is a rollback by someone who can write it. Every
line is a genuinely signed head, so the same operating-system user can cut the
file back to an older one and the anchor will resume from there. Closing that
means running the anchor under another user or outside this host, which is
deployment; `SECURITY.md` lists it and a test holds it open.

The child holds only the audit **public** key. Heads are Ed25519-signed, so
checking one needs nothing that could make one: the anchor can refuse a forged
head but cannot forge one itself, and nothing that reaches its memory can.

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

from .audit import AuditAuthority, AuditEvent, AuditVerifier, rehydrate_event
from .audit_chain import (AuditAnchor, AuditHead, AuditRecord, _bounded_chain, _count,
                          _digest, _verify_head)
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
_MAX_STATE_BYTES = 16 * 1024 * 1024
_RECORD_KEYS = frozenset({"event", "index", "previous_hash", "record_hash"})


def _fail(message: str) -> None:
    raise ContractError(message)


class _Unreachable(Exception):
    """The child did not answer as a well-behaved anchor process would."""


class _Refused(Exception):
    """The child answered its start with a refusal, such as an unusable state file."""


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

def _verifier_from(init: dict[str, Any]) -> AuditVerifier:
    if set(init) != {"kind", "public_key"} or init["kind"] != "init":
        _fail("anchor process expected an init message")
    if type(init["public_key"]) is not str:
        _fail("anchor public_key must be hex")
    try:
        key = bytes.fromhex(init["public_key"])
    except ValueError:
        key = b""
    return AuditVerifier(public_key=key)


def _head(value: Any) -> AuditHead:
    if not isinstance(value, dict) or set(value) != _HEAD_KEYS:
        _fail("anchor request carries a malformed head")
    return AuditHead(value["version"], value["count"], value["head_hash"], value["signature"])


def _record(value: Any) -> AuditRecord:
    """Rebuild a record so the unchanged chain checks can run over it.

    Its event's attribution is not proven by rebuilding it; it is proven by the
    signed head the commit is checked against, which covers every record.
    """
    if not isinstance(value, dict) or set(value) != _RECORD_KEYS:
        _fail("anchor request carries a malformed record")
    return AuditRecord(value["index"], rehydrate_event(value["event"]),
                       value["previous_hash"], value["record_hash"])


def _committed(state: tuple[int, str]) -> dict[str, Any]:
    count, head_hash = state
    return {"count": count, "head_hash": head_hash, "kind": "ok"}


def _handle(message: dict[str, Any], anchor: AuditAnchor,
            verifier: AuditVerifier) -> dict[str, Any]:
    """Answer one request. There is deliberately no third kind."""
    if message.get("kind") == "committed" and set(message) == {"kind"}:
        return _committed(anchor.committed)
    if message.get("kind") != "commit" or set(message) != {"head", "kind", "records"}:
        _fail("anchor request is not recognised")
    head = _head(message["head"])
    if not isinstance(message["records"], list):
        _fail("anchor request records must be a list")
    records = [_record(value) for value in message["records"]]
    return _committed(anchor.commit(head, records, authority=verifier))


def _head_line(head: AuditHead) -> bytes:
    return canonical({"count": head.count, "head_hash": head.head_hash,
                      "signature": head.signature, "version": head.version}) + b"\n"


def _load(path: str | None, verifier: AuditVerifier) -> AuditAnchor:
    """The anchor as the state file left it, or a fresh one if there is none yet.

    Every line is checked, not only the last: a file whose history does not
    hold together is not one to resume from, whatever its final line says.
    """
    if path is None:
        return AuditAnchor()
    try:
        with open(path, "rb") as stream:
            data = stream.read(_MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        return AuditAnchor()
    except OSError:
        _fail("anchor state file cannot be read")
    if len(data) > _MAX_STATE_BYTES:
        _fail("anchor state file is too large")
    if not data:
        return AuditAnchor()
    if not data.endswith(b"\n"):
        _fail("anchor state file ends in a torn line")
    head = None
    for line in data[:-1].split(b"\n"):
        current = _head(_decode(line, "anchor state line"))
        _verify_head(current, authority=verifier)
        if head is not None and current.count <= head.count:
            _fail("anchor state file does not rise strictly")
        head = current
    return AuditAnchor.resumed(head, authority=verifier)


def _append(path: str, head: AuditHead) -> None:
    """Durably record a head before the commit is answered."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        _write_all(fd, _head_line(head))
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_frame(stream: BinaryIO) -> bytes | None:
    header = stream.read(4)
    if len(header) != 4:
        return None
    size = int.from_bytes(header, "big")
    if size > _MAX_REQUEST_BYTES:
        return None
    data = stream.read(size)
    return data if len(data) == size else None


def _refusal(refusal: ContractError) -> dict[str, Any]:
    return {"kind": "refused", "message": str(refusal)[:_MAX_REFUSAL_CHARS]}


def _serve(requests: BinaryIO, replies: BinaryIO, state_path: str | None = None) -> int:
    """Serve until the writer closes the pipe. A torn frame ends the process."""
    data = _read_frame(requests)
    if data is None:
        return 1
    try:
        verifier = _verifier_from(_decode(data, "anchor init"))
    except ContractError:
        return 1
    try:
        anchor = _load(state_path, verifier)
    except ContractError as refusal:
        # Answered, not just exited: the writer should learn why it has no
        # anchor, and starting at zero instead would be the silent reset.
        replies.write(_frame(_refusal(refusal), _MAX_REPLY_BYTES))
        replies.flush()
        return 1
    replies.write(_frame(_committed(anchor.committed), _MAX_REPLY_BYTES))
    replies.flush()
    while (data := _read_frame(requests)) is not None:
        before = anchor.committed
        try:
            message = _decode(data, "anchor request")
            reply = _handle(message, anchor, verifier)
        except ContractError as refusal:
            reply = _refusal(refusal)
        if state_path is not None and anchor.committed != before:
            # Only a commit moves the anchor, so `message` is one with a head.
            try:
                _append(state_path, _head(message["head"]))
            except OSError:
                # Memory has moved and the file has not. Ending here leaves
                # the file as the truth a restart resumes from.
                return 2
        replies.write(_frame(reply, _MAX_REPLY_BYTES))
        replies.flush()
    return 0


# --- the writer's side -------------------------------------------------------

def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
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

    def __init__(self, *, verifier: AuditVerifier,
                 state_path: str | os.PathLike[str] | None = None) -> None:
        if not isinstance(verifier, AuditVerifier):
            _fail("verifier must be an AuditVerifier")
        if state_path is not None:
            # Absolute, because the child resolves it, not the caller.
            if isinstance(state_path, os.PathLike):
                state_path = os.fspath(state_path)
            if type(state_path) is not str or not os.path.isabs(state_path):
                _fail("state_path must be an absolute path")
        self._verifier = verifier
        self._state_path = state_path
        self._lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._close = lambda: None

    def close(self) -> None:
        """End the anchor process. Without a state file, what it committed goes too."""
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
        command = [sys.executable, "-m", "geniusnew.anchor_process"]
        if self._state_path is not None:
            command.append(self._state_path)
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0, close_fds=True, env=_minimal_environment(),
        )
        self._close = weakref.finalize(self, _stop, self._process)
        init = _frame({"kind": "init", "public_key": self._verifier.public_key.hex()},
                      _MAX_REQUEST_BYTES)
        try:
            _state(_decode(_send(self._process, init), "anchor reply"))
        except ContractError as refusal:
            raise _Refused(str(refusal)) from None
        return self._process

    @property
    def committed(self) -> tuple[int, str]:
        return self._exchange({"kind": "committed"})

    def commit(self, head: AuditHead, records: Iterable[AuditRecord], *,
               authority: AuditAuthority | AuditVerifier) -> tuple[int, str]:
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
            except _Refused as refusal:
                self._close()
                _fail(f"anchor process refused to start: {refusal}")
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
    _write_all(process.stdin.fileno(), frame)
    deadline = time.monotonic() + _REPLY_SECONDS
    size = int.from_bytes(_read_exact(process.stdout, 4, deadline), "big")
    if size > _MAX_REPLY_BYTES:
        raise _Unreachable()
    return _read_exact(process.stdout, size, deadline)


if __name__ == "__main__":
    raise SystemExit(_serve(sys.stdin.buffer, sys.stdout.buffer,
                            sys.argv[1] if len(sys.argv) > 1 else None))
