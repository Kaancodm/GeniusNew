"""One-job AF_UNIX transport for a separately managed worker UID.

The Core signs only after the complete response and server EOF are authenticated.
The service process receives no signing key, Handoff, permit, database credentials,
or anchor state. A systemd instance and its cleanup remain separate prerequisites.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from dataclasses import asdict
import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import select
import socket
import stat
import struct
import subprocess
import sys
import sysconfig
import time
from typing import Any

from .contracts import ContractError, canonical
from .isolation import IsolationLimits
from .isolation import (_SandboxDenied, _audit_hook, _install_filesystem_rules,
                        _install_process_filter, _set_resource_limits)
from .results import WorkerAuthority
from .workers import (DeterministicSummarizer, WorkerBoundaryError, WorkerRunner,
                      _WorkerIsolationViolation, _WorkerResourceExhausted)
from .worker_bootstrap import runtime_paths as _python_runtime_paths

_VERSION = 1
_HELLO_LIMIT = 256
_PAYLOAD_LIMIT = 32 * 1024
_SO_PASSPIDFD = 76
_SCM_PIDFD = 4
_UCREDS = struct.Struct("3i")
_FD = struct.Struct("i")
_HEADER = struct.Struct("!I")
_HOST_REFERENCE = "/run/geniusnew-worker/ipc/host-state.json"
_HOST_NAMESPACES = ("mnt", "pid", "user", "cgroup")
_BOOTSTRAP = str(Path(__file__).with_name("worker_bootstrap.py"))
_LOADER_CACHE = Path("/etc/ld.so.cache")
_LOADER_PRELOAD = Path("/etc/ld.so.preload")
_LOADER_ENVIRONMENT = (
    "LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT LD_DEBUG LD_DEBUG_OUTPUT LD_DYNAMIC_WEAK "
    "LD_BIND_NOT LD_BIND_NOW LD_HWCAP_MASK LD_ORIGIN_PATH LD_PROFILE LD_PROFILE_OUTPUT "
    "LD_SHOW_AUXV LD_TRACE_LOADED_OBJECTS LD_USE_LOAD_BIAS LD_VERBOSE LD_WARN "
    "LD_PREFER_MAP_32BIT_EXEC GLIBC_TUNABLES GCONV_PATH LOCPATH"
)
_CONTROL_BYTES = (socket.CMSG_SPACE(_UCREDS.size)
                  + socket.CMSG_SPACE(_FD.size * 8)
                  + socket.CMSG_SPACE(_FD.size))


def _fail(message: str) -> None:
    """Raise a contract refusal without producing a worker outcome."""
    raise ContractError(message)


@dataclass(frozen=True)
class WorkerServiceProfile:
    """Fixed numeric identities and root-installed paths for one worker UID."""

    core_uid: int
    worker_uid: int
    worker_gid: int
    socket_gid: int
    socket_path: str
    lease_path: str
    unit_prefix: str = "geniusnew-worker@"
    runtime_root: str = "/run/geniusnew-worker/jobs"
    phase_seconds: float = 5.0
    total_seconds: float = 30.0

    def __post_init__(self) -> None:
        """Reject unsafe identities, paths, unit names, or deadline settings.

        IDs must be positive, Core and worker UIDs distinct, and worker and
        socket GIDs distinct. Paths must be normalized under /run. Phase
        timeouts span 0.1 through 30 seconds; the total must exceed the phase
        timeout and be at most 120 seconds.
        """
        ids = (self.core_uid, self.worker_uid, self.worker_gid, self.socket_gid)
        if any(type(value) is not int or value <= 0 for value in ids):
            _fail("worker service identities must be positive numeric IDs")
        if self.core_uid == self.worker_uid:
            _fail("Core and worker service must have different UIDs")
        if self.worker_gid == self.socket_gid:
            _fail("worker must not be a member of the Core-only socket group")
        for path in (self.socket_path, self.lease_path, self.runtime_root):
            if (type(path) is not str or not path.startswith("/run/")
                    or os.path.normpath(path) != path):
                _fail("worker service paths must be canonical paths under /run")
        if self.socket_path == self.lease_path:
            _fail("worker socket and lease must be different files")
        if self.unit_prefix != "geniusnew-worker@":
            _fail("worker service unit prefix is invalid")
        if self.runtime_root != "/run/geniusnew-worker/jobs":
            _fail("worker service runtime root is invalid")
        if (type(self.phase_seconds) not in (int, float)
                or not 0.1 <= self.phase_seconds <= 30):
            _fail("worker service phase deadline is invalid")
        if (type(self.total_seconds) not in (int, float)
                or not self.phase_seconds < self.total_seconds <= 120):
            _fail("worker service total deadline is invalid")


def _pidfd_pid(fd: int) -> int:
    """Read the unique numeric PID reported by a kernel pidfd."""
    try:
        with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as handle:
            fields = handle.read().splitlines()
    except OSError as exc:
        raise ContractError("worker pidfd cannot be inspected") from exc
    pids = [line.split(":", 1)[1].strip() for line in fields if line.startswith("Pid:")]
    if len(pids) != 1 or not pids[0].isdigit():
        _fail("worker pidfd has no unambiguous PID")
    return int(pids[0])


def _pidfd_exited(fd: int) -> bool:
    """Return whether the pidfd signals exit, refusing ambiguous poll states."""
    try:
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        events = poller.poll(0)
    except (OSError, ValueError) as exc:
        raise ContractError("worker pidfd state is unreadable") from exc
    if any(mask & (select.POLLERR | select.POLLNVAL) for _, mask in events):
        _fail("worker pidfd state is unreadable")
    if any(mask & select.POLLHUP and not mask & select.POLLIN for _, mask in events):
        _fail("worker pidfd exit state is ambiguous")
    return any(mask & select.POLLIN for _, mask in events)


@dataclass
class FrameIdentity:
    """Kernel-provided identity bound to every recvmsg of one server response."""

    expected_uid: int
    expected_gid: int
    pid: int | None = None
    pidfd: int | None = None

    def close(self) -> None:
        """Close and clear the retained sender pidfd, if one is bound."""
        if self.pidfd is not None:
            os.close(self.pidfd)
            self.pidfd = None

    def accept(self, data: bytes, controls: list[tuple[int, int, bytes]], flags: int) -> None:
        """Authenticate fragment controls and retain only the first valid pidfd.

        The first data fragment binds a live PID/pidfd to the expected UID/GID;
        later fragments must retain that identity. EOF requires a prior binding
        and permits no pidfd. Retain the first valid pidfd for `close()` and
        close other received descriptors, including forbidden passed FDs.
        """
        credentials: list[bytes] = []
        pidfds: list[bytes] = []
        owned_fds: list[int] = []
        unexpected = False
        try:
            for level, kind, value in controls:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    # recvmsg has already installed every complete FD in our table.
                    unexpected = True
                    if len(value) % _FD.size:
                        unexpected = True
                    for offset in range(0, len(value) - len(value) % _FD.size, _FD.size):
                        received = _FD.unpack_from(value, offset)[0]
                        if received >= 0:
                            owned_fds.append(received)
                elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                    credentials.append(value)
                elif level == socket.SOL_SOCKET and kind == _SCM_PIDFD:
                    pidfds.append(value)
                    if len(value) == _FD.size:
                        received = _FD.unpack(value)[0]
                        if received >= 0:
                            owned_fds.append(received)
                else:
                    unexpected = True
            if not data:
                # SO_PASSCRED may attach a zero-filled SCM_CREDENTIALS record
                # to stream EOF; it never supplies a new sender identity.
                valid_eof_credential = (not credentials or
                    (len(credentials) == 1 and len(credentials[0]) == _UCREDS.size
                     and _UCREDS.unpack(credentials[0]) in (
                         (0, 0, 0),
                         (self.pid, self.expected_uid, self.expected_gid))))
                if (self.pid is None or flags or unexpected or pidfds
                        or not valid_eof_credential):
                    _fail("worker EOF has invalid controls")
                return
            if flags or unexpected:
                _fail("worker control message is truncated or forbidden")
            if len(credentials) != 1 or len(credentials[0]) != _UCREDS.size:
                _fail("worker credentials are missing or ambiguous")
            peer = _UCREDS.unpack(credentials[0])
            if (peer[0] <= 0 or peer[1] != self.expected_uid
                    or peer[2] != self.expected_gid
                    or (self.pid is not None and peer[0] != self.pid)):
                _fail("worker credentials changed or do not match the fixed UID/GID")
            if len(pidfds) != 1 or len(pidfds[0]) != _FD.size:
                _fail("worker pidfd is missing or ambiguous")
            received = _FD.unpack(pidfds[0])[0]
            if self.pidfd is None:
                if received < 0:
                    _fail("first worker fragment has no live pidfd")
                if _pidfd_pid(received) != peer[0] or _pidfd_exited(received):
                    _fail("worker pidfd does not identify a live sender")
                self.pid = peer[0]
                self.pidfd = received
                owned_fds.remove(received)
            elif received < 0:
                if received < -4095 or not _pidfd_exited(self.pidfd):
                    _fail("worker pidfd sentinel arrived before sender exit")
            elif _pidfd_pid(received) != self.pid:
                _fail("worker pidfd changed across fragments")
        finally:
            for descriptor in owned_fds:
                os.close(descriptor)


def _recv_exact(connection: socket.socket, length: int, *, deadline: float,
                identity: FrameIdentity) -> bytes:
    """Read the requested bytes by the deadline, authenticating every fragment.

    `deadline` is an absolute monotonic time in seconds, shared across all
    fragments. Raise `ContractError` on timeout, truncation, receive failure,
    or invalid sender identity.
    """
    chunks = []
    remaining = length
    while remaining:
        wait = deadline - time.monotonic()
        if wait <= 0 or not select.select((connection,), (), (), wait)[0]:
            _fail("worker frame deadline exceeded")
        try:
            data, controls, flags, _ = connection.recvmsg(remaining, _CONTROL_BYTES)
        except OSError as exc:
            raise ContractError("worker frame cannot be received") from exc
        identity.accept(data, controls, flags)
        if not data:
            _fail("worker frame was truncated")
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


def _read_frame(connection: socket.socket, *, limit: int, deadline: float,
                identity: FrameIdentity) -> dict[str, Any]:
    """Read one bounded canonical JSON object from an authenticated sender.

    `limit` bounds the JSON body in bytes, excluding the four-byte header;
    empty bodies are refused. `deadline` is an absolute monotonic time in
    seconds. Framing, identity, and JSON refusals raise `ContractError`.
    """
    size = _HEADER.unpack(_recv_exact(connection, _HEADER.size,
                                      deadline=deadline, identity=identity))[0]
    if not 0 < size <= limit:
        _fail("worker frame length is invalid")
    raw = _recv_exact(connection, size, deadline=deadline, identity=identity)
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("worker frame is not ASCII JSON") from exc
    if type(value) is not dict or canonical(value) != raw:
        _fail("worker frame is not canonical JSON")
    return value


def _write_frame(connection: socket.socket, value: dict[str, Any], *,
                 limit: int, deadline: float) -> bytes:
    """Send a bounded canonical JSON frame and return its JSON body bytes.

    The body must contain 1 through `limit` bytes. `deadline` is an absolute
    monotonic time in seconds for the whole send. Serialization, size,
    timeout, and send refusals raise `ContractError`.
    """
    raw = canonical(value)
    if not 0 < len(raw) <= limit:
        _fail("worker outbound frame length is invalid")
    frame = memoryview(_HEADER.pack(len(raw)) + raw)
    while frame:
        wait = deadline - time.monotonic()
        if wait <= 0 or not select.select((), (connection,), (), wait)[1]:
            _fail("worker outbound frame deadline exceeded")
        try:
            sent = connection.send(frame)
        except BlockingIOError:
            continue
        except OSError as exc:
            raise ContractError("worker outbound frame cannot be sent") from exc
        if sent <= 0:
            _fail("worker outbound frame was not sent")
        frame = frame[sent:]
    return raw


def _expect_eof(connection: socket.socket, *, deadline: float,
                identity: FrameIdentity) -> None:
    """Require authenticated EOF by the deadline and refuse trailing bytes.

    `deadline` is an absolute monotonic time in seconds.
    Raise `ContractError` for extra bytes, invalid controls, timeout, or a
    receive failure.
    """
    wait = deadline - time.monotonic()
    if wait <= 0 or not select.select((connection,), (), (), wait)[0]:
        _fail("worker EOF deadline exceeded")
    try:
        data, controls, flags, _ = connection.recvmsg(1, _CONTROL_BYTES)
    except OSError as exc:
        raise ContractError("worker EOF cannot be received") from exc
    identity.accept(data, controls, flags)
    if data:
        _fail("worker sent bytes after its result")


def _validate_ready(value: dict[str, Any], nonce: str) -> None:
    """Require the exact READY shape, protocol version, and handshake nonce."""
    if (type(value) is not dict or set(value) != {"kind", "version", "nonce"}
            or value["kind"] != "READY" or value["version"] != _VERSION
            or value["nonce"] != nonce):
        _fail("worker READY is invalid")


class _OccupiedCgroup(ContractError):
    pass


@dataclass(frozen=True)
class _Invocation:
    unit: str
    invocation_id: str
    cgroup: Path
    runtime_dir: Path
    pid: int


class WorkerLifecycle:
    """Read-only systemd/cgroup evidence and a Core-only host-wide lease."""

    def __init__(self, profile: WorkerServiceProfile) -> None:
        """Bind lifecycle checks to the fixed worker service profile."""
        self.profile = profile

    def _systemctl(self, *arguments: str) -> str:
        """Read bounded ASCII systemctl output, refusing errors or warnings.

        Raise `ContractError` on a two-second timeout, execution failure, stderr,
        nonzero exit, output over 8192 bytes, or non-ASCII output.
        """
        try:
            completed = subprocess.run(
                ["/usr/bin/systemctl", *arguments], capture_output=True,
                stdin=subprocess.DEVNULL, timeout=2, check=False,
                env={"LANG": "C", "LC_ALL": "C"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContractError("worker systemd state cannot be read") from exc
        if (completed.returncode != 0 or completed.stderr
                or len(completed.stdout) > 8192):
            _fail("worker systemd state cannot be read")
        try:
            return completed.stdout.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ContractError("worker systemd state is not ASCII") from exc

    def _show(self, unit: str, *properties: str) -> dict[str, str]:
        """Read exactly the requested unit properties, rejecting duplicate fields."""
        output = self._systemctl("show", unit, "--no-pager",
                                 *(f"-p{property_name}" for property_name in properties))
        if type(output) is not str:
            _fail("worker systemd state is unreadable")
        values = {}
        for line in output.splitlines():
            if "=" not in line:
                _fail("worker systemd properties are malformed")
            name, value = line.split("=", 1)
            if name in values:
                _fail("worker systemd properties are ambiguous")
            values[name] = value
        if set(values) != set(properties):
            _fail("worker systemd properties are incomplete")
        return values

    def _check_node(self, path: str, *, kind: str, gid: int,
                    mode: int) -> os.stat_result:
        """Check root ownership, path types, and exact IPC permissions.

        `kind` is "socket" or "file"; `gid` and `mode` specify the required group
        and permission bits. Require one link, root-controlled ancestor
        directories, and a root-owned 0710 parent in `gid`. Unsafe or unreadable
        metadata raises `ContractError`.
        """
        target = Path(path)
        current = Path("/")
        for part in target.parts[1:-1]:
            current /= part
            try:
                info = current.lstat()
            except OSError as exc:
                raise ContractError("worker service directory is unreadable") from exc
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or info.st_mode & 0o022):
                _fail("worker service directory is not root controlled")
        try:
            parent = target.parent.lstat()
            info = target.lstat()
        except OSError as exc:
            raise ContractError("worker service path is missing or unreadable") from exc
        expected = {"socket": stat.S_ISSOCK, "file": stat.S_ISREG}[kind]
        if (not expected(info.st_mode) or info.st_uid != 0 or info.st_gid != gid
                or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1):
            _fail("worker service path metadata is unsafe")
        if (parent.st_uid != 0 or parent.st_gid != gid
                or stat.S_IMODE(parent.st_mode) != 0o710):
            _fail("worker service parent metadata is unsafe")
        return info

    @staticmethod
    def _root_controlled(path: Path, *, regular: bool) -> None:
        """Require root control of every path component and traversed symlink.

        `regular` selects a regular-file leaf rather than a directory. Check all
        traversed components and symlink targets, allowing at most 40 links.
        Unsafe or uninspectable paths raise `ContractError`.
        """
        try:
            if not path.is_absolute():
                _fail("worker installed path is not absolute")
            current = Path("/")
            pending = deque((".", *path.parts[1:]))
            links = 0
            while pending:
                part = pending.popleft()
                candidate = current.parent if part == ".." else current / part
                info = candidate.lstat()
                link = stat.S_ISLNK(info.st_mode)
                expected = stat.S_ISDIR if pending or not regular else stat.S_ISREG
                if (info.st_uid != 0 or (not link and info.st_mode & 0o022)
                        or not (link or expected(info.st_mode))):
                    _fail("worker installed path is not root controlled")
                if link:
                    links += 1
                    if links > 40:
                        _fail("worker installed path has too many symlinks")
                    target = candidate.readlink()
                    # Inspect every link target before following its suffix.
                    # resolve() alone hides mutable intermediate link parents.
                    if target.is_absolute():
                        current = Path("/")
                        parts = (".", *target.parts[1:])
                    else:
                        parts = target.parts or (".",)
                    pending.extendleft(reversed(parts))
                else:
                    current = candidate
        except (OSError, RuntimeError) as exc:
            raise ContractError("worker installed path cannot be inspected") from exc

    def _check_runtime_tree(self, path: Path) -> None:
        """Check the complete runtime tree, visiting directory inodes once."""
        # Follow directory aliases as imports do, but visit each inode once so
        # a root-owned symlink cycle cannot make the inspection unbounded.
        pending = [path]
        seen = set()
        try:
            while pending:
                current = pending.pop()
                info = current.stat()
                directory = stat.S_ISDIR(info.st_mode)
                self._root_controlled(current, regular=not directory)
                identity = (info.st_dev, info.st_ino)
                if directory and identity not in seen:
                    seen.add(identity)
                    pending.extend(current.iterdir())
        except (OSError, RuntimeError) as exc:
            raise ContractError("worker Python runtime cannot be inspected") from exc

    def _check_optional_runtime_path(self, path: Path) -> None:
        """Check an existing runtime path or its nearest existing parent."""
        # CPython includes a zip search slot even when that archive is absent.
        # Its nearest existing parent must prevent an untrusted future insert.
        try:
            path.lstat()
        except FileNotFoundError:
            parent = path.parent
            while True:
                try:
                    parent.lstat()
                    break
                except FileNotFoundError:
                    parent = parent.parent
            self._root_controlled(parent, regular=False)
        else:
            self._check_runtime_tree(path)

    def _check_python_runtime(self) -> None:
        """Verify isolated startup and root control of Python runtime paths.

        These checks do not attest code already executed before they run.
        """
        if sys.flags.isolated != 1:
            _fail("Core requires isolated Python startup")
        try:
            self._check_loader_configuration()
            self._check_native_mappings()
            executables = {Path(sys.executable), Path(sys._base_executable)}
            directories = {Path(sys.prefix), Path(sys.base_prefix),
                           Path(sys.exec_prefix), Path(sys.base_exec_prefix),
                           Path(sysconfig.get_config_var("LIBDIR"))}
            for executable in executables:
                self._root_controlled(executable, regular=True)
                directories.update((executable.parent, executable.resolve().parent))
            for directory in directories:
                self._root_controlled(directory, regular=False)
                if any(name.endswith("._pth") for name in os.listdir(directory)):
                    _fail("worker Python path overrides are forbidden")
                self._check_optional_runtime_path(directory / "pyvenv.cfg")
            required = _python_runtime_paths()
            for value in (*required, *sys.path):
                if type(value) is not str or not os.path.isabs(value):
                    _fail("worker Python import path is not fixed and absolute")
            for value in required:
                self._check_runtime_tree(Path(value))
            # Also cover shared libraries not loaded by this Core yet.
            self._check_runtime_tree(Path(sysconfig.get_config_var("LIBDIR")))
            # Check initial interpreter paths too: encodings and runpy load
            # before the fixed bootstrap replaces sys.path with required.
            for value in sys.path:
                self._check_optional_runtime_path(Path(value))
        except ContractError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ContractError("worker Python startup state cannot be inspected") from exc

    def _check_loader_configuration(self) -> None:
        """Refuse loader overrides, unsafe configuration, and native preloads."""
        if any(name.startswith("LD_") or name in _LOADER_ENVIRONMENT.split()
               for name in os.environ):
            _fail("Core loader environment is not fixed")
        try:
            self._check_optional_runtime_path(_LOADER_CACHE)
            self._check_optional_runtime_path(_LOADER_PRELOAD)
            try:
                preload = _LOADER_PRELOAD.read_bytes()
            except FileNotFoundError:
                preload = b""
            if preload:
                _fail("worker native preloads are forbidden")
        except OSError as exc:
            raise ContractError("worker loader configuration cannot be inspected") from exc

    def _check_native_mappings(self) -> None:
        """Check executable mappings against root-controlled files and inodes."""
        # The fixed bootstrap imports the same native modules as this Core.
        # Check the actual ELF loader and DSOs, not just LIBDIR's directory mode.
        # The trusted installer must establish this runtime before the first exec.
        try:
            mappings = Path("/proc/self/maps").read_text(encoding="ascii")
            inspected = set()
            for line in mappings.splitlines():
                fields = line.split(maxsplit=5)
                if (len(fields) < 5 or re.fullmatch(r"[r-][w-][x-][ps]", fields[1]) is None):
                    _fail("Core executable mappings are malformed")
                if "x" not in fields[1]:
                    continue
                path = fields[5] if len(fields) == 6 else ""
                if path in ("[vdso]", "[vsyscall]") and fields[4] == "0":
                    continue
                if (not path.startswith("/") or path.endswith(" (deleted)") or "\\" in path):
                    _fail("Core executable mapping has no fixed file")
                target = Path(path)
                self._root_controlled(target, regular=True)
                info = target.stat()
                device = tuple(int(value, 16) for value in fields[3].split(":"))
                if (int(fields[4]) != info.st_ino
                        or device != (os.major(info.st_dev), os.minor(info.st_dev))):
                    _fail("Core executable mapping file was replaced")
                inspected.add(path)
            if not inspected:
                _fail("Core executable mappings are missing")
        except ContractError:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            raise ContractError("Core native runtime cannot be inspected") from exc

    def _check_installation(self) -> None:
        """Check the Python runtime, installed worker modules, and bootstrap."""
        from . import contracts, isolation, results, workers

        self._check_python_runtime()
        package = Path(__file__).parent
        self._root_controlled(package, regular=False)
        for module in (contracts, isolation, results, workers):
            self._root_controlled(Path(module.__file__), regular=True)
        self._root_controlled(Path(__file__), regular=True)
        self._root_controlled(Path(_BOOTSTRAP), regular=True)

    def _check_unit_file(self, name: str) -> None:
        """Inspect a loaded unit, querying service templates through an instance."""
        # systemd exposes a template through an instance name. bind() checks
        # the actual accepted instance again before any job payload is sent.
        unit = name.replace("@.service", "@internal.service")
        properties = self._show(unit, "LoadState", "FragmentPath", "DropInPaths",
                                "NeedDaemonReload")
        self._check_unit_source(properties, name)

    def _check_unit_source(self, properties: dict[str, str], name: str) -> None:
        """Require the fixed installed unit without drop-ins or a pending reload."""
        path = Path(properties["FragmentPath"])
        if (properties["LoadState"] != "loaded" or properties["DropInPaths"]
                or properties["NeedDaemonReload"] != "no"
                or path.name != name or not path.is_absolute()):
            _fail("worker unit is not the fixed installed template")
        self._root_controlled(path, regular=True)
        self._check_unit_content(path, name)

    def _check_unit_content(self, path: Path, name: str) -> None:
        """Require the exact service or socket directives and fixed bootstrap."""
        try:
            source = path.read_text(encoding="ascii")
        except (OSError, UnicodeError) as exc:
            raise ContractError("worker installed unit cannot be read") from exc
        if len(source) > 8192:
            _fail("worker installed unit is oversized")
        fields: dict[str, dict[str, str]] = {}
        section: str | None = None
        for raw in source.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                if section in fields:
                    _fail("worker installed unit repeats a section")
                fields[section] = {}
                continue
            if section is None or "=" not in line:
                _fail("worker installed unit has malformed directives")
            key, value = line.split("=", 1)
            if key in fields[section]:
                _fail("worker installed unit repeats a directive")
            fields[section][key] = value
        expected_unit = {"Description": ("GeniusNew isolated worker for one accepted "
                                         "connection" if name.endswith("@.service")
                                         else "GeniusNew isolated worker socket")}
        if name.endswith("@.service"):
            expected_service = {
                "Type": "exec", "Slice": "system.slice",
                "User": str(self.profile.worker_uid),
                "Group": str(self.profile.worker_gid),
                "SupplementaryGroups": "", "StandardInput": "socket",
                "StandardOutput": "null", "StandardError": "null",
                "LimitCORE": "0", "CapabilityBoundingSet": "",
                "AmbientCapabilities": "", "NoNewPrivileges": "yes",
                "PrivateNetwork": "yes", "SystemCallFilter": "~socket socketpair",
                "SystemCallErrorNumber": "EPERM", "KillMode": "control-group",
                "SendSIGKILL": "yes", "Restart": "no",
                "TimeoutStartSec": "5s", "RuntimeMaxSec": "12s",
                "TimeoutStopSec": "2s", "TasksMax": "4", "MemoryMax": "512M",
                "UMask": "0077", "RuntimeDirectory": "geniusnew-worker/jobs/%i",
                "RuntimeDirectoryMode": "0700", "RuntimeDirectoryPreserve": "no",
                "UnsetEnvironment": _LOADER_ENVIRONMENT,
            }
            service = fields.get("Service", {})
            command = service.pop("ExecStart", None)
            if fields != {"Unit": expected_unit, "Service": expected_service}:
                _fail("worker installed service directives are not fixed")
            if command is None:
                _fail("worker installed service has no fixed bootstrap")
            try:
                arguments = shlex.split(command)
            except ValueError as exc:
                raise ContractError("worker bootstrap command is malformed") from exc
            if not arguments:
                _fail("worker installed service has no fixed bootstrap")
            expected = [sys.executable, "-I", "-S", _BOOTSTRAP,
                        "--core-uid", str(self.profile.core_uid), "--worker-uid",
                        str(self.profile.worker_uid), "--worker-gid",
                        str(self.profile.worker_gid)]
            if arguments != expected or not arguments[0].startswith("/"):
                _fail("worker installed service bootstrap is not fixed")
            self._root_controlled(Path(arguments[0]), regular=True)
        else:
            expected_socket = {
                "ListenStream": self.profile.socket_path,
                "Accept": "yes", "MaxConnections": "1", "SocketUser": "root",
                "SocketGroup": str(self.profile.socket_gid), "SocketMode": "0660",
                "DirectoryMode": "0710", "RemoveOnStop": "yes",
            }
            if fields != {"Unit": expected_unit, "Socket": expected_socket}:
                _fail("worker installed socket directives are not fixed")

    def _check_runtime_root(self) -> None:
        """Require root control of the configured runtime parent directory."""
        self._root_controlled(Path(self.profile.runtime_root), regular=False)

    def _check_exec(self, value: str) -> None:
        """Require systemd ExecStart to use the fixed interpreter and bootstrap.

        Refuse unexpected syntax, command, or identity arguments with
        `ContractError`; malformed shell quoting can propagate `ValueError`.
        """
        if not value.startswith("{ ") or not value.endswith(" }"):
            _fail("worker ExecStart is malformed")
        fields = {}
        for item in value[2:-2].split(" ; "):
            if "=" not in item:
                _fail("worker ExecStart is malformed")
            key, item_value = item.split("=", 1)
            if key in fields:
                _fail("worker ExecStart is ambiguous")
            fields[key] = item_value
        executable = fields.get("path")
        arguments = fields.get("argv[]")
        if (not executable or not executable.startswith("/")
                or fields.get("ignore_errors") != "no" or not arguments):
            _fail("worker ExecStart is not fixed")
        expected = [sys.executable, "-I", "-S", _BOOTSTRAP,
                    "--core-uid", str(self.profile.core_uid), "--worker-uid",
                    str(self.profile.worker_uid), "--worker-gid",
                    str(self.profile.worker_gid)]
        if shlex.split(arguments) != expected or executable != sys.executable:
            _fail("worker ExecStart arguments are not fixed")
        self._root_controlled(Path(executable), regular=True)

    def _check_instance_settings(self, properties: dict[str, str], instance: str) -> None:
        """Check effective instance hardening and its fixed execution command."""
        expected = {
            "User": str(self.profile.worker_uid),
            "Group": str(self.profile.worker_gid),
            "SupplementaryGroups": "",
            "CapabilityBoundingSet": "",
            "AmbientCapabilities": "",
            "NoNewPrivileges": "yes",
            "PrivateNetwork": "yes",
            "StandardInput": "socket",
            "StandardOutput": "null",
            "StandardError": "null",
            "LimitCORE": "0",
            "LimitCORESoft": "0",
            "KillMode": "control-group",
            "SendSIGKILL": "yes",
            "Restart": "no",
            "RuntimeMaxUSec": "12s",
            "TimeoutStopUSec": "2s",
            "RuntimeDirectory": self.profile.runtime_root.removeprefix("/run/") + "/" + instance,
            "RuntimeDirectoryMode": "0700",
            "RuntimeDirectoryPreserve": "no",
            "Slice": "system.slice",
            "SystemCallFilter": "~socket socketpair",
            "SystemCallErrorNumber": "1",
            "UnsetEnvironment": _LOADER_ENVIRONMENT,
        }
        if any(properties.get(key) != value for key, value in expected.items()):
            _fail("worker instance hardening does not match its fixed profile")
        self._check_exec(properties["ExecStart"])

    def _check_core_identity(self) -> None:
        """Require the fixed Core UID, allowed groups, and no Linux capabilities."""
        if (os.getuid() != self.profile.core_uid
                or os.geteuid() != self.profile.core_uid
                or self.profile.worker_gid in (os.getgid(), *os.getgroups())
                or self.profile.socket_gid not in (os.getgid(), *os.getgroups())):
            _fail("Core does not have its fixed unprivileged service identity")
        try:
            status = Path("/proc/self/status").read_text(encoding="ascii")
        except OSError as exc:
            raise ContractError("Core privilege state cannot be read") from exc
        uids = re.findall(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
                          status, re.MULTILINE)
        if len(uids) != 1 or any(int(value) != self.profile.core_uid
                                 for value in uids[0]):
            _fail("Core UID state is ambiguous")
        for field in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
            values = re.findall(rf"^{field}:\s+([0-9a-fA-F]+)$", status, re.MULTILINE)
            if len(values) != 1 or int(values[0], 16) != 0:
                _fail("Core retains Linux capabilities")
        if re.findall(r"^NoNewPrivs:\s+(\d+)\s*$", status, re.MULTILINE) != ["1"]:
            _fail("Core can gain privileges")

    @staticmethod
    def _check_id_maps() -> None:
        """Require initial host UID and GID mappings for ownership evidence."""
        # Without this gate, root ownership inside an unprivileged user
        # namespace would not establish the provenance of the reference file.
        for name in ("uid_map", "gid_map"):
            try:
                values = Path(f"/proc/self/{name}").read_text(encoding="ascii").split()
            except (OSError, UnicodeError) as exc:
                raise ContractError("Core ID mapping cannot be read") from exc
            if values != ["0", "0", "4294967295"]:
                _fail("Core does not have the initial ID mapping")

    @staticmethod
    def _namespace_ids(pid: str) -> dict[str, list[int]]:
        """Return device and inode identities for the required process namespaces.

        `pid` names a procfs PID or "self" for the current process.
        Inspect mount, PID, user, and cgroup namespaces. Raise `ContractError`
        if their descriptors cannot be opened or inspected.
        """
        namespaces = {}
        try:
            for name in _HOST_NAMESPACES:
                descriptor = os.open(f"/proc/{pid}/ns/{name}", os.O_RDONLY | os.O_CLOEXEC)
                try:
                    info = os.fstat(descriptor)
                    namespaces[name] = [info.st_dev, info.st_ino]
                finally:
                    os.close(descriptor)
        except OSError as exc:
            raise ContractError("Core namespace state cannot be read") from exc
        return namespaces

    def _host_paths(self) -> dict[str, str]:
        """Return the host paths whose inodes bind the provisioning reference."""
        return {"root": "/", "proc": "/proc", "cgroup": "/sys/fs/cgroup",
                "runtime": self.profile.runtime_root, "lease": self.profile.lease_path}

    def _host_snapshot(self, *, pid: str = "self", lease_fd: int | None = None) -> bytes:
        """Serialize boot, namespace, and path identities, optionally using an open
        lease.

        Return canonical JSON bytes.
        `pid` selects whose namespaces to inspect. When supplied, `lease_fd`
        provides the lease inode instead of looking up the lease path. Invalid
        boot identity or unreadable evidence raises `ContractError`.
        """
        namespaces = self._namespace_ids(pid)
        try:
            boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            paths = {}
            for name, path in self._host_paths().items():
                info = (os.fstat(lease_fd) if name == "lease" and lease_fd is not None
                        else os.stat(path))
                paths[name] = [info.st_dev, info.st_ino]
        except (OSError, UnicodeError) as exc:
            raise ContractError("Core host paths or boot identity cannot be read") from exc
        if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot) is None:
            _fail("Core boot identity is invalid")
        return canonical({"version": 1, "boot_id": boot,
                          "namespaces": namespaces, "paths": paths})

    def host_reference(self) -> bytes:
        """Produce boot evidence during trusted host provisioning, never dispatch.

        Root and the host boot path are already trusted for the lease, units and
        installed code. A container's root is not an independent host witness.
        The operator must run this from the actual host before starting Core.
        """
        if os.getuid() != 0 or os.geteuid() != 0:
            _fail("host reference must be provisioned by host root")
        self._check_id_maps()
        if self._namespace_ids("self") != self._namespace_ids("1"):
            _fail("host provisioning does not share the manager namespaces")
        try:
            own, manager = os.stat("/"), os.stat("/proc/1/root")
        except OSError as exc:
            raise ContractError("host provisioning root cannot be inspected") from exc
        if (own.st_dev, own.st_ino) != (manager.st_dev, manager.st_ino):
            _fail("host provisioning does not share the manager root")
        self._check_node(self.profile.lease_path, kind="file",
                         gid=self.profile.socket_gid, mode=0o660)
        self._check_runtime_root()
        self._check_installation()
        return self._host_snapshot(pid="1")

    def _check_host_namespaces(self, *, lease_fd: int | None = None) -> None:
        """Match the trusted boot reference and require a complete procfs view.

        `lease_fd`, if supplied, binds the comparison to the open lease inode.
        Also require the initial ID maps and a procfs mount without hidepid
        restrictions. Invalid, changed, or unreadable evidence raises
        `ContractError`.
        """
        # Nonroot Core cannot dereference /proc/1/ns across the UID boundary.
        # A boot-bound root reference preserves the comparison without granting
        # ptrace capabilities. Path inodes also detect chroot and split leases.
        self._check_id_maps()
        info = self._check_node(_HOST_REFERENCE, kind="file",
                                gid=self.profile.socket_gid, mode=0o440)
        try:
            descriptor = os.open(_HOST_REFERENCE, os.O_RDONLY | os.O_NOFOLLOW
                                 | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                opened = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                    _fail("Core host reference inode changed during open")
                raw = os.read(descriptor, 4097)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise ContractError("Core host reference cannot be read") from exc
        # Comparing canonical bytes also rejects extra keys, non-integer inode
        # values, duplicate JSON members, trailing bytes and partial writes.
        if raw != self._host_snapshot(lease_fd=lease_fd):
            _fail("Core host reference does not match its boot, namespaces and paths")
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text(encoding="ascii")
        except OSError as exc:
            raise ContractError("Core procfs mount state cannot be read") from exc
        proc_mounts = []
        for line in mountinfo.splitlines():
            halves = line.split(" - ", 1)
            before = halves[0].split()
            after = halves[1].split() if len(halves) == 2 else []
            if len(before) > 5 and before[4] == "/proc":
                proc_mounts.append((before, after))
        if len(proc_mounts) != 1 or len(proc_mounts[0][1]) < 3 or proc_mounts[0][1][0] != "proc":
            _fail("Core cannot prove a complete procfs process view")
        options = set(proc_mounts[0][0][5].split(",")) | set(proc_mounts[0][1][2].split(","))
        if any(option.startswith("hidepid=") and option != "hidepid=0"
               for option in options):
            _fail("Core procfs hides worker processes")

    def preflight(self) -> None:
        """Verify Core identity, host evidence, installed code, and socket activation."""
        self._check_core_identity()
        self._check_host_namespaces()
        self._check_node(self.profile.socket_path, kind="socket",
                         gid=self.profile.socket_gid, mode=0o660)
        self._check_node(self.profile.lease_path, kind="file",
                         gid=self.profile.socket_gid, mode=0o660)
        self._check_runtime_root()
        self._check_installation()
        socket_unit = self.profile.unit_prefix[:-1] + ".socket"
        self._check_unit_file(socket_unit)
        self._check_unit_file(self.profile.unit_prefix + ".service")
        properties = self._show(socket_unit, "ActiveState", "Accept",
                                "MaxConnections", "SocketUser", "SocketGroup",
                                "SocketMode", "Listen", "RemoveOnStop")
        if (properties["ActiveState"] != "active" or properties["Accept"] != "yes"
                or properties["MaxConnections"] != "1"
                or properties["SocketUser"] != "root"
                or properties["SocketGroup"] not in (str(self.profile.socket_gid),)
                or properties["SocketMode"] not in ("0660", "660")
                or properties["Listen"] != self.profile.socket_path + " (Stream)"
                or properties["RemoveOnStop"] != "yes"):
            _fail("worker socket unit does not match its fixed profile")

    def acquire(self) -> int:
        """Acquire a nonblocking lease and return its checked, caller-owned descriptor.

        Verify the open inode against the host reference. The caller owns the
        returned descriptor and holds the lock until closing it. An occupied
        lease or failed verification raises `ContractError`; failures after
        opening close the descriptor before propagating.
        """
        self._check_node(self.profile.lease_path, kind="file",
                         gid=self.profile.socket_gid, mode=0o660)
        try:
            descriptor = os.open(self.profile.lease_path,
                                 os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                                 | os.O_CLOEXEC)
        except OSError as exc:
            raise ContractError("worker lease cannot be opened") from exc
        try:
            path = os.lstat(self.profile.lease_path)
            opened = os.fstat(descriptor)
            if (path.st_dev, path.st_ino) != (opened.st_dev, opened.st_ino):
                _fail("worker lease inode changed during open")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ContractError("worker service slot is occupied") from exc
            self._check_host_namespaces(lease_fd=descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def _require_empty_cgroup(self, path: Path) -> None:
        """Require a verifiably absent cgroup or an empty process subtree.

        A missing cgroup is accepted only if absent from its parent's listing.
        Occupied groups raise `_OccupiedCgroup`; unsafe, ambiguous, or unreadable
        state raises `ContractError`.
        """
        try:
            try:
                info = path.lstat()
            except FileNotFoundError:
                if path.name in os.listdir(path.parent):
                    _fail("worker cgroup absence is ambiguous")
                return
            if not stat.S_ISDIR(info.st_mode):
                _fail("worker cgroup is not a directory")
            visited = False
            for root, _directories, _files in os.walk(path, onerror=lambda error: _fail(
                    "worker cgroup is unreadable")):
                visited = True
                if Path(root, "cgroup.procs").read_text(encoding="ascii").strip():
                    raise _OccupiedCgroup("worker cgroup is occupied")
            if not visited:
                _fail("worker cgroup disappeared during inspection")
        except OSError as exc:
            raise ContractError("worker cgroup is unreadable") from exc

    @staticmethod
    def _runtime_absent(path: Path) -> bool:
        """Return whether the runtime path is absent, refusing unreadable state."""
        try:
            path.lstat()
        except FileNotFoundError:
            return True
        except OSError as exc:
            raise ContractError("worker runtime state is unreadable") from exc
        return False

    def _cgroup_path(self, group: str) -> Path:
        """Resolve a cgroup path confined to the host system slice."""
        if (type(group) is not str or not group.startswith("/system.slice/")
                or ".." in group.split("/") or group.endswith("/")):
            _fail("worker cgroup path is unexpected")
        path = Path("/sys/fs/cgroup") / group.lstrip("/")
        if not path.resolve().is_relative_to("/sys/fs/cgroup/system.slice"):
            _fail("worker cgroup path escapes system.slice")
        return path

    def _uid_processes(self) -> list[int]:
        """List processes with any worker UID field, refusing unreadable evidence."""
        found = []
        try:
            entries = os.scandir("/proc")
            with entries:
                for entry in entries:
                    if not entry.name.isdigit():
                        continue
                    try:
                        status = Path(entry.path, "status").read_text(encoding="ascii")
                    except FileNotFoundError:
                        continue  # PID exited between the directory read and open.
                    except OSError as exc:
                        raise ContractError("worker UID process state is unreadable") from exc
                    matches = re.findall(
                        r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
                        status, re.MULTILINE)
                    if len(matches) != 1:
                        _fail("worker UID process state is ambiguous")
                    if self.profile.worker_uid in map(int, matches[0]):
                        found.append(int(entry.name))
        except OSError as exc:
            raise ContractError("worker UID process listing is unreadable") from exc
        return found

    def assert_idle(self) -> None:
        """Require no active worker instance, residual runtime, or worker UID process."""
        prefix = self.profile.unit_prefix
        output = self._systemctl("list-units", "--all", "--plain", "--no-legend",
                                 prefix + "*.service")
        for line in output.splitlines():
            parts = line.split()
            if not parts or not parts[0].startswith(prefix) or not parts[0].endswith(
                    ".service"):
                _fail("worker unit list is ambiguous")
            properties = self._show(parts[0], "ActiveState", "MainPID", "ControlGroup")
            if (properties["ActiveState"] not in ("inactive", "failed")
                    or properties["MainPID"] != "0"):
                _fail("previous worker invocation is still active")
            if properties["ControlGroup"]:
                self._require_empty_cgroup(self._cgroup_path(properties["ControlGroup"]))
            instance = parts[0][len(prefix):-len(".service")]
            if not self._runtime_absent(Path(self.profile.runtime_root) / instance):
                _fail("previous worker runtime directory remains")
        cgroup_parent = Path("/sys/fs/cgroup/system.slice")
        try:
            for name in os.listdir(cgroup_parent):
                if name.startswith(prefix) and name.endswith(".service"):
                    self._require_empty_cgroup(cgroup_parent / name)
        except OSError as exc:
            raise ContractError("worker cgroup listing is unreadable") from exc
        try:
            runtime = Path(self.profile.runtime_root)
            info = runtime.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or info.st_mode & 0o022 or os.listdir(runtime)):
                _fail("worker runtime root is unsafe or not empty")
        except OSError as exc:
            raise ContractError("worker runtime root is unreadable") from exc
        if self._uid_processes():
            _fail("worker UID still has a live process")

    def bind(self, pid: int, pidfd: int) -> _Invocation:
        """Bind a live sender to its active hardened invocation and private runtime.

        `pidfd` must already identify `pid`. Return the unit, invocation ID,
        cgroup, and private runtime directory after confirming liveness before
        and after inspection. Failed lifecycle checks raise `ContractError`;
        other inspection errors propagate.
        """
        if _pidfd_exited(pidfd):
            _fail("worker exited before the invocation check")
        try:
            lines = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines()
        except OSError as exc:
            raise ContractError("worker PID cgroup is unreadable") from exc
        if len(lines) != 1 or not lines[0].startswith("0::"):
            _fail("worker PID cgroup is ambiguous")
        group = lines[0][3:]
        match = re.fullmatch(r"/system\.slice/(" + re.escape(self.profile.unit_prefix)
                             + r"[A-Za-z0-9_\\-]+\.service)", group)
        if match is None:
            _fail("worker PID is outside the expected unit")
        unit = match.group(1)
        settings = ("User", "Group", "SupplementaryGroups", "CapabilityBoundingSet",
                    "AmbientCapabilities", "NoNewPrivileges", "PrivateNetwork",
                    "StandardInput", "StandardOutput", "StandardError", "LimitCORE",
                    "LimitCORESoft", "KillMode", "SendSIGKILL", "Restart",
                    "RuntimeMaxUSec", "TimeoutStopUSec", "RuntimeDirectory",
                    "RuntimeDirectoryMode", "RuntimeDirectoryPreserve", "Slice",
                    "SystemCallFilter", "SystemCallErrorNumber", "UnsetEnvironment", "ExecStart")
        properties = self._show(unit, "ActiveState", "InvocationID",
                                "ControlGroup", "MainPID", "LoadState", "FragmentPath",
                                "DropInPaths", "NeedDaemonReload", *settings)
        if (properties["ActiveState"] != "active" or properties["ControlGroup"] != group
                or properties["MainPID"] != str(pid)
                or re.fullmatch(r"[0-9a-f]{32}", properties["InvocationID"]) is None):
            _fail("worker PID does not match an active invocation")
        instance = unit[len(self.profile.unit_prefix):-len(".service")]
        self._check_unit_source(properties, self.profile.unit_prefix + ".service")
        self._check_instance_settings(properties, instance)
        self._check_runtime_root()
        runtime = Path(self.profile.runtime_root) / instance
        try:
            info = runtime.lstat()
        except OSError as exc:
            raise ContractError("worker runtime directory is unreadable") from exc
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.profile.worker_uid
                or info.st_gid != self.profile.worker_gid
                or stat.S_IMODE(info.st_mode) != 0o700):
            _fail("worker runtime directory metadata is unsafe")
        if _pidfd_exited(pidfd):
            _fail("worker exited before payload")
        return _Invocation(unit, properties["InvocationID"], self._cgroup_path(group),
                           runtime, pid)

    def await_end(self, invocation: _Invocation, pidfd: int, deadline: float) -> None:
        """Wait until sender exit, instance termination, and cleanup are confirmed.

        `deadline` is an absolute monotonic time in seconds.
        Require an inactive or failed unit, no main PID, an empty or absent
        cgroup, a removed runtime directory, pidfd exit, and no worker UID
        processes. Occupied cgroups are retried. Changed invocations, timeout,
        or other evidence refusals raise `ContractError`.
        """
        while time.monotonic() < deadline:
            properties = self._show(invocation.unit, "ActiveState", "InvocationID",
                                    "ControlGroup", "MainPID")
            if properties["InvocationID"] not in (invocation.invocation_id, ""):
                _fail("worker invocation changed before cleanup")
            if (properties["ActiveState"] in ("inactive", "failed")
                    and properties["MainPID"] == "0"):
                try:
                    self._require_empty_cgroup(invocation.cgroup)
                except _OccupiedCgroup:
                    pass
                else:
                    if (self._runtime_absent(invocation.runtime_dir) and _pidfd_exited(pidfd)
                            and not self._uid_processes()):
                        return
            time.sleep(0.05)
        _fail("worker invocation end and cleanup were not confirmed")


def _request(payload: dict[str, str], nonce: str) -> dict[str, Any]:
    """Build the fixed summarizer request with a text payload and default limits."""
    if set(payload) != {"text"} or type(payload["text"]) is not str:
        _fail("worker service accepts only a text payload")
    return {"kind": "REQUEST", "version": _VERSION, "nonce": nonce,
            "worker": "summarize", "payload": {"text": payload["text"]},
            "limits": asdict(IsolationLimits())}


def _validate_result(value: dict[str, Any], nonce: str,
                     request_digest: str) -> Any:
    """Validate result identity and return text output or a permitted error code.

    Require the expected nonce, version, and SHA-256 digest of the request
    body. Identity, shape, or error-code mismatches raise `ContractError`.
    """
    common = {"kind", "version", "nonce", "request_digest"}
    if (type(value) is not dict or value.get("kind") != "RESULT"
            or value.get("version") != _VERSION or value.get("nonce") != nonce
            or value.get("request_digest") != request_digest):
        _fail("worker RESULT identity or digest is invalid")
    if set(value) == common | {"output"}:
        output = value["output"]
        if type(output) is not dict or set(output) != {"text"} or type(output["text"]) is not str:
            _fail("worker RESULT output is invalid")
        return output
    if set(value) == common | {"error"} and value["error"] in (
            "WORKER_FAILED", "ISOLATION_VIOLATED", "RESOURCE_EXHAUSTED",
            "OUTPUT_REJECTED"):
        return value["error"]
    _fail("worker RESULT shape or error code is invalid")


class _CoreExchange:
    def __init__(self, connection: socket.socket, profile: WorkerServiceProfile,
                 lifecycle: WorkerLifecycle, payload: dict[str, str],
                 total_deadline: float) -> None:
        """Prepare one connection exchange with a fixed identity and total deadline.

        The deadline is an absolute monotonic time in seconds.
        """
        self.connection = connection
        self.profile = profile
        self.lifecycle = lifecycle
        self.payload = payload
        self.total_deadline = total_deadline
        self.identity = FrameIdentity(profile.worker_uid, profile.worker_gid)
        self.invocation: _Invocation | None = None

    def _deadline(self) -> float:
        """Return the next phase deadline capped by the total exchange deadline."""
        return min(self.total_deadline, time.monotonic() + self.profile.phase_seconds)

    def run(self) -> Any:
        """Authenticate READY, bind the invocation, and validate one result and EOF.

        Bind the sender to its systemd invocation before dispatch. Return a
        validated output mapping or worker error code; protocol and lifecycle
        refusals propagate as `ContractError`. The caller confirms instance
        cleanup separately and owns the connection and retained pidfd.
        """
        nonce = secrets.token_hex(32)
        _write_frame(self.connection, {"kind": "HELLO", "version": _VERSION,
                                       "nonce": nonce}, limit=_HELLO_LIMIT,
                     deadline=self._deadline())
        ready = _read_frame(self.connection, limit=_HELLO_LIMIT,
                            deadline=self._deadline(), identity=self.identity)
        _validate_ready(ready, nonce)
        if self.identity.pid is None or self.identity.pidfd is None:
            _fail("worker READY has no bound process")
        self.invocation = self.lifecycle.bind(self.identity.pid, self.identity.pidfd)
        if _pidfd_exited(self.identity.pidfd):
            _fail("worker exited before receiving payload")
        request = _request(self.payload, nonce)
        work_deadline = min(self.total_deadline, time.monotonic()
                            + float(request["limits"]["wall_seconds"]))
        wire = _write_frame(self.connection, request, limit=_PAYLOAD_LIMIT,
                            deadline=min(self._deadline(), work_deadline))
        try:
            self.connection.shutdown(socket.SHUT_WR)
        except OSError as exc:
            raise ContractError("worker request end cannot be sent") from exc
        digest = hashlib.sha256(wire).hexdigest()
        result = _read_frame(self.connection, limit=_PAYLOAD_LIMIT,
                             deadline=min(self._deadline(), work_deadline),
                             identity=self.identity)
        _expect_eof(self.connection, deadline=min(self._deadline(), work_deadline),
                    identity=self.identity)
        return _validate_result(result, nonce, digest)


class ServiceWorkerRunner(WorkerRunner):
    """Core-side runner; the lease encloses handshake, work and manager cleanup."""

    def __init__(self, worker: DeterministicSummarizer, *, authority: WorkerAuthority,
                 profile: WorkerServiceProfile,
                 lifecycle: WorkerLifecycle | None = None) -> None:
        """Create a runner for the fixed summarizer and a validated service profile."""
        if type(worker) is not DeterministicSummarizer:
            _fail("worker service supports only the fixed summarizer")
        if not isinstance(profile, WorkerServiceProfile):
            _fail("worker service needs a fixed profile")
        super().__init__(worker, authority=authority)
        self.profile = profile
        self.lifecycle = lifecycle or WorkerLifecycle(profile)
        self._pending: list[tuple[int, FrameIdentity | None]] = []

    def preflight(self) -> None:
        """Check service readiness, then verify idle state under the exclusive lease."""
        self.lifecycle.preflight()
        descriptor = self.lifecycle.acquire()
        try:
            self.lifecycle.assert_idle()
        finally:
            os.close(descriptor)

    def _connect(self) -> socket.socket:
        """Connect with credential and pidfd reception enabled in nonblocking mode."""
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(self.profile.phase_seconds)
            connection.connect(self.profile.socket_path)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            connection.setsockopt(socket.SOL_SOCKET, _SO_PASSPIDFD, 1)
            connection.setblocking(False)
        except OSError as exc:
            connection.close()
            raise ContractError("worker service connection cannot be authenticated") from exc
        return connection

    def _run_worker(self, payload: dict[str, str]) -> dict[str, str]:
        """Run one authenticated job and retain the lease until cleanup is confirmed.

        Transport or lifecycle failures raise `WorkerBoundaryError`. After
        acquisition, unconfirmed cleanup retains the lease and blocks later
        calls on this runner. Confirmed worker error codes instead raise
        `_WorkerIsolationViolation`, `_WorkerResourceExhausted`, or `RuntimeError`,
        or return None for OUTPUT_REJECTED, for `execute` to sign as failures.
        """
        if self._pending:
            raise WorkerBoundaryError("worker service has an unconfirmed prior instance")
        try:
            self.lifecycle.preflight()
            lease = self.lifecycle.acquire()
        except ContractError as exc:
            raise WorkerBoundaryError("worker service preflight failed") from exc
        exchange: _CoreExchange | None = None
        outcome: Any = None
        problem: ContractError | None = None
        confirmed = False
        try:
            self.lifecycle.assert_idle()
            with self._connect() as connection:
                exchange = _CoreExchange(connection, self.profile, self.lifecycle,
                                         dict(payload), time.monotonic()
                                         + self.profile.total_seconds)
                try:
                    outcome = exchange.run()
                except ContractError as exc:
                    problem = exc
            if exchange is not None and exchange.invocation is not None:
                self.lifecycle.await_end(exchange.invocation, exchange.identity.pidfd,
                                         exchange.total_deadline)
                confirmed = True
        except ContractError as exc:
            problem = exc
        except Exception as exc:
            problem = ContractError("worker service transport failed")
        finally:
            if confirmed:
                os.close(lease)
                if exchange is not None:
                    exchange.identity.close()
            else:
                # A Core crash releases flock, but a restarted Core still reads
                # systemd/cgroup state under its new lease before any connect.
                self._pending.append((lease, None if exchange is None else exchange.identity))
        if problem is not None:
            raise WorkerBoundaryError("worker service transaction was not authenticated") from None
        if outcome == "ISOLATION_VIOLATED":
            raise _WorkerIsolationViolation()
        if outcome == "RESOURCE_EXHAUSTED":
            raise _WorkerResourceExhausted()
        if outcome == "WORKER_FAILED":
            raise RuntimeError("worker failed")
        if outcome == "OUTPUT_REJECTED":
            return None
        return outcome


def _plain_exact(descriptor: int, length: int, deadline: float) -> bytes:
    """Read exactly the requested bytes from a descriptor before the deadline.

    `deadline` is an absolute monotonic time in seconds.
    Retry nonblocking reads. Timeout, truncation, and read failures raise
    `ContractError`.
    """
    chunks = []
    while length:
        wait = deadline - time.monotonic()
        if wait <= 0 or not select.select((descriptor,), (), (), wait)[0]:
            _fail("worker service input deadline exceeded")
        try:
            data = os.read(descriptor, length)
        except BlockingIOError:
            continue
        except OSError as exc:
            raise ContractError("worker service input cannot be read") from exc
        if not data:
            _fail("worker service input was truncated")
        chunks.append(data)
        length -= len(data)
    return b"".join(chunks)


def _plain_frame(descriptor: int, *, limit: int, deadline: float) -> dict[str, Any]:
    """Read one bounded canonical ASCII JSON object from the Core descriptor.

    The JSON body must contain 1 through `limit` bytes; `deadline` is an
    absolute monotonic time in seconds. Input and framing refusals raise
    `ContractError`.
    """
    size = _HEADER.unpack(_plain_exact(descriptor, _HEADER.size, deadline))[0]
    if not 0 < size <= limit:
        _fail("worker service input length is invalid")
    data = _plain_exact(descriptor, size, deadline)
    try:
        value = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("worker service input is not ASCII JSON") from exc
    if type(value) is not dict or canonical(value) != data:
        _fail("worker service input is not canonical JSON")
    return value


def _plain_expect_eof(descriptor: int, deadline: float) -> None:
    """Require the Core request to end by the deadline without trailing bytes.

    `deadline` is an absolute monotonic time in seconds.
    Extra bytes, timeout, or unreadable/ambiguous EOF raise `ContractError`.
    """
    wait = deadline - time.monotonic()
    if wait <= 0 or not select.select((descriptor,), (), (), wait)[0]:
        _fail("worker service request end deadline exceeded")
    try:
        data = os.read(descriptor, 1)
    except BlockingIOError as exc:
        raise ContractError("worker service request end is ambiguous") from exc
    except OSError as exc:
        raise ContractError("worker service request end cannot be read") from exc
    if data:
        _fail("worker service received bytes after its request")


def _plain_write(descriptor: int, value: dict[str, Any], deadline: float) -> None:
    """Send one bounded canonical JSON frame before the deadline.

    The canonical JSON body may contain at most 32 KiB.
    `deadline` is an absolute monotonic time in seconds. Retry nonblocking
    writes; serialization, size, timeout, and write refusals raise
    `ContractError`.
    """
    data = canonical(value)
    if not 0 < len(data) <= _PAYLOAD_LIMIT:
        _fail("worker service output length is invalid")
    frame = memoryview(_HEADER.pack(len(data)) + data)
    while frame:
        wait = deadline - time.monotonic()
        if wait <= 0 or not select.select((), (descriptor,), (), wait)[1]:
            _fail("worker service output deadline exceeded")
        try:
            written = os.write(descriptor, frame)
        except BlockingIOError:
            continue
        except OSError as exc:
            raise ContractError("worker service output cannot be sent") from exc
        if written <= 0:
            _fail("worker service output could not be sent")
        frame = frame[written:]


def _validate_hello(value: dict[str, Any]) -> str:
    """Check the exact HELLO shape and return its random hexadecimal nonce."""
    if (set(value) != {"kind", "version", "nonce"} or value["kind"] != "HELLO"
            or value["version"] != _VERSION or type(value["nonce"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", value["nonce"]) is None):
        _fail("worker service HELLO is invalid")
    return value["nonce"]


def _validate_request(value: dict[str, Any], nonce: str,
                      ceiling: IsolationLimits) -> IsolationLimits:
    """Check the fixed worker request and return limits within the ceiling.

    Require a matching nonce, one string `text`, and all limit fields. Each
    limit must pass `IsolationLimits` validation and be no greater than
    `ceiling`. Invalid requests or limits raise `ContractError`.
    """
    if (set(value) != {"kind", "version", "nonce", "worker", "payload", "limits"}
            or value["kind"] != "REQUEST" or value["version"] != _VERSION
            or value["nonce"] != nonce or value["worker"] != "summarize"):
        _fail("worker service REQUEST shape is invalid")
    payload = value["payload"]
    if type(payload) is not dict or set(payload) != {"text"} or type(payload["text"]) is not str:
        _fail("worker service REQUEST payload is invalid")
    limits = value["limits"]
    if type(limits) is not dict or set(limits) != set(asdict(ceiling)):
        _fail("worker service REQUEST limits are incomplete")
    requested = IsolationLimits(**limits)
    if any(getattr(requested, key) > getattr(ceiling, key) for key in asdict(ceiling)):
        _fail("worker service REQUEST exceeds fixed limits")
    return requested


def _assert_null_outputs() -> None:
    """Require both standard output descriptors to refer to the null device."""
    null = os.stat(os.devnull)
    for descriptor in (1, 2):
        try:
            current = os.fstat(descriptor)
        except OSError as exc:
            raise ContractError("worker standard output is unavailable") from exc
        if not stat.S_ISCHR(current.st_mode) or current.st_rdev != null.st_rdev:
            _fail("worker standard output is not /dev/null")


def _assert_no_extra_fds() -> None:
    """Refuse inherited descriptors beyond standard input, output, and error."""
    try:
        descriptors = [int(name) for name in os.listdir("/proc/self/fd")]
    except (OSError, ValueError) as exc:
        raise ContractError("worker descriptor table cannot be read") from exc
    for descriptor in descriptors:
        if descriptor <= 2:
            continue
        try:
            os.fstat(descriptor)
        except OSError as exc:
            if exc.errno == errno.EBADF:  # /proc/self/fd's own transient descriptor.
                continue
            raise ContractError("worker descriptor table is unreadable") from exc
        _fail("worker inherited an extra descriptor")


def _set_nondumpable() -> None:
    """Disable process dumpability and verify that the kernel accepted it."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = (ctypes.c_int,) + (ctypes.c_ulong,) * 4
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(4, 0, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 0:
        _fail("worker process is still dumpable")


def _assert_worker_identity(worker_uid: int, worker_gid: int) -> None:
    """Require the fixed nonroot identity, allowed groups, and no capabilities.

    Supplementary groups may contain only the primary worker GID. Identity
    refusals or unreadable privilege state raise `ContractError`.
    """
    # systemd initgroups retains the primary GID in the supplementary vector.
    # Repeating that GID adds no authority; every other group must refuse.
    if (os.geteuid() != worker_uid or os.getegid() != worker_gid
            or any(group != worker_gid for group in os.getgroups()) or worker_uid == 0):
        _fail("worker process has an unexpected OS identity")
    try:
        status = Path("/proc/self/status").read_text(encoding="ascii")
    except OSError as exc:
        raise ContractError("worker process privileges cannot be read") from exc
    for field, expected in (("Uid", worker_uid), ("Gid", worker_gid)):
        values = re.findall(rf"^{field}:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$",
                            status, re.MULTILINE)
        if len(values) != 1 or any(int(value) != expected for value in values[0]):
            _fail("worker process identity state is ambiguous")
    if re.findall(r"^NoNewPrivs:\s+(\d+)\s*$", status, re.MULTILINE) != ["1"]:
        _fail("worker process can gain privileges")
    for field in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        values = re.findall(rf"^{field}:\s+([0-9a-fA-F]+)$", status, re.MULTILINE)
        if len(values) != 1 or int(values[0], 16) != 0:
            _fail("worker process retains Linux capabilities")


def _worker_root(worker_uid: int, worker_gid: int) -> str:
    """Return the private runtime directory bound to this systemd instance."""
    value = os.environ.get("RUNTIME_DIRECTORY")
    if (type(value) is not str or not value.startswith("/run/geniusnew-worker/jobs/")
            or ":" in value or os.path.normpath(value) != value):
        _fail("worker has no fixed manager-owned runtime directory")
    try:
        info = os.lstat(value)
    except OSError as exc:
        raise ContractError("worker runtime directory cannot be read") from exc
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != worker_uid
            or info.st_gid != worker_gid or stat.S_IMODE(info.st_mode) != 0o700):
        _fail("worker runtime directory is not private")
    WorkerLifecycle._root_controlled(Path(value).parent, regular=False)
    try:
        group = Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise ContractError("worker unit identity cannot be read") from exc
    if (len(group) != 1 or group[0] != "0::/system.slice/geniusnew-worker@"
            + Path(value).name + ".service"):
        _fail("worker runtime directory does not match its unit instance")
    return value


def _service_read_paths() -> tuple[str, ...]:
    """Return sandbox read roots limited to worker packages and Python runtime."""
    import cryptography

    # Import search roots are broader than sandbox read roots. In particular,
    # a source installation must not expose files beside the geniusnew package.
    return tuple(sorted({os.path.realpath(path) for path in (
        str(Path(__file__).parent), str(Path(cryptography.__file__).parent),
        *_python_runtime_paths()[1:])}))


def _run_service_connection(connection: socket.socket, *, core_uid: int,
                            worker_uid: int, worker_gid: int) -> None:
    """Authenticate the Core, install isolation, and process exactly one job.

    Authenticate the peer UID and worker setup before READY and payload
    receipt. Change process limits, dumpability, working directory, and
    environment; install irreversible filesystem, syscall, and audit
    restrictions. Require one request followed by EOF. Ordinary worker
    exceptions and sandbox denials become closed error codes in the result;
    setup, protocol, and response errors propagate. The caller closes the
    connection.
    """
    _assert_worker_identity(worker_uid, worker_gid)
    _assert_null_outputs()
    _assert_no_extra_fds()
    if (connection.family != socket.AF_UNIX or
            connection.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM or
            connection.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 0):
        _fail("worker did not receive an accepted AF_UNIX stream")
    peer = _UCREDS.unpack(connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                _UCREDS.size))
    if peer[0] <= 0 or peer[1] != core_uid:
        _fail("worker Core peer UID is invalid")
    descriptor = connection.fileno()
    connection.setblocking(False)
    hello = _plain_frame(descriptor, limit=_HELLO_LIMIT,
                         deadline=time.monotonic() + 5)
    nonce = _validate_hello(hello)
    root = _worker_root(worker_uid, worker_gid)
    ceiling = IsolationLimits()
    _set_resource_limits(ceiling)
    _set_nondumpable()
    os.chdir(root)
    os.environ.clear()
    os.environ.update({"HOME": root, "TMPDIR": root, "TMP": root, "TEMP": root})
    state = {"violated": False, "bootstrap": True}
    worker = DeterministicSummarizer()
    read_paths = _service_read_paths()
    _install_filesystem_rules(root, read_paths)
    _install_process_filter()
    import tempfile
    import sys

    tempfile.tempdir = root
    sys.addaudithook(_audit_hook(root, state, read_paths))
    _plain_write(descriptor, {"kind": "READY", "version": _VERSION,
                              "nonce": nonce}, time.monotonic() + 5)
    request = _plain_frame(descriptor, limit=_PAYLOAD_LIMIT,
                           deadline=time.monotonic() + 5)
    requested = _validate_request(request, nonce, ceiling)
    _plain_expect_eof(descriptor, time.monotonic() + 5)
    _set_resource_limits(requested)
    state["bootstrap"] = False
    request_wire = canonical(request)
    digest = hashlib.sha256(request_wire).hexdigest()
    result = {"kind": "RESULT", "version": _VERSION, "nonce": nonce,
              "request_digest": digest}
    def wall_expired(_signal: int, _frame: Any) -> None:
        """Interrupt worker execution when its local wall timer expires."""
        raise _WorkerResourceExhausted()

    previous_handler = signal.signal(signal.SIGALRM, wall_expired)
    signal.setitimer(signal.ITIMER_REAL, float(requested.wall_seconds))
    try:
        output = worker.run({"text": request["payload"]["text"]})
        if state["violated"]:
            result["error"] = "ISOLATION_VIOLATED"
        elif (type(output) is not dict or set(output) != {"text"}
              or type(output["text"]) is not str):
            result["error"] = "OUTPUT_REJECTED"
        else:
            result["output"] = output
    except _SandboxDenied:
        result["error"] = "ISOLATION_VIOLATED"
    except MemoryError:
        result["error"] = "RESOURCE_EXHAUSTED"
    except _WorkerResourceExhausted:
        result["error"] = "RESOURCE_EXHAUSTED"
    except Exception:
        result["error"] = "WORKER_FAILED"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    _plain_write(descriptor, result, time.monotonic() + 5)


def main() -> int:
    """Serve one inherited connection, returning two if it cannot complete.

    Return zero after a completed exchange or two for any setup/exchange
    exception, closing the wrapped socket on exit. Argument parsing occurs
    first and may raise `SystemExit`.
    """
    parser = argparse.ArgumentParser(prog="python -m geniusnew.worker_service")
    parser.add_argument("--core-uid", type=int, required=True)
    parser.add_argument("--worker-uid", type=int, required=True)
    parser.add_argument("--worker-gid", type=int, required=True)
    args = parser.parse_args()
    try:
        if any(value <= 0 for value in (args.core_uid, args.worker_uid, args.worker_gid)):
            _fail("worker service identity is invalid")
        connection = socket.socket(fileno=0)
        with connection:
            _run_service_connection(connection, core_uid=args.core_uid,
                                    worker_uid=args.worker_uid,
                                    worker_gid=args.worker_gid)
    except BaseException:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
