"""One bounded IPC exchange with a separately started inference process.

The service manager starts one broker instance for one accepted Unix-socket
connection. This module handles that connection; it neither starts a provider
client nor grants approval, reserves budget, or signs a result.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import socket
import struct
import time
from typing import Callable

from .contracts import ContractError, canonical
from .llm_contracts import BrokerRequest, BrokerResponse, Usage, decode_request, decode_response


_MAX_FRAME_BYTES = 32 * 1024
_FRAME_HEADER_BYTES = 4
_FAILED = canonical({"kind": "failed"})


def _fail(message: str) -> None:
    raise ContractError(message)


def _timeout(value: object) -> float:
    if (type(value) not in (int, float) or isinstance(value, bool)
            or not 0.01 <= float(value) <= 30.0):
        _fail("broker timeout is outside its allowed range")
    return float(value)


def _uid(value: object) -> int:
    if type(value) is not int or value < 0:
        _fail("broker peer uid is invalid")
    return value


def _peer_uid(connection: socket.socket) -> int:
    """Read Linux kernel credentials rather than trusting message fields."""
    if not hasattr(socket, "SO_PEERCRED"):
        _fail("broker peer credentials are unavailable")
    uid = None
    try:
        credentials = connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"))
        _pid, uid, _gid = struct.unpack("iII", credentials)
    except (OSError, struct.error):
        pass
    if uid is None:
        _fail("broker peer credentials are unavailable")
    return uid


def _read_exact(connection: socket.socket, size: int, deadline: float) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _fail("broker IPC timed out")
        chunk = None
        try:
            connection.settimeout(remaining)
            chunk = connection.recv(size - len(chunks))
        except (OSError, TimeoutError):
            pass
        if chunk is None:
            _fail("broker IPC failed")
        if not chunk:
            _fail("broker IPC ended before a complete frame")
        chunks.extend(chunk)
    return bytes(chunks)


def _read_frame(connection: socket.socket, deadline: float) -> bytes:
    header = _read_exact(connection, _FRAME_HEADER_BYTES, deadline)
    size = struct.unpack("!I", header)[0]
    if not 1 <= size <= _MAX_FRAME_BYTES:
        _fail("broker frame length is invalid")
    return _read_exact(connection, size, deadline)


def _write_frame(connection: socket.socket, data: bytes, deadline: float) -> None:
    if type(data) is not bytes or not 1 <= len(data) <= _MAX_FRAME_BYTES:
        _fail("broker frame length is invalid")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _fail("broker IPC timed out")
    sent = False
    try:
        connection.settimeout(remaining)
        connection.sendall(struct.pack("!I", len(data)) + data)
        sent = True
    except (OSError, TimeoutError):
        pass
    if not sent:
        _fail("broker IPC failed")


@dataclass(frozen=True, repr=False)
class ProviderResult:
    """Untrusted provider content plus separately reported usage."""

    text: str
    usage: Usage | None

    def __post_init__(self) -> None:
        # Reuse the response contract's strict size and type validation without
        # accepting provider-supplied job bindings.
        BrokerResponse("provider-result", "0" * 64, self.text, self.usage)

    def __repr__(self) -> str:
        return "ProviderResult(text=<redacted>)"


def invoke(socket_path: str, request: BrokerRequest, *, expected_uid: int,
           timeout_seconds: float) -> BrokerResponse:
    """Send one request and accept only a bounded response from the expected peer."""
    timeout = _timeout(timeout_seconds)
    expected_uid = _uid(expected_uid)
    if (type(socket_path) is not str or not os.path.isabs(socket_path)
            or "\x00" in socket_path):
        _fail("broker socket path is invalid")
    encoded_path = None
    try:
        encoded_path = os.fsencode(socket_path)
    except UnicodeEncodeError:
        pass
    if encoded_path is None or len(encoded_path) > 100:
        _fail("broker socket path is invalid")
    if not isinstance(request, BrokerRequest):
        _fail("broker request is invalid")
    remaining_ttl = request.expires_at - time.time()
    if remaining_ttl <= 0:
        _fail("broker request has expired")
    deadline = time.monotonic() + min(timeout, remaining_ttl)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connected = False
        try:
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.connect(socket_path)
            connected = True
        except (OSError, TimeoutError):
            pass
        if not connected:
            _fail("broker connection failed")
        if _peer_uid(connection) != expected_uid:
            _fail("broker peer uid does not match configuration")
        _write_frame(connection, request.to_bytes(), deadline)
        response_wire = _read_frame(connection, deadline)
        if response_wire == _FAILED:
            _fail("broker refused the request")
        response = decode_response(
            response_wire, job_id=request.job_id,
            handoff_sha256=request.handoff_sha256)
        if time.time() >= request.expires_at:
            _fail("broker response arrived after the request expired")
        return response
    finally:
        connection.close()


def serve_once(connection: socket.socket,
               provider: Callable[[BrokerRequest], ProviderResult], *,
               expected_uid: int, timeout_seconds: float) -> bool:
    """Handle one socket-activated call, returning false on any refusal.

    Provider exceptions and their text stay inside this process. A refusal has
    one fixed wire representation so it cannot become a second data channel.
    """
    timeout = _timeout(timeout_seconds)
    expected_uid = _uid(expected_uid)
    if not callable(provider):
        _fail("broker provider is invalid")
    deadline = time.monotonic() + timeout
    try:
        if _peer_uid(connection) != expected_uid:
            _fail("broker caller uid does not match configuration")
        request = decode_request(_read_frame(connection, deadline), now=int(time.time()))
        result = provider(request)
        if not isinstance(result, ProviderResult):
            _fail("broker provider result is invalid")
        if time.time() >= request.expires_at:
            _fail("broker request expired during execution")
        response = BrokerResponse(request.job_id, request.handoff_sha256,
                                  result.text, result.usage)
        _write_frame(connection, response.to_bytes(), deadline)
        return True
    except Exception:
        try:
            _write_frame(connection, _FAILED, deadline)
        except Exception:
            pass
        return False
