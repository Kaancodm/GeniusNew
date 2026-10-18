from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

DEFAULT_REPO = Path(os.environ.get("GENIUSNEW_REPO", Path(__file__).resolve().parents[2]))

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
    ("D1", "Portal→Core-Vertrag", None),
    ("D2", "Portal-Identität", None),
    ("D3", "Portal-Ansichten", None),
    ("C5", "Betrieb + Restore", "3497e699c3b42c51eb0d26742af7b3e6de042f58"),
    ("E1", "Persistenz-Angriffs-Demo", "122a6902fc8a88488dd9312b4803571fb85c8ab2"),
    ("E2", "Security-Review final", None),
    ("E3", "Fresh-host-Probe", None),
]

# Merge and CI evidence verified against GitHub and local main on 2026-10-05.
VERIFIED_CI_SHA = "3497e699c3b42c51eb0d26742af7b3e6de042f58"
GATE_ORDER = ("A1", "B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7",
              "C1", "C2", "C3", "C4", "A2", "C5", "E3", "D1", "D2", "D3", "E1", "E2")
GATE_NOTES = {
    "A2": "Eigener OS-Nutzer auf dem Server bleibt ungeprüft.",
    "C2": "Anker-Dienst implementiert; produktive Nutzertrennung ungeprüft.",
    "C5": "Restore-Probe-Code gemergt; produktiver Backup-/Restore-Betrieb ungeprüft.",
    "B4": "Reviewnachweis am aktuellen Stand ungeprüft; Roadmap nennt Nachholbedarf.",
    "B5": "Reviewnachweis am aktuellen Stand ungeprüft; Roadmap nennt Nachholbedarf.",
    "E3": "Frische Installation vor Portal-Aufbau nachweisen; keinen Anker zurücksetzen.",
    "E2": "Unabhängiges Gesamt-Review am exakten Release-Head erforderlich.",
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
        return _simple("yellow", "CLI installiert · Anmeldung ungeprüft")

    code, out = _run([exe, "--version"], cwd=Path.home())
    version = out.splitlines()[0] if out else "installed"
    return _simple("green" if code == 0 else "yellow", version)


def gate_status(repo: Path, ref: str | None) -> str:
    if ref is None:
        return "red"
    is_commit = len(ref) == 40 and all(c in "0123456789abcdef" for c in ref)
    target = ref if is_commit else (ref if ref.startswith("origin/") else f"origin/{ref}")
    code, _ = _run(["git", "rev-parse", "--verify", f"{target}^{{commit}}"], cwd=repo)
    if code != 0:
        return "red"
    code, _ = _run(["git", "merge-base", "--is-ancestor", target, "main"], cwd=repo)
    return "green" if code == 0 else ("yellow" if code == 1 else "red")


def gate_records(repo: Path) -> list[dict[str, str]]:
    records = []
    for gate, name, ref in BETA_GATES:
        state = gate_status(repo, ref)
        detail = ("Merge-Commit auf main nachgewiesen; kein vollständiger Gate-PASS."
                  if state == "green" else
                  "Code vorhanden, aber nicht auf main." if state == "yellow" else
                  "Kein Merge-Nachweis auf main verfügbar.")
        if gate in GATE_NOTES:
            detail += " " + GATE_NOTES[gate]
        records.append({"id": gate, "name": name, "status": state,
                        "detail": detail, "commit": ref or ""})
    return records


def python_for(repo: Path) -> str:
    python = repo / ".venv/bin/python3"
    return str(python) if python.exists() else "python3"


def commands_for(repo: Path) -> dict[str, str]:
    root = shlex.quote(str(repo))
    executable = shlex.quote(python_for(repo))
    return {
        "tmux": COMMANDS["tmux"], "docker": COMMANDS["docker"],
        "git": f"cd {root} && git status --short --branch",
        "tests": f"cd {root} && {executable} -m unittest discover -s tests -q",
        "demo": f"cd {root} && ./scripts/demo.sh",
        "refusals": f"cd {root} && {executable} scripts/refusals.py",
    }


def _priorities(gates: list[dict[str, str]], tools: dict[str, dict[str, str]]) -> list[dict[str, str | None]]:
    steps: list[dict[str, str | None]] = []
    for tool in ("codex", "gh", "gemini"):
        if tools[tool]["status"] != "green":
            steps.append({"title": f"{tool} anmelden", "detail": tools[tool]["detail"], "action": None})
    by_id = {gate["id"]: gate for gate in gates}
    for gate in (by_id[name] for name in GATE_ORDER if name in by_id):
        if gate["status"] != "green":
            action = "tests" if gate["id"] in {"A1", "A2", "B0"} else None
            steps.append({"title": f"{gate['id']} {gate['name']}", "detail": "nächstes offenes Beta-Gate", "action": action})
    return steps[:3]


def project_resume(
    gates: list[dict[str, str]], tools: dict[str, dict[str, str]], repo: Path
) -> dict[str, Any]:
    by_id = {gate["id"]: gate for gate in gates}
    missing = [name for name in ("codex", "gh", "gemini") if tools[name]["status"] != "green"]
    open_gates = [by_id[name] for name in GATE_ORDER
                  if name in by_id and by_id[name]["status"] != "green"]
    if missing:
        focus = "Gate 0 – Entwicklungsumgebung fertigstellen"
        stopped = "Agent-/GitHub-Logins prüfen; Modellkontingent separat prüfen"
    elif open_gates:
        focus = open_gates[0]["id"] + " – " + open_gates[0]["name"]
        stopped = "Nächster offener Implementierungs-/Nachweisschritt"
    else:
        focus = "Implementierungen auf main – Abschlussnachweise prüfen"
        stopped = "Gesamtprüfung und ausdrückliche Release-Freigabe stehen separat"

    steps: list[dict[str, str | None]] = []
    login_commands = {"codex": "codex login", "gh": "gh auth login", "gemini": "agy"}
    for name in missing:
        steps.append({"title": f"{name} anmelden", "type": "command",
                      "value": login_commands[name],
                      "detail": "TERM-Befehl kopieren; Auth und Kontingent getrennt prüfen"})
    if by_id["A1"]["status"] != "green":
        steps.append({"title": "A1 Seccomp – PR #57", "type": "url",
                      "value": "https://github.com/Kaancodm/GeniusNew/pull/57",
                      "detail": "Aktuellen Merge-/Review-Nachweis prüfen"})
    if by_id["B0"]["status"] != "green":
        steps.append({"title": "B0 DB-Design – PR #70", "type": "url",
                      "value": "https://github.com/Kaancodm/GeniusNew/pull/70",
                      "detail": "Aktuellen Merge-/Review-Nachweis prüfen"})
    for gate in open_gates:
        if gate["id"] in {"A1", "B0"}:
            continue
        document = "docs/setup/SERVER-TOOL.md" if gate["id"] == "E3" else "docs/ROADMAP-V02.md"
        steps.append({"title": gate["id"] + " " + gate["name"], "type": "url",
                      "value": "https://github.com/Kaancodm/GeniusNew/blob/main/" + document,
                      "detail": GATE_NOTES.get(gate["id"], "Nachweis am exakten Head prüfen")})

    _, main_sha = _run(["git", "rev-parse", "main"], cwd=repo)
    ci_matches = main_sha == VERIFIED_CI_SHA
    return {
        "focus": focus, "stopped_at": stopped,
        "main_head": repo_status(repo).get("head", ""),
        "main_sha": main_sha,
        "merged_gates": sum(1 for gate in gates if gate["status"] == "green"),
        "total_gates": len(gates), "resume": steps[:5],
        "release_ready": False,
        "evidence_note": "Code auf main ist kein Beta-/Release-PASS. Vollständige Gates am selben SHA bleiben zu prüfen.",
        "ci": {"status": "green" if ci_matches else "yellow",
               "detail": "contracts erfolgreich · geprüft 05.10.2026" if ci_matches else
                         "Kein aktueller CI-Nachweis für diesen main-SHA"},
        "pending_evidence": [
            "E3: frische Installation auf sauberem Host nachweisen",
            "A2/C2: eigene OS-Nutzer und produktives Betriebsmodell ungeprüft",
            "B4/B5: aktuelle Review-Freigaben ungeprüft; Roadmap nennt Nachholbedarf",
        ],
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
    gates = gate_records(repo)
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
        "commands": commands_for(repo),
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
         "status_label": "TEXT-REVIEW GEPRÜFT",
         "detail": "Browser-Zugang und geprüfte CLI-Textreviews · Kontingent aktuell ungeprüft",
         "url": "https://apps.abacus.ai/chatllm/", "url_label": "Abacus.AI öffnen",
         "models": [
             {"name": "Gemini 3.8 Flash", "purpose": "Kleine Textreviews",
              "verified_on": "05.10.2026", "model_id": "GEMINI_3_8_FLASH_THINKING",
              "term_command": "/home/kaan/src/geniusnew-abacus-setup-20260930/start.sh flash",
              "button_label": "Flash · TERM kopieren"},
             {"name": "Sonnet 5.5", "purpose": "Anspruchsvolle Textreviews",
              "verified_on": "05.10.2026", "model_id": "CLAUDE_V5_5_SONNET",
              "term_command": "/home/kaan/src/geniusnew-abacus-setup-20260930/start.sh sonnet",
              "button_label": "Sonnet · TERM kopieren"},
         ],
         "note": "Testnachweis vom 05.10.2026: Antworten und Starter geprüft, Plan-Modus. "
                 "Vor jedem Auftrag Kontingent prüfen. Repo-Automatik ungeprüft. "
                 "Das Deck startet keine Abacus-Aufträge."},
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
