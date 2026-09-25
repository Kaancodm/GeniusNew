"""The audit anchor in a process of its own. `docs/ROADMAP-V01.md` step 8.

`audit_chain.AuditAnchor` says of itself that keeping it in the same process as
the chain defeats its purpose: whoever writes the log could reach into the
anchor's memory and move it backwards. Here the anchor's state lives in another
interpreter, and the writer holds nothing but a socket path. Through it it can
ask two things — commit this signed head over these records, and what is
committed — and there is no message that resets, rewinds or overwrites anything.

The anchor process runs the existing `AuditAnchor` unchanged. It receives
records as data, rebuilds them for checking, and applies the same verification
and the same extension rule the in-process anchor always applied, so this
module adds a boundary and no new chain logic.

## Who starts it, and who stops it

Not the service. `start` is the anchor's own lifecycle path: it launches the
process in a session of its own, hands it the audit public key once over a
pipe it then closes, and returns an `AnchorHandle` — the one object that can stop it.
Whoever runs the service keeps that handle; the service itself is given an
`AnchorClient`, which holds a socket path and nothing else: no process, no
pipe, no stop. A service that restarts connects to the same anchor and finds
everything it committed still committed, so a restart is no longer a reset.

The key reaches the anchor from its starter, never from the writer. The writer
cannot choose which key its heads are checked against.

## What the boundary is, and is not

It is a memory and a lifecycle boundary against the service's code. It is
**not** a boundary against the operating-system user: the anchor runs as the
same user, so a process of that user can still signal it — which fails closed,
below, but ends the anchor. And the commitments live in memory only: a
restarted *anchor* starts at zero. Running it under a user of its own is a
deployment decision; persisting it is a decision of its own, recorded as open
in `docs/ADR-002-anchor-persistence.md`. `SECURITY.md` lists both.

The anchor holds only the audit **public** key. Heads are Ed25519-signed, so
checking one needs nothing that could make one: the anchor can refuse a forged
head but cannot forge one itself, and nothing that reaches its memory can.

## Failing closed

An anchor that is gone, silent past the deadline, or answering in a shape it
should not, is reported as `ContractError` and never as an anchored state.
`verify(..., anchor=...)` therefore refuses a chain rather than skipping the
anchor check when the anchor cannot be reached. A socket path that is already
taken makes `start` refuse rather than replace whatever holds it: a second
anchor on the same path would be a silent reset.
"""

from __future__ import annotations

from contextlib import suppress
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from typing import Any, BinaryIO, Iterable
import weakref

from .audit import AuditAuthority, AuditEvent, AuditVerifier, rehydrate_event
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
# One request per connection, served in turn. A client that connects and then
# stalls holds the anchor for at most this long.
_CONNECTION_SECONDS = 10.0

_HEAD_KEYS = frozenset({"count", "head_hash", "signature", "version"})
_RECORD_KEYS = frozenset({"event", "index", "previous_hash", "record_hash"})


def _fail(message: str) -> None:
    raise ContractError(message)


class _Unreachable(Exception):
    """The anchor did not answer as a well-behaved anchor process would."""


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


def _socket_path(value: Any) -> str:
    if type(value) is not str or not os.path.isabs(value):
        _fail("anchor socket_path must be an absolute path")
    return value


# --- the anchor process ------------------------------------------------------

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


def _read_frame(stream: BinaryIO, limit: int = _MAX_REQUEST_BYTES) -> bytes | None:
    header = stream.read(4)
    if len(header) != 4:
        return None
    size = int.from_bytes(header, "big")
    if size > limit:
        return None
    data = stream.read(size)
    return data if len(data) == size else None


def _answer(data: bytes | None, anchor: AuditAnchor,
            verifier: AuditVerifier) -> bytes | None:
    """One request frame in, one reply frame out. A torn frame gets no reply."""
    if data is None:
        return None
    try:
        reply = _handle(_decode(data, "anchor request"), anchor, verifier)
    except ContractError as refusal:
        reply = {"kind": "refused", "message": str(refusal)[:_MAX_REFUSAL_CHARS]}
    return _frame(reply, _MAX_REPLY_BYTES)


def _serve_connection(connection: socket.socket, anchor: AuditAnchor,
                      verifier: AuditVerifier) -> None:
    connection.settimeout(_CONNECTION_SECONDS)
    with suppress(OSError):
        with connection.makefile("rb") as requests:
            reply = _answer(_read_frame(requests), anchor, verifier)
        if reply is not None:
            connection.sendall(reply)


def _stopped(signum: int, frame: Any) -> None:
    raise SystemExit(0)


def _serve(init: BinaryIO, ready: BinaryIO, socket_path: str) -> int:
    """Take the public key from the starter, then answer on the socket until stopped.

    Reports ready only once the socket is bound, so a starter that got an
    answer knows the path is this anchor's. A path that is already bound —
    another anchor, or a stale file from one that died — is not taken over.
    """
    data = _read_frame(init)
    if data is None:
        return 1
    try:
        verifier = _verifier_from(_decode(data, "anchor init"))
    except ContractError:
        return 1
    anchor = AuditAnchor()
    signal.signal(signal.SIGTERM, _stopped)
    os.umask(0o077)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(socket_path)
        except OSError:
            return 1
        try:
            listener.listen(16)
            ready.write(_frame(_committed(anchor.committed), _MAX_REPLY_BYTES))
            ready.flush()
            ready.close()
            while True:
                connection, _ = listener.accept()
                with connection:
                    _serve_connection(connection, anchor, verifier)
        finally:
            with suppress(OSError):
                os.unlink(socket_path)


# --- the lifecycle path ------------------------------------------------------

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


def _receive(stream: BinaryIO, deadline: float) -> bytes:
    size = int.from_bytes(_read_exact(stream, 4, deadline), "big")
    if size > _MAX_REPLY_BYTES:
        raise _Unreachable()
    return _read_exact(stream, size, deadline)


def _close_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            with suppress(OSError):
                stream.close()


def _stop(process: subprocess.Popen[bytes]) -> None:
    # SIGTERM lets the anchor remove its own socket. Killing is only for an
    # anchor that does not stop; its socket file then stays, and the next
    # `start` on that path refuses until someone removes it on purpose.
    _close_pipes(process)
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


class AnchorHandle:
    """The one object that can stop an anchor.

    Kept by whoever started it — an operator, a supervisor, the demo — and
    never handed to the service. An anchor whose handle is dropped is stopped
    with it, so a starter that exits does not leave a process behind.
    """

    def __init__(self, process: subprocess.Popen[bytes], socket_path: str) -> None:
        self._process = process
        self._socket_path = socket_path
        self._stop = weakref.finalize(self, _stop, process)

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def socket_path(self) -> str:
        return self._socket_path

    def stop(self) -> None:
        """End the anchor. Everything it committed is gone with it."""
        self._stop()


def start(*, verifier: AuditVerifier, socket_path: str) -> AnchorHandle:
    """Start an anchor listening on `socket_path`. Its lifecycle is the caller's.

    It is given the verifier, never the authority: the anchor must be able to
    refuse a forged head and must not be able to make one. The process gets a
    session of its own, so a signal to the starter's process group — a Ctrl-C
    at the service's terminal — does not reach it.
    """
    if not isinstance(verifier, AuditVerifier):
        _fail("verifier must be an AuditVerifier")
    socket_path = _socket_path(socket_path)
    process = subprocess.Popen(
        [sys.executable, "-m", "geniusnew.anchor_process", "--socket", socket_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        bufsize=0, close_fds=True, env=_minimal_environment(),
        start_new_session=True,
    )
    try:
        _write_all(process.stdin, _frame({"kind": "init",
                                          "public_key": verifier.public_key.hex()},
                                         _MAX_REQUEST_BYTES))
        process.stdin.close()
        _state(_decode(_receive(process.stdout, time.monotonic() + _REPLY_SECONDS),
                       "anchor reply"))
    except (_Unreachable, OSError, ValueError):
        # ValueError is ContractError's base class, so a malformed ready frame
        # lands here too. The socket path is left alone: if it was taken, it
        # belongs to whoever took it.
        _stop(process)
        _fail("anchor process did not start")
    _close_pipes(process)
    return AnchorHandle(process, socket_path)


# --- the writer's side -------------------------------------------------------

def _state(reply: dict[str, Any]) -> tuple[int, str]:
    if (reply.get("kind") == "refused" and set(reply) == {"kind", "message"}
            and type(reply["message"]) is str):
        raise ContractError(reply["message"])
    if reply.get("kind") != "ok" or set(reply) != {"count", "head_hash", "kind"}:
        _fail("anchor process sent an invalid reply")
    return _count(reply["count"], "anchor count"), _digest(reply["head_hash"], "anchor head_hash")


class AnchorClient(AuditAnchor):
    """What the service holds: the path to an anchor someone else started.

    It is a subclass so that `audit_chain.verify` accepts it where it accepts
    any anchor, and it deliberately does not call the base constructor: there
    is no local count or hash to fall back on if the anchor cannot be asked.
    It can ask the two questions and nothing else. It cannot start, stop or
    reset the anchor, and dropping it changes nothing on the other side.
    """

    def __init__(self, socket_path: str) -> None:
        self._socket_path = _socket_path(socket_path)

    @property
    def committed(self) -> tuple[int, str]:
        return self._exchange({"kind": "committed"})

    def commit(self, head: AuditHead, records: Iterable[AuditRecord], *,
               authority: AuditAuthority | AuditVerifier) -> tuple[int, str]:
        """Send a head and its chain; the anchor verifies with its own authority.

        `authority` is accepted for the base signature and not sent: the key
        the anchor checks with is the one its starter gave it, not one the
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
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(_REPLY_SECONDS)
                connection.connect(self._socket_path)
                connection.sendall(frame)
                with connection.makefile("rb") as replies:
                    reply = _read_frame(replies, _MAX_REPLY_BYTES)
        except OSError:
            reply = None
        if reply is None:
            _fail("anchor process is unreachable")
        return _state(_decode(reply, "anchor reply"))


def _main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "--socket":
        return 2
    return _serve(sys.stdin.buffer, sys.stdout.buffer, argv[1])


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
