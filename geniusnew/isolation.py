"""Process-level isolation for the least-trusted worker code.

Roadmap step 11 requires a process boundary with no network access, no writes
outside a temporary directory, and explicit time/resource limits. The signing
key stays in the parent process: only ``Worker.run(payload)`` crosses this
boundary, and the parent validates and signs whatever comes back.

This is deliberately a v0.1 process sandbox, not a microVM. The boundary uses
POSIX resource limits plus Python's audit-hook mechanism, so it constrains
ordinary Python worker code and the escape attempts covered by the tests. It
does not claim to contain hostile native code or a preloaded FFI capable of raw
syscalls; that stronger boundary is outside the v0.1 scope.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import select
import signal
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
    """Bound one worker process.

    Hard limits are intentionally small because v0.1 workers are deterministic
    pure functions. Raising them later is a deployment decision, not something
    untrusted input may request.
    """

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
    """The worker raised inside the child process."""


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
    """Return the child-side audit hook.

    Audit hooks cannot be removed through Python's public API. The state bit is
    separate from the exception so a worker that catches ``BaseException``
    cannot turn a forbidden attempt into a successful result.
    """

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
            # audit event does not expose dir_fd; otherwise a worker could open
            # an outside directory read-only and write relative to that fd.
            if args[1] is None or not _path_is_inside(root, args[0]):
                deny()

    return hook


def _set_resource_limits(limits: IsolationLimits) -> None:
    try:
        import resource
    except ImportError as exc:  # pragma: no cover - exercised by support check
        raise ContractError("process isolation requires POSIX resource limits") from exc

    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_file_bytes, limits.max_file_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits.max_open_files, limits.max_open_files))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


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


def _child_main(worker: Worker, payload: Mapping[str, str], write_fd: int,
                root: str, limits: IsolationLimits) -> None:
    try:
        _set_resource_limits(limits)
        os.chdir(root)
        os.environ.clear()
        os.environ.update({"HOME": root, "TMPDIR": root, "TEMP": root, "TMP": root})
        tempfile.tempdir = root
        state = {"violated": False}
        sys.addaudithook(_audit_hook(root, state))

        try:
            output = worker.run(dict(payload))
            if state["violated"]:
                _write_message(write_fd, {"kind": "isolation_violated"})
            else:
                _write_message(write_fd, {"kind": "ok", "output": output})
        except _SandboxDenied:
            _write_message(write_fd, {"kind": "isolation_violated"})
        except MemoryError:
            _write_message(write_fd, {"kind": "resource_exhausted"})
        except Exception:
            _write_message(write_fd, {"kind": "worker_failed"})
    except BaseException:
        # Initialization and audit failures are fail-closed. No exception text
        # crosses the process boundary.
        try:
            _write_message(write_fd, {"kind": "isolation_violated"})
        except BaseException:
            pass
    finally:
        try:
            os.close(write_fd)
        finally:
            os._exit(0)


def _kill_and_reap(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def _read_child(read_fd: int, pid: int, wall_seconds: float) -> tuple[bytes, int]:
    deadline = time.monotonic() + wall_seconds
    chunks: list[bytes] = []
    size = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_and_reap(pid)
            raise _WorkerResourceExhausted()
        ready, _, _ = select.select((read_fd,), (), (), remaining)
        if not ready:
            _kill_and_reap(pid)
            raise _WorkerResourceExhausted()
        chunk = os.read(read_fd, 4096)
        if not chunk:
            break
        size += len(chunk)
        if size > _MAX_CHILD_MESSAGE_BYTES:
            _kill_and_reap(pid)
            raise _WorkerIsolationViolation()
        chunks.append(chunk)
    _, status = os.waitpid(pid, 0)
    return b"".join(chunks), status


def _decode_child_message(data: bytes, status: int) -> Any:
    if os.WIFSIGNALED(status):
        raise _WorkerResourceExhausted()
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
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
    with tempfile.TemporaryDirectory(prefix="geniusnew-worker-") as root:
        root = os.path.realpath(root)
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            _child_main(worker, payload, write_fd, root, limits)
        os.close(write_fd)
        try:
            data, status = _read_child(read_fd, pid, float(limits.wall_seconds))
        finally:
            os.close(read_fd)
        return _decode_child_message(data, status)


class IsolatedWorkerRunner(WorkerRunner):
    """Run only the work function in a constrained child process.

    The ``WorkerAuthority`` remains in the parent process inherited from
    ``WorkerRunner`` and never crosses into ``Worker.run``.
    """

    def __init__(self, worker: Worker, *, authority: WorkerAuthority,
                 limits: IsolationLimits | None = None) -> None:
        if not callable(getattr(os, "fork", None)):
            _fail("process isolation requires POSIX fork support")
        if limits is None:
            limits = IsolationLimits()
        if not isinstance(limits, IsolationLimits):
            _fail("limits must be IsolationLimits")
        try:
            import resource  # noqa: F401
        except ImportError as exc:
            raise ContractError("process isolation requires POSIX resource limits") from exc
        self._limits = limits
        super().__init__(worker, authority=authority)

    @property
    def limits(self) -> IsolationLimits:
        return self._limits

    def _run_worker(self, payload: Mapping[str, str]) -> Mapping[str, str]:
        return _run_isolated(self._worker, payload, self._limits)
