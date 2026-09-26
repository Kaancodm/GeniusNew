"""Process-level isolation for the least-trusted worker code.

Roadmap step 11 requires a process boundary with no network access, no writes
outside a temporary directory, and explicit time/resource limits. The signing
key stays in the parent process: only a description of the worker plus its
payload crosses an exec boundary, and the parent validates and signs whatever
comes back.

This is deliberately a v0.1 process sandbox, not a microVM. The boundary uses a
fresh Python interpreter, POSIX resource limits, Python's audit-hook mechanism
and one kernel-enforced rule: a seccomp filter that kills the child the moment
it tries to start another process or program. The hook only sees what Python
reports, and `_posixsubprocess` reports nothing; the filter sees the syscall.
Reads are still bounded only by the hook (`SECURITY.md`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping

from .contracts import ContractError, canonical
from .results import WorkerAuthority
from .workers import (
    Worker,
    WorkerRunner,
    _WorkerIsolationViolation,
    _WorkerResourceExhausted,
)

_MAX_CHILD_MESSAGE_BYTES = 32 * 1024
_MAX_CHILD_REQUEST_BYTES = 32 * 1024
_WRITE_FLAGS = (
    getattr(os, "O_WRONLY", 0)
    | getattr(os, "O_RDWR", 0)
    | getattr(os, "O_CREAT", 0)
    | getattr(os, "O_TRUNC", 0)
    | getattr(os, "O_APPEND", 0)
)
_FORBIDDEN_FS_EVENTS = frozenset({
    "os.remove",
    "os.rename",
    "os.rmdir",
    "os.mkdir",
    "os.chmod",
    "os.chown",
    "os.utime",
    "os.truncate",
    "os.symlink",
    "os.link",
})
_FORBIDDEN_PROCESS_EVENTS = frozenset({
    "os.fork",
    "os.forkpty",
    "os.posix_spawn",
    "os.kill",
    "os.killpg",
    "os.system",
    "pty.spawn",
    "subprocess.Popen",
    "resource.setrlimit",
    "resource.prlimit",
})
_FORBIDDEN_READ_ROOTS = ("/proc", "/sys", "/dev")

# Per architecture: the audit arch the filter must see, the syscalls that start
# a process or a program, and the two clone variants. aarch64 has no fork or
# vfork syscall; libc builds both from clone there.
_FILTER_ARCHES = {
    "x86_64": {"arch": 0xC000003E, "kill": (57, 58, 59, 322),
               "clone": 56, "clone3": 435},
    "aarch64": {"arch": 0xC00000B7, "kill": (221, 281),
                "clone": 220, "clone3": 435},
}
_X32_SYSCALL_BIT = 0x40000000
_CLONE_THREAD = 0x00010000
_ENOSYS = 38
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_JGE_K = 0x35
_BPF_JSET_K = 0x45
_BPF_RET_K = 0x06
_SIGSYS = getattr(signal, "SIGSYS", None)  # absent on Windows
_PR_SET_SECCOMP = 22
_PR_SET_NO_NEW_PRIVS = 38
_SECCOMP_MODE_FILTER = 2


def _fail(message: str) -> None:
    raise ContractError(message)


@dataclass(frozen=True)
class IsolationLimits:
    """Bound one worker process."""

    wall_seconds: float = 2.0
    cpu_seconds: int = 1
    memory_bytes: int = 512 * 1024 * 1024
    max_file_bytes: int = 1024 * 1024
    max_open_files: int = 64

    def __post_init__(self) -> None:
        if (type(self.wall_seconds) not in (int, float)
                or isinstance(self.wall_seconds, bool)
                or not 0.05 <= float(self.wall_seconds) <= 30.0):
            _fail("wall_seconds must be between 0.05 and 30")
        if type(self.cpu_seconds) is not int or not 1 <= self.cpu_seconds <= 10:
            _fail("cpu_seconds must be between 1 and 10")
        if (type(self.memory_bytes) is not int
                or not 128 * 1024 * 1024 <= self.memory_bytes <= 2 * 1024 * 1024 * 1024):
            _fail("memory_bytes must be between 128 MiB and 2 GiB")
        if (type(self.max_file_bytes) is not int
                or not 4096 <= self.max_file_bytes <= 16 * 1024 * 1024):
            _fail("max_file_bytes must be between 4 KiB and 16 MiB")
        if type(self.max_open_files) is not int or not 16 <= self.max_open_files <= 256:
            _fail("max_open_files must be between 16 and 256")


class _SandboxDenied(BaseException):
    """Raised by the audit hook before a forbidden operation can happen."""


class _RemoteWorkerFailed(Exception):
    """The worker raised inside the fresh interpreter."""


def _resource_supported() -> bool:
    try:
        import resource  # noqa: F401
    except ImportError:
        return False
    return True


def _path_is_inside(root: str, value: Any) -> bool:
    if isinstance(value, int):
        return True
    try:
        path = os.fsdecode(os.fspath(value))
    except TypeError:
        return False
    target = os.path.realpath(os.path.abspath(path))
    try:
        return os.path.commonpath((root, target)) == root
    except ValueError:
        return False


def _open_is_write(mode: Any, flags: Any) -> bool:
    if isinstance(mode, str) and any(marker in mode for marker in ("w", "a", "x", "+")):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _sensitive_read(value: Any) -> bool:
    if isinstance(value, int):
        return False
    try:
        target = os.path.realpath(os.path.abspath(os.fsdecode(os.fspath(value))))
    except TypeError:
        return True
    return any(
        target == root or target.startswith(root + os.sep)
        for root in _FORBIDDEN_READ_ROOTS
    )


def _audit_hook(root: str, state: dict[str, bool]):
    """Return the child-side audit hook."""

    def deny() -> None:
        state["violated"] = True
        raise _SandboxDenied()

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event.startswith("socket."):
            deny()
        if (event in _FORBIDDEN_PROCESS_EVENTS
                or event.startswith("os.exec")
                or event.startswith("os.spawn")):
            deny()
        if event.startswith("ctypes."):
            deny()
        if event in _FORBIDDEN_FS_EVENTS:
            deny()
        if event == "open" and len(args) >= 3:
            if _open_is_write(args[1], args[2]):
                # Low-level os.open write calls are refused entirely because
                # the audit event does not expose dir_fd. Normal builtins.open
                # writes are accepted only under the empty per-job sandbox.
                if args[1] is None or not _path_is_inside(root, args[0]):
                    deny()
            elif _sensitive_read(args[0]):
                # The child gets a minimal environment and no inherited parent
                # descriptors. Blocking proc/sys/dev reads closes the obvious
                # path back into the parent's environment, memory and FDs.
                deny()

    return hook


def _set_resource_limits(limits: IsolationLimits) -> None:
    try:
        import resource
    except ImportError as exc:  # pragma: no cover - parent refuses first
        raise ContractError("process isolation requires POSIX resource limits") from exc

    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_file_bytes, limits.max_file_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits.max_open_files, limits.max_open_files))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _process_filter_supported() -> bool:
    # A 32-bit interpreter on a 64-bit kernel would present another audit arch
    # and be killed on its first syscall; refuse it here rather than there.
    return (sys.platform.startswith("linux")
            and struct.calcsize("P") == 8
            and os.uname().machine in _FILTER_ARCHES)


def _bpf(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("HBBI", code, jt, jf, k)


def _process_filter(machine: str) -> bytes:
    """Return a classic-BPF seccomp program for `machine`.

    Kill on: a foreign audit arch (a syscall ABI the table does not describe),
    x32 syscall numbers, fork, vfork, execve, execveat, and clone without
    CLONE_THREAD. Threads stay allowed because the interpreter may start them
    and they share the filter. clone3 passes its flags in memory the filter
    cannot read, so it gets ENOSYS and libc falls back to clone, which it can.
    """
    spec = _FILTER_ARCHES.get(machine)
    if spec is None:
        _fail("no seccomp process filter for this architecture")
    kill = _bpf(_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS)
    program = [
        _bpf(_BPF_LD_W_ABS, 0, 0, 4),                   # seccomp_data.arch
        _bpf(_BPF_JEQ_K, 1, 0, spec["arch"]),
        kill,
        _bpf(_BPF_LD_W_ABS, 0, 0, 0),                   # seccomp_data.nr
        _bpf(_BPF_JGE_K, 0, 1, _X32_SYSCALL_BIT),
        kill,
    ]
    for number in spec["kill"]:
        program += [_bpf(_BPF_JEQ_K, 0, 1, number), kill]
    program += [
        _bpf(_BPF_JEQ_K, 0, 1, spec["clone3"]),
        _bpf(_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | _ENOSYS),
        _bpf(_BPF_JEQ_K, 0, 3, spec["clone"]),
        _bpf(_BPF_LD_W_ABS, 0, 0, 16),                  # low word of args[0]
        _bpf(_BPF_JSET_K, 1, 0, _CLONE_THREAD),
        kill,
        _bpf(_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
    ]
    return b"".join(program)


def _install_process_filter(prctl: Callable[..., int] | None = None) -> None:
    """Make the kernel kill this process if it tries to start another one.

    Called in the child before the audit hook, which refuses ctypes. A filter
    cannot be removed once installed, so worker code cannot undo it either.
    `prctl` is injectable so a test can drive the refusals without filtering
    the test runner itself.
    """
    import ctypes

    class _SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    program = _process_filter(os.uname().machine)
    if prctl is None:
        prctl = ctypes.CDLL(None, use_errno=True).prctl
        prctl.argtypes = (ctypes.c_int,) + (ctypes.c_ulong,) * 4
        prctl.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(program, len(program))
    fprog = _SockFprog(len(program) // 8, ctypes.addressof(buffer))
    if prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        _fail("could not set no_new_privs for the worker")
    if prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.addressof(fprog), 0, 0) != 0:
        _fail("could not install the worker's seccomp filter")


def _worker_spec(worker: Worker) -> dict[str, Any]:
    cls = type(worker)
    module = type.__getattribute__(cls, "__module__")
    qualname = type.__getattribute__(cls, "__qualname__")
    state = dict(object.__getattribute__(worker, "__dict__"))
    tool = type.__getattribute__(cls, "tool")
    if "<locals>" in qualname or not module or not qualname:
        _fail("isolated worker class must be importable by module and qualified name")
    if any(type(key) is not str for key in state):
        _fail("isolated worker state keys must be strings")
    try:
        canonical(state)
    except ContractError as exc:
        raise ContractError("isolated worker state must be canonical JSON") from exc
    return {
        "module": module,
        "qualname": qualname,
        "state": state,
        "tool": tool,
    }


def _python_path() -> str:
    paths: list[str] = []
    for value in sys.path:
        if not isinstance(value, str):
            continue
        path = os.getcwd() if value == "" else os.path.abspath(value)
        if path not in paths:
            paths.append(path)
    return os.pathsep.join(paths)


def _request(worker: Worker, payload: Mapping[str, str],
             limits: IsolationLimits) -> bytes:
    value = {
        "version": 1,
        "worker": _worker_spec(worker),
        "payload": dict(payload),
        "limits": asdict(limits),
    }
    data = canonical(value)
    if len(data) > _MAX_CHILD_REQUEST_BYTES:
        _fail("isolated worker request is too large")
    return data


def _minimal_environment() -> dict[str, str]:
    return {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": _python_path(),
    }


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _read_process(process: subprocess.Popen[bytes],
                  wall_seconds: float) -> tuple[bytes, int]:
    assert process.stdout is not None
    fd = process.stdout.fileno()
    deadline = time.monotonic() + wall_seconds
    chunks: list[bytes] = []
    size = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_process(process)
            raise _WorkerResourceExhausted()
        ready, _, _ = select.select((fd,), (), (), remaining)
        if not ready:
            _kill_process(process)
            raise _WorkerResourceExhausted()
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        size += len(chunk)
        if size > _MAX_CHILD_MESSAGE_BYTES:
            _kill_process(process)
            raise _WorkerIsolationViolation()
        chunks.append(chunk)

    remaining = max(0.01, deadline - time.monotonic())
    try:
        status = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        _kill_process(process)
        raise _WorkerResourceExhausted() from None
    return b"".join(chunks), status


def _decode_child_message(data: bytes, status: int) -> Any:
    # Only the seccomp filter sends SIGSYS: the child tried to start a process.
    if _SIGSYS is not None and status == -_SIGSYS:
        raise _WorkerIsolationViolation()
    if status < 0:
        raise _WorkerResourceExhausted()
    if status != 0:
        raise _RemoteWorkerFailed()
    try:
        value = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _WorkerIsolationViolation() from None
    if not isinstance(value, dict) or canonical(value) != data:
        raise _WorkerIsolationViolation()

    kind = value.get("kind")
    if kind == "ok" and set(value) == {"kind", "output"}:
        return value["output"]
    if kind == "worker_failed" and set(value) == {"kind"}:
        raise _RemoteWorkerFailed()
    if kind == "isolation_violated" and set(value) == {"kind"}:
        raise _WorkerIsolationViolation()
    if kind == "resource_exhausted" and set(value) == {"kind"}:
        raise _WorkerResourceExhausted()
    if kind == "output_rejected" and set(value) == {"kind"}:
        return None
    raise _WorkerIsolationViolation()


def _run_isolated(worker: Worker, payload: Mapping[str, str],
                  limits: IsolationLimits) -> Any:
    request = _request(worker, payload, limits)
    with tempfile.TemporaryDirectory(prefix="geniusnew-worker-") as root:
        with subprocess.Popen(
            [sys.executable, "-m", "geniusnew.isolation_child", os.path.realpath(root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=_minimal_environment(),
        ) as process:
            assert process.stdin is not None
            try:
                try:
                    process.stdin.write(request)
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    raise _WorkerIsolationViolation() from None
                data, status = _read_process(process, float(limits.wall_seconds))
                return _decode_child_message(data, status)
            finally:
                # Reap even on an unexpected read/write failure before Popen's
                # context manager closes both pipes. Never rely on GC, or let
                # __exit__ wait indefinitely for a child after a parent error.
                _kill_process(process)


def _write_message(fd: int, message: dict[str, Any]) -> None:
    try:
        data = canonical(message)
    except ContractError:
        data = canonical({"kind": "output_rejected"})
    if len(data) > _MAX_CHILD_MESSAGE_BYTES:
        data = canonical({"kind": "output_rejected"})
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


class IsolatedWorkerRunner(WorkerRunner):
    """Run only the work function in a constrained fresh interpreter."""

    def __init__(self, worker: Worker, *, authority: WorkerAuthority,
                 limits: IsolationLimits | None = None) -> None:
        if not _resource_supported():
            _fail("process isolation requires POSIX resource limits")
        if not _process_filter_supported():
            _fail("process isolation requires a Linux seccomp process filter")
        if limits is None:
            limits = IsolationLimits()
        if not isinstance(limits, IsolationLimits):
            _fail("limits must be IsolationLimits")
        super().__init__(worker, authority=authority)
        # Capture only JSON data needed to reconstruct the worker; do not pass
        # this runner or its signing authority across the exec boundary.
        _worker_spec(worker)
        self._limits = limits

    @property
    def limits(self) -> IsolationLimits:
        return self._limits

    def _run_worker(self, payload: Mapping[str, str]) -> Mapping[str, str]:
        return _run_isolated(self._worker, payload, self._limits)
