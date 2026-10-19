"""The core side of the A2/C5 system probe.

Runs as the probe's core UID in a transient systemd unit, without root,
capabilities or systemd write access. One dispatch is: take the core-only
lease without blocking, prove from the kernel cgroup, systemd and the managed
job paths that no earlier worker instance lives, connect, send HELLO, read
READY with kernel credentials on every chunk, bind the sender to one unit
invocation, and only then send the payload. The lease is released after
systemd, the cgroup and the job path all confirm the instance ended.

Prints one JSON object per line: progress events and a final outcome. Neither
carries the payload, a secret or worker text; reasons come from the closed set
of refusal messages in `contract.py`.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from .contract import (
    ABSENT,
    BODY_MAX,
    EMPTY,
    HANDSHAKE_MAX,
    POPULATED,
    PRESENT,
    READY_FIELDS,
    RESULT_FIELDS,
    UNIT_PROPERTIES,
    UNKNOWN,
    CredentialStream,
    ProbeRefused,
    bind_invocation,
    canonical,
    cgroup_populated,
    check_result,
    classify_errno,
    decode_body,
    digest,
    encode_frame,
    end_confirmed,
    hello,
    new_nonce,
    parse_properties,
    parse_unit_list,
    peer_credentials,
    request,
    require_idle,
    require_nonce,
    require_text,
    unit_has_ended,
    unit_of_process,
)

_EMIT_LOCK = threading.Lock()
_SYSTEMCTL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "SYSTEMD_PAGER": "cat",
                  "SYSTEMD_COLORS": "0", "SYSTEMD_LESS": ""}


def emit(value: dict[str, Any]) -> None:
    with _EMIT_LOCK:
        sys.stdout.write(canonical(value).decode("ascii") + "\n")
        sys.stdout.flush()


def _systemctl(config: dict[str, Any], *arguments: str) -> str | None:
    try:
        completed = subprocess.run([config["systemctl"], *arguments], capture_output=True,
                                   text=True, timeout=5, env=_SYSTEMCTL_ENV)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _cgroup_state(path: str) -> str:
    try:
        with open(os.path.join(path, "cgroup.events"), encoding="ascii") as handle:
            return POPULATED if cgroup_populated(handle.read()) else EMPTY
    except FileNotFoundError:
        pass
    except (OSError, ProbeRefused):
        return UNKNOWN
    try:
        os.lstat(path)
    except FileNotFoundError:
        return ABSENT
    except OSError:
        return UNKNOWN
    return UNKNOWN  # the directory exists but its events file does not


def _path_state(path: str) -> str:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return ABSENT
    except OSError:
        return UNKNOWN
    return PRESENT


def _job_entries(config: dict[str, Any]) -> list[str] | None:
    try:
        return sorted(os.listdir(config["jobs"]))
    except OSError:
        return None


def check_idle(config: dict[str, Any]) -> None:
    slice_state = _cgroup_state(os.path.join(config["cgroup_root"], config["slice"]))
    listing = _systemctl(config, "list-units", "--all", "--plain", "--no-legend",
                         "--no-pager", "--full", "--type=service", f"{config['prefix']}*@*.service")
    units = parse_unit_list(require_text(listing))
    require_idle(slice_state, units, _job_entries(config))


def _unit_properties(config: dict[str, Any], unit: str) -> dict[str, str]:
    text = _systemctl(config, "show", f"--property={','.join(UNIT_PROPERTIES)}", unit)
    return parse_properties(require_text(text))


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="ascii") as handle:
            return handle.read()
    except OSError:
        return None


def wait_for_end(config: dict[str, Any], bound: dict[str, str] | None) -> dict[str, Any]:
    """Hold the slot until the instance has ended and been cleaned up, or say why not."""
    started = time.monotonic()
    deadline = started + config["deadlines"]["end"]
    while True:
        try:
            done = True
            if bound is not None:
                ended = unit_has_ended(_unit_properties(config, bound["unit"]),
                                       bound["invocation"])
                cgroup = _cgroup_state(os.path.join(config["cgroup_root"],
                                                    bound["cgroup"].lstrip("/")))
                job = _path_state(os.path.join(config["jobs"], bound["instance"]))
                done = end_confirmed(ended, cgroup, job)
            if done:
                check_idle(config)
                return {"end_confirmed": True,
                        "end_wait_ms": int((time.monotonic() - started) * 1000)}
        except ProbeRefused as exc:
            reason = str(exc)
            if "unknown" in reason:
                return {"end_confirmed": False, "end_reason": reason}
            if time.monotonic() >= deadline:
                return {"end_confirmed": False, "end_reason": reason}
        if time.monotonic() >= deadline:
            return {"end_confirmed": False, "end_reason": "end not confirmed before deadline"}
        time.sleep(0.05)


def _open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def _payload(config: dict[str, Any]) -> str:
    # The shared config is readable by every worker UID; the payload lives in a
    # core-only file so a refused identity cannot hold it before the handshake.
    with open(config["payload_file"], encoding="ascii") as handle:
        return handle.read()


def _bind(config: dict[str, Any], stream: CredentialStream) -> dict[str, str]:
    sender = stream.sender
    assert sender is not None
    proc = require_text(_read_text(f"/proc/{sender.pid}/cgroup"))
    pattern = re.compile(re.escape(config["prefix"]) + r"[a-z]+@[A-Za-z0-9_.:\\-]+\.service")
    path, unit = unit_of_process(proc, config["slice"], pattern)
    invocation = bind_invocation(_unit_properties(config, unit), sender.pid, path)
    instance = unit.split("@", 1)[1][:-len(".service")]
    return {"unit": unit, "invocation": invocation, "cgroup": path, "instance": instance}


def dispatch(config: dict[str, Any], scenario: str, *, late_passcred: bool = False,
             notify: Callable[[str], None] = lambda event: emit({"event": event})
             ) -> dict[str, Any]:
    out: dict[str, Any] = {"scenario": scenario, "connected": False,
                           "payload_bytes_sent": 0}
    lease = open(config["lease"], "rb")  # an open file description of our own
    try:
        try:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            out["outcome"] = "refused_lease_busy"
            return out
        notify("lease_acquired")
        try:
            check_idle(config)
        except ProbeRefused as exc:
            out.update(outcome="refused_before_connect", reason=str(exc))
            return out
        out.update(_exchange(config, scenario, late_passcred, out, notify))
        return out
    finally:
        lease.close()


def _exchange(config: dict[str, Any], scenario: str, late_passcred: bool,
              out: dict[str, Any], notify: Callable[[str], None]) -> dict[str, Any]:
    deadlines = config["deadlines"]
    fds_before = _open_fds()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound: dict[str, str] | None = None
    received_at: float | None = None
    try:
        try:
            sock.settimeout(deadlines["handshake"])
            sock.connect(config["sockets"][scenario])
        except OSError as exc:
            return {"outcome": "connect_failed",
                    "reason": errno.errorcode.get(exc.errno or 0, "unknown errno")}
        out["connected"] = True
        notify("connected")
        # What a client sees through SO_PEERCRED is the systemd listener, not
        # the instance that will answer: recorded, never relied on.
        out["listener_uid"] = peer_credentials(sock).uid
        if late_passcred:
            time.sleep(deadlines["late_passcred"])
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        nonce = new_nonce()
        stream = CredentialStream(sock, uid=config["uids"]["worker"],
                                  gid=config["gids"]["worker"])
        try:
            sock.sendall(encode_frame(hello(nonce), HANDSHAKE_MAX))
            handshake_deadline = time.monotonic() + deadlines["handshake"]
            ready = decode_body(stream.read_frame(HANDSHAKE_MAX, handshake_deadline),
                                "ready", READY_FIELDS)
            require_nonce(ready, nonce)
            bound = _bind(config, stream)
        except ProbeRefused as exc:
            out["ready_chunks"] = stream.chunks
            return {"outcome": "refused_before_payload", "reason": str(exc)}
        except OSError as exc:
            return {"outcome": "refused_before_payload",
                    "reason": errno.errorcode.get(exc.errno or 0, "unknown errno")}
        out.update(ready_chunks=stream.chunks, sender_is_main_pid=True, unit=bound["unit"])

        frame = encode_frame(request(nonce, _payload(config)), BODY_MAX)
        sock.sendall(frame)
        out["payload_bytes_sent"] = len(frame)
        notify("payload_sent")
        result_deadline = time.monotonic() + deadlines["result"]
        try:
            value = decode_body(stream.read_frame(BODY_MAX, result_deadline),
                                "result", RESULT_FIELDS)
            output = check_result(value, nonce, digest(frame[4:]))
            received_at = time.monotonic()
            out["result_received"] = True
            notify("result_received")
            # With Accept=yes systemd keeps its own copy of the connection until
            # the instance is dead, so this EOF also waits for the whole cgroup.
            stream.expect_eof(result_deadline)
            out["eof_wait_ms"] = int((time.monotonic() - received_at) * 1000)
        except ProbeRefused as exc:
            return {"outcome": "no_valid_result", "reason": str(exc),
                    "result_chunks": stream.chunks}
        except OSError as exc:
            return {"outcome": "no_valid_result",
                    "reason": errno.errorcode.get(exc.errno or 0, "unknown errno")}
        notify("eof")
        return {"outcome": "completed", "output": summarize(output)}
    finally:
        sock.close()
        out["descriptors_leaked"] = _open_fds() - fds_before
        if out["connected"]:
            out.update(wait_for_end(config, bound))
        if received_at is not None:
            out["result_to_end_ms"] = int((time.monotonic() - received_at) * 1000)


def summarize(output: dict[str, Any]) -> dict[str, Any]:
    """Keep only counts, flags and access classes from untrusted worker output."""
    summary: dict[str, Any] = {}
    if type(output.get("length")) is int:
        summary["length"] = output["length"]
    targets = output.get("targets")
    if isinstance(targets, dict):
        summary["targets"] = {
            str(name)[:40]: {str(way)[:10]: (value if value in ("denied", "readable",
                                                                  "missing", "error")
                                             else "error")
                             for way, value in attempts.items()}
            for name, attempts in targets.items() if isinstance(attempts, dict)}
    identity = output.get("identity")
    if isinstance(identity, dict):
        summary["identity"] = {
            key: identity[key] for key in ("resuid", "resgid", "groups", "cap_effective_zero",
                                           "cap_permitted_zero", "no_new_privs", "dumpable")
            if key in identity and (type(identity[key]) in (int, bool) or (
                isinstance(identity[key], list)
                and all(type(item) is int for item in identity[key])))}
    return summary


def two_threads(config: dict[str, Any], scenario: str) -> dict[str, Any]:
    barrier = threading.Barrier(2)
    outcomes: list[dict[str, Any]] = []
    lock = threading.Lock()

    def run() -> None:
        barrier.wait()
        value = dispatch(config, scenario, notify=lambda event: None)
        with lock:
            outcomes.append(value)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return {"outcome": "two_threads", "threads": sorted(
        outcomes, key=lambda value: value.get("outcome", ""))}


def stranger(config: dict[str, Any], scenario: str) -> dict[str, Any]:
    """Act as a client the worker must not answer: send HELLO, count what comes back."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            sock.settimeout(config["deadlines"]["handshake"])
            sock.connect(config["sockets"][scenario])
        except OSError as exc:
            return {"outcome": "connect_failed", "access": classify_errno(exc.errno),
                    "reason": errno.errorcode.get(exc.errno or 0, "unknown errno")}
        received, ended = 0, False
        try:
            sock.sendall(encode_frame(hello(new_nonce()), HANDSHAKE_MAX))
        except OSError:
            return {"outcome": "stranger", "bytes_received": 0, "eof": True}
        deadline = time.monotonic() + config["deadlines"]["handshake"]
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
            except TimeoutError:
                break
            except OSError:
                ended = True
                break
            if not chunk:
                ended = True
                break
            received += len(chunk)
        return {"outcome": "stranger", "bytes_received": received, "eof": ended}
    finally:
        sock.close()


def raw_second(config: dict[str, Any], scenario: str) -> dict[str, Any]:
    """While one leased dispatch runs, connect again without the lease."""
    started = threading.Event()
    first: dict[str, Any] = {}

    def run() -> None:
        first.update(dispatch(config, scenario, notify=lambda event: (
            started.set() if event == "payload_sent" else None)))
        started.set()

    thread = threading.Thread(target=run)
    thread.start()
    started.wait(config["deadlines"]["handshake"] + config["deadlines"]["result"])
    second = stranger(config, scenario)
    thread.join()
    return {"outcome": "raw_second", "first": first, "second": second}


def own_secret(config: dict[str, Any], _: str) -> dict[str, Any]:
    """Positive control: the core itself can open its secret, so a denial means something."""
    try:
        os.close(os.open(config["targets"]["root_secret"]["path"], os.O_RDONLY | os.O_CLOEXEC))
        access = classify_errno(None)
    except OSError as exc:
        access = classify_errno(exc.errno)
    return {"outcome": "own_secret", "access": access, "groups": sorted(os.getgroups())}


MODES: dict[str, Callable[[dict[str, Any], str], dict[str, Any]]] = {
    "dispatch": dispatch,
    "late-passcred": lambda config, scenario: dispatch(config, scenario, late_passcred=True),
    "two-threads": two_threads,
    "stranger": stranger,
    "raw-second": raw_second,
    "own-secret": own_secret,
}


def main(argv: list[str]) -> int:
    if len(argv) != 4 or argv[2] not in MODES:
        return 2
    with open(argv[1], "rb") as handle:
        config = json.loads(handle.read())
    if argv[3] not in config["sockets"]:
        return 2
    outcome = MODES[argv[2]](config, argv[3])
    emit({"event": "outcome", "mode": argv[2], **outcome})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
