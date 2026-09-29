from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .checks import DEFAULT_REPO

_MAX_OUTPUT = 24_000

_ACTIONS: dict[str, tuple[list[str], float]] = {
    "git_status": (["git", "status", "--short", "--branch"], 5.0),
    "tests": (
        ["python3", "-m", "unittest", "discover", "-s", "tests", "-q"],
        30.0,
    ),
    "demo": (["./scripts/demo.sh"], 30.0),
    "docker_status": (
        ["docker", "info", "--format", "Server={{.ServerVersion}} Security={{json .SecurityOptions}}"],
        8.0,
    ),
}
def allowed_actions() -> tuple[str, ...]:
    return tuple(_ACTIONS)


def run_action(name: str, repo: Path = DEFAULT_REPO) -> dict[str, object]:
    if name not in _ACTIONS:
        raise ValueError("action is not allowlisted")
    argv, timeout = _ACTIONS[name]
    env = {
        **os.environ,
        "LC_ALL": "C",
        "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus",
        "DOCKER_HOST": f"unix:///run/user/{os.getuid()}/docker.sock",
    }
    try:
        p = subprocess.run(
            argv,
            cwd=repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        output = p.stdout[-_MAX_OUTPUT:]
        return {"action": name, "ok": p.returncode == 0, "exit_code": p.returncode, "output": output}
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or ""
        if isinstance(raw, bytes):
            raw = raw.decode(errors="replace")
        return {"action": name, "ok": False, "exit_code": None, "output": raw[-_MAX_OUTPUT:] + "\nTIMEOUT"}