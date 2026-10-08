#!/usr/bin/env python3
"""Start reproducible development sessions without changing shared checkouts."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request

HOME_DIR = Path.home()
REPO = HOME_DIR / "GeniusNew"
ROOT = HOME_DIR / "tasks/geniusnew/workflow"
CONFIG = HOME_DIR / ".config/genius-workflow"
RUNTIME = HOME_DIR / ".local/state/genius-workflow"
TOOLS = ("codex", "claude", "gemini-a", "gemini-b", "cursor", "warp")
MODEL = "qwen2.5-coder:3b"
GEMINI_AUTH_ENV = ("GEMINI_CLI_HOME", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                   "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_ACCESS_TOKEN",
                   "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION")


class WorkflowRefused(SystemExit):
    """The launcher cannot establish an authorized isolated task environment."""


def probe(argv: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 125, ""


def repository(raw: str) -> Path:
    path = Path(raw).expanduser().resolve(strict=True)
    rc, top = probe(["git", "-C", str(path), "rev-parse", "--show-toplevel"])
    if rc or Path(top.strip()).resolve() != path:
        raise WorkflowRefused("Bitte den Wurzelpfad eines Git-Worktrees verwenden.")
    for flags in (("--all",), ("--push", "--all")):
        rc, output = probe(["git", "-C", str(path), "remote", "get-url", *flags, "origin"])
        remotes = output.splitlines()
        if rc or not remotes or any(not re.fullmatch(
                r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
                r"Kaancodm/GeniusNew(?:\.git)?", remote) for remote in remotes):
            raise WorkflowRefused("Dieser Starter ist auf Kaancodm/GeniusNew begrenzt.")
    return path


def workspace(raw: str) -> Path:
    path = repository(raw)
    rc, git_dir = probe(["git", "-C", str(path), "rev-parse", "--absolute-git-dir"])
    common_rc, common = probe(["git", "-C", str(path), "rev-parse", "--git-common-dir"])
    if rc or common_rc or Path(git_dir.strip()).resolve() == (path / common.strip()).resolve():
        raise WorkflowRefused("Entwicklung braucht einen separaten verknüpften Worktree.")
    rc, branch = probe(["git", "-C", str(path), "branch", "--show-current"])
    if rc or not branch.strip() or branch.strip() in ("main", "master"):
        raise WorkflowRefused("Entwicklung braucht einen benannten Arbeitsbranch.")
    return path


def argv_for(tool: str, path: Path, review: bool) -> tuple[list[str], dict[str, str]]:
    env = dict(os.environ)
    if tool == "codex":
        argv = ["codex", "-C", str(path), "--sandbox",
                "read-only" if review else "workspace-write",
                "--ask-for-approval", "on-request"]
    elif tool == "claude":
        argv = ["claude", "--permission-mode", "plan" if review else "acceptEdits"]
    elif tool in ("gemini-a", "gemini-b"):
        argv = ["gemini", "--approval-mode", "plan"]
        for name in GEMINI_AUTH_ENV:
            env.pop(name, None)
        env["GEMINI_CLI_HOME"] = str(HOME_DIR if tool == "gemini-a" else CONFIG / "gemini-b")
    elif tool == "warp":
        if review:
            raise WorkflowRefused("Warp CLI hat keinen hier geprüften Review-Modus.")
        argv = ["warp"]
    else:
        argv = ["cursor-agent", "--sandbox", "enabled", "--workspace", str(path)]
        argv += ["--mode", "plan"] if review else ["--auto-review"]
    if not shutil.which(argv[0]):
        raise WorkflowRefused(f"{tool}: CLI fehlt.")
    return argv, env


def session_name(tool: str, path: Path, review: bool = False) -> str:
    import hashlib
    mode = "review" if review or tool.startswith("gemini-") else "dev"
    return tool + "-" + mode + "-" + hashlib.sha256(str(path).encode()).hexdigest()[:10]


def tmux_base() -> list[str]:
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    return ["tmux", "-S", str(RUNTIME / "tmux.sock")]


def start(tool: str, raw: str, review: bool, dry_run: bool) -> None:
    path = workspace(raw)
    argv, env = argv_for(tool, path, review)
    if dry_run:
        print(json.dumps({"workspace": str(path), "argv": argv,
                          "profile_b": env.get("GEMINI_CLI_HOME")}, ensure_ascii=False))
        return
    base = tmux_base()
    name = session_name(tool, path, review)
    with (RUNTIME / "start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        exists = subprocess.run(base + ["has-session", "-t", "=" + name],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if exists.returncode == 0:
            mode_flag = " --review" if review else ""
            raise WorkflowRefused(f"Sitzung vorhanden: genius-workflow attach {tool} {path}{mode_flag}")
        # CLI plan modes can change. Every session retains exclusive workspace
        # ownership until an independently enforced read-only sandbox exists.
        for writer in TOOLS:
            for mode in (False, True):
                active = subprocess.run(base + ["has-session", "-t", "=" + session_name(writer, path, mode)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if active.returncode == 0:
                    raise WorkflowRefused("Eine Sitzung arbeitet bereits in diesem Worktree.")
        # The profile applies only to this new tmux session, never the user's HOME.
        if tool.startswith("gemini-"):
            # tmux's existing server also has an environment: unset conflicting
            # credentials in the actual child, not just the client process.
            argv = ["env", *[arg for name in GEMINI_AUTH_ENV for arg in ("-u", name)],
                    "GEMINI_CLI_HOME=" + env["GEMINI_CLI_HOME"], *argv]
        subprocess.run(base + ["new-session", "-d", "-s", name, "-c", str(path), *argv],
                       env=env, check=True)
    print(f"Sitzung gestartet. Verbinden: genius-workflow attach {tool} {path}")


def status() -> None:
    for label, command in (
        ("Codex", ["codex", "login", "status"]),
        ("Claude", ["claude", "auth", "status"]),
        ("Cursor", ["cursor-agent", "status"]),
        ("GitHub", ["gh", "auth", "status"]),
    ):
        rc, output = probe(command)
        ok = rc == 0 and not re.search(r"not (?:logged|authenticated)", output, re.I)
        print(f"{label}: {'Anmeldung erkannt' if ok else 'Anmeldung ungeprüft'}; Kontingent ungeprüft")
    for label, path in (
        ("Gemini A", HOME_DIR / ".gemini/oauth_creds.json"),
        ("Gemini B", CONFIG / "gemini-b/.gemini/oauth_creds.json"),
    ):
        print(f"{label}: {'Anmeldedatei vorhanden' if path.is_file() else 'Anmeldung fehlt'}")
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
            models = [m["name"] for m in json.load(response).get("models", [])]
        print("Lokale KI: " + ", ".join(models))
    except (OSError, ValueError):
        print("Lokale KI: nicht erreichbar")
    print("Kontingente, Cloud-Abos und End-to-End-Verbindungen werden hier nicht bestätigt.")
    print("Warp, Gumloop, HARPA, Framer, Replit, Microsoft: siehe docs/DEV-WORKFLOW.md")


def new_task(task: str) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", task):
        raise WorkflowRefused("Aufgabenname: 1–60 Kleinbuchstaben/Ziffern/Bindestriche.")
    target = ROOT / task
    if target.exists():
        raise WorkflowRefused("Aufgabe existiert bereits; vorhandene Arbeit bleibt erhalten.")
    repository(str(REPO))
    subprocess.run(["git", "-C", str(REPO), "fetch", "origin", "main"], check=True)
    ROOT.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(REPO), "worktree", "add", "-b",
                    "workflow/" + task, str(target), "origin/main"], check=True)
    print(target)


def main() -> None:
    parser = argparse.ArgumentParser(description="GeniusNew Entwicklungsstarter")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    new = sub.add_parser("new")
    new.add_argument("task")
    for action in ("start", "attach"):
        child = sub.add_parser(action)
        child.add_argument("tool", choices=TOOLS)
        child.add_argument("workspace")
        child.add_argument("--review", action="store_true")
        if action == "start":
            child.add_argument("--dry-run", action="store_true")
    ai = sub.add_parser("ai")
    ai.add_argument("prompt")
    args = parser.parse_args()
    if args.action == "status":
        status()
    elif args.action == "new":
        new_task(args.task)
    elif args.action == "start":
        start(args.tool, args.workspace, args.review, args.dry_run)
    elif args.action == "attach":
        path = workspace(args.workspace)
        base = tmux_base()
        os.execvp(base[0], base + ["attach-session", "-t", "=" + session_name(args.tool, path, args.review)])
    else:
        data = json.dumps({"model": MODEL, "prompt": args.prompt, "stream": False,
                           "options": {"num_ctx": 2048, "num_predict": 256,
                                       "num_thread": 4}}).encode()
        request = urllib.request.Request("http://127.0.0.1:11434/api/generate", data,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.load(response)
        if result.get("error"):
            raise WorkflowRefused("Lokale KI: " + str(result["error"]))
        print(result.get("response", ""))


if __name__ == "__main__":
    main()
