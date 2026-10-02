from __future__ import annotations

import os
import argparse
import fcntl
import subprocess
from pathlib import Path

from .checks import DEFAULT_REPO, _which, agent_socket, agent_session, tool_status

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
    return (*_ACTIONS, "hermes_status")


def run_action(name: str, repo: Path = DEFAULT_REPO) -> dict[str, object]:
    if name not in allowed_actions():
        raise ValueError("action is not allowlisted")
    if name == "hermes_status":
        installed = tool_status("hermes")
        session = agent_session("hermes")
        ok = installed["status"] == "green"
        return {"action": name, "ok": ok, "exit_code": 0 if ok else 1,
                "output": installed["detail"] + "\n" + session["detail"]}
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
    except OSError:
        return {"action": name, "ok": False, "exit_code": None, "output": "Programm nicht verfügbar."}
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or ""
        if isinstance(raw, bytes):
            raw = raw.decode(errors="replace")
        return {"action": name, "ok": False, "exit_code": None, "output": raw[-_MAX_OUTPUT:] + "\nTIMEOUT"}

class AgentStartRefused(RuntimeError):
    """Fail-closed terminal-start refusal for Control Deck agents."""


def start_agent(name: str, *, detach: bool = False) -> None:
    """Explicit Hermes terminal operation; never exposed as an HTTP action."""
    if name != "hermes":
        raise AgentStartRefused("agent is not allowlisted")
    binary = _which("hermes")
    tmux = _which("tmux")
    if not binary or not tmux:
        raise AgentStartRefused("Agent oder tmux fehlt")
    runtime = Path.home() / ".local/share/genius-control-deck/agents/hermes"
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    session = "hermes"
    base = [tmux, "-S", agent_socket()]
    with (runtime / "start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        alive = subprocess.run(base + ["has-session", "-t", session],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not alive:
            argv = [binary, "chat", "--cli", "--max-turns", "8", "--run-budget", "120"]
            subprocess.run(base + ["new-session", "-d", "-s", session,
                                   "-c", str(runtime), *argv], check=True)
    print(f"{session}: Start angefordert; Live-Status im Control Deck prüfen.")
    if not detach:
        os.execv(tmux, base + ["attach-session", "-t", session])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start a fixed agent session from TERM")
    parser.add_argument("--start-agent", required=True, choices=("hermes",))
    parser.add_argument("--detach", action="store_true")
    args = parser.parse_args()
    start_agent(args.start_agent, detach=args.detach)
