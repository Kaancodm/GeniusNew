"""Fresh-interpreter entry point for GeniusNew worker isolation."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from typing import Any

from .contracts import ContractError, canonical
from .isolation import (
    IsolationLimits,
    _MAX_CHILD_REQUEST_BYTES,
    _SandboxDenied,
    _audit_hook,
    _install_process_filter,
    _set_resource_limits,
    _write_message,
)
from .workers import Worker


def _decode_request(data: bytes) -> dict[str, Any]:
    if not data or len(data) > _MAX_CHILD_REQUEST_BYTES:
        raise ContractError("invalid isolation request")
    try:
        value = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("invalid isolation request") from exc
    if not isinstance(value, dict) or canonical(value) != data:
        raise ContractError("invalid isolation request")
    if set(value) != {"version", "worker", "payload", "limits"} or value["version"] != 1:
        raise ContractError("invalid isolation request")
    if not isinstance(value["worker"], dict):
        raise ContractError("invalid isolation worker")
    if set(value["worker"]) != {"module", "qualname", "state", "tool"}:
        raise ContractError("invalid isolation worker")
    return value


def _resolve_worker(spec: dict[str, Any]) -> Worker:
    module_name = spec["module"]
    qualname = spec["qualname"]
    state = spec["state"]
    tool = spec["tool"]
    if not all(type(value) is str and value for value in (module_name, qualname, tool)):
        raise ContractError("invalid isolation worker")
    if not isinstance(state, dict) or any(type(key) is not str for key in state):
        raise ContractError("invalid isolation worker")
    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    if not isinstance(target, type) or not issubclass(target, Worker):
        raise ContractError("invalid isolation worker")
    worker = object.__new__(target)
    object.__getattribute__(worker, "__dict__").update(state)
    class_tool = type.__getattribute__(target, "tool")
    if class_tool != tool:
        raise ContractError("isolation worker tool changed across the boundary")
    return worker


def child_entry(root: str) -> int:
    response_fd = os.dup(1)
    devnull = os.open(os.devnull, os.O_RDWR)
    request_data = sys.stdin.buffer.read(_MAX_CHILD_REQUEST_BYTES + 1)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)

    try:
        request = _decode_request(request_data)
        limits = IsolationLimits(**request["limits"])
        _set_resource_limits(limits)
        root = os.path.realpath(root)
        os.chdir(root)
        os.environ.clear()
        os.environ.update({"HOME": root, "TMPDIR": root, "TEMP": root, "TMP": root})
        tempfile.tempdir = root
        state = {"violated": False}
        # The filter goes in first: the hook refuses ctypes, which installing
        # it needs. From here the kernel kills any process start the hook
        # cannot see; a failure to install lands in the outer handler.
        _install_process_filter()
        sys.addaudithook(_audit_hook(root, state))

        try:
            worker = _resolve_worker(request["worker"])
            output = worker.run(dict(request["payload"]))
            if state["violated"]:
                _write_message(response_fd, {"kind": "isolation_violated"})
            else:
                _write_message(response_fd, {"kind": "ok", "output": output})
        except _SandboxDenied:
            _write_message(response_fd, {"kind": "isolation_violated"})
        except MemoryError:
            _write_message(response_fd, {"kind": "resource_exhausted"})
        except Exception:
            _write_message(response_fd, {"kind": "worker_failed"})
    except BaseException:
        try:
            _write_message(response_fd, {"kind": "isolation_violated"})
        except BaseException:
            pass
    finally:
        os.close(response_fd)
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    return child_entry(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
