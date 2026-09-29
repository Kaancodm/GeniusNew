from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

DEFAULT_REPO = Path(os.environ.get("GENIUSNEW_REPO", "/home/kaan/GeniusNew"))

BETA_GATES = [
    ("A1", "Worker-Seccomp", "origin/codex/seccomp-process-boundary-v2"),
    ("B0", "DB-Design", "origin/chatgpt/db-design"),
    ("B1", "PostgreSQL-Fundament", None),
    ("B2", "Job-Ledger persistent", None),
    ("B3", "Acceptance-Ledger persistent", None),
    ("B4", "Pending Jobs + Approvals", None),
    ("B5", "Audit-Chain persistent", None),
    ("B6", "Manipulationssichtbarkeit", None),
    ("B7", "Crash-Recovery", None),
    ("C1", "Server-Entrypoint", "origin/claude/serve-entrypoint"),
    ("C2", "Anchor-Service", "origin/claude/anchor-service"),
    ("C3", "Approver-HTTP-Route", None),
    ("C4", "HTTP-Härtung", "origin/claude/http-limits"),
    ("A2", "Landlock / Host-Dateien", "origin/claude/a2-landlock-design"),
    ("D1", "Portal→Core-Vertrag", None),
    ("D2", "Portal-Identität", None),
    ("D3", "Portal-Ansichten", None),
    ("C5", "Betrieb + Restore", None),
    ("E1", "Persistenz-Angriffs-Demo", None),
    ("E2", "Security-Review final", None),
    ("E3", "Fresh-host-Probe", None),
]

COMMANDS = {
    "tmux": "tmux attach -t genius",
    "git": "cd ~/GeniusNew && git status --short --branch",
    "tests": "cd ~/GeniusNew && python3 -m unittest discover -s tests -q",
    "demo": "cd ~/GeniusNew && ./scripts/demo.sh",
    "refusals": "cd ~/GeniusNew && python3 scripts/refusals.py",
    "docker": "docker info",
}


def _run(args: list[str], *, cwd: Path | None = None, timeout: float = 3.0) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd or DEFAULT_REPO),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
            env={
                **os.environ,
                "LC_ALL": "C",
                "PATH": (
                    f"{Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin:"
                    + os.environ.get("PATH", "")
                ),
                "XDG_RUNTIME_DIR": os.environ.get(
                    "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
                ),
                "DBUS_SESSION_BUS_ADDRESS": os.environ.get(
                    "DBUS_SESSION_BUS_ADDRESS",
                    f"unix:path=/run/user/{os.getuid()}/bus",
                ),
                "DOCKER_HOST": os.environ.get(
                    "DOCKER_HOST",
                    f"unix:///run/user/{os.getuid()}/docker.sock",
                ),
            },
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return completed.returncode, completed.stdout.strip()


def _simple(status: str, detail: str = "") -> dict[str, str]:
    return {"status": status, "detail": detail}


def repo_status(repo: Path = DEFAULT_REPO) -> dict[str, Any]:
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        return _simple("red", "repo missing")
    _, branch = _run(["git", "branch", "--show-current"], cwd=repo)
    _, head = _run(["git", "rev-parse", "--short=12", "HEAD"], cwd=repo)
    _, dirty = _run(["git", "status", "--porcelain"], cwd=repo)
    return {
        "status": "yellow" if dirty else "green",
        "branch": branch or "detached",
        "head": head,
        "dirty": bool(dirty),
    }


def service_status(name: str, *, user: bool = False) -> dict[str, str]:
    args = ["systemctl"]
    if user:
        args.append("--user")
    args += ["is-active", name]
    code, out = _run(args, cwd=Path.home())
    state = out.splitlines()[0] if out else "unknown"
    return _simple("green" if code == 0 and state == "active" else "red", state)


def _which(name: str) -> str | None:
    search_path = (
        f"{Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin:"
        + os.environ.get("PATH", "")
    )
    return shutil.which(name, path=search_path)


def docker_status() -> dict[str, str]:
    if not _which("docker"):
        return _simple("red", "not installed")
    code, out = _run(["docker", "info", "--format", "{{.ServerVersion}}"], cwd=Path.home())
    if code == 0:
        return _simple("green", out)
    detail = out.splitlines()[0] if out else "daemon unavailable"
    return _simple("red", detail)


def tool_status(name: str) -> dict[str, str]:
    exe = _which(name)
    if not exe:
        return _simple("red", "missing")

    if name == "claude":
        code, out = _run([exe, "auth", "status"], cwd=Path.home())
        try:
            logged_in = bool(json.loads(out).get("loggedIn")) if out else False
        except json.JSONDecodeError:
            logged_in = False
        return _simple("green" if logged_in else "yellow", "logged in" if logged_in else "login required")
    if name == "codex":
        code, out = _run([exe, "login", "status"], cwd=Path.home())
        logged_in = code == 0 and "not logged in" not in out.lower()
        return _simple("green" if logged_in else "yellow", "logged in" if logged_in else "login required")
    if name == "gh":
        code, _ = _run([exe, "auth", "status"], cwd=Path.home())
        return _simple("green" if code == 0 else "yellow", "logged in" if code == 0 else "login required")

    code, out = _run([exe, "--version"], cwd=Path.home())
    version = out.splitlines()[0] if out else "installed"
    return _simple("green" if code == 0 else "yellow", version)


def gate_status(repo: Path, ref: str | None) -> str:
    if ref is None:
        return "red"
    remote_ref = ref if ref.startswith("origin/") else f"origin/{ref}"
    code, _ = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/{remote_ref}"],
        cwd=repo,
    )
    if code != 0:
        return "red"
    code, _ = _run(["git", "merge-base", "--is-ancestor", remote_ref, "main"], cwd=repo)
    return "green" if code == 0 else "yellow"


def snapshot(repo: Path = DEFAULT_REPO) -> dict[str, Any]:
    gates = [
        {"id": gate, "name": name, "status": gate_status(repo, ref)}
        for gate, name, ref in BETA_GATES
    ]
    return {
        "generated_at": int(time.time()),
        "repo": repo_status(repo),
        "services": {
            "tailscale": service_status("tailscaled"),
            "desktop_commander": service_status("desktop-commander-remote.service", user=True),
            "docker": docker_status(),
        },
        "tools": {
            "claude": tool_status("claude"),
            "codex": tool_status("codex"),
            "gemini": tool_status("gemini"),
            "gh": tool_status("gh"),
        },
        "tmux": _simple(
            "green" if Path(f"/tmp/tmux-{os.getuid()}/default").exists() else "yellow",
            "genius session" if Path(f"/tmp/tmux-{os.getuid()}/default").exists() else "check from TERM",
        ),
        "gates": gates,
        "commands": COMMANDS,
    }