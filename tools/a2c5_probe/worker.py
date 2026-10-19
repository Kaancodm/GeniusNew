"""The worker side of the A2/C5 system probe.

systemd starts one instance per accepted connection (`Accept=yes`) under the
probe's worker UID and hands over the connection as fd 3. The instance checks
that the peer is the configured core UID, makes itself undumpable, answers
HELLO with READY and only then reads the request. Each scenario then behaves
the way a broken or hostile worker might: fragmenting READY, switching sender,
passing a descriptor, hanging, crashing, or leaving a process behind.

stdout and stderr go to /dev/null in the unit; nothing here writes a payload
anywhere. The only file it writes outside its job directory is a count of the
bytes it received after READY, so the driver can confirm that a refused
handshake delivered no payload.
"""

from __future__ import annotations

import ctypes
import json
import os
import resource
import signal
import socket
import sys
import time
from array import array
from typing import Any

from .contract import (
    BODY_MAX,
    HANDSHAKE_MAX,
    HELLO_FIELDS,
    REQUEST_FIELDS,
    ProbeRefused,
    canonical,
    check_request,
    classify_errno,
    decode_body,
    digest,
    encode_frame,
    frame_length,
    new_nonce,
    peer_credentials,
    ready,
    require_hardened,
    require_listen_fds,
    require_peer,
    result,
)

_PR_GET_DUMPABLE = 3
_PR_SET_DUMPABLE = 4
_HOLD_SECONDS = 2.0
_LINGER_SECONDS = 2.0
_READ_SECONDS = 5.0


def _recv_exact(conn: socket.socket, size: int, counter: list[int] | None = None) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            break
        if counter is not None:
            counter[0] += len(chunk)
        data += chunk
    return bytes(data)


def _harden() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    require_hardened(libc.prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0),
                     resource.getrlimit(resource.RLIMIT_CORE))


def _send_ready(conn: socket.socket, scenario: str, nonce: str) -> None:
    frame = encode_frame(ready(nonce), HANDSHAKE_MAX)
    if scenario == "fragmented":
        # Split the length prefix itself, then the body, with pauses so that
        # the core sees several chunks, each with its own kernel credential.
        for start, end in ((0, 2), (2, 4), (4, 20), (20, 40), (40, len(frame))):
            conn.sendall(frame[start:end])
            time.sleep(0.05)
        return
    if scenario == "sender":
        # Same UID, another process: the second half must not count as ours.
        half = len(frame) // 2
        child = os.fork()
        if child == 0:
            try:
                conn.sendall(frame[:half])
            finally:
                os._exit(0)
        os.waitpid(child, 0)
        time.sleep(0.05)
        conn.sendall(frame[half:])
        return
    if scenario == "rights":
        descriptor = os.open(os.devnull, os.O_RDONLY)
        try:
            conn.sendmsg([frame], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                    array("i", [descriptor]))])
        finally:
            os.close(descriptor)
        return
    conn.sendall(frame)


def _escape() -> None:
    """Leave a process behind in a new session that ignores SIGTERM and SIGHUP."""
    child = os.fork()
    if child == 0:
        try:
            os.setsid()
            if os.fork() != 0:
                os._exit(0)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            os.closerange(0, 1024)
            time.sleep(3600)
        finally:
            os._exit(0)
    os.waitpid(child, 0)


def _attempt(target: dict[str, str]) -> dict[str, str]:
    """Try one target by Python and by libc directly; record only the class."""
    path, kind = target["path"], target["kind"]
    outcome: dict[str, str] = {}
    if kind == "connect":
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(path)
            outcome["python"] = classify_errno(None)
        except OSError as exc:
            outcome["python"] = classify_errno(exc.errno)
        finally:
            probe.close()
        return outcome
    try:
        if kind == "dir":
            os.listdir(path)
        else:
            os.close(os.open(path, os.O_RDONLY | os.O_CLOEXEC))
        outcome["python"] = classify_errno(None)
    except OSError as exc:
        outcome["python"] = classify_errno(exc.errno)
    libc = ctypes.CDLL(None, use_errno=True)
    flags = os.O_RDONLY | os.O_CLOEXEC | (os.O_DIRECTORY if kind == "dir" else 0)
    descriptor = libc.open(os.fsencode(path), flags)
    if descriptor >= 0:
        os.close(descriptor)
        outcome["libc"] = classify_errno(None)
    else:
        outcome["libc"] = classify_errno(ctypes.get_errno())
    return outcome


def _identity() -> dict[str, Any]:
    with open("/proc/self/status", encoding="ascii") as handle:
        status = dict(line.rstrip("\n").split(":\t", 1) for line in handle if ":\t" in line)
    libc = ctypes.CDLL(None, use_errno=True)
    return {
        "resuid": list(os.getresuid()),
        "resgid": list(os.getresgid()),
        "groups": sorted(os.getgroups()),
        "cap_effective_zero": int(status.get("CapEff", "1"), 16) == 0,
        "cap_permitted_zero": int(status.get("CapPrm", "1"), 16) == 0,
        "no_new_privs": status.get("NoNewPrivs") == "1",
        "dumpable": libc.prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0),
    }


def _observe(config: dict[str, Any], scenario: str, values: dict[str, Any]) -> None:
    directory = os.path.join(config["observe"], scenario)
    name = os.path.join(directory, f"{os.getpid()}.json")
    with open(name, "wb") as handle:
        handle.write(canonical(values))


def serve(config: dict[str, Any], scenario: str, conn: socket.socket) -> None:
    peer = peer_credentials(conn)
    try:
        require_peer(peer, config["uids"]["core"])
    except ProbeRefused:
        return  # say nothing to a stranger
    _harden()
    early_nonce = new_nonce()
    if scenario == "early":
        conn.sendall(encode_frame(ready(early_nonce), HANDSHAKE_MAX))
    conn.settimeout(_READ_SECONDS)
    header = _recv_exact(conn, 4)
    greeting = decode_body(_recv_exact(conn, frame_length(header, HANDSHAKE_MAX)),
                           "hello", HELLO_FIELDS)
    nonce = greeting["nonce"]
    if scenario != "early":
        _send_ready(conn, scenario, nonce)

    received = [0]
    observation = {"bytes_after_ready": 0, "request_read": False}
    try:
        header = _recv_exact(conn, 4, received)
        body = b""
        if len(header) == 4:
            body = _recv_exact(conn, frame_length(header, BODY_MAX), received)
        text = check_request(decode_body(body, "request", REQUEST_FIELDS), nonce)
        observation["request_read"] = True
    finally:
        observation["bytes_after_ready"] = received[0]
        _observe(config, scenario, observation)

    runtime = os.environ.get("RUNTIME_DIRECTORY", "")
    if runtime:
        with open(os.path.join(runtime, "residue"), "wb") as handle:
            handle.write(b"job residue without payload\n")

    request_digest = digest(body)
    output: dict[str, Any] = {"length": len(text)}
    if scenario == "access":
        targets = dict(config["targets"])
        targets["core_environ"] = {"path": f"/proc/{peer.pid}/environ", "kind": "file"}
        output = {
            "targets": {name: _attempt(target) for name, target in sorted(targets.items())},
            "identity": _identity(),
        }
    if scenario == "hold":
        time.sleep(_HOLD_SECONDS)
    if scenario == "hang":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        _escape()
        time.sleep(3600)
    if scenario == "crash":
        _escape()
        os.abort()
    conn.sendall(encode_frame(result(nonce, request_digest, "ok", output), BODY_MAX))
    if scenario == "linger":
        conn.close()
        _escape()
        time.sleep(_LINGER_SECONDS)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 2
    with open(argv[1], "rb") as handle:
        config = json.loads(handle.read())
    require_listen_fds(os.environ, os.getpid())
    conn = socket.socket(fileno=3)
    try:
        serve(config, argv[2], conn)
    finally:
        try:
            conn.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
