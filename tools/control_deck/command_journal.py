"""Shared command evidence, separate from job state and execution authority.

A green result is bound to the fixed command, the target that ran it, the commit
and the source bytes it ran against, and to its own output evidence. Anything
that does not match the current state is shown as stale, never as a pass.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile
import time

from geniusnew.contracts import ContractError

CATALOG = {
    "decision-tests": {"title": "Entscheidungszentrale, Journal und Kontext prüfen", "argv": ["python3", "-B", "-W", "error::ResourceWarning", "-m", "unittest", "tools.control_deck.test_decisions", "tools.control_deck.test_command_journal", "tools.control_deck.test_operating_context", "-q"]},
    "deck-tests": {"title": "Bestehendes Control Deck prüfen", "argv": ["python3", "-B", "-W", "error::ResourceWarning", "-m", "unittest", "discover", "-s", "tests", "-p", "test_control_deck.py", "-q"]},
}
TARGETS = ("server", "termius", "powershell")
MAX_BYTES = 128 * 1024
MAX_ARCHIVES = 8
MAX_TAIL = 4000
_RAN = re.compile(rb"Ran [0-9]+ tests? in [0-9.]+s")


def target_profile() -> dict:
    """Connection metadata only; never read SSH keys or credentials."""
    configured = os.environ.get("GENIUSNEW_COMMAND_TARGETS")
    if not configured:
        return {}
    try:
        fd = os.open(configured, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return {}
            data = stream.read(4097)
        if len(data) > 4096:
            return {}
        raw = json.loads(data)
        host, user = raw["ssh_host"], raw["ssh_user"]
        if not isinstance(host, str) or len(host) > 253 or not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in host.split(".")):
            return {}
        if not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user):
            return {}
        return {"ssh_host": host, "ssh_user": user}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return {}


def default_path() -> Path:
    return Path(os.environ.get("GENIUSNEW_COMMAND_JOURNAL", "~/.local/state/genius-control-deck/commands.json")).expanduser()


def _journal_argument(path: Path) -> str:
    """Show the journal path relative to ~ so a copied command expands on the target."""
    home = Path.home()
    try:
        return "~/" + shlex.quote(str(path.relative_to(home)))
    except ValueError:
        return shlex.quote(str(path))


def _runner(command_id: str, repo: str, target: str) -> str:
    return " ".join(["python3", "-B", "-m", "tools.control_deck.command_journal", "--run", command_id,
                     "--repo", shlex.quote(repo), "--output", _journal_argument(default_path()),
                     "--target", target])


def variants(repo: Path, command_id: str, states: dict, profile: dict) -> list:
    """Each target shows only its own latest evidence; other targets stay pending."""
    remote = "cd -- " + shlex.quote(str(repo)) + " && " + _runner(command_id, str(repo), "termius")
    server = "cd -- " + shlex.quote(str(repo)) + " && " + _runner(command_id, str(repo), "server")
    result = [
        {"target": "server", "label": "Server · Bash", "where": "Direkt im Server-Terminal",
         "command": server, "status": states.get("server", "pending"), "copy_ready": True},
        {"target": "termius", "label": "iPhone/iPad · Termius → Server", "where": "In der bestehenden SSH-Sitzung zum Server",
         "command": remote, "status": states.get("termius", "pending"), "copy_ready": True},
    ]
    powershell = "cd -- " + shlex.quote(str(repo)) + " && " + _runner(command_id, str(repo), "powershell")
    if profile:
        # PowerShell single-quoted strings do not expand $, backticks or subexpressions.
        literal = powershell.replace("'", "''")
        command = "ssh -T " + profile["ssh_user"] + "@" + profile["ssh_host"] + " '" + literal + "'"
        result.append({"target": "powershell", "label": "Laptop · PowerShell → Server",
                       "where": "In PowerShell auf dem Laptop; führt den Test per SSH auf dem Server aus",
                       "command": command, "status": states.get("powershell", "pending"), "copy_ready": True,
                       "detail": "Nur ein Lauf mit Ziel powershell belegt diesen Startweg. Server-Erfolg ist kein Nachweis."})
    else:
        result.append({"target": "powershell", "label": "Laptop · PowerShell → Server",
                       "where": "PowerShell auf dem Laptop", "command": "",
                       "status": states.get("powershell", "pending"),
                       "copy_ready": False, "detail": "Geprüftes SSH-Zielprofil fehlt; kein Platzhalter zum blinden Kopieren."})
    return result


def source_digest(repo: Path) -> str:
    """Hash the whole control deck and the test modules that exercise it."""
    deck = sorted(p for p in (repo / "tools" / "control_deck").rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    names = [p.relative_to(repo).as_posix() for p in deck] + ["tests/test_control_deck.py", "tests/test_decision_desk.py"]
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode() + b"\0" + (repo / name).read_bytes() + b"\0")
    return digest.hexdigest()


def _git_head(repo: Path) -> str | None:
    """Commit the checkout is on now. None means unknown, which is never green."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                             capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    head = out.stdout.strip()
    return head if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", head) else None


def _evidence(ran: str, tail: str) -> str:
    return hashlib.sha256((ran + "\n" + tail).encode("utf-8")).hexdigest()


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ContractError("journal type")
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ContractError("journal size")
    return data


def _validate(raw: dict, now: float) -> list:
    if not isinstance(raw, dict) or raw.get("schema") != 1:
        raise ContractError("journal schema")
    events = raw.get("events")
    if not isinstance(events, list) or len(events) > 100:
        raise ContractError("journal capacity")
    last = 0.0
    for item in events:
        if not isinstance(item, dict) or item.get("command_id") not in CATALOG or item.get("target") not in TARGETS:
            raise ContractError("command")
        date = datetime.fromisoformat(item["finished_at"].replace("Z", "+00:00"))
        if date.tzinfo is None or not last <= date.timestamp() <= now + 30:
            raise ContractError("journal time")
        last = date.timestamp()
        if type(item.get("exit_code")) is not int or type(item.get("tests")) is not int or item["tests"] < 0:
            raise ContractError("exit evidence")
        if not isinstance(item.get("head"), str) or re.fullmatch(r"[0-9a-f]{40}", item["head"]) is None:
            raise ContractError("head evidence")
        if not isinstance(item.get("source_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["source_sha256"]):
            raise ContractError("source digest")
        ran, tail = item.get("ran_line"), item.get("output_tail")
        if not isinstance(ran, str) or len(ran) > 200 or not isinstance(tail, str) or len(tail) > MAX_TAIL:
            raise ContractError("evidence text")
        found = re.fullmatch(r"Ran ([0-9]+) tests? in [0-9.]+s", ran)
        if (int(found[1]) if found else 0) != item["tests"]:
            raise ContractError("evidence count")
        if item.get("evidence_sha256") != _evidence(ran, tail):
            raise ContractError("evidence digest")
        if not isinstance(item.get("repo"), str) or len(item["repo"]) > 240:
            raise ContractError("context")
    return events


def _archive_path(path: Path, index: int) -> Path:
    return path.with_name(path.name + f".archive-{index}.json")


def _history(path: Path, now: float) -> tuple[list, list, list]:
    raw = json.loads(_read(path))
    archives = []
    if isinstance(raw, dict) and raw.get("schema") == 2:
        archives = raw.get("archives")
        if (not isinstance(archives, list) or not 1 <= len(archives) <= MAX_ARCHIVES
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in archives)):
            raise ContractError("archive manifest")
        raw = {**raw, "schema": 1}
    active = _validate(raw, now)
    history = []
    for index, expected in enumerate(archives, 1):
        data = _read(_archive_path(path, index))
        if hashlib.sha256(data).hexdigest() != expected:
            raise ContractError("archive digest")
        segment = _validate(json.loads(data), now)
        if not segment:
            raise ContractError("archive empty")
        history.extend(segment)
    # Validate ordering across boundaries as well as inside every segment.
    times = [datetime.fromisoformat(e["finished_at"].replace("Z", "+00:00")).timestamp()
             for e in history + active]
    if times != sorted(times):
        raise ContractError("archive order")
    return archives, history, active


def _events(path: Path, now: float) -> list:
    _, history, active = _history(path, now)
    return history + active


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path: Path, data: bytes, *, archive: bool = False) -> None:
    if len(data) > MAX_BYTES:
        raise ContractError("journal size")
    fd, name = tempfile.mkstemp(prefix=".commands-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if archive:
            # Publish without ever replacing an existing evidence segment.
            os.link(name, path)
        else:
            os.replace(name, path)
        _sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def rotate(path: Path) -> dict:
    """Explicit, bounded archival; never execute a catalog command or delete evidence."""
    import fcntl
    lock = os.open(str(path) + ".lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            raise ContractError("lock type")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        archives, _, active = _history(path, time.time())
        if not active:
            raise ContractError("nothing to archive")
        if len(archives) >= MAX_ARCHIVES:
            raise ContractError("archive capacity")
        data = json.dumps({"schema": 1, "events": active}).encode()
        destination = _archive_path(path, len(archives) + 1)
        if os.path.lexists(destination):
            # A failed manifest publication is retryable only with identical bytes.
            if _read(destination) != data:
                raise ContractError("archive collision")
            _sync_directory(path.parent)
        else:
            _write(destination, data, archive=True)
        digest = hashlib.sha256(data).hexdigest()
        _write(path, json.dumps({"schema": 2, "archives": archives + [digest], "events": []}).encode())
        return {"archive": str(destination), "sha256": digest, "events": len(active)}
    finally:
        os.close(lock)


def _state(event: dict, digest: str | None, head: str | None) -> str:
    if digest is None or head is None:
        return "pending"
    if event["source_sha256"] != digest or event["head"] != head:
        return "stale"
    return event["status"]


def snapshot(repo: Path, path: Path | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    try:
        events = _events(path or default_path(), now)
        status = "ready"
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, AttributeError):
        events, status = [], "unavailable"
    rendered = []
    for event in events:
        item = dict(event)
        item["status"] = "passed" if item["exit_code"] == 0 and item["tests"] > 0 else "failed"
        item["title"] = CATALOG[item["command_id"]]["title"]
        item["command"] = _runner(item["command_id"], item["repo"], item["target"])
        rendered.append(item)
    try:
        digest = source_digest(repo)
    except OSError:
        digest = None
    head = _git_head(repo)
    commands = []
    profile = target_profile()
    for key, definition in CATALOG.items():
        matching = [event for event in rendered if event["command_id"] == key and event["repo"] == str(repo.resolve())]
        latest_per_target = {}
        for event in matching:
            latest_per_target[event["target"]] = event
        states = {target: _state(event, digest, head) for target, event in latest_per_target.items()}
        state = _state(matching[-1], digest, head) if matching else "pending"
        commands.append({"id": key, "title": definition["title"],
                         "command": _runner(key, str(repo), "server"),
                         "repo": str(repo), "status": state,
                         "variants": variants(repo, key, states, profile)})
    return {"status": status, "commands": commands, "events": list(reversed(rendered)),
            "write_actions": False, "authority": "execution_metadata_only"}


def run(command_id: str, repo: Path, path: Path, target: str) -> dict:
    import fcntl
    if command_id not in CATALOG or target not in TARGETS or not repo.is_dir():
        raise ContractError("run context")
    head = _git_head(repo)
    if head is None:
        raise ContractError("head unknown")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = os.open(str(path) + ".lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            raise ContractError("lock type")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            archives, _, events = _history(path, time.time())
        except FileNotFoundError:
            # Missing referenced segments or a missing active file beside archives
            # must never be interpreted as a new, empty history.
            if os.path.lexists(path) or any(os.path.lexists(_archive_path(path, i))
                                           for i in range(1, MAX_ARCHIVES + 1)):
                raise ContractError("journal missing")
            archives, events = [], []
        if len(archives) < MAX_ARCHIVES and os.path.lexists(_archive_path(path, len(archives) + 1)):
            raise ContractError("archive pending")
        if len(events) >= 100:
            raise ContractError("journal full")
        before = source_digest(repo)
        outcome = subprocess.run(CATALOG[command_id]["argv"], cwd=repo,
                                 capture_output=True, timeout=120, check=False)
        output = outcome.stdout + outcome.stderr
        found = _RAN.search(output)
        ran = found.group(0).decode() if found else ""
        tests = int(re.match(r"Ran ([0-9]+)", ran)[1]) if ran else 0
        tail = output[-MAX_TAIL:].decode("utf-8", "replace")
        # Source or head changing during the run means this result describes neither state.
        unchanged = source_digest(repo) == before and _git_head(repo) == head
        event = {"command_id": command_id, "target": target,
                 "finished_at": datetime.now(timezone.utc).isoformat(),
                 "exit_code": outcome.returncode if unchanged else -2,
                 "tests": tests, "head": head, "source_sha256": before,
                 "ran_line": ran, "output_tail": tail, "evidence_sha256": _evidence(ran, tail),
                 "repo": str(repo.resolve())}
        # Capacity/serialization failure cannot silently discard the previous log.
        raw = {"schema": 1, "events": events + [event]}
        if archives:
            raw.update(schema=2, archives=archives)
        _write(path, json.dumps(raw).encode())
        return event
    finally:
        os.close(lock)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one fixed test command and record its result")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--run", choices=CATALOG)
    action.add_argument("--archive", action="store_true", help="archive the current segment; never run a command")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", choices=TARGETS,
                        help="where the operator started this run; recorded as given")
    args = parser.parse_args()
    if not args.archive and (args.repo is None or args.target is None):
        parser.error("--run requires --repo and --target")
    try:
        result = rotate(args.output) if args.archive else run(args.run, args.repo, args.output, args.target)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        parser.exit(1, "Run not confirmed; inspect the execution environment.\n")
    print(json.dumps(result))
    if not args.archive and (result["exit_code"] != 0 or result["tests"] == 0):
        parser.exit(1)


if __name__ == "__main__":
    main()
