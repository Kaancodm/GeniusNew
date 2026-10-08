from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

DEFAULT_REPO = Path(os.environ.get("GENIUSNEW_REPO", "/home/kaan/GeniusNew"))

BETA_GATES = [
    ("A1", "Worker-Seccomp", "8a358d1aa620c664c1f073eb87febdddf81f130a"),
    ("B0", "DB-Design", "a3958f057e2bee370bf0f3d1ff43776679a55db3"),
    ("B1", "PostgreSQL-Fundament", "706e76d800f86b7618f0737a5e9ced6529556496"),
    ("B2", "Job-Ledger persistent", "9a797b1d951651b1e72a353af6c8fb1bf0b412a4"),
    ("B3", "Acceptance-Ledger persistent", "3b81265e9aa332f697a2d6fe3166bb5e7d66640f"),
    ("B4", "Pending Jobs + Approvals", "18381c2237856c93b14c283a1bda636cb398d1a1"),
    ("B5", "Audit-Chain persistent", "a3f2a3c55fb112a872b872d065e3c9d10df53a83"),
    ("B6", "Manipulationssichtbarkeit", "a9705d10136ddd3ab0e48538db69e874b79df491"),
    ("B7", "Crash-Recovery", "dfbc50bc7f4f921d495f2c71156db8cd496e05dd"),
    ("C1", "Server-Entrypoint", "67c525f17d3f5b4c4855f7976eedd9dc62f727cc"),
    ("C2", "Anchor-Service", "24f521bb81a3ae604da3aef00072690f7df4dd16"),
    ("C3", "Approver-HTTP-Route", "b047798068f1d750f8c8ebf47c357fd7d771a53f"),
    ("C4", "HTTP-Härtung", "080f1f4e4460aa28b54ef3f9f698e598d8be03e5"),
    ("A2", "Landlock / Host-Dateien", "3297f939e99479249de352e96b2bb00aa9c67d8b"),
    ("C5", "Betrieb + Restore", None),
    ("E3", "Fresh-host-Probe", None),
    ("D1", "Portal→Core-Vertrag", None),
    ("D2", "Portal-Identität", None),
    ("D3", "Portal-Ansichten", None),
    ("E1", "Persistenz-Angriffs-Demo", "122a6902fc8a88488dd9312b4803571fb85c8ab2"),
    ("E2", "Security-Review final", None),
]

# The required DB reviews have not been evidenced at the current head.
PENDING_REVIEWS = {"B4": 94, "B5": 95}
PENDING_OPERATIONS = {
    "A2": "Landlock implementiert; HOLD: eigener Worker-OS-Nutzer im Betrieb nicht nachgewiesen",
    "C2": "Ankerdienst implementiert; HOLD: eigener Anker-OS-Nutzer im Betrieb nicht nachgewiesen",
}

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
    if name == "gemini":
        agy = _which("agy")
        if agy:
            code, out = _run([agy, "models"], cwd=Path.home(), timeout=8.0)
            logged_in = code == 0 and "gemini-" in out
            return _simple(
                "green" if logged_in else "yellow",
                "logged in via Antigravity" if logged_in else "Antigravity login required",
            )

    exe = _which(name)
    if not exe:
        return _simple("red", "missing")

    if name == "hermes":
        return _simple("green", "CLI installiert · Modellzugriff ungeprüft")

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
    if name == "gemini":
        code, out = _run(
            [exe, "-p", "Reply only OK", "--output-format", "json"],
            cwd=Path.home(),
            timeout=8.0,
        )
        logged_in = code == 0 and '"error"' not in out
        return _simple(
            "green" if logged_in else "yellow",
            "logged in" if logged_in else "login required",
        )

    code, out = _run([exe, "--version"], cwd=Path.home())
    version = out.splitlines()[0] if out else "installed"
    return _simple("green" if code == 0 else "yellow", version)


def gate_status(repo: Path, ref: str | None) -> str:
    if ref is None:
        return "red"
    proof = ref if len(ref) == 40 and all(c in "0123456789abcdef" for c in ref) else None
    remote_ref = ref if ref.startswith("origin/") else f"origin/{ref}"
    check = (["git", "cat-file", "-e", f"{proof}^{{commit}}"] if proof else
             ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/{remote_ref}"])
    code, _ = _run(check, cwd=repo)
    if code != 0:
        return "red"
    code, _ = _run(["git", "merge-base", "--is-ancestor", proof or remote_ref,
                    "refs/remotes/origin/main"], cwd=repo)
    return "green" if code == 0 else "yellow"


def _priorities(gates: list[dict[str, str]], tools: dict[str, dict[str, str]]) -> list[dict[str, str | None]]:
    steps: list[dict[str, str | None]] = []
    for tool in ("codex", "gh", "gemini"):
        if tools[tool]["status"] != "green":
            steps.append({"title": f"{tool} anmelden", "detail": tools[tool]["detail"], "action": None})
    for gate in gates:
        if gate["status"] != "green":
            action = "tests" if gate["id"] in {"A1", "B0"} else None
            steps.append({"title": f"{gate['id']} {gate['name']}",
                          "detail": gate["detail"], "action": action})
    return steps[:3]


def project_resume(
    gates: list[dict[str, str]], tools: dict[str, dict[str, str]], repo: Path
) -> dict[str, Any]:
    missing = [name for name in ("codex", "gh", "gemini") if tools[name]["status"] != "green"]
    if missing:
        focus = "Gate 0 – Entwicklungsumgebung fertigstellen"
        stopped = "Agent-/GitHub-Logins"
    else:
        next_gate = next((gate for gate in gates if gate["status"] != "green"), None)
        focus = (f"{next_gate['id']} – {next_gate['name']}" if next_gate else
                 "Implementierungen auf main nachgewiesen")
        stopped = (next_gate["detail"] if next_gate else
                   "Beta-Freigabe benötigt Betriebs- und Security-Nachweise am aktuellen Head")

    steps: list[dict[str, str | None]] = []
    login_commands = {
        "codex": "codex login",
        "gh": "gh auth login",
        "gemini": "agy",
    }
    for name in missing:
        steps.append({
            "title": f"{name} anmelden",
            "type": "command",
            "value": login_commands[name],
            "detail": "TERM-Befehl kopieren und Auth im Browser bestätigen",
        })
    for gate in gates:
        if gate["status"] == "green":
            continue
        review_pr = PENDING_REVIEWS.get(gate["id"])
        steps.append({
            "title": f"{gate['id']} {gate['name']}",
            "type": "url" if review_pr else "action" if gate["id"] in {"A1", "B0"} else "info",
            "value": (f"https://github.com/Kaancodm/GeniusNew/pull/{review_pr}"
                      if review_pr else "tests" if gate["id"] in {"A1", "B0"} else ""),
            "detail": gate["detail"],
        })
        if len(steps) >= 5:
            break

    merged = sum(1 for gate in gates if gate.get("implementation_status") == "green")
    return {
        "focus": focus,
        "stopped_at": stopped,
        "main_head": _run(["git", "rev-parse", "--short=12", "refs/remotes/origin/main"],
                          cwd=repo)[1],
        "merged_gates": merged,
        "total_gates": len(gates),
        "resume": steps[:5],
    }


def research_items() -> list[dict[str, str]]:
    path = Path(__file__).with_name("research.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items", [])
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)][:20]
    except (OSError, json.JSONDecodeError):
        return []


def snapshot(repo: Path = DEFAULT_REPO) -> dict[str, Any]:
    gates = []
    for gate, name, ref in BETA_GATES:
        status = gate_status(repo, ref)
        implementation_status = status
        review_pr = PENDING_REVIEWS.get(gate)
        if status == "green" and (review_pr or gate in PENDING_OPERATIONS):
            status = "yellow"
        if implementation_status == "green" and review_pr:
            detail = (f"PR #{review_pr} gemergt; "
                      "HOLD: erforderlicher Claude-DB-Review nicht nachgewiesen")
        elif implementation_status == "green" and gate in PENDING_OPERATIONS:
            detail = PENDING_OPERATIONS[gate]
        else:
            detail = ("Implementierung auf origin/main nicht nachgewiesen" if status == "red" else
                      "Implementierung auf origin/main nachgewiesen" if status == "green" else
                      "Implementierungsnachweis noch nicht auf origin/main")
        gates.append({"id": gate, "name": name, "status": status, "detail": detail,
                      "implementation_status": implementation_status})
    tools = {
        "claude": tool_status("claude"),
        "codex": tool_status("codex"),
        "gemini": tool_status("gemini"),
        "gh": tool_status("gh"),
    }
    return {
        "generated_at": int(time.time()),
        "repo": repo_status(repo),
        "services": {
            "tailscale": service_status("tailscaled"),
            "desktop_commander": service_status("desktop-commander-remote.service", user=True),
            "docker": docker_status(),
        },
        "tools": tools,
        "priorities": _priorities(gates, tools),
        "project": project_resume(gates, tools, repo),
        "research": research_items(),
        "tmux": _simple(
            "green" if Path(f"/tmp/tmux-{os.getuid()}/default").exists() else "yellow",
            "genius session" if Path(f"/tmp/tmux-{os.getuid()}/default").exists() else "check from TERM",
        ),
        "gates": gates,
        "commands": COMMANDS,
    }

def agent_socket() -> str:
    return f"/run/user/{os.getuid()}/genius-deck-agents.sock"


def agent_session(name: str) -> dict[str, str]:
    if name != "hermes":
        return _simple("yellow", "Kein Server-Adapter")
    code, out = _run(
        ["tmux", "-S", agent_socket(), "list-panes", "-t", name,
         "-F", "#{pane_dead}:#{pane_current_command}"],
        cwd=Path.home(),
    )
    active = code == 0 and any(line.startswith("0:") for line in out.splitlines())
    return _simple("green" if active else "yellow",
                   "Terminal geöffnet · Eingabe/Anmeldung in TERM" if active else "Sitzung nicht gestartet")


def agent_hub(repo: Path = DEFAULT_REPO) -> dict[str, Any]:
    """Inspect supported access points without model calls or credential reads."""
    launch = repo
    cards = [
        {"id": "grok", "name": "Grok", "status": "yellow",
         "detail": "Web-App · Anmeldung wird im Browser geprüft",
         "url": "https://grok.com/", "url_label": "Grok öffnen"},
        {"id": "grok_bot", "name": "Grok Bot", "status": "yellow",
         "detail": "Externer Bot · hier kein unterstützter Steuerungsadapter",
         "note": "Vorhandenen Bot auf deinem Gerät öffnen. SSH-Einrichtung allein ist kein Live-Nachweis.",
         "disabled_label": "Direktstart nicht verfügbar"},
        {"id": "abacus", "name": "Abacus.AI", "status": "yellow",
         "detail": "ChatLLM im Browser · Anmeldung wird dort geprüft",
         "url": "https://apps.abacus.ai/chatllm/", "url_label": "Abacus.AI öffnen",
         "note": "Vorläufiger Ersatz für Grok Build · kein Server-CLI-, Repo- oder Secret-Zugriff."},
    ]
    installed = tool_status("hermes")
    running = agent_session("hermes")
    available = installed["status"] == "green"
    cards.append({
        "id": "hermes", "name": "Hermes",
        "status": running["status"] if available else "red",
        "detail": installed["detail"] + " · " + running["detail"],
        "command": (
            f"cd {shlex.quote(str(launch))} && python3 -m tools.control_deck.actions "
            "--start-agent hermes"
        ) if available else None,
        "action": "hermes_status",
        "note": "Nutzt den konfigurierten Modellanbieter; kein eigenes Modellkontingent. Keine automatische Aufgabe.",
    })
    return {"generated_at": int(time.time()), "agents": cards}
