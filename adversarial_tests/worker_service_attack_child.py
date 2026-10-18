"""Test-only payloads; no installed service, units, or credentials are used."""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import sys
import time

# -I intentionally excludes the checkout and tests from the import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geniusnew.worker_service as service


class Attack:
    """Preload native call targets before the actual service installs its sandbox."""

    def __init__(self, mode, target, port):
        self.mode = mode
        self.path = os.fsencode(target)
        libc = ctypes.CDLL(None)
        self.open = libc.open
        self.open.argtypes = (ctypes.c_char_p, ctypes.c_int, ctypes.c_uint)
        self.open.restype = ctypes.c_int
        self.close = libc.close
        self.close.argtypes = (ctypes.c_int,)
        self.socket = libc.socket
        self.socket.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int)
        self.connect = libc.connect
        self.connect.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
        self.fork = libc.fork
        self.fork.restype = ctypes.c_int
        location = getattr(libc, "__errno_location")
        location.restype = ctypes.POINTER(ctypes.c_int)
        self.errno = location()
        # sockaddr_in layout: native family, network-order port and address.
        address = struct.pack("=H", socket.AF_INET) + struct.pack("!H", port)
        address += socket.inet_aton("127.0.0.1") + b"\0" * 8
        self.address = ctypes.create_string_buffer(address)
        self.pointer = ctypes.addressof(self.address)

    def run(self, payload):
        if self.mode == "baseline":
            return {"text": "FIXTURE_OK"}
        if self.mode == "python_read":
            with open(os.fsdecode(self.path), "rb") as handle:
                handle.read(1)
            return {"text": "ESCAPED"}
        if self.mode in ("native_read", "native_write"):
            flags = os.O_RDONLY if self.mode == "native_read" else os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = self.open(self.path, flags | os.O_CLOEXEC, 0o600)
            number = self.errno[0]
            if descriptor >= 0:
                self.close(descriptor)
                return {"text": "NATIVE_ALLOWED"}
            return {"text": "NATIVE_DENIED:" + str(number)}
        if self.mode == "python_network":
            with socket.socket():
                pass
            return {"text": "ESCAPED"}
        if self.mode == "native_tcp":
            descriptor = self.socket(socket.AF_INET, socket.SOCK_STREAM | socket.SOCK_CLOEXEC, 0)
            if descriptor < 0:
                return {"text": "SOCKET_INCONCLUSIVE"}
            try:
                result = self.connect(descriptor, self.pointer, 16)
                number = self.errno[0]
            finally:
                self.close(descriptor)
            return {"text": "NATIVE_ALLOWED" if result == 0 else "NATIVE_DENIED:" + str(number)}
        if self.mode == "native_fork":
            child = self.fork()
            if child == 0:
                os._exit(71)
            if child < 0:
                return {"text": "FORK_INCONCLUSIVE"}
            _, status = os.waitpid(child, 0)
            return {"text": "NATIVE_ALLOWED" if os.waitstatus_to_exitcode(status) == 71 else "FORK_INCONCLUSIVE"}
        raise ValueError("unknown test payload")


def response_fixture(connection, mode):
    """Malicious peer fixture; the receiving Core parser remains unmodified."""
    descriptor = connection.fileno()
    deadline = time.monotonic() + 5
    hello = service._plain_frame(descriptor, limit=256, deadline=deadline)
    nonce = service._validate_hello(hello)
    service._plain_write(descriptor, {"kind": "READY", "version": 1, "nonce": nonce}, deadline)
    request = service._plain_frame(descriptor, limit=32768, deadline=deadline)
    service._plain_expect_eof(descriptor, deadline)
    if mode == "fixture_crash":
        raise SystemExit(71)
    result = {"kind": "RESULT", "version": 1, "nonce": nonce,
              "request_digest": hashlib.sha256(service.canonical(request)).hexdigest(),
              "output": {"text": "FIXTURE_OK"}}
    if mode == "nonce":
        result["nonce"] = "x" * 64
    elif mode == "digest":
        result["request_digest"] = "x" * 64
    elif mode == "extra":
        result["extra"] = True
    elif mode == "output":
        result["output"] = {"text": 42}
    elif mode == "error":
        result.pop("output")
        result["error"] = "UNTRUSTED_ERROR"
    raw = service.canonical(result)
    if mode == "noncanonical":
        raw = json.dumps(result).encode("ascii")
    if mode == "duplicate":
        raw = b'{"kind":"RESULT",' + raw[1:]
    if mode == "oversized":
        connection.sendall(struct.pack("!I", 32769))
        return
    frame = struct.pack("!I", len(raw)) + raw
    if mode == "truncated":
        frame = frame[:-3]
    connection.sendall(frame)
    if mode == "trailing":
        connection.sendall(b"!")


def main():
    kind, mode, root, target, port = sys.argv[1:]
    if os.geteuid() == 0 or kind not in ("control", "sandbox", "response"):
        return 2
    if kind == "control":
        print(json.dumps(Attack(mode, target, int(port)).run({})), flush=True)
        return 0
    with socket.socket(fileno=0) as connection:
        if kind == "response":
            response_fixture(connection, mode)
            return 0
        attack = Attack(mode, target, int(port))
        # Only the systemd directory and payload factory are fixture seams.
        # The actual nonroot UID/group, NNP and every capability check stay active.
        service._worker_root = lambda *_: root
        service.DeterministicSummarizer = lambda: attack
        service._run_service_connection(connection, core_uid=os.geteuid(),
                                        worker_uid=os.geteuid(), worker_gid=os.getegid())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
