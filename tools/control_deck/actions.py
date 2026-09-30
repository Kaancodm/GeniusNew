from __future__ import annotations

import os
import argparse
import fcntl
import json
import subprocess
from pathlib import Path

from .checks import DEFAULT_REPO, _which, agent_socket, agent_session, tool_status

_MAX_OUTPUT = 24_000

_ACTIONS: dict[str, tuple[list[str], float]] = {
    "grok_build_status": ([str(Path.home() / ".local/bin/grok"), "--version"], 8.0),
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

def start_agent(name: str, *, detach: bool = False) -> None:
    """Explicit terminal operation; never exposed as an HTTP action."""
    if name not in {"grok_build", "hermes"}:
        raise ValueError("agent is not allowlisted")
    binary = _which("grok" if name == "grok_build" else "hermes")
    tmux = _which("tmux")
    if not binary or not tmux:
        raise RuntimeError("Agent oder tmux fehlt")
    if name == "grok_build" and not _which("bwrap"):
        raise RuntimeError("bubblewrap fehlt; Sandbox-Start verweigert")
    runtime = Path.home() / ".local/share/genius-control-deck/agents" / name
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    session = "grok-build" if name == "grok_build" else "hermes"
    base = [tmux, "-S", agent_socket()]
    # Serialise starts from independent terminals without touching other tmux servers.
    with (runtime / "start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        alive = subprocess.run(base + ["has-session", "-t", session],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not alive:
            if name == "grok_build":
                config = runtime / ".grok"
                config.mkdir(exist_ok=True, mode=0o700)
                deny = [str(Path.home() / ".ssh"), str(Path.home() / ".hermes"),
                        str(Path.home() / ".config")]
                profile = (
                    '[profiles.genius_deck]\nextends = "strict"\nrestrict_network = true\n'
                    + "read_only = " + json.dumps([str(DEFAULT_REPO)]) + "\n"
                    + "deny = " + json.dumps([path for path in deny if Path(path).exists()]) + "\n"
                )
                target = config / "sandbox.toml"
                if target.exists() and target.read_text() != profile:
                    raise RuntimeError("Sandbox-Profil geändert; Start benötigt Prüfung")
                if not target.exists():
                    target.write_text(profile)
                argv = [binary, "--sandbox", "genius_deck", "--disable-web-search",
                        "--no-subagents", "--permission-mode", "plan",
                        "--deny", "Bash", "--deny", "Edit", "--deny", "Write",
                        "--deny", "MCPTool", "--deny", "WebFetch",
                        "--deny", f"Read({Path.home()}/.grok/**)"]
            else:
                argv = [binary, "chat", "--cli", "--max-turns", "8", "--run-budget", "120"]
            subprocess.run(base + ["new-session", "-d", "-s", session,
                                   "-c", str(runtime), *argv], check=True)
    print(f"{session}: Start angefordert; Live-Status im Control Deck prüfen.")
    if not detach:
        os.execv(tmux, base + ["attach-session", "-t", session])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start a fixed agent session from TERM")
    parser.add_argument("--start-agent", required=True, choices=("grok_build", "hermes"))
    parser.add_argument("--detach", action="store_true")
    args = parser.parse_args()
    start_agent(args.start_agent, detach=args.detach)
