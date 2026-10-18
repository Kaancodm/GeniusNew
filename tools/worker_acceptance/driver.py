"""Bounded host acceptance driver for the unchanged installed worker service."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

# A root invocation must reject a user-owned import root before loading helpers.
def _fail(code: str) -> None:
    raise SystemExit(code)


HERE = Path(__file__).resolve().parent
if os.geteuid() == 0:
    for ancestor in (HERE, *HERE.parents):
        metadata = ancestor.lstat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or not stat.S_ISDIR(metadata.st_mode):
            _fail("UNTRUSTED_DRIVER")
    for filename in ("driver.py", "common.py", "core.py"):
        metadata = (HERE / filename).lstat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or not stat.S_ISREG(metadata.st_mode):
            _fail("UNTRUSTED_DRIVER")
sys.path.insert(0, str(HERE))
from common import (CASES, CORE_UID, PYTHON, SOURCE_COMMIT, WORKER_UID,
                    Refused, _fail, aggregate, canonical, digest, evaluate,
                    identity_matches, load_manifest, protected, verify_installed)


def read_properties(unit: str) -> dict:
    command = ["/usr/bin/systemctl", "show", unit, "--no-pager"]
    names = ("MainPID", "InvocationID", "ControlGroup", "ActiveState", "User", "Group",
             "FragmentPath", "DropInPaths", "NeedDaemonReload")
    command.extend("-p" + name for name in names)
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=3, env={"LANG": "C", "LC_ALL": "C"}, check=False)
    if result.returncode or result.stderr or len(result.stdout) > 8192:
        _fail("MANAGER_UNREADABLE")
    lines = result.stdout.decode("ascii").splitlines()
    fields = dict(line.split("=", 1) for line in lines)
    if set(fields) != set(names) or len(lines) != len(names):
        _fail("MANAGER_UNREADABLE")
    return fields


def fd_pid(descriptor: int) -> int:
    source = Path(f"/proc/self/fdinfo/{descriptor}").read_text(encoding="ascii")
    matches = re.findall(r"^Pid:\s+(\d+)\s*$", source, re.MULTILINE)
    if len(matches) != 1:
        _fail("PIDFD_UNVERIFIED")
    return int(matches[0])


def read_status(pid: int) -> dict:
    source = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
    return {name: list(map(int, re.findall(r"\d+", value)))
            for name, value in (line.split(":", 1) for line in source.splitlines())
            if name in ("Uid", "Gid")}


@dataclass
class WorkerTarget:
    descriptor: int
    event: dict
    verified: bool = False
    stopped: bool = False

    def live(self) -> bool:
        return not select.select((self.descriptor,), (), (), 0)[0]

    def send(self, number: int) -> None:
        if not self.verified or number not in (signal.SIGSTOP, signal.SIGCONT, signal.SIGKILL):
            _fail("SIGNAL_NOT_AUTHORIZED")
        signal.pidfd_send_signal(self.descriptor, number)
        if number == signal.SIGSTOP:
            self.stopped = True
        if number == signal.SIGCONT:
            self.stopped = False

    def close(self) -> None:
        os.close(self.descriptor)


def bind_target(event: dict, checkpoint: str) -> WorkerTarget:
    if (type(event) is not dict or set(event) != {"event", "pid", "unit", "invocation",
                                               "cgroup", "connect_calls", "checkpoint"}
            or event.get("event") != "BOUND" or event.get("connect_calls") != 1
            or checkpoint not in ("before_request", "after_request_write")
            or event.get("checkpoint") != checkpoint
            or type(event.get("pid")) is not int or event["pid"] <= 1
            or type(event.get("unit")) is not str
            or re.fullmatch(r"geniusnew-worker@[^/\s]+\.service", event["unit"]) is None):
        _fail("SIGNAL_TARGET_UNVERIFIED")
    descriptor = os.pidfd_open(event["pid"], 0)
    target = WorkerTarget(descriptor, event)
    try:
        pid = fd_pid(descriptor)
        status = read_status(pid)
        lines = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines()
        if len(lines) != 1 or not lines[0].startswith("0::/system.slice/"):
            _fail("SIGNAL_TARGET_UNVERIFIED")
        cgroup = lines[0][3:]
        members = list(map(int, Path("/sys/fs/cgroup" + cgroup, "cgroup.procs").read_text().split()))
        properties = read_properties(event["unit"])
        identity_matches(event, pid, status, properties, cgroup, members)
        if event["cgroup"] != "/sys/fs/cgroup" + cgroup or not target.live():
            _fail("SIGNAL_TARGET_UNVERIFIED")
        target.verified = True
        return target
    except BaseException:
        target.close()
        raise


class Child:
    """Own one direct Core subprocess and its pidfd; never signal by search."""

    def __init__(self, mode: str):
        self.mode = mode
        self.process = None
        self.descriptor = None
        self.buffer = b""
        self.total = 0
        self.started = time.monotonic()

    def start(self, stage: Path) -> None:
        command = ["/usr/bin/setpriv", "--reuid=20001", "--regid=20001", "--groups=20003",
                   "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all",
                   "--no-new-privs", PYTHON, "-I", "-B", str(stage / "core.py"),
                   "--manifest", str(stage / "manifest.json"), "--mode", self.mode]
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK,
                                        {signal.SIGALRM, signal.SIGINT, signal.SIGTERM, signal.SIGHUP})
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, close_fds=True, cwd="/",
                                            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"})
            self.descriptor = os.pidfd_open(self.process.pid, 0)
        except BaseException:
            self.close()
            raise
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)

    def event(self, seconds: float = 45) -> dict:
        deadline = min(time.monotonic() + seconds, self.started + 45)
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select((self.process.stdout,), (), (), remaining)[0]:
                _fail("CHILD_TIMEOUT")
            data = os.read(self.process.stdout.fileno(), 2048)
            self.total += len(data)
            if not data or self.total > 4096:
                _fail("CHILD_OUTPUT_INVALID")
            self.buffer += data
        line, self.buffer = self.buffer.split(b"\n", 1)
        value = json.loads(line.decode("ascii"))
        if type(value) is not dict or len(line) > 2048:
            _fail("CHILD_OUTPUT_INVALID")
        return value

    def resume(self) -> None:
        self.process.stdin.write(b"CONTINUE\n")
        self.process.stdin.flush()

    def kill_owned(self) -> None:
        if self.process is None:
            return
        if self.descriptor is None:
            # Do not fall back to PID-based signalling. The trusted helper has
            # its own 45s alarm; EOF releases its bounded control wait.
            self.process.stdin.close()
            self.process.wait(timeout=50)
            return
        if self.process.poll() is None:
            if fd_pid(self.descriptor) != self.process.pid:
                _fail("OWNED_CHILD_UNVERIFIED")
            signal.pidfd_send_signal(self.descriptor, signal.SIGKILL)
        self.process.wait(timeout=3)

    def done(self) -> dict:
        event = self.event()
        code = self.process.wait(timeout=max(0.1, self.started + 45 - time.monotonic()))
        trailing = self.process.stdout.read(4097)
        if self.buffer or trailing:
            _fail("CHILD_OUTPUT_INVALID")
        evaluate(self.mode, event, code)
        return event

    def close(self) -> None:
        self.kill_owned()
        if self.process is not None:
            self.process.stdin.close()
            self.process.stdout.close()
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def ensure_no_foreign_processes() -> None:
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            status = read_status(int(path.name))
        except FileNotFoundError:
            continue
        if set(status) != {"Uid", "Gid"}:
            _fail("PROCESS_INVENTORY_UNKNOWN")
        if any(value in (CORE_UID, WORKER_UID) for value in status["Uid"]):
            _fail("FOREIGN_PROCESS_PRESENT")


class Suite:
    def __init__(self, stage: Path):
        self.stage = stage
        self.children = []
        self.targets = []
        self.observations = []

    def spawn(self, mode: str) -> Child:
        child = Child(mode)
        self.children.append(child)
        child.start(self.stage)
        return child

    def one(self, mode: str) -> dict:
        child = self.spawn(mode)
        try:
            event = child.done()
            self.observations.append({"mode": mode, "connect_calls": event["connect_calls"]})
            return event
        finally:
            child.close()
            self.children.remove(child)

    def baseline(self) -> None:
        self.one("baseline")

    def lease(self) -> None:
        holder = self.spawn("lease_holder")
        try:
            if holder.event() != {"event": "HELD"}:
                _fail("LEASE_NOT_HELD")
            self.one("lease_busy")
            holder.resume()
            holder.done()
        finally:
            holder.close()
            self.children.remove(holder)
        self.baseline()

    def crash(self, *, kill_core: bool, checkpoint: str) -> None:
        child = self.spawn("controlled" if checkpoint == "before_request" else "controlled_after")
        target = None
        try:
            target = bind_target(child.event(), checkpoint)
            self.targets.append(target)
            self.observations.append({"event": "BOUND", "checkpoint": checkpoint,
                                      "mode": "controlled" if checkpoint == "before_request" else "controlled_after",
                                      "connect_calls": 1})
            if kill_core:
                target.send(signal.SIGSTOP)
                until = time.monotonic() + 1
                while True:
                    state = Path(f"/proc/{target.event['pid']}/status").read_text(encoding="ascii")
                    if re.search(r"^State:\s+T\b", state, re.MULTILINE):
                        break
                    if time.monotonic() >= until:
                        _fail("WORKER_NOT_STOPPED")
                    time.sleep(0.01)
                child.kill_owned()
                if not target.live():
                    _fail("OLD_WORKER_NOT_LIVE")
                self.one("idle_busy")
                if not target.live():
                    _fail("OLD_WORKER_NOT_LIVE")
                target.send(signal.SIGCONT)
            else:
                target.send(signal.SIGKILL)
                child.resume()
                child.done()
            self.one("idle")
            self.baseline()
        finally:
            if target is not None and target.stopped and target.live():
                target.send(signal.SIGCONT)
            child.close()
            self.children.remove(child)

    def cleanup(self) -> str:
        good = True
        for target in self.targets:
            try:
                if target.stopped and target.live():
                    target.send(signal.SIGCONT)
            except BaseException:
                good = False
        for child in list(self.children):
            try:
                child.close()
            except BaseException:
                good = False
            self.children.remove(child)
        try:
            self.one("idle")
            ensure_no_foreign_processes()
        except BaseException:
            good = False
        for target in self.targets:
            try:
                if target.live():
                    good = False
                target.close()
            except BaseException:
                good = False
        return "PASS" if good else "HOLD"


def inspect(manifest_path: Path, report_dir: Path | None) -> dict:
    manifest = load_manifest(manifest_path)
    if report_dir is not None and not report_dir.is_absolute():
        _fail("REPORT_PATH_INVALID")
    for filename in ("driver.py", "common.py", "core.py"):
        ast.parse((HERE / filename).read_text(encoding="utf-8"), filename=filename)
    return manifest


def run(manifest_path: Path, report_dir: Path, acknowledge: bool) -> tuple[dict, int]:
    if not acknowledge or os.getuid() != 0 or os.geteuid() != 0:
        _fail("RUN_NOT_AUTHORIZED")
    if sys.flags.isolated != 1 or not sys.dont_write_bytecode:
        _fail("DRIVER_STARTUP_INVALID")
    if report_dir != HERE.parent.parent / "reports":
        _fail("REPORT_PATH_INVALID")
    protected(HERE, directory=True)
    protected(manifest_path)
    protected(report_dir, directory=True)
    manifest = inspect(manifest_path, report_dir)
    verify_installed(manifest)
    ensure_no_foreign_processes()
    stage = Path(tempfile.mkdtemp(prefix="gn-worker-acceptance-", dir="/run"))
    suite = Suite(stage)
    cases = []
    cleanup = "HOLD"
    try:
        for filename in ("common.py", "core.py"):
            source = HERE / filename
            protected(source)
            shutil.copyfile(source, stage / filename)
            os.chmod(stage / filename, 0o444)
        shutil.copyfile(manifest_path, stage / "manifest.json")
        os.chmod(stage / "manifest.json", 0o444)
        os.chmod(stage, 0o755)
        for case in CASES:
            started = time.monotonic()
            first_observation = len(suite.observations)
            try:
                if case == "baseline":
                    suite.baseline()
                elif case == "lease":
                    suite.lease()
                elif case.startswith("raw_"):
                    suite.one(case)
                    suite.baseline()
                else:
                    suite.crash(kill_core=case.startswith("core_crash"),
                                checkpoint="after_request_write" if case.endswith("after_request_write") else "before_request")
                cases.append({"case": case, "status": "PASS", "cleanup": "PASS",
                              "code": "OBSERVED", "observations": suite.observations[first_observation:],
                              "elapsed_ms": round((time.monotonic() - started) * 1000)})
            except BaseException:
                cases.append({"case": case, "status": "HOLD", "cleanup": "UNKNOWN",
                              "code": "CASE_INCONCLUSIVE",
                              "observations": suite.observations[first_observation:],
                              "elapsed_ms": round((time.monotonic() - started) * 1000)})
                break
    finally:
        signal.alarm(0)
        cleanup = suite.cleanup()
        shutil.rmtree(stage)
    report = aggregate(cases, cleanup)
    uname = os.uname()
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    metadata = {"hostname": uname.nodename, "kernel": uname.release,
                "machine": uname.machine, "boot_id": boot_id,
                "python": ".".join(map(str, sys.version_info[:3]))}
    if any(type(value) is not str or re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", value) is None
           for value in metadata.values()):
        _fail("HOST_METADATA_INVALID")
    metadata["utc"] = datetime.now(timezone.utc).isoformat()
    report["host"] = metadata
    report["manifest_sha256"] = digest(manifest_path)
    report["harness_sha256"] = {name: digest(HERE / name) for name in ("driver.py", "common.py", "core.py")}
    destination = report_dir / "worker-acceptance.json"
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical(report) + b"\n")
    return report, 0 if report["bounded_suite"] == "PASS" else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--report-dir")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--check", action="store_true")
    choice.add_argument("--run", action="store_true")
    parser.add_argument("--acknowledge-worker-test", action="store_true")
    args = parser.parse_args(argv)
    manifest_path = Path(args.manifest)
    report_dir = Path(args.report_dir) if args.report_dir else None
    try:
        inspect(manifest_path, report_dir)
        if not args.run:
            print(json.dumps({"check": "PASS", "operational_acceptance": "HOLD",
                              "source_commit": SOURCE_COMMIT}, sort_keys=True))
            return 0
        if report_dir is None:
            _fail("REPORT_PATH_INVALID")
        def deadline(_signal, _frame):
            _fail("DRIVER_TIMEOUT")
        signal.signal(signal.SIGALRM, deadline)
        signal.signal(signal.SIGTERM, deadline)
        signal.signal(signal.SIGHUP, deadline)
        signal.alarm(360)
        report, code = run(manifest_path, report_dir, args.acknowledge_worker_test)
        print(json.dumps(report, sort_keys=True))
        return code
    except BaseException:
        print(json.dumps({"bounded_suite": "HOLD", "operational_acceptance": "HOLD",
                          "code": "DRIVER_INCONCLUSIVE"}, sort_keys=True))
        return 2
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
