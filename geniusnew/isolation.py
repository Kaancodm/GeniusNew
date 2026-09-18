"""Process-level isolation for the least-trusted worker code.

Roadmap step 11 requires a process boundary with no network access, no writes
outside a temporary directory, and explicit time/resource limits. The signing
key stays in the parent process: only a description of the worker plus its
payload crosses an exec boundary, and the parent validates and signs whatever
comes back.

This is deliberately a v0.1 process sandbox, not a microVM. The boundary uses a
fresh Python interpreter, POSIX resource limits and Python's audit-hook
mechanism. It does not claim to contain hostile native code or raw syscalls; the
stronger OS boundary belongs after v0.1.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import select
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

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
})


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
        if event == "open" and len(args) >= 3 and _open_is_write(args[1], args[2]):
            # Low-level os.open write calls are refused entirely because the
            # audit event does not expose dir_fd. Normal builtins.open writes
            # are accepted only under the empty per-job sandbox root.
            if args[1] is None or not _path_is_inside(root, args[0]):
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
        process = subprocess.Popen(
            [sys.executable, "-m", "geniusnew.isolation_child", os.path.realpath(root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=_minimal_environment(),
        )
        assert process.stdin is not None
        try:
            process.stdin.write(request)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            _kill_process(process)
            raise _WorkerIsolationViolation() from None
        data, status = _read_process(process, float(limits.wall_seconds))
        return _decode_child_message(data, status)


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
