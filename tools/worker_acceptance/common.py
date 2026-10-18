"""Pure acceptance contracts; this module never imports GeniusNew."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

SOURCE_COMMIT = "9b11f7a89fe948d65d6b2eb3337111a5b11d3a57"
PYTHON = "/opt/geniusnew/.venv/bin/python"
PACKAGE = Path("/opt/geniusnew/.venv/lib/python3.13/site-packages/geniusnew")
UNITS = ("/etc/systemd/system/geniusnew-worker.socket",
         "/etc/systemd/system/geniusnew-worker@.service")
CORE_UID, CORE_GID, WORKER_UID, WORKER_GID, SOCKET_GID = 20001, 20001, 20002, 20002, 20003
CASES = ("baseline", "lease", "raw_nonce", "raw_extra", "raw_limits",
         "raw_noncanonical", "raw_oversized", "raw_truncated", "raw_trailing",
         "core_crash_before_request", "worker_crash_before_request",
         "core_crash_after_request_write", "worker_crash_after_request_write")
GAPS = ("malicious_worker_filesystem_body", "malicious_worker_network_body",
        "malicious_worker_syscall_body", "malicious_worker_response_fixture")
HEX = re.compile(r"[0-9a-f]{64}\Z")


class Refused(Exception):
    """A fixed diagnostic code, never an arbitrary exception message."""


def _fail(code: str) -> None:
    raise Refused(code)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def protected(path: Path, *, directory: bool = False) -> None:
    """Reject links and every writable/nonroot component of an absolute path."""
    if not path.is_absolute() or str(path) != os.path.normpath(path):
        _fail("UNTRUSTED_PATH")
    current = Path("/")
    for index, part in enumerate(path.parts[1:]):
        current /= part
        info = current.lstat()
        last = index == len(path.parts) - 2
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            _fail("UNTRUSTED_PATH")
        expected = stat.S_ISDIR if not last or directory else stat.S_ISREG
        if not expected(info.st_mode):
            _fail("UNTRUSTED_PATH")


def load_manifest(path: Path) -> dict:
    """Bound manifest size and reject ambiguous or unpinned source declarations."""
    if not path.is_absolute() or path.stat().st_size > 32768:
        _fail("MANIFEST_INVALID")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("MANIFEST_INVALID")
            result[key] = value
        return result
    manifest = json.loads(path.read_text(encoding="ascii"), object_pairs_hook=unique)
    if type(manifest) is not dict or manifest.get("source_commit") != SOURCE_COMMIT:
        _fail("MANIFEST_INVALID")
    sources = manifest.get("expected_sources")
    if type(sources) is not dict or not sources:
        _fail("MANIFEST_INVALID")
    for name, value in sources.items():
        if (type(name) is not str or re.fullmatch(r"geniusnew/[a-z_][a-z0-9_]*\.py", name) is None
                or type(value) is not str or HEX.fullmatch(value) is None):
            _fail("MANIFEST_INVALID")
    if "geniusnew/__init__.py" not in sources or "geniusnew/worker_service.py" not in sources:
        _fail("MANIFEST_INVALID")
    units = manifest.get("expected_units")
    if type(units) is not dict or set(units) != set(UNITS):
        _fail("MANIFEST_INVALID")
    if any(type(value) is not str or HEX.fullmatch(value) is None for value in units.values()):
        _fail("MANIFEST_INVALID")
    return manifest


def verify_installed(manifest: dict) -> None:
    """Hash all installed package sources before the first GeniusNew import."""
    protected(PACKAGE, directory=True)
    sources = manifest["expected_sources"]
    actual = {"geniusnew/" + item.name for item in PACKAGE.glob("*.py")}
    if actual != set(sources) or any(PACKAGE.glob("*/__init__.py")):
        _fail("SOURCE_MISMATCH")
    for name, expected in sources.items():
        path = PACKAGE / Path(name).name
        protected(path)
        if digest(path) != expected:
            _fail("SOURCE_MISMATCH")
    for name, expected in manifest["expected_units"].items():
        path = Path(name)
        protected(path)
        if digest(path) != expected:
            _fail("UNIT_MISMATCH")


def evaluate(mode: str, event: dict, returncode: int | None) -> str:
    """Only the exact mode-specific semantic success counts as PASS."""
    expected = {"baseline": "BASELINE_OK", "lease_holder": "LEASE_RELEASED",
                "lease_busy": "LEASE_OCCUPIED", "idle_busy": "LIVE_WORKER_REFUSED",
                "idle": "IDLE_CONFIRMED", "controlled": "WORKER_CRASH_REFUSED",
                "controlled_after": "WORKER_CRASH_REFUSED"}
    code = "REQUEST_REFUSED" if mode.startswith("raw_") else expected.get(mode)
    connections = 1 if mode.startswith("raw_") or mode in ("baseline", "controlled", "controlled_after") else 0
    if (returncode != 0 or type(event) is not dict or set(event) != {"event", "code", "connect_calls"}
            or event.get("event") != "DONE" or event.get("code") != code or code is None):
        _fail("CASE_INCONCLUSIVE")
    if type(event["connect_calls"]) is not int or event["connect_calls"] != connections:
        _fail("CASE_INCONCLUSIVE")
    return "PASS"


def aggregate(cases: list[dict], cleanup: str) -> dict:
    """Operational acceptance stays HOLD even when every bounded case passes."""
    names = [case.get("case") for case in cases]
    complete = len(names) == len(CASES) and set(names) == set(CASES)
    passed = complete and cleanup == "PASS" and all(case.get("status") == "PASS" for case in cases)
    return {"schema": 1, "source_commit": SOURCE_COMMIT,
            "bounded_suite": "PASS" if passed else "HOLD",
            "operational_acceptance": "HOLD", "uncovered_gates": list(GAPS),
            "cleanup": cleanup, "cases": cases}


def identity_matches(event: dict, pidfd_pid: int, status: dict, properties: dict,
                     cgroup: str, members: list[int]) -> bool:
    """An event is merely a claim until kernel and manager identities agree."""
    pid = event.get("pid")
    unit = event.get("unit")
    invocation = event.get("invocation")
    if (type(pid) is not int or pid <= 1 or pid != pidfd_pid
            or type(unit) is not str or re.fullmatch(r"geniusnew-worker@[^/\s]+\.service", unit) is None
            or type(invocation) is not str or re.fullmatch(r"[0-9a-f]{32}", invocation) is None):
        _fail("SIGNAL_TARGET_UNVERIFIED")
    if (status.get("Uid") != [WORKER_UID] * 4 or status.get("Gid") != [WORKER_GID] * 4
            or cgroup != "/system.slice/" + unit or members != [pid]
            or properties.get("MainPID") != str(pid) or properties.get("InvocationID") != invocation
            or properties.get("ControlGroup") != cgroup or properties.get("ActiveState") != "active"
            or properties.get("User") != str(WORKER_UID) or properties.get("Group") != str(WORKER_GID)
            or properties.get("FragmentPath") != UNITS[1] or properties.get("DropInPaths") != ""
            or properties.get("NeedDaemonReload") != "no"):
        _fail("SIGNAL_TARGET_UNVERIFIED")
    return True
