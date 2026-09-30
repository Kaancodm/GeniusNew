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
can reach. As a child it is **not** a lifecycle boundary: the service starts
this process, so it can also end it.

`serve` removes that. Started on its own — by the operator, under its own
operating-system user — the anchor listens on a Unix socket and the service
holds an `AnchorClient` instead. A socket, unlike a pipe the service created,
can be answered by whoever gets to its path first, so every served reply is
signed with the anchor's own Ed25519 key over the nonce of the request it
answers. The client is configured with the public half and refuses any reply
that is unsigned, signed by another key, or signed for another request. What a
separate user buys — the service can neither stop the anchor nor cut its state
file back — is a property of the deployment, not of this code; `SECURITY.md`
says which one a given installation has.

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

import argparse
from contextlib import suppress
import json
import os
import re
import select
import signal
import socket
import stat
import subprocess
import sys
import time
from threading import Lock
from typing import Any, BinaryIO, Iterable
import weakref

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (Ed25519PrivateKey,
                                                              Ed25519PublicKey)

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

# The served protocol wraps the child's messages; it does not change them.
_REQUEST_KEYS = frozenset({"nonce", "request"})
_REPLY_KEYS = frozenset({"nonce", "reply", "signature"})
_NONCE_BYTES = 32
_NONCE = re.compile(r"\A[0-9a-f]{64}\Z")
# Domain separation: a reply signature cannot be replayed as any other
# signature this project makes, and no other signature passes as a reply.
_REPLY_LABEL = b"geniusnew/anchor-reply/ed25519/v1\n"
_SOCKET_MODE = 0o660
_BACKLOG = 8


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
        try:
            message = _decode(data, "anchor request")
        except ContractError as refusal:
            reply = _refusal(refusal)
        else:
            reply = _answer(message, anchor, verifier, state_path)
            if reply is None:
                return 2
        replies.write(_frame(reply, _MAX_REPLY_BYTES))
        replies.flush()
    return 0


def _answer(message: dict[str, Any], anchor: AuditAnchor, verifier: AuditVerifier,
            state_path: str | None) -> dict[str, Any] | None:
    """One request answered, and written down first if it moved the anchor.

    None means the head could not be written: memory has moved and the file
    has not, and the caller must end rather than answer, so the file stays the
    truth a restart resumes from.
    """
    before = anchor.committed
    try:
        reply = _handle(message, anchor, verifier)
    except ContractError as refusal:
        return _refusal(refusal)
    if state_path is not None and anchor.committed != before:
        # Only a commit moves the anchor, so `message` is one with a head.
        try:
            _append(state_path, _head(message["head"]))
        except OSError:
            return None
    return reply


# --- the served anchor -------------------------------------------------------

def _reply_message(nonce: str, reply: dict[str, Any]) -> bytes:
    return _REPLY_LABEL + canonical({"nonce": nonce, "reply": reply})


def _nonce(value: Any) -> str:
    if type(value) is not str or not _NONCE.match(value):
        _fail("anchor nonce must be 32 bytes of lowercase hex")
    return value


def _serve_connection(requests: BinaryIO, replies: BinaryIO, anchor: AuditAnchor,
                      verifier: AuditVerifier, key: Ed25519PrivateKey,
                      state_path: str) -> int:
    """Answer one client until it hangs up. 2 means a head could not be written."""
    while (data := _read_frame(requests)) is not None:
        try:
            envelope = _decode(data, "anchor request")
            if set(envelope) != _REQUEST_KEYS:
                _fail("anchor request envelope is malformed")
            nonce = _nonce(envelope["nonce"])
            if not isinstance(envelope["request"], dict):
                _fail("anchor request envelope is malformed")
        except ContractError:
            # Without a nonce there is nothing to bind a refusal to, and an
            # unbound reply is worth less to the client than none at all.
            return 0
        reply = _answer(envelope["request"], anchor, verifier, state_path)
        if reply is None:
            return 2
        signature = key.sign(_reply_message(nonce, reply)).hex()
        replies.write(_frame({"nonce": nonce, "reply": reply, "signature": signature},
                             _MAX_REPLY_BYTES))
        replies.flush()
    return 0


def _absolute(path: Any, noun: str) -> str:
    if isinstance(path, os.PathLike):
        path = os.fspath(path)
    if type(path) is not str or not os.path.isabs(path):
        _fail(f"{noun} must be an absolute path")
    return path


def _reply_key(path: str) -> Ed25519PrivateKey:
    """The anchor's identity across its own restarts: made once, owner-only.

    A new key on every start would force the service to be reconfigured each
    time, and a reconfiguration step is where an operator learns to accept
    whatever key is presented.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = None
    except OSError:
        _fail("anchor key file cannot be created")
    if fd is not None:
        key = Ed25519PrivateKey.generate()
        try:
            _write_all(fd, key.private_bytes(serialization.Encoding.Raw,
                                             serialization.PrivateFormat.Raw,
                                             serialization.NoEncryption()))
            os.fsync(fd)
        finally:
            os.close(fd)
        return key
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        _fail("anchor key file cannot be read")
    try:
        mode = os.fstat(fd).st_mode
        data = os.read(fd, 33)
    finally:
        os.close(fd)
    if not stat.S_ISREG(mode) or mode & 0o077:
        _fail("anchor key file must be a regular file only its owner can read")
    if len(data) != 32:
        _fail("anchor key file must hold exactly 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(data)


def _public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)


def _listen(path: str) -> socket.socket:
    if os.path.lexists(path):
        # Taking a path over would let a stale or planted socket, or whatever
        # else sits there, decide who answers as the anchor.
        _fail("anchor socket path already exists")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Set before bind, so the socket is never reachable wider than intended.
    previous = os.umask(0o777 & ~_SOCKET_MODE)
    try:
        server.bind(path)
    except OSError:
        server.close()
        _fail("anchor socket cannot be bound")
    finally:
        os.umask(previous)
    server.listen(_BACKLOG)
    return server


class _Stopping:
    """SIGTERM ends the anchor between connections, never inside one.

    Ending inside one could interrupt the append of a head and leave a torn
    line, which the next start refuses: safe, but an outage an ordinary stop
    should not cause.
    """

    def __init__(self) -> None:
        self.busy = False
        self.requested = False

    def __call__(self, signum: int, frame: Any) -> None:
        if not self.busy:
            raise SystemExit(0)
        self.requested = True


def _run_server(*, socket_path: str, state_path: str, key_path: str,
                audit_public_key: str) -> int:
    """Serve one anchor on a socket until stopped. Refuses before binding."""
    socket_path = _absolute(socket_path, "socket path")
    state_path = _absolute(state_path, "state path")
    key = _reply_key(_absolute(key_path, "key path"))
    verifier = _verifier_from({"kind": "init", "public_key": audit_public_key})
    anchor = _load(state_path, verifier)
    server = _listen(socket_path)
    bound = os.stat(socket_path)
    stopping = _Stopping()
    signal.signal(signal.SIGTERM, stopping)
    try:
        while True:
            connection, _ = server.accept()
            stopping.busy = True
            with connection:
                # A client that stops mid-frame must not hold the anchor.
                connection.settimeout(_REPLY_SECONDS)
                reader = connection.makefile("rb")
                writer = connection.makefile("wb")
                try:
                    code = _serve_connection(reader, writer, anchor, verifier, key,
                                             state_path)
                except OSError:
                    code = 0
                finally:
                    for stream in (reader, writer):
                        with suppress(OSError):
                            stream.close()
            stopping.busy = False
            if code or stopping.requested:
                return code
    finally:
        server.close()
        # Only the socket this process bound: never a path someone replaced.
        with suppress(OSError):
            current = os.stat(socket_path)
            if (current.st_dev, current.st_ino) == (bound.st_dev, bound.st_ino):
                os.unlink(socket_path)


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
        return self._exchange(_commit_request(head, records))

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


def _commit_request(head: AuditHead, records: Iterable[AuditRecord]) -> dict[str, Any]:
    """The commit as data, checked before anything leaves this process."""
    if not isinstance(head, AuditHead):
        _fail("head is invalid")
    chain = _bounded_chain(records, _count(head.count, "head.count") + 1)
    if not all(isinstance(record, AuditRecord) and isinstance(record.event, AuditEvent)
               for record in chain):
        _fail("records must be AuditRecord values carrying an AuditEvent")
    return {
        "head": {"count": head.count, "head_hash": head.head_hash,
                 "signature": head.signature, "version": head.version},
        "kind": "commit",
        "records": [record.to_dict() for record in chain],
    }


class AnchorClient(AuditAnchor):
    """The service's side of a served anchor: it asks, and believes only signed answers.

    Like `AnchorProcess` it does not call the base constructor, so there is no
    local state to fall back on. Each request opens its own connection, so an
    anchor restarted between two requests is simply asked again — and must
    answer with the same key, since the key is what the client trusts, not the
    path.
    """

    def __init__(self, *, socket_path: str | os.PathLike[str],
                 reply_public_key: bytes) -> None:
        self._socket_path = _absolute(socket_path, "socket_path")
        if type(reply_public_key) is not bytes or len(reply_public_key) != 32:
            _fail("reply_public_key must be 32 bytes")
        try:
            self._reply_key = Ed25519PublicKey.from_public_bytes(reply_public_key)
        except ValueError:
            raise ContractError("reply_public_key is not an Ed25519 public key") from None
        self._lock = Lock()

    @property
    def committed(self) -> tuple[int, str]:
        return self._exchange({"kind": "committed"})

    def commit(self, head: AuditHead, records: Iterable[AuditRecord], *,
               authority: AuditAuthority | AuditVerifier) -> tuple[int, str]:
        """As `AnchorProcess.commit`: the anchor checks with the key it was started with."""
        return self._exchange(_commit_request(head, records))

    def _exchange(self, request: dict[str, Any]) -> tuple[int, str]:
        nonce = os.urandom(_NONCE_BYTES).hex()
        frame = _frame({"nonce": nonce, "request": request}, _MAX_REQUEST_BYTES)
        with self._lock:
            try:
                data = _ask(self._socket_path, frame)
            except (_Unreachable, OSError):
                data = None
        if data is None:
            _fail("anchor service is unreachable")
        envelope = _decode(data, "anchor reply")
        if set(envelope) != _REPLY_KEYS:
            _fail("anchor reply envelope is malformed")
        # Compared before the signature, so a correctly signed reply to some
        # earlier request cannot stand in for this one.
        if envelope["nonce"] != nonce:
            _fail("anchor reply answers another request")
        if not self._signed(nonce, envelope["reply"], envelope["signature"]):
            _fail("anchor reply is not signed by the anchor")
        return _state(envelope["reply"])

    def _signed(self, nonce: str, reply: Any, signature: Any) -> bool:
        if not isinstance(reply, dict) or type(signature) is not str:
            return False
        try:
            self._reply_key.verify(bytes.fromhex(signature), _reply_message(nonce, reply))
        except (ValueError, InvalidSignature):
            return False
        return True


def _ask(path: str, frame: bytes) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(_REPLY_SECONDS)
        connection.connect(path)
        connection.sendall(frame)
        size = int.from_bytes(_receive(connection, 4), "big")
        if size > _MAX_REPLY_BYTES:
            raise _Unreachable()
        return _receive(connection, size)


def _receive(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    while size:
        chunk = connection.recv(size)
        if not chunk:
            raise _Unreachable()
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _send(process: subprocess.Popen[bytes], frame: bytes) -> bytes:
    _write_all(process.stdin.fileno(), frame)
    deadline = time.monotonic() + _REPLY_SECONDS
    size = int.from_bytes(_read_exact(process.stdout, 4, deadline), "big")
    if size > _MAX_REPLY_BYTES:
        raise _Unreachable()
    return _read_exact(process.stdout, size, deadline)


def _main(argv: list[str]) -> int:
    """Child mode by default; `serve` and `public-key` for an operator."""
    if not argv or argv[0] not in ("serve", "public-key"):
        return _serve(sys.stdin.buffer, sys.stdout.buffer, argv[0] if argv else None)
    parser = argparse.ArgumentParser(prog="python -m geniusnew.anchor_process")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the anchor on a Unix socket")
    for option in ("--socket", "--state", "--key", "--audit-public-key"):
        serve.add_argument(option, required=True)
    public = commands.add_parser("public-key", help="print the anchor's reply key")
    public.add_argument("--key", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "public-key":
            print(_public_bytes(_reply_key(_absolute(arguments.key, "key path"))).hex())
            return 0
        return _run_server(socket_path=arguments.socket, state_path=arguments.state,
                           key_path=arguments.key,
                           audit_public_key=arguments.audit_public_key)
    except ContractError as refusal:
        print(f"anchor refused to start: {refusal}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
