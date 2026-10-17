"""Decisions of the A2/C5 system probe, kept apart from its I/O.

The probe (`docs/A2-C5-SYSTEM-PROBE.md`) runs a core and a worker under two
real UIDs behind temporary systemd units and answers each question with PASS,
FAIL or HOLD. Everything that decides lives here, so the unit tests can drive
every refusal without root or systemd; `core.py`, `worker.py` and `driver.py`
only read, send and spawn.

The rule this module enforces: an answer rests only on states that were read
and have a known shape. A state that could not be read, or reads as something
unexpected, refuses. The caller reports that refusal as HOLD, never as an empty
cgroup, an ended job or a denied read.

Stdlib only, no import from `geniusnew`: the driver copies this package to a
root-owned path that the probe's core and worker UIDs execute, so it must not
depend on the checkout being readable to them. It is a probe of the operating
system boundary from PR #140, not the runtime implementation of it.
"""

from __future__ import annotations

import array
import hashlib
import json
import os
import re
import secrets
import socket
import struct
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

PASS, FAIL, HOLD = "PASS", "FAIL", "HOLD"
VERDICTS = (PASS, FAIL, HOLD)

VERSION = 1
HANDSHAKE_MAX = 256
BODY_MAX = 32 * 1024
TEXT_MAX = 16 * 1024
HELLO_FIELDS = frozenset({"kind", "nonce", "version"})
READY_FIELDS = HELLO_FIELDS
REQUEST_FIELDS = frozenset({"kind", "nonce", "version", "text"})
RESULT_FIELDS = frozenset({"kind", "nonce", "version", "digest", "status", "output"})
RESULT_STATUSES = frozenset({"ok", "rejected"})

CREDENTIALS_FORMAT = "iII"  # struct ucred: pid_t, uid_t, gid_t
CREDENTIALS_SIZE = struct.calcsize(CREDENTIALS_FORMAT)
# Room for the credential plus a few passed descriptors, so that a sender who
# attaches SCM_RIGHTS is seen and refused rather than hidden by truncation.
ANCILLARY_SPACE = socket.CMSG_SPACE(CREDENTIALS_SIZE) + socket.CMSG_SPACE(16 * 4)

UNIT_ENDED = frozenset({"inactive", "failed"})
UNIT_RUNNING = frozenset({"active", "activating", "deactivating", "reloading", "refreshing"})
UNIT_LOAD_STATES = frozenset({"loaded", "not-found"})
UNIT_PROPERTIES = ("ActiveState", "ControlGroup", "InvocationID", "LoadState", "MainPID")

ABSENT, EMPTY, POPULATED, PRESENT, UNKNOWN = "absent", "empty", "populated", "present", "unknown"

DENIED, READABLE, MISSING, ERROR = "denied", "readable", "missing", "error"
ACCESS_CLASSES = frozenset({DENIED, READABLE, MISSING, ERROR})

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_SAFE_TEXT = re.compile(r"[A-Za-z0-9 _.,:;@/+()=\\-]{0,200}")
_MAX_REPORT_DEPTH = 6


class ProbeRefused(Exception):
    """A message or a host state the probe does not accept."""


def _fail(message: str) -> None:
    raise ProbeRefused(message)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _reject_constant(_: str) -> None:
    raise ValueError("JSON constants are not canonical")


def _loads(data: bytes) -> Any:
    try:
        return json.loads(data.decode("ascii"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        return None


def new_nonce() -> str:
    return secrets.token_hex(32)


# --- Framing -----------------------------------------------------------------

def encode_frame(value: Mapping[str, Any], limit: int) -> bytes:
    body = canonical(dict(value))
    if len(body) > limit:
        _fail("outgoing frame is too large")
    return struct.pack("!I", len(body)) + body


def frame_length(header: bytes, limit: int) -> int:
    if len(header) != 4:
        _fail("frame header is truncated")
    (length,) = struct.unpack("!I", header)
    if length == 0:
        _fail("frame is empty")
    if length > limit:
        _fail("incoming frame is too large")
    return length


def decode_body(data: bytes, kind: str, fields: frozenset[str]) -> dict[str, Any]:
    value = _loads(data)
    if not isinstance(value, dict):
        _fail("frame is not a JSON object")
    if canonical(value) != data:
        _fail("frame is not canonical JSON")
    if set(value) != fields:
        _fail("frame has unexpected fields")
    if value["kind"] != kind:
        _fail("frame has an unexpected kind")
    if value["version"] != VERSION or type(value["version"]) is not int:
        _fail("frame has an unsupported version")
    if type(value["nonce"]) is not str or not _HEX64.fullmatch(value["nonce"]):
        _fail("nonce must be 64 lowercase hex characters")
    return value


def hello(nonce: str) -> dict[str, Any]:
    return {"kind": "hello", "nonce": nonce, "version": VERSION}


def ready(nonce: str) -> dict[str, Any]:
    return {"kind": "ready", "nonce": nonce, "version": VERSION}


def request(nonce: str, text: str) -> dict[str, Any]:
    return {"kind": "request", "nonce": nonce, "text": text, "version": VERSION}


def result(nonce: str, digest: str, status: str, output: Mapping[str, Any]) -> dict[str, Any]:
    return {"digest": digest, "kind": "result", "nonce": nonce, "output": dict(output),
            "status": status, "version": VERSION}


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def require_nonce(value: Mapping[str, Any], nonce: str) -> None:
    if value["nonce"] != nonce:
        _fail("nonce does not match this handshake")


def check_request(value: Mapping[str, Any], nonce: str) -> str:
    require_nonce(value, nonce)
    if type(value["text"]) is not str or len(value["text"]) > TEXT_MAX:
        _fail("request text has the wrong shape")
    return value["text"]


def check_result(value: Mapping[str, Any], nonce: str, request_digest: str) -> dict[str, Any]:
    require_nonce(value, nonce)
    if value["digest"] != request_digest:
        _fail("result is not bound to this request")
    if value["status"] not in RESULT_STATUSES:
        _fail("result status is outside the closed set")
    if not isinstance(value["output"], dict):
        _fail("result output must be an object")
    return value["output"]


# --- Kernel credentials --------------------------------------------------------

@dataclass(frozen=True)
class Credentials:
    pid: int
    uid: int
    gid: int


def close_passed_descriptors(ancdata: Iterable[tuple[int, int, bytes]]) -> int:
    """Close every descriptor a sender passed, so that a refusal leaks none."""
    count = 0
    for level, kind, data in ancdata:
        if (level, kind) != (socket.SOL_SOCKET, socket.SCM_RIGHTS):
            continue
        descriptors = array.array("i")
        descriptors.frombytes(data[:len(data) - len(data) % descriptors.itemsize])
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass
            count += 1
    return count


def credentials_from(ancdata: list[tuple[int, int, bytes]], flags: int) -> Credentials:
    """Return the kernel's sender credentials for one received chunk.

    Data a sender wrote before the reader set SO_PASSCRED carries no sender:
    the kernel reports pid 0 and the overflow UID. That is refused here, so a
    READY written before the handshake cannot borrow the worker's identity.
    """
    passed = close_passed_descriptors(ancdata)
    if passed:
        _fail("descriptor passing is refused")
    if flags & socket.MSG_CTRUNC:
        _fail("control data was truncated")
    if any((level, kind) != (socket.SOL_SOCKET, socket.SCM_CREDENTIALS)
           for level, kind, _ in ancdata):
        _fail("control message is of an unknown type")
    if len(ancdata) != 1:
        _fail("every chunk needs exactly one kernel credential")
    if len(ancdata[0][2]) != CREDENTIALS_SIZE:
        _fail("kernel credential has the wrong size")
    pid, uid, gid = struct.unpack(CREDENTIALS_FORMAT, ancdata[0][2])
    if pid <= 0:
        _fail("data carries no sender process")
    return Credentials(pid, uid, gid)


def peer_credentials(sock: socket.socket) -> Credentials:
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, CREDENTIALS_SIZE)
    return Credentials(*struct.unpack(CREDENTIALS_FORMAT, raw))


class CredentialStream:
    """Read exact byte counts, checking the sender's credentials on every chunk.

    The kernel never merges stream data from two writers into one recvmsg, so
    checking each chunk binds every byte to one process. The first chunk binds
    the sender; any later chunk from another process, UID or GID refuses, also
    inside a frame that arrives in fragments.
    """

    def __init__(self, sock: Any, *, uid: int, gid: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._sock = sock
        self._uid = uid
        self._gid = gid
        self._clock = clock
        self.sender: Credentials | None = None
        self.chunks = 0

    def _receive(self, size: int, deadline: float) -> tuple[bytes, list, int]:
        remaining = deadline - self._clock()
        if remaining <= 0:
            _fail("deadline passed while waiting for the worker")
        self._sock.settimeout(remaining)
        try:
            chunk, ancdata, flags, _ = self._sock.recvmsg(size, ANCILLARY_SPACE)
        except TimeoutError:
            chunk, ancdata, flags = None, [], 0
        if chunk is None:
            _fail("deadline passed while waiting for the worker")
        return chunk, ancdata, flags

    def _accept(self, sender: Credentials) -> None:
        if sender.uid != self._uid:
            _fail("sender is not the configured worker UID")
        if sender.gid != self._gid:
            _fail("sender is not the configured worker GID")
        if self.sender is not None and sender != self.sender:
            _fail("sender changed inside the stream")
        self.sender = sender
        self.chunks += 1

    def read_exact(self, size: int, deadline: float) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk, ancdata, flags = self._receive(size - len(data), deadline)
            if not chunk:
                close_passed_descriptors(ancdata)
                _fail("stream ended inside a frame")
            self._accept(credentials_from(ancdata, flags))
            data += chunk
        return bytes(data)

    def read_frame(self, limit: int, deadline: float) -> bytes:
        return self.read_exact(frame_length(self.read_exact(4, deadline), limit), deadline)

    def expect_eof(self, deadline: float) -> None:
        chunk, ancdata, _ = self._receive(1, deadline)
        close_passed_descriptors(ancdata)
        if chunk:
            _fail("worker sent bytes after its last frame")


# --- Worker bootstrap ----------------------------------------------------------

def require_listen_fds(environ: Mapping[str, str], pid: int) -> None:
    if environ.get("LISTEN_PID") != str(pid):
        _fail("socket activation is not meant for this process")
    if environ.get("LISTEN_FDS") != "1":
        _fail("exactly one accepted connection is expected")


def require_peer(peer: Credentials, uid: int) -> None:
    if peer.pid <= 0:
        _fail("connection has no peer process")
    if peer.uid != uid:
        _fail("peer is not the configured core UID")


def require_hardened(dumpable: int, core_limit: tuple[int, int]) -> None:
    if dumpable != 0:
        _fail("worker is still dumpable")
    if tuple(core_limit) != (0, 0):
        _fail("worker may still write a core dump")


# --- Lifecycle -------------------------------------------------------------------

def require_text(value: str | None) -> str:
    if value is None:
        _fail("systemd state is unknown")
    return value


def cgroup_populated(events: str) -> bool:
    fields = dict(line.split(" ", 1) for line in events.splitlines() if " " in line)
    if fields.get("populated") not in ("0", "1"):
        _fail("cgroup.events state is unknown")
    return fields["populated"] == "1"


def unit_of_process(proc_cgroup: str, slice_name: str, unit: re.Pattern[str]) -> tuple[str, str]:
    """Return (cgroup path, unit) of a worker process from /proc/<pid>/cgroup."""
    unified = [line[3:] for line in proc_cgroup.splitlines() if line.startswith("0::")]
    if len(unified) != 1:
        _fail("process is not in exactly one unified cgroup")
    parts = unified[0].split("/")
    if len(parts) != 3 or parts[0] != "" or parts[1] != slice_name:
        _fail("process is outside the worker slice")
    if not unit.fullmatch(parts[2]):
        _fail("process is not in a worker instance unit")
    return unified[0], parts[2]


def parse_properties(text: str, names: Iterable[str] = UNIT_PROPERTIES) -> dict[str, str]:
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    if not set(names) <= set(values):
        _fail("unit properties are incomplete")
    return {name: values[name] for name in names}


def bind_invocation(properties: Mapping[str, str], pid: int, cgroup_path: str) -> str:
    """Bind the READY sender to one running unit invocation, or refuse."""
    if properties["LoadState"] != "loaded":
        _fail("worker unit is not loaded")
    if properties["ActiveState"] != "active":
        _fail("worker unit is not active")
    if properties["MainPID"] != str(pid):
        _fail("sender is not the unit's main process")
    if properties["ControlGroup"] != cgroup_path:
        _fail("unit cgroup does not match the sender")
    if not _HEX32.fullmatch(properties["InvocationID"]):
        _fail("worker unit has no invocation ID")
    return properties["InvocationID"]


def unit_has_ended(properties: Mapping[str, str], invocation: str) -> bool:
    load, active = properties["LoadState"], properties["ActiveState"]
    if load not in UNIT_LOAD_STATES:
        _fail("unit load state is unknown")
    if active not in UNIT_ENDED | UNIT_RUNNING:
        _fail("unit active state is unknown")
    if load == "loaded" and properties["InvocationID"] not in ("", invocation):
        _fail("unit name now belongs to another invocation")
    return active in UNIT_ENDED


def end_confirmed(unit_ended: bool, cgroup: str, job_directory: str) -> bool:
    """True only when systemd, the kernel cgroup and the job path all agree."""
    if cgroup not in (ABSENT, EMPTY, POPULATED):
        _fail("unit cgroup state is unknown")
    if job_directory not in (ABSENT, PRESENT):
        _fail("job directory state is unknown")
    return unit_ended and cgroup != POPULATED and job_directory == ABSENT


def parse_unit_list(text: str) -> list[tuple[str, str]]:
    """Return (unit, active state) from `systemctl list-units --plain --no-legend`."""
    units = []
    for line in text.splitlines():
        tokens = line.split()
        if tokens and tokens[0] in ("●", "*"):
            tokens = tokens[1:]
        if not tokens:
            continue
        if len(tokens) < 4:
            _fail("unit list has an unknown shape")
        units.append((tokens[0], tokens[2]))
    return units


def require_idle(slice_state: str, units: Iterable[tuple[str, str]],
                 job_entries: list[str] | None) -> None:
    """Refuse a connect unless no earlier worker instance lives or awaits cleanup.

    A missing lock or a fresh socket says nothing about this; only the slice's
    kernel cgroup, systemd's unit states and the managed job paths do.
    """
    if slice_state not in (ABSENT, EMPTY, POPULATED):
        _fail("worker slice state is unknown")
    if slice_state == POPULATED:
        _fail("a worker process is still alive")
    states = [active for _, active in units]
    if any(active not in UNIT_ENDED | UNIT_RUNNING for active in states):
        _fail("worker instance state is unknown")
    if any(active not in UNIT_ENDED for active in states):
        _fail("a worker instance has not ended")
    if job_entries is None:
        _fail("job directory listing is unknown")
    if job_entries:
        _fail("cleanup of an earlier job is pending")


# --- Findings and report ---------------------------------------------------------

def classify_errno(error: int | None) -> str:
    """Map an access attempt to a closed class; only EACCES/EPERM count as denied."""
    import errno as errno_codes

    if error is None:
        return READABLE
    if error in (errno_codes.EACCES, errno_codes.EPERM):
        return DENIED
    if error == errno_codes.ENOENT:
        return MISSING
    return ERROR


def access_verdict(observed: Mapping[str, Any], expected_denied: Iterable[str]) -> str:
    names = list(expected_denied)
    values = [observed.get(name) for name in names]
    if not names:
        return HOLD
    if READABLE in values:
        return FAIL
    if all(value == DENIED for value in values):
        return PASS
    return HOLD


def overall(verdicts: Iterable[str]) -> str:
    """PASS only for a non-empty set of PASS; a FAIL wins over a HOLD."""
    values = list(verdicts)
    if not values or any(value not in VERDICTS for value in values):
        return HOLD
    if FAIL in values:
        return FAIL
    if HOLD in values:
        return HOLD
    return PASS


def safe_text(value: str, limit: int = 120) -> str:
    """Host facts go into the report; keep them inside the report's alphabet."""
    cleaned = "".join(char if _SAFE_TEXT.fullmatch(char) else "_" for char in str(value))
    return cleaned[:limit]


def require_safe_observations(value: Any, depth: int = 0) -> None:
    """Refuse report content outside small numbers, flags and short plain text."""
    if depth > _MAX_REPORT_DEPTH:
        _fail("report nesting is too deep")
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str or not _SAFE_TEXT.fullmatch(key):
                _fail("report key has an unsafe shape")
            require_safe_observations(item, depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            require_safe_observations(item, depth + 1)
        return
    if isinstance(value, str):
        if not _SAFE_TEXT.fullmatch(value):
            _fail("report text has an unsafe shape")
        return
    if value is not None and type(value) not in (bool, int, float):
        _fail("report holds an unsupported value")


def require_no_secret(text: str, values: Iterable[str]) -> None:
    leaked = [value for value in values if value and value in text]
    if leaked:
        _fail("report would contain a secret or payload value")
