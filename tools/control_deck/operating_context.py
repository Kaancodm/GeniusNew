"""Read a private user-maintained operating bulletin without changing policy."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from geniusnew.contracts import ContractError


def require_current(context: dict, assigned_digest: str, receipt_digest: str) -> None:
    """Adapter integration gate: no assignment may use an unconfirmed revision."""
    current = context.get("context_sha256")
    if context.get("status") != "ready" or not isinstance(current, str) or re.fullmatch(r"[0-9a-f]{64}", current) is None:
        raise ContractError("CONTEXT_UNAVAILABLE")
    if assigned_digest != current or receipt_digest != current:
        raise ContractError("CONTEXT_CHANGED_OR_NOT_ACKNOWLEDGED")


def snapshot(path: Path | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    try:
        source = path or Path(os.environ.get("GENIUSNEW_OPERATING_CONTEXT", "~/.local/state/genius-control-deck/operating-context.json")).expanduser()
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ContractError("context type")
            content = stream.read(65537)
        if len(content) > 65536:
            raise ContractError("context size")
        raw = json.loads(content)
        if not isinstance(raw, dict) or raw.get("schema") != 1:
            raise ContractError("context schema")
        version = raw.get("revision")
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", version):
            raise ContractError("context revision")
        date = datetime.fromisoformat(raw["updated_at"].replace("Z", "+00:00"))
        if date.tzinfo is None or date.timestamp() > now + 30:
            raise ContractError("context date")
        rules, tools, acknowledgements = raw.get("rules"), raw.get("tools"), raw.get("acknowledgements", [])
        if not isinstance(rules, list) or len(rules) > 50 or not isinstance(tools, list) or len(tools) > 50 or not isinstance(acknowledgements, list) or len(acknowledgements) > 50:
            raise ContractError("context records")
        for rule in rules:
            if not isinstance(rule, dict) or any(not isinstance(rule.get(k), str) or not 1 <= len(rule[k]) <= 1200 for k in ["id", "text", "source"]):
                raise ContractError("rule")
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("state") not in {"unknown", "exhausted", "not_connected"}:
                # AVAILABLE needs a separate live probe; a static bulletin cannot assert it.
                raise ContractError("tool state")
            if any(not isinstance(tool.get(k), str) or not 1 <= len(tool[k]) <= 500 for k in ["name", "detail", "source"]):
                raise ContractError("tool")
        agents = []
        digest = hashlib.sha256(json.dumps({"revision": version, "updated_at": raw["updated_at"],
                                           "rules": rules, "tools": tools}, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
        for name in ["Codex", "Claude", "Gemini"]:
            # A manual line, even one claiming "verified", is a self-report. Only an
            # authenticated adapter receipt could ever be stronger, and none exists yet.
            claimed = any(isinstance(item, dict) and item.get("agent") == name
                          and item.get("revision") == version
                          and item.get("context_sha256") == digest
                          for item in acknowledgements)
            agents.append({"name": name, "status": "MANUAL_UNAUTHENTICATED" if claimed else "NOT_CONFIRMED"})
        return {"status": "ready", "revision": version, "updated_at": raw["updated_at"],
                "context_sha256": digest,
                "rules": rules, "tools": tools, "agents": agents, "write_actions": False,
                "authority": "private_user_bulletin_not_a_review_approval"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        return {"status": "unavailable", "rules": [], "tools": [], "agents": [], "write_actions": False}


if __name__ == "__main__":
    print(json.dumps(snapshot(), ensure_ascii=False))
