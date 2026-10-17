"""Driver of the A2/C5 system probe: run as root on a disposable Linux host.

    sudo python3 -m tools.a2c5_probe run --disposable-host --report-dir DIR

It creates four temporary system users and a socket group, writes temporary
units under /run/systemd/system, runs every scenario through transient core
units, judges the outcomes and removes all of it again. The report (JSON and
Markdown) holds host versions, the full commit SHA and one verdict per finding;
before it is written it is checked to contain no canary, secret or payload.

Exit status: 0 PASS, 1 FAIL, 2 HOLD. Anything the probe could not read or set
up is HOLD; only a run in which every finding is PASS is PASS.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime
import grp
import hashlib
import json
import os
import platform
import pwd
import queue
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contract import (
    DENIED,
    FAIL,
    HOLD,
    PASS,
    READABLE,
    ProbeRefused,
    access_verdict,
    canonical,
    overall,
    require_no_secret,
    require_safe_observations,
    safe_text,
)

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parent.parent
CODE_FILES = ("__init__.py", "contract.py", "core.py", "worker.py")
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
TOOLS = ("systemctl", "systemd-run", "useradd", "userdel", "groupadd", "groupdel", "git")
ROLES = ("core", "worker", "anchor", "foreign")
# Which UID serves each socket scenario. The impostor answers like a worker
# but under a foreign UID; every other instance runs as the worker UID.
SCENARIOS = {
    "ok": "worker", "fragmented": "worker", "early": "worker", "sender": "worker",
    "rights": "worker", "impostor": "foreign", "hold": "worker", "hang": "worker",
    "crash": "worker", "linger": "worker", "access": "worker",
}
DEADLINES = {"handshake": 5.0, "result": 4.0, "end": 20.0, "late_passcred": 1.5}
RUNTIME_MAX_SECONDS = 6
STOP_TIMEOUT_SECONDS = 2
LINGER_MIN_MS = 1500
NOT_IDLE_REASONS = frozenset({
    "a worker process is still alive", "a worker instance has not ended",
    "cleanup of an earlier job is pending"})
SECRET_TARGETS = ("root_secret", "db_dsn", "secrets_dir")
ANCHOR_TARGETS = ("anchor_state", "anchor_dir")
BOUNDARY_TARGETS = ("lease", "worker_socket", "core_environ")


@dataclass
class Finding:
    id: str
    check: str
    verdict: str
    observations: dict[str, Any] = field(default_factory=dict)


def _ways(names: Iterable[str], ways: Iterable[str] = ("python", "libc")) -> list[str]:
    return [f"{name}:{way}" for name in names for way in ways]


# --- Judgements: pure, so a test can show that missing data is never PASS ----

def judge_preflight(*, disposable: bool, euid: int, systemd_init: bool, cgroup2: bool,
                    system_bus: bool, tools: Mapping[str, str | None],
                    interpreter_trusted: bool, commit: Mapping[str, Any]) -> list[Finding]:
    checks = [
        ("P.disposable_host", disposable, {"flag_given": disposable}),
        ("P.root", euid == 0, {"euid_is_root": euid == 0}),
        ("P.systemd_init", systemd_init, {"systemd_is_pid1": systemd_init}),
        ("P.cgroup2", cgroup2, {"unified_cgroup2_at_sys_fs_cgroup": cgroup2}),
        ("P.system_bus", system_bus, {"system_bus_socket_present": system_bus}),
        ("P.tools", all(tools.values()),
         {"missing": sorted(name for name, path in tools.items() if not path)}),
        ("P.interpreter", interpreter_trusted, {"root_owned_path": interpreter_trusted}),
        ("P.commit", commit.get("clean") is True and len(str(commit.get("sha", ""))) == 40,
         {"sha_known": len(str(commit.get("sha", ""))) == 40,
          "clean": commit.get("clean") is True}),
    ]
    return [Finding(name, "preflight", PASS if ok else HOLD, observations)
            for name, ok, observations in checks]


def judge_refusal(outcome: Mapping[str, Any] | None, observation: Mapping[str, Any] | None,
                  expected_reason: str | None = None) -> str:
    """A refused handshake passes only if neither side saw a payload byte."""
    if outcome is None:
        return HOLD
    sent = outcome.get("payload_bytes_sent")
    seen = None if observation is None else observation.get("bytes_after_ready")
    if (type(sent) is int and sent > 0) or (type(seen) is int and seen > 0):
        return FAIL
    if outcome.get("outcome") != "refused_before_payload" or sent != 0 or seen != 0:
        return HOLD
    if expected_reason is not None and outcome.get("reason") != expected_reason:
        return HOLD
    if outcome.get("end_confirmed") is not True:
        return HOLD
    return PASS


def judge_completed(outcome: Mapping[str, Any] | None, *, min_ready_chunks: int = 1,
                    observation: Mapping[str, Any] | None = None) -> str:
    if outcome is None or outcome.get("outcome") != "completed":
        return HOLD
    if not isinstance(outcome.get("ready_chunks"), int) or outcome["ready_chunks"] < min_ready_chunks:
        return HOLD
    if outcome.get("sender_is_main_pid") is not True or outcome.get("end_confirmed") is not True:
        return HOLD
    if observation is not None and observation.get("request_read") is not True:
        return HOLD
    return PASS


def judge_strangers(without_group: Mapping[str, Any] | None,
                    with_group: Mapping[str, Any] | None) -> str:
    if without_group is None or with_group is None:
        return HOLD
    received = with_group.get("bytes_received")
    if without_group.get("outcome") == "stranger" or (isinstance(received, int) and received > 0):
        return FAIL
    if without_group.get("outcome") != "connect_failed" or without_group.get("access") != DENIED:
        return HOLD
    if with_group.get("outcome") != "stranger" or with_group.get("bytes_received") != 0:
        return HOLD
    return PASS if with_group.get("eof") is True else HOLD


def judge_pair(outcomes: list[Mapping[str, Any] | None], accepted_delta: int | None,
               max_instances: int | None) -> str:
    """Two dispatches at once: exactly one runs, the other is refused before connecting."""
    if isinstance(max_instances, int) and max_instances > 1:
        return FAIL
    known = [value for value in outcomes if value is not None]
    if len(known) != len(outcomes) or max_instances is None:
        return HOLD
    by_kind = {str(value.get("outcome")): value for value in known}
    if sorted(by_kind) != ["completed", "refused_lease_busy"] or accepted_delta != 1:
        return HOLD
    if by_kind["refused_lease_busy"].get("connected") is not False:
        return HOLD
    return PASS if by_kind["completed"].get("end_confirmed") is True else HOLD


def judge_raw_second(outcome: Mapping[str, Any] | None, max_instances: int | None) -> str:
    if isinstance(max_instances, int) and max_instances > 1:
        return FAIL
    if outcome is None or max_instances is None:
        return HOLD
    first, second = outcome.get("first") or {}, outcome.get("second") or {}
    if isinstance(second.get("bytes_received"), int) and second["bytes_received"] > 0:
        return FAIL
    if first.get("outcome") != "completed" or first.get("end_confirmed") is not True:
        return HOLD
    if second.get("outcome") == "connect_failed":
        return PASS
    return PASS if second.get("bytes_received") == 0 and second.get("eof") is True else HOLD


def judge_end(outcome: Mapping[str, Any] | None, expected: str, *, jobs_seen: int | None,
              leftover: int | None) -> str:
    """A job ended only if systemd, the cgroup, the job path and /proc all agree."""
    if outcome is not None and outcome.get("end_confirmed") is True and isinstance(
            leftover, int) and leftover > 0:
        return FAIL  # confirmed an end while a worker process still lived
    if outcome is None or outcome.get("outcome") != expected:
        return HOLD
    if outcome.get("end_confirmed") is not True or leftover != 0:
        return HOLD
    if not isinstance(jobs_seen, int) or jobs_seen < 1:
        return HOLD  # no job directory ever existed, so its removal proves nothing
    return PASS


def judge_linger(first: Mapping[str, Any] | None, second: Mapping[str, Any] | None,
                 *, max_instances: int | None, leftover: int | None) -> str:
    if (isinstance(max_instances, int) and max_instances > 1) or (
            isinstance(leftover, int) and leftover > 0):
        return FAIL
    if first is None or second is None or max_instances is None or leftover is None:
        return HOLD
    # A result without EOF in time is refused by the core; either way the slot
    # must stay closed until the lingering instance has ended.
    if first.get("outcome") not in ("completed", "no_valid_result"):
        return HOLD
    if first.get("result_received") is not True or first.get("end_confirmed") is not True:
        return HOLD
    held = first.get("result_to_end_ms")
    if not isinstance(held, int) or held < LINGER_MIN_MS:
        return HOLD
    if second.get("outcome") != "refused_lease_busy":
        return HOLD
    return PASS


def judge_core_restart(*, killed: bool, second: Mapping[str, Any] | None,
                       accepted_delta: int | None, third: Mapping[str, Any] | None,
                       max_instances: int | None, leftover: int | None) -> str:
    if (isinstance(max_instances, int) and max_instances > 1) or (
            isinstance(leftover, int) and leftover > 0):
        return FAIL
    if second is not None and second.get("connected") is True:
        return FAIL  # connected while the previous instance was alive
    if not killed or second is None or third is None or max_instances is None:
        return HOLD
    if second.get("outcome") != "refused_before_connect" or second.get(
            "reason") not in NOT_IDLE_REASONS or accepted_delta != 0:
        return HOLD
    if third.get("outcome") != "completed" or third.get("end_confirmed") is not True:
        return HOLD
    return PASS if leftover == 0 else HOLD


def judge_unknown_states(cases: Mapping[str, Mapping[str, Any] | None]) -> str:
    """An unreadable lifecycle state must refuse before connect, never pass as idle."""
    if not cases:
        return HOLD
    if any(value is not None and value.get("connected") is True for value in cases.values()):
        return FAIL
    for value in cases.values():
        if value is None or value.get("outcome") != "refused_before_connect":
            return HOLD
        if "unknown" not in str(value.get("reason", "")):
            return HOLD
    return PASS


def judge_identity(identity: Mapping[str, Any] | None, uids: Mapping[str, int],
                   gids: Mapping[str, int]) -> str:
    if not identity:
        return HOLD
    worker_uid, worker_gid = uids["worker"], gids["worker"]
    foreign_gids = {gids[role] for role in ("core", "anchor", "foreign", "ipc")}
    resuid, resgid, groups = identity.get("resuid"), identity.get("resgid"), identity.get("groups")
    if not isinstance(resuid, list) or not isinstance(resgid, list) or not isinstance(groups, list):
        return HOLD
    if (set(resuid) != {worker_uid} or set(resgid) != {worker_gid}
            or set(groups) & foreign_gids or not set(groups) <= {worker_gid}):
        return FAIL
    if identity.get("cap_effective_zero") is False or identity.get("cap_permitted_zero") is False:
        return FAIL
    if (identity.get("cap_effective_zero") is not True
            or identity.get("cap_permitted_zero") is not True
            or identity.get("dumpable") != 0):
        return HOLD
    return PASS


def flatten_targets(targets: Mapping[str, Any] | None) -> dict[str, str]:
    flat: dict[str, str] = {}
    for name, attempts in (targets or {}).items():
        if isinstance(attempts, dict):
            for way, value in attempts.items():
                flat[f"{name}:{way}"] = value
    return flat


def judge_access(flat: Mapping[str, str], names: Iterable[str],
                 controls: Mapping[str, bool]) -> str:
    """Denials count only next to positive controls that show a read can succeed."""
    verdict = access_verdict(flat, names)
    if verdict == FAIL:
        return FAIL
    if not controls or not all(controls.values()):
        return HOLD
    return verdict


def judge_concurrency(max_instances: int | None, errors: int, samples: int) -> str:
    if isinstance(max_instances, int) and max_instances > 1:
        return FAIL
    if max_instances is None or errors or samples < 10:
        return HOLD
    return PASS


def judge_cleanup(state: Mapping[str, Any]) -> str:
    expected = ("processes_gone", "units_gone", "unit_files_gone", "run_directory_gone",
                "users_gone", "groups_gone")
    return PASS if all(state.get(name) is True for name in expected) else HOLD


# --- Host facts ----------------------------------------------------------------

def _run(command: list[str], timeout: float = 10) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              env={"PATH": SAFE_PATH, "LANG": "C", "SYSTEMD_PAGER": "cat",
                                   "SYSTEMD_COLORS": "0"})
    except (OSError, subprocess.TimeoutExpired):
        return None


def _os_release() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "unknown"


def _cgroup2_at_root() -> bool:
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) > 4 and fields[4] == "/sys/fs/cgroup" and " - " in line:
                    if line.split(" - ", 1)[1].split()[0] == "cgroup2":
                        return True
    except OSError:
        pass
    return False


def _landlock_abi() -> int:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        value = libc.syscall(444, None, 0, 1)
        return int(value) if value > 0 else 0
    except (OSError, AttributeError):
        return 0


def _systemd_init() -> bool:
    try:
        with open("/proc/1/comm", encoding="ascii") as handle:
            return os.path.isdir("/run/systemd/system") and handle.read().strip() == "systemd"
    except OSError:
        return False


def host_facts(systemctl: str | None) -> dict[str, Any]:
    version = _run([systemctl, "--version"]) if systemctl else None
    systemd = version.stdout.splitlines()[0] if version and version.stdout else "unknown"
    return {
        "kernel": safe_text(os.uname().release),
        "machine": safe_text(os.uname().machine),
        "os": safe_text(_os_release()),
        "systemd": safe_text(systemd),
        "python": safe_text(platform.python_version()),
        "cgroup2": _cgroup2_at_root(),
        "landlock_abi": _landlock_abi(),
    }


def commit_state(git: str | None) -> dict[str, Any]:
    if not git:
        return {"sha": "unknown", "clean": False}
    base = [git, "-c", f"safe.directory={REPO}", "-C", str(REPO)]
    head = _run(base + ["rev-parse", "HEAD"])
    sha = head.stdout.strip() if head and head.returncode == 0 else ""
    tracked = _run(base + ["status", "--porcelain", "--untracked-files=no"])
    probe = _run(base + ["status", "--porcelain", "--", "tools/a2c5_probe"])
    clean = bool(tracked and tracked.returncode == 0 and not tracked.stdout.strip()
                 and probe and probe.returncode == 0 and not probe.stdout.strip())
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        return {"sha": "unknown", "clean": False}
    return {"sha": sha, "clean": clean}


def interpreter_trusted(path: str) -> bool:
    """The worker and core UIDs execute this interpreter; only root may change it."""
    current = Path(path)
    for part in [current, *current.parents]:
        try:
            info = os.lstat(part)
        except OSError:
            return False
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return False
    return True


# --- The temporary lab --------------------------------------------------------

class CoreRun:
    """One transient core unit; reads its JSON lines as they arrive."""

    def __init__(self, unit: str, process: subprocess.Popen[str]) -> None:
        self.unit = unit
        self.process = process
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.outcome: dict[str, Any] | None = None
        self._seen: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                self.events.put(value)
        self.events.put(None)

    def wait_event(self, name: str, timeout: float) -> bool:
        if name in self._seen:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                return False
            if value is None:
                self.events.put(None)
                return False
            if value.get("event") == "outcome":
                self.outcome = {k: v for k, v in value.items() if k != "event"}
                self.events.put(None)
                return name == "outcome"
            self._seen.append(str(value.get("event")))
            if value.get("event") == name:
                return True
        return False

    def result(self, timeout: float) -> dict[str, Any] | None:
        if self.outcome is None:
            self.wait_event("outcome", timeout)
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        return self.outcome


class Monitor(threading.Thread):
    """Sample the worker slice as root: populated instances and job directories."""

    def __init__(self, slice_path: Path, jobs: Path) -> None:
        super().__init__(daemon=True)
        self.slice_path, self.jobs = slice_path, jobs
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.max_instances = 0
        self.errors = 0
        self.samples = 0
        self.window_instances = 0
        self.window_jobs = 0

    def _sample(self) -> tuple[int, int] | None:
        instances = 0
        try:
            children = list(os.scandir(self.slice_path))
        except FileNotFoundError:
            children = []
        except OSError:
            return None
        for entry in children:
            if not entry.name.endswith(".service") or not entry.is_dir(follow_symlinks=False):
                continue
            try:
                with open(os.path.join(entry.path, "cgroup.events"), encoding="ascii") as handle:
                    if "populated 1" in handle.read():
                        instances += 1
            except FileNotFoundError:
                continue
            except OSError:
                return None
        try:
            jobs = len(os.listdir(self.jobs))
        except OSError:
            return None
        return instances, jobs

    def run(self) -> None:
        while not self.stop.is_set():
            # A cgroup torn down mid-scan can fail one read; only a repeat counts.
            sample = self._sample() or self._sample()
            with self.lock:
                if sample is None:
                    self.errors += 1
                else:
                    self.samples += 1
                    self.max_instances = max(self.max_instances, sample[0])
                    self.window_instances = max(self.window_instances, sample[0])
                    self.window_jobs = max(self.window_jobs, sample[1])
            time.sleep(0.02)

    def window(self) -> dict[str, int]:
        with self.lock:
            value = {"max_instances": self.window_instances, "max_job_dirs": self.window_jobs}
            self.window_instances = self.window_jobs = 0
            return value


class Lab:
    def __init__(self, run_id: str, python: str, tools: Mapping[str, str]) -> None:
        self.run_id = run_id
        self.python = python
        self.tools = dict(tools)
        self.base = Path(f"/run/gnprobe{run_id}")
        self.prefix = f"gnprobe{run_id}-"
        self.slice = f"gnprobe{run_id}w.slice"
        self.unit_dir = Path("/run/systemd/system")
        self.names = {role: f"gnp{run_id}{role[0]}" for role in ROLES}
        self.ipc = f"gnp{run_id}ipc"
        self.uids: dict[str, int] = {}
        self.gids: dict[str, int] = {}
        self.unit_files: list[Path] = []
        self.forbidden: list[str] = []
        self.code_digests: dict[str, str] = {}
        self.payload = "probe payload " + secrets.token_hex(32)
        self.monitor = Monitor(Path("/sys/fs/cgroup") / self.slice, self.base / "jobs")
        self.cores = 0
        self.created_users: list[str] = []
        self.created_group = False

    # Setup

    def _tool(self, name: str, *arguments: str) -> None:
        completed = _run([self.tools[name], *arguments], timeout=30)
        if completed is None or completed.returncode != 0:
            raise RuntimeError(f"{name} failed")

    def _systemctl(self, *arguments: str) -> str | None:
        completed = _run([self.tools["systemctl"], *arguments], timeout=30)
        return completed.stdout if completed and completed.returncode == 0 else None

    def _mkdir(self, path: Path, uid: int, gid: int, mode: int) -> None:
        path.mkdir(mode=0o700)
        os.chown(path, uid, gid)
        os.chmod(path, mode)

    def _write(self, path: Path, data: bytes, uid: int, gid: int, mode: int) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(descriptor, data)
        finally:
            os.close(descriptor)
        os.chown(path, uid, gid)
        os.chmod(path, mode)

    def setup(self) -> None:
        self._tool("groupadd", "--system", self.ipc)
        self.created_group = True
        for role in ROLES:
            self._tool("useradd", "--system", "--user-group", "--no-create-home",
                       "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin",
                       self.names[role])
            self.created_users.append(self.names[role])
            entry = pwd.getpwnam(self.names[role])
            self.uids[role], self.gids[role] = entry.pw_uid, entry.pw_gid
        self.gids["ipc"] = grp.getgrnam(self.ipc).gr_gid

        self._mkdir(self.base, 0, 0, 0o755)
        for name in ("code", "jobs", "observe"):
            self._mkdir(self.base / name, 0, 0, 0o755)
        self._mkdir(self.base / "code" / "a2c5_probe", 0, 0, 0o755)
        for name in CODE_FILES:
            data = (PACKAGE / name).read_bytes()
            self.code_digests[name] = hashlib.sha256(data).hexdigest()
            self._write(self.base / "code" / "a2c5_probe" / name, data, 0, 0, 0o644)
        self._mkdir(self.base / "sock", 0, self.gids["ipc"], 0o750)
        for scenario, role in SCENARIOS.items():
            self._mkdir(self.base / "observe" / scenario, self.uids[role], self.gids[role], 0o700)

        core, anchor = self.uids["core"], self.uids["anchor"]
        self._mkdir(self.base / "secrets", core, self.gids["core"], 0o700)
        root_secret, dsn, head = (secrets.token_hex(32) for _ in range(3))
        self.forbidden += [root_secret, dsn, head, self.payload]
        self._write(self.base / "secrets" / "root-secret", root_secret.encode() + b"\n",
                    core, self.gids["core"], 0o600)
        self._write(self.base / "secrets" / "db-dsn",
                    f"host=/nonexistent dbname=probe password={dsn}\n".encode(),
                    core, self.gids["core"], 0o600)
        self._mkdir(self.base / "anchor", anchor, self.gids["anchor"], 0o700)
        self._write(self.base / "anchor" / "state.json", canonical({"head": head}) + b"\n",
                    anchor, self.gids["anchor"], 0o600)
        self._write(self.base / "lease", b"", 0, self.gids["core"], 0o640)
        self._write(self.base / "public.txt", b"public control\n", 0, 0, 0o644)

        config = {
            "run_id": self.run_id,
            "uids": self.uids, "gids": self.gids,
            "prefix": self.prefix, "slice": self.slice, "cgroup_root": "/sys/fs/cgroup",
            "jobs": str(self.base / "jobs"), "observe": str(self.base / "observe"),
            "lease": str(self.base / "lease"),
            "sockets": {name: str(self.base / "sock" / f"{name}.sock") for name in SCENARIOS},
            "systemctl": self.tools["systemctl"],
            "deadlines": DEADLINES,
            "payload": self.payload,
            "targets": {
                "root_secret": {"path": str(self.base / "secrets" / "root-secret"), "kind": "file"},
                "db_dsn": {"path": str(self.base / "secrets" / "db-dsn"), "kind": "file"},
                "secrets_dir": {"path": str(self.base / "secrets"), "kind": "dir"},
                "anchor_state": {"path": str(self.base / "anchor" / "state.json"), "kind": "file"},
                "anchor_dir": {"path": str(self.base / "anchor"), "kind": "dir"},
                "lease": {"path": str(self.base / "lease"), "kind": "file"},
                "worker_socket": {"path": str(self.base / "sock" / "ok.sock"), "kind": "connect"},
                "control_public": {"path": str(self.base / "public.txt"), "kind": "file"},
            },
        }
        self._write(self.base / "config.json", canonical(config), 0, 0, 0o644)
        for scenario, role in SCENARIOS.items():
            self._unit(f"{self.prefix}{scenario}.socket", self._socket_unit(scenario))
            self._unit(f"{self.prefix}{scenario}@.service", self._service_unit(scenario, role))
        self._tool("systemctl", "daemon-reload")
        self._tool("systemctl", "start", *(f"{self.prefix}{name}.socket" for name in SCENARIOS))
        self.monitor.start()

    def _unit(self, name: str, text: str) -> None:
        path = self.unit_dir / name
        self._write(path, text.encode(), 0, 0, 0o644)
        self.unit_files.append(path)

    def _socket_unit(self, scenario: str) -> str:
        return (f"[Unit]\nDescription=GeniusNew A2/C5 probe socket {scenario}\n\n"
                f"[Socket]\nListenStream={self.base}/sock/{scenario}.sock\nAccept=yes\n"
                f"MaxConnections=1\nSocketUser=root\nSocketGroup={self.ipc}\n"
                f"SocketMode=0660\nRemoveOnStop=yes\n")

    def _service_unit(self, scenario: str, role: str) -> str:
        return (f"[Unit]\nDescription=GeniusNew A2/C5 probe worker {scenario}\n"
                f"CollectMode=inactive-or-failed\n\n"
                f"[Service]\nType=simple\nUser={self.names[role]}\nGroup={self.names[role]}\n"
                f"Slice={self.slice}\nWorkingDirectory={self.base}/code\n"
                f"ExecStart={self.python} -E -s -m a2c5_probe.worker "
                f"{self.base}/config.json {scenario}\n"
                f"StandardInput=null\nStandardOutput=null\nStandardError=null\n"
                f"KillMode=control-group\nSendSIGKILL=yes\nRestart=no\n"
                f"RuntimeMaxSec={RUNTIME_MAX_SECONDS}\nTimeoutStopSec={STOP_TIMEOUT_SECONDS}\n"
                f"RuntimeDirectory=gnprobe{self.run_id}/jobs/%i\nRuntimeDirectoryMode=0700\n"
                f"LimitCORE=0\nNoNewPrivileges=yes\nCapabilityBoundingSet=\nUMask=0077\n")

    # Helpers while running

    def core(self, mode: str, scenario: str, *, role: str = "core",
             groups: Iterable[str] = ("ipc",), properties: Iterable[str] = ()) -> CoreRun:
        self.cores += 1
        unit = f"{self.prefix}core{self.cores}"
        group_names = {"ipc": self.ipc}
        command = [self.tools["systemd-run"], "--quiet", "--pipe", "--collect",
                   f"--unit={unit}", f"--uid={self.names[role]}", f"--gid={self.names[role]}",
                   "--property=NoNewPrivileges=yes", "--property=CapabilityBoundingSet=",
                   f"--property=WorkingDirectory={self.base}/code", "--property=UMask=0077",
                   *(f"--property=SupplementaryGroups={group_names[g]}" for g in groups),
                   *(f"--property={value}" for value in properties),
                   "--", self.python, "-E", "-s", "-m", "a2c5_probe.core",
                   str(self.base / "config.json"), mode, scenario]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True,
                                   env={"PATH": SAFE_PATH, "LANG": "C"})
        return CoreRun(f"{unit}.service", process)

    def run_core(self, mode: str, scenario: str, **options: Any) -> dict[str, Any] | None:
        return self.core(mode, scenario, **options).result(timeout=60)

    def accepted(self, scenario: str) -> int | None:
        text = self._systemctl("show", "--property=NAccepted", "--value",
                               f"{self.prefix}{scenario}.socket")
        try:
            return int(text.strip()) if text is not None else None
        except ValueError:
            return None

    def observation(self, scenario: str) -> dict[str, Any] | None:
        directory = self.base / "observe" / scenario
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            return None
        if len(names) != 1:
            return None
        try:
            value = json.loads((directory / names[0]).read_bytes())
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def clear_observations(self, scenario: str) -> None:
        directory = self.base / "observe" / scenario
        for name in os.listdir(directory):
            (directory / name).unlink()

    def leftover(self, role: str = "worker") -> int | None:
        """Count processes holding the role's UID in any of real/effective/saved/fs."""
        if role not in self.uids:
            return 0  # setup stopped before this user existed; nothing can run as it
        uid = str(self.uids[role])
        count = 0
        try:
            entries = [name for name in os.listdir("/proc") if name.isdigit()]
        except OSError:
            return None
        for name in entries:
            try:
                with open(f"/proc/{name}/status", encoding="ascii", errors="replace") as handle:
                    for line in handle:
                        if line.startswith("Uid:"):
                            if uid in line.split()[1:]:
                                count += 1
                            break
            except FileNotFoundError:
                continue
            except OSError:
                return None
        return count

    def settle(self, timeout: float = 25.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sample = self.monitor._sample()
            if sample == (0, 0) and self.leftover() == 0 and self.leftover("foreign") == 0:
                return True
            time.sleep(0.1)
        return False

    def begin(self, *scenarios: str) -> bool:
        settled = self.settle()
        for scenario in scenarios:
            self.clear_observations(scenario)
        self.monitor.window()
        return settled

    # Scenarios

    def run_all(self) -> list[Finding]:
        findings: list[Finding] = []
        for step in (self.c1_fragmented, self.c1_refusals, self.c1_strangers,
                     self.c2_two_cores, self.c2_two_threads, self.c2_raw_second,
                     self.c3_success, self.c3_hang, self.c3_crash, self.c3_linger,
                     self.c3_core_restart, self.c3_unknown_states, self.c4_access):
            try:
                findings += step()
            except Exception as exc:  # a broken step is HOLD, never a silent skip
                findings.append(Finding(f"error.{step.__name__}", "lab", HOLD,
                                        {"error": type(exc).__name__}))
        window = self.monitor.window()
        findings.append(Finding("C2.max_concurrency", "2", judge_concurrency(
            self.monitor.max_instances, self.monitor.errors, self.monitor.samples),
            {"max_instances": self.monitor.max_instances, "monitor_errors": self.monitor.errors,
             "samples": self.monitor.samples, "last_window": window}))
        return findings

    def c1_fragmented(self) -> list[Finding]:
        settled = self.begin("fragmented")
        outcome = self.run_core("dispatch", "fragmented")
        observation = self.observation("fragmented")
        verdict = judge_completed(outcome, min_ready_chunks=3, observation=observation)
        return [Finding("C1.fragmented_ready", "1", verdict if settled else HOLD, {
            "settled": settled, "outcome": (outcome or {}).get("outcome"),
            "ready_chunks": (outcome or {}).get("ready_chunks"),
            "listener_uid_seen_by_client": (outcome or {}).get("listener_uid"),
            "sender_is_main_pid": (outcome or {}).get("sender_is_main_pid"),
            "payload_sent_after_ready": bool((outcome or {}).get("payload_bytes_sent")),
            "end_confirmed": (outcome or {}).get("end_confirmed"),
            "worker_read_request": (observation or {}).get("request_read")})]

    def c1_refusals(self) -> list[Finding]:
        cases = (
            ("C1.wrong_uid_service", "dispatch", "impostor", "sender is not the configured worker UID"),
            ("C1.sender_switch", "dispatch", "sender", "sender changed inside the stream"),
            ("C1.early_ready", "dispatch", "early", None),
            ("C1.unattributed_early_data", "late-passcred", "early", "data carries no sender process"),
            ("C1.descriptor_passing", "dispatch", "rights", "descriptor passing is refused"),
        )
        findings = []
        for name, mode, scenario, reason in cases:
            settled = self.begin(scenario)
            outcome = self.run_core(mode, scenario)
            observation = self.observation(scenario)
            verdict = judge_refusal(outcome, observation, reason)
            if scenario == "rights" and verdict == PASS and (outcome or {}).get(
                    "descriptors_leaked") != 0:
                verdict = HOLD
            findings.append(Finding(name, "1", verdict if settled or verdict == FAIL else HOLD, {
                "settled": settled, "outcome": (outcome or {}).get("outcome"),
                "reason": (outcome or {}).get("reason"),
                "payload_bytes_sent": (outcome or {}).get("payload_bytes_sent"),
                "worker_bytes_after_ready": (observation or {}).get("bytes_after_ready"),
                "descriptors_leaked": (outcome or {}).get("descriptors_leaked"),
                "end_confirmed": (outcome or {}).get("end_confirmed")}))
        return findings

    def c1_strangers(self) -> list[Finding]:
        settled = self.begin()
        without = self.run_core("stranger", "ok", role="foreign", groups=())
        with_group = self.run_core("stranger", "ok", role="foreign")
        verdict = judge_strangers(without, with_group)
        return [Finding("C1.foreign_client", "1", verdict if settled or verdict == FAIL else HOLD, {
            "settled": settled,
            "without_group": {k: (without or {}).get(k) for k in ("outcome", "access", "reason")},
            "with_group": {k: (with_group or {}).get(k)
                           for k in ("outcome", "bytes_received", "eof")}})]

    def c2_two_cores(self) -> list[Finding]:
        settled = self.begin("hold")
        before = self.accepted("hold")
        runs = [self.core("dispatch", "hold"), self.core("dispatch", "hold")]
        outcomes = [run.result(timeout=60) for run in runs]
        after = self.accepted("hold")
        delta = after - before if after is not None and before is not None else None
        window = self.monitor.window()
        verdict = judge_pair(outcomes, delta, window["max_instances"])
        return [Finding("C2.two_core_processes", "2", verdict if settled or verdict == FAIL else HOLD, {
            "settled": settled, "outcomes": sorted(str((o or {}).get("outcome")) for o in outcomes),
            "accepted_connections": delta, **window})]

    def c2_two_threads(self) -> list[Finding]:
        settled = self.begin("hold")
        before = self.accepted("hold")
        outcome = self.run_core("two-threads", "hold")
        after = self.accepted("hold")
        delta = after - before if after is not None and before is not None else None
        window = self.monitor.window()
        threads = (outcome or {}).get("threads")
        outcomes = list(threads) if isinstance(threads, list) and len(threads) == 2 else [None, None]
        verdict = judge_pair(outcomes, delta, window["max_instances"])
        return [Finding("C2.two_threads_one_core", "2", verdict if settled or verdict == FAIL else HOLD, {
            "settled": settled, "outcomes": sorted(str((o or {}).get("outcome")) for o in outcomes),
            "accepted_connections": delta, **window})]

    def c2_raw_second(self) -> list[Finding]:
        settled = self.begin("hold")
        outcome = self.run_core("raw-second", "hold")
        window = self.monitor.window()
        verdict = judge_raw_second(outcome, window["max_instances"])
        second = (outcome or {}).get("second") or {}
        return [Finding("C2.second_connection_without_lease", "2",
                        verdict if settled or verdict == FAIL else HOLD, {
                            "settled": settled,
                            "first": ((outcome or {}).get("first") or {}).get("outcome"),
                            "second": second.get("outcome"),
                            "second_bytes_received": second.get("bytes_received"),
                            "second_eof": second.get("eof"), **window})]

    def _end_case(self, name: str, scenario: str, expected: str) -> list[Finding]:
        settled = self.begin(scenario)
        outcome = self.run_core("dispatch", scenario)
        leftover = self.leftover()
        window = self.monitor.window()
        verdict = judge_end(outcome, expected, jobs_seen=window["max_job_dirs"], leftover=leftover)
        return [Finding(name, "3", verdict if settled or verdict == FAIL else HOLD, {
            "settled": settled, "outcome": (outcome or {}).get("outcome"),
            "reason": (outcome or {}).get("reason"),
            "end_confirmed": (outcome or {}).get("end_confirmed"),
            "end_wait_ms": (outcome or {}).get("end_wait_ms"),
            "eof_wait_ms": (outcome or {}).get("eof_wait_ms"),
            "end_reason": (outcome or {}).get("end_reason"),
            "worker_processes_after_end": leftover, **window})]

    def c3_success(self) -> list[Finding]:
        return self._end_case("C3.end_after_success", "ok", "completed")

    def c3_hang(self) -> list[Finding]:
        return self._end_case("C3.end_after_hang", "hang", "no_valid_result")

    def c3_crash(self) -> list[Finding]:
        return self._end_case("C3.end_after_crash", "crash", "no_valid_result")

    def c3_linger(self) -> list[Finding]:
        settled = self.begin("linger", "ok")
        first = self.core("dispatch", "linger")
        reached = first.wait_event("result_received", 30)
        second = self.run_core("dispatch", "ok") if reached else None
        first_outcome = first.result(timeout=60)
        leftover = self.leftover()
        window = self.monitor.window()
        verdict = judge_linger(first_outcome, second, max_instances=window["max_instances"],
                               leftover=leftover)
        return [Finding("C3.early_close_then_linger", "3",
                        verdict if settled or verdict == FAIL else HOLD, {
                            "settled": settled, "first": (first_outcome or {}).get("outcome"),
                            "first_reason": (first_outcome or {}).get("reason"),
                            "first_eof_wait_ms": (first_outcome or {}).get("eof_wait_ms"),
                            "first_result_to_end_ms": (first_outcome or {}).get("result_to_end_ms"),
                            "second_during_linger": (second or {}).get("outcome"),
                            "worker_processes_after_end": leftover, **window})]

    def c3_core_restart(self) -> list[Finding]:
        settled = self.begin("hang", "ok")
        first = self.core("dispatch", "hang")
        killed = first.wait_event("payload_sent", 30)
        if killed:
            self._systemctl("kill", "--signal=SIGKILL", "--kill-whom=all", first.unit)
        first.result(timeout=30)
        before = self.accepted("ok")
        second = self.run_core("dispatch", "ok") if killed else None
        after = self.accepted("ok")
        delta = after - before if after is not None and before is not None else None
        recovered = self.settle()
        third = self.run_core("dispatch", "ok") if recovered else None
        leftover = self.leftover()
        window = self.monitor.window()
        verdict = judge_core_restart(killed=killed, second=second, accepted_delta=delta,
                                     third=third, max_instances=window["max_instances"],
                                     leftover=leftover)
        return [Finding("C3.core_restart_reconciliation", "3",
                        verdict if settled or verdict == FAIL else HOLD, {
                            "settled": settled, "core_killed_mid_job": killed,
                            "restarted_core": (second or {}).get("outcome"),
                            "restarted_core_reason": (second or {}).get("reason"),
                            "restarted_core_connected": (second or {}).get("connected"),
                            "accepted_connections_while_refused": delta,
                            "old_instance_ended": recovered,
                            "later_core": (third or {}).get("outcome"),
                            "worker_processes_after_end": leftover, **window})]

    def c3_unknown_states(self) -> list[Finding]:
        settled = self.begin("ok")
        cases: dict[str, dict[str, Any] | None] = {}
        jobs = self.base / "jobs"
        os.chmod(jobs, 0o700)
        try:
            cases["job_directory_unreadable"] = self.run_core("dispatch", "ok")
        finally:
            os.chmod(jobs, 0o755)
        slice_path = Path("/sys/fs/cgroup") / self.slice
        if slice_path.is_dir():
            mode = stat.S_IMODE(os.stat(slice_path).st_mode)
            os.chmod(slice_path, 0o700)
            try:
                cases["slice_cgroup_unreadable"] = self.run_core("dispatch", "ok")
            finally:
                os.chmod(slice_path, mode)
        else:
            cases["slice_cgroup_unreadable"] = None
        cases["systemd_bus_unreachable"] = self.run_core(
            "dispatch", "ok", properties=("InaccessiblePaths=-/run/dbus/system_bus_socket",))
        verdict = judge_unknown_states(cases)
        return [Finding("C3.unreadable_state_refuses", "3",
                        verdict if settled or verdict == FAIL else HOLD, {
                            "settled": settled,
                            **{name: {"outcome": (value or {}).get("outcome"),
                                      "reason": (value or {}).get("reason"),
                                      "connected": (value or {}).get("connected")}
                               for name, value in cases.items()}})]

    def c4_access(self) -> list[Finding]:
        settled = self.begin("access")
        outcome = self.run_core("dispatch", "access")
        own = self.run_core("own-secret", "ok")
        output = (outcome or {}).get("output") or {}
        flat = flatten_targets(output.get("targets"))
        files_in_place = self._targets_in_place()
        controls = {
            "core_reads_own_secret": (own or {}).get("access") == READABLE,
            "core_in_socket_group": self.gids["ipc"] in ((own or {}).get("groups") or []),
            "worker_reads_public_control": flat.get("control_public:python") == READABLE
            and flat.get("control_public:libc") == READABLE,
            "targets_in_place": files_in_place,
            "job_completed": (outcome or {}).get("outcome") == "completed",
        }
        hold = not settled
        identity = output.get("identity")
        groups = [
            ("C4.worker_identity", judge_identity(identity, self.uids, self.gids),
             {"identity": identity, "worker_uid": self.uids["worker"],
              "core_uid": self.uids["core"]}),
            ("C4.test_secrets", judge_access(flat, _ways(SECRET_TARGETS), controls),
             {name: flat.get(name) for name in _ways(SECRET_TARGETS)}),
            ("C4.anchor_test_state", judge_access(flat, _ways(ANCHOR_TARGETS), controls),
             {name: flat.get(name) for name in _ways(ANCHOR_TARGETS)}),
            ("C4.core_only_paths", judge_access(
                flat, _ways(("lease", "core_environ")) + ["worker_socket:python"], controls),
             {name: flat.get(name)
              for name in _ways(("lease", "core_environ")) + ["worker_socket:python"]}),
        ]
        return [Finding(name, "4", HOLD if hold and verdict != FAIL else verdict,
                        {**observations, "controls": controls, "settled": settled})
                for name, verdict, observations in groups]

    def _targets_in_place(self) -> bool:
        expected = {
            self.base / "secrets": (self.uids["core"], 0o700, True),
            self.base / "secrets" / "root-secret": (self.uids["core"], 0o600, False),
            self.base / "secrets" / "db-dsn": (self.uids["core"], 0o600, False),
            self.base / "anchor": (self.uids["anchor"], 0o700, True),
            self.base / "anchor" / "state.json": (self.uids["anchor"], 0o600, False),
            self.base / "lease": (0, 0o640, False),
        }
        for path, (uid, mode, directory) in expected.items():
            try:
                info = os.lstat(path)
            except OSError:
                return False
            if (info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode
                    or stat.S_ISDIR(info.st_mode) != directory):
                return False
        return True

    # Teardown

    def teardown(self) -> Finding:
        self.monitor.stop.set()
        patterns = (f"{self.prefix}*", self.slice)
        self._systemctl("stop", *patterns)
        self._systemctl("kill", "--signal=SIGKILL", "--kill-whom=all", *patterns)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and (
                self.leftover() or self.leftover("foreign") or self.leftover("core")):
            time.sleep(0.1)
        for path in self.unit_files:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        self._systemctl("daemon-reload")
        self._systemctl("stop", self.slice)
        self._systemctl("reset-failed", *patterns)
        shutil.rmtree(self.base, ignore_errors=True)
        for name in self.created_users:
            _run([self.tools["userdel"], name], timeout=30)
        if self.created_group:
            _run([self.tools["groupdel"], self.ipc], timeout=30)

        listing = self._systemctl("list-units", "--all", "--plain", "--no-legend", "--full",
                                  *patterns)
        processes = [self.leftover(role) for role in ROLES if role in self.uids]
        state = {
            "processes_gone": all(count == 0 for count in processes),
            "units_gone": listing is not None and not listing.strip(),
            "unit_files_gone": not any(path.exists() for path in self.unit_files),
            "run_directory_gone": not self.base.exists(),
            "users_gone": all(not _exists(pwd.getpwnam, name) for name in self.names.values()),
            "groups_gone": all(not _exists(grp.getgrnam, name)
                               for name in [self.ipc, *self.names.values()]),
        }
        return Finding("X.cleanup", "cleanup", judge_cleanup(state), state)


def _exists(lookup: Any, name: str) -> bool:
    try:
        lookup(name)
    except KeyError:
        return False
    return True


# --- Report ------------------------------------------------------------------

def build_report(*, facts: Mapping[str, Any], commit: Mapping[str, Any],
                 findings: list[Finding], started: str, finished: str,
                 run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "probe": "geniusnew-a2c5-system-probe",
        "format": 1,
        "verdict": overall(finding.verdict for finding in findings),
        "commit": dict(commit),
        "host": dict(facts),
        "run": dict(run),
        "started_utc": started,
        "finished_utc": finished,
        "findings": [asdict(finding) for finding in findings],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# A2/C5-Systemprobe: Bericht", "",
        f"- Gesamturteil: **{report['verdict']}**",
        f"- Commit: `{report['commit'].get('sha')}` (sauber: {report['commit'].get('clean')})",
        f"- Start/Ende (UTC): {report['started_utc']} / {report['finished_utc']}", "",
        "## Host", "", "| Merkmal | Wert |", "| --- | --- |",
        *(f"| {key} | {value} |" for key, value in sorted(report["host"].items())), "",
        "## Befunde", "", "| Befund | Prüfpunkt | Urteil | Beobachtungen |",
        "| --- | --- | --- | --- |",
    ]
    for finding in report["findings"]:
        observed = json.dumps(finding["observations"], sort_keys=True).replace("|", "/")
        lines.append(f"| {finding['id']} | {finding['check']} | {finding['verdict']} | "
                     f"`{observed}` |")
    lines += ["", "PASS gilt nur, wenn jeder Befund PASS ist; nicht lesbare oder unbekannte "
              "Zustände sind HOLD.", ""]
    return "\n".join(lines)


def write_report(report: dict[str, Any], directory: Path,
                 forbidden: Iterable[str]) -> tuple[str, Path]:
    """Write JSON and Markdown, or a bare HOLD report if the content is not clean."""
    values = list(forbidden)
    try:
        require_safe_observations(report)
        text = json.dumps(report, indent=2, sort_keys=True) + "\n"
        markdown = render_markdown(report)
        require_no_secret(text + markdown, values)
    except ProbeRefused as exc:
        report = {**{key: report[key] for key in ("probe", "format", "commit", "host",
                                                  "started_utc", "finished_utc")},
                  "verdict": HOLD, "run": {},
                  "findings": [asdict(Finding("X.report_redaction", "report", HOLD,
                                              {"reason": str(exc)}))]}
        text = json.dumps(report, indent=2, sort_keys=True) + "\n"
        markdown = render_markdown(report)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"a2c5-probe-{report['started_utc'].replace(':', '').replace('-', '')}"
    json_path = directory / f"{stem}.json"
    json_path.write_text(text, encoding="ascii")
    (directory / f"{stem}.md").write_text(markdown, encoding="utf-8")
    return report["verdict"], json_path


# --- Entry point ---------------------------------------------------------------

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _terminate(signum: int, _: Any) -> None:
    raise KeyboardInterrupt(signum)


def run(arguments: argparse.Namespace) -> int:
    started = _now()
    tools = {name: shutil.which(name, path=SAFE_PATH) for name in TOOLS}
    facts = host_facts(tools["systemctl"])
    commit = commit_state(tools["git"])
    python = os.path.realpath(sys.executable)
    findings = judge_preflight(
        disposable=arguments.disposable_host, euid=os.geteuid(), systemd_init=_systemd_init(),
        cgroup2=bool(facts["cgroup2"]), system_bus=os.path.exists("/run/dbus/system_bus_socket"),
        tools=tools, interpreter_trusted=interpreter_trusted(python), commit=commit)
    forbidden: list[str] = []
    run_info: dict[str, Any] = {}
    if overall(finding.verdict for finding in findings) == PASS:
        signal.signal(signal.SIGTERM, _terminate)
        lab = Lab(secrets.token_hex(4), python, {k: v for k, v in tools.items() if v})
        try:
            lab.setup()
            run_info = {"run_id": lab.run_id, "uids": lab.uids, "gids": lab.gids,
                        "code_sha256": lab.code_digests, "deadlines": DEADLINES}
            findings += lab.run_all()
        except (Exception, KeyboardInterrupt) as exc:
            findings.append(Finding("X.lab", "lab", HOLD, {"error": type(exc).__name__}))
        finally:
            findings.append(lab.teardown())
            forbidden = lab.forbidden
    report = build_report(facts=facts, commit=commit, findings=findings, started=started,
                          finished=_now(), run=run_info)
    verdict, path = write_report(report, Path(arguments.report_dir), forbidden)
    print(f"A2/C5 system probe: {verdict} ({path})")
    return {PASS: 0, FAIL: 1}.get(verdict, 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GeniusNew A2/C5 system probe")
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("run", help="run the probe on this disposable host")
    probe.add_argument("--disposable-host", action="store_true",
                       help="confirm that this host is an isolated, disposable test host")
    probe.add_argument("--report-dir", required=True, help="directory for the report files")
    return run(parser.parse_args(argv))
