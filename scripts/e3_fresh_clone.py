#!/usr/bin/env python3
"""E3 evidence runner: a fresh clone, followed literally, with a protocol.

Gate E3 (`docs/ROADMAP-V02.md`) asks for one thing no test in this repository
can give: a clean Linux host, a fresh clone, the documented steps followed
word for word, and a protocol naming the exact commit. This script performs
the machine-checkable half of that — environment evidence, clone, pinned
install, the full suite, the demo — and renders the protocol as Markdown.
Bringing the *service* up (separate OS users, systemd units, PostgreSQL
roles per `docs/POSTGRES-B1.md` and the C5 operations guide) stays an
operator stage; the runner only reports its prerequisites and never claims
it for them.

Every stage ends PASS, FAIL, BLOCKED or UNKNOWN, and the difference matters: BLOCKED
names the missing prerequisite on this host (Landlock ABI below 4, no libpq,
no test DSN), so a sandbox run reads as what it is — a partial run that
proves the clone, not a failed product and not a passed gate. The overall
verdict is PASS only when the approved commit, suite and demo actually passed
here. UNKNOWN means a dependent stage could not run after an earlier stop.

The clone target must not exist yet: reusing a directory would smuggle in
whatever state made the last attempt look good. Commands run as argv lists
without a shell, except `./scripts/demo.sh` itself, which is the documented
artefact under test. Only ever point this at a repository you trust — a
fresh-clone check executes the clone's code by definition.

Usage (on the target host, from any directory):
    python3 e3_fresh_clone.py --repo https://github.com/Kaancodm/GeniusNew.git \
        --ref main --expect-commit <approved-full-SHA> \
        --dir ~/e3/GeniusNew --protocol ~/e3/PROTOCOL.md
    GENIUSNEW_TEST_ADMIN_DSN=... (per docs/POSTGRES-B1.md) for the full suite

Exit codes: 0 PASS, 1 FAIL, 2 unusable arguments/protocol, 3 BLOCKED,
4 UNKNOWN. The operator must obtain the approved SHA before this run.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
UNKNOWN = "UNKNOWN"
INFO = "INFO"

_TAIL_LINES = 12
_TIMEOUT_SECONDS = 3600
_COMMIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
# landlock_create_ruleset(2) with LANDLOCK_CREATE_RULESET_VERSION returns the
# supported ABI; the syscall number is 444 on the architectures the worker
# filter knows. A probe that cannot answer is reported as 0, never guessed up.
_SYS_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_VERSION_FLAG = 1 << 0


@dataclass(frozen=True)
class Completed:
    returncode: int
    output: str


Run = Callable[..., Completed]


def _run(command: list[str], *, cwd: Path | None = None,
         env: dict[str, str] | None = None) -> Completed:
    try:
        done = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=_TIMEOUT_SECONDS, check=False)
        return Completed(done.returncode, done.stdout or "")
    except OSError as exc:
        return Completed(127, f"could not start {command[0]!r}: {exc}")
    except subprocess.TimeoutExpired:
        return Completed(124, f"timed out after {_TIMEOUT_SECONDS}s")


@dataclass(frozen=True)
class Environment:
    sysname: str
    kernel: str
    machine: str
    python: str
    git: str | None
    libpq: str | None
    landlock_abi: int
    test_dsn_set: bool
    systemd_running: bool

    def blockers(self) -> dict[str, str]:
        """stage name -> reason this host cannot run it, empty when able."""
        blocked: dict[str, str] = {}
        if self.git is None:
            blocked["clone"] = "git is not on PATH"
        missing = [name for name, present in
                   (("libpq (libpq5)", self.libpq is not None),
                    ("GENIUSNEW_TEST_ADMIN_DSN", self.test_dsn_set))
                   if not present]
        if missing:
            blocked["tests"] = "missing " + " and ".join(missing)
        if self.landlock_abi < 4:
            blocked["demo"] = (f"Landlock ABI {self.landlock_abi or 'unknown'}; "
                               "the worker needs ABI 4 (Linux 6.7)")
        return blocked


@dataclass
class Stage:
    name: str
    status: str = UNKNOWN
    evidence: list[str] = field(default_factory=list)


def _mark_refusal(stage: Stage, status: str, reason: str) -> None:
    """Keep a denied stage and its reason together for the protocol."""
    stage.status = status
    stage.evidence.append(reason)


def _landlock_abi() -> int:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.syscall(_SYS_LANDLOCK_CREATE_RULESET, None, 0,
                              _LANDLOCK_VERSION_FLAG)
        return int(result) if result > 0 else 0
    except (OSError, ValueError, AttributeError):
        return 0


def _systemd_running() -> bool:
    """Ask systemd itself; a leftover /run directory in a container lies."""
    if not Path("/run/systemd/system").is_dir() or shutil.which("systemctl") is None:
        return False
    done = _run(["systemctl", "is-system-running"])
    return done.output.strip() in ("running", "degraded")


def probe_environment() -> Environment:
    return Environment(
        sysname=platform.system() or "unknown",
        kernel=platform.release() or "unknown",
        machine=platform.machine() or "unknown",
        python=platform.python_version(),
        git=shutil.which("git"),
        libpq=ctypes.util.find_library("pq"),
        landlock_abi=_landlock_abi() if platform.system() == "Linux" else 0,
        test_dsn_set=bool(os.environ.get("GENIUSNEW_TEST_ADMIN_DSN")),
        systemd_running=_systemd_running(),
    )


def tail(output: str, lines: int = _TAIL_LINES) -> list[str]:
    """The last `lines` non-empty lines, bounded so evidence stays readable."""
    kept = [line for line in output.splitlines() if line.strip()]
    return kept[-lines:]


def verdict(stages: list[Stage]) -> str:
    """PASS needs every gated stage green; INFO stages never decide."""
    gated = [stage.status for stage in stages if stage.status != INFO]
    if FAIL in gated:
        return FAIL
    if BLOCKED in gated:
        return BLOCKED
    if not gated or any(status != PASS for status in gated):
        return UNKNOWN
    return PASS


def _stage_command(stage: Stage, Run: Run, command: list[str], *,
                   cwd: Path | None = None, env: dict[str, str] | None = None,
                   expect: str | None = None) -> Completed:
    done = Run(command, cwd=cwd, env=env)
    stage.evidence.extend(tail(done.output))
    ok = done.returncode == 0 and (expect is None or expect in done.output)
    stage.status = PASS if ok else FAIL
    if not ok:
        stage.evidence.append(f"exit={done.returncode} command={' '.join(command)}")
    return done


def run_e3(*, repo: str, ref: str, expected_sha: str | None = None,
           target: Path, env_info: Environment,
           Run: Run = _run, python: str = sys.executable) -> tuple[list[Stage], str]:
    """Run every stage; return the stages and the resolved commit (or '')."""
    if target.exists():
        raise ValueError(f"target {target} already exists; a fresh clone "
                         "must start from nothing")
    blocked = env_info.blockers()
    stages: list[Stage] = []
    sha = ""

    candidate = Stage("candidate", PASS)
    stages.append(candidate)
    if expected_sha is None:
        _mark_refusal(candidate, BLOCKED, "approved commit SHA is missing")
    if expected_sha is not None and (not isinstance(expected_sha, str)
                                     or not _COMMIT_SHA.fullmatch(expected_sha)):
        _mark_refusal(candidate, FAIL,
                      "approved commit must be a full lowercase 40/64-digit hex SHA")
    if candidate.status == PASS:
        candidate.evidence.append(f"approved commit {expected_sha}")

    stages.append(Stage("environment", PASS, [
        f"{env_info.sysname} {env_info.kernel} {env_info.machine}",
        f"python {env_info.python}, git {'yes' if env_info.git else 'MISSING'}, "
        f"libpq {env_info.libpq or 'MISSING'}, Landlock ABI {env_info.landlock_abi or 'unknown'}, "
        f"test DSN {'set' if env_info.test_dsn_set else 'MISSING'}, "
        f"systemd {'running' if env_info.systemd_running else 'not detected'}",
    ]))

    clone = Stage("clone")
    stages.append(clone)
    if candidate.status != PASS:
        clone.evidence.append("needs the approved candidate")
    elif "clone" in blocked:
        clone.status = BLOCKED
        clone.evidence.append(blocked["clone"])
    else:
        _stage_command(clone, Run, ["git", "clone", "--branch", ref,
                                    "--single-branch", repo, str(target)])
        if clone.status == PASS:
            done = Run(["git", "rev-parse", "HEAD"], cwd=target)
            resolved = done.output.strip()
            sha = (resolved if done.returncode == 0
                   and _COMMIT_SHA.fullmatch(resolved)
                   else "")
            clone.evidence.append(f"HEAD {sha}" if sha else "HEAD unreadable")
            if not sha:
                _mark_refusal(clone, FAIL, "commit resolution failed or was invalid")
            if sha and sha != expected_sha:
                _mark_refusal(clone, FAIL, f"HEAD {sha} != approved {expected_sha}")

    install = Stage("install")
    stages.append(install)
    venv_python = target / ".venv" / "bin" / "python"
    if clone.status != PASS:
        install.evidence.append("needs the clone stage")
    else:
        _stage_command(install, Run, [python, "-m", "venv",
                                      str(target / ".venv")])
        if install.status == PASS:
            _stage_command(install, Run, [str(venv_python), "-m", "pip", "install",
                                          "--require-hashes", "-r", "requirements.txt"],
                           cwd=target)

    smoke = Stage("smoke")
    stages.append(smoke)
    if install.status != PASS:
        smoke.evidence.append("needs the install stage")
    else:
        _stage_command(smoke, Run,
                       [str(venv_python), "-c",
                        "import geniusnew.contracts, geniusnew.http_entry, "
                        "geniusnew.audit_chain; print('imports OK')"],
                       cwd=target, expect="imports OK")
        if smoke.status == PASS and (target / "scripts" / "bench_audit.py").exists():
            _stage_command(smoke, Run, [str(venv_python), "scripts/bench_audit.py",
                                        "--quick"],
                           cwd=target, expect="binding ceiling")

    tests = Stage("tests")
    stages.append(tests)
    if install.status != PASS:
        tests.evidence.append("needs the install stage")
    elif "tests" in blocked:
        tests.status = BLOCKED
        tests.evidence.append(blocked["tests"] + " (docs/POSTGRES-B1.md)")
    else:
        _stage_command(tests, Run,
                       [str(venv_python), "-W", "error::ResourceWarning",
                        "-m", "unittest", "discover", "-s", "tests"],
                       cwd=target, expect="OK")

    demo = Stage("demo")
    stages.append(demo)
    if install.status != PASS:
        demo.evidence.append("needs the install stage")
    elif "demo" in blocked:
        demo.status = BLOCKED
        demo.evidence.append(blocked["demo"])
    else:
        env = dict(os.environ)
        env["PATH"] = f"{target / '.venv' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
        _stage_command(demo, Run, ["./scripts/demo.sh"], cwd=target, env=env,
                       expect="PASS —")

    service = Stage("service-prereqs", INFO, [
        f"systemd {'running' if env_info.systemd_running else 'not detected'}; "
        "bringing the service up (own OS users, units, database roles) is the "
        "operator stage after docs/POSTGRES-B1.md and the C5 guide — reported, "
        "never performed or claimed by this runner.",
    ])
    stages.append(service)
    return stages, sha


def _markdown_field(value: object) -> str:
    """Keep one untrusted value inside a Markdown line or table cell."""
    quoted = json.dumps(str(value), ensure_ascii=True)[1:-1]
    return "".join(f"&#{ord(char)};" if char in "&<>`*_[]|\\#" else char
                   for char in quoted)


def render_protocol(*, repo: str, ref: str, expected_sha: str | None,
                    sha: str, when: str,
                    env_info: Environment, stages: list[Stage]) -> str:
    """The dated E3 protocol: what ran, what it printed, what it proved."""
    lines = [
        f"# E3 fresh-clone protocol ({_markdown_field(when)})",
        "",
        f"- Repository: {_markdown_field(repo)}",
        f"- Ref: {_markdown_field(ref)}",
        f"- Approved candidate: {_markdown_field(expected_sha or 'NOT PROVIDED')}",
        f"- Resolved commit: {_markdown_field(sha or 'NOT RESOLVED')}",
        f"- Host: {_markdown_field(env_info.sysname)} "
        f"{_markdown_field(env_info.kernel)} {_markdown_field(env_info.machine)}, "
        f"Python {_markdown_field(env_info.python)}, "
        f"Landlock ABI {_markdown_field(env_info.landlock_abi or 'unknown')}, "
        f"libpq {_markdown_field(env_info.libpq or 'missing')}",
        f"- Verdict: **{verdict(stages)}**",
        "",
        "| Stage | Result |",
        "| --- | --- |",
    ]
    lines.extend(f"| {_markdown_field(stage.name)} | {_markdown_field(stage.status)} |"
                 for stage in stages)
    for stage in stages:
        lines += ["", f"## {_markdown_field(stage.name)} — {_markdown_field(stage.status)}", ""]
        lines.extend("    " + json.dumps(str(item), ensure_ascii=True)
                     for item in (stage.evidence or ["(no output)"]))
    lines += [
        "",
        "A PASS verdict covers clone → pinned install → full suite → demo at the",
        "commit above. The expected SHA is supplied by the operator; approval",
        "outside this runner must be checked against its decision record.",
        "The running-service half of gate E3 (own OS users, systemd",
        "units, database roles) is the operator stage per `docs/POSTGRES-B1.md`",
        "and the C5 guide and is not claimed by this protocol.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None, *, Run: Run = _run,
         env_info: Environment | None = None, when: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="E3 evidence runner: fresh clone, literal steps, protocol.")
    parser.add_argument("--repo", required=True, help="clone URL or local path")
    parser.add_argument("--ref", default="main", help="branch or tag to clone")
    parser.add_argument("--expect-commit", help="approved full 40/64-digit commit SHA")
    parser.add_argument("--dir", required=True, type=Path,
                        help="clone target; must not exist yet")
    parser.add_argument("--protocol", type=Path,
                        help="also write the Markdown protocol here")
    args = parser.parse_args(argv)
    info = env_info or probe_environment()
    try:
        stages, sha = run_e3(repo=args.repo, ref=args.ref,
                             expected_sha=args.expect_commit, target=args.dir,
                             env_info=info, Run=Run)
    except ValueError as exc:
        print(f"e3_fresh_clone: {exc}", file=sys.stderr)
        return 2
    if when is None:
        import datetime
        when = datetime.date.today().isoformat()
    text = render_protocol(repo=args.repo, ref=args.ref,
                           expected_sha=args.expect_commit, sha=sha, when=when,
                           env_info=info, stages=stages)
    if args.protocol:
        try:
            args.protocol.parent.mkdir(parents=True, exist_ok=True)
            args.protocol.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(f"e3_fresh_clone: cannot write protocol: {exc}", file=sys.stderr)
            return 2
    for stage in stages:
        print(f"{stage.name}: {stage.status}")
    result = verdict(stages)
    print(f"E3 fresh-clone verdict: {result} (commit {sha or 'unresolved'})")
    return {PASS: 0, FAIL: 1, BLOCKED: 3, UNKNOWN: 4}[result]


if __name__ == "__main__":
    raise SystemExit(main())
