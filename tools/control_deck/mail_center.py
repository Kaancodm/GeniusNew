from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_MAIL_SNAPSHOT = Path(
    os.environ.get(
        "GENIUSNEW_MAIL_SNAPSHOT",
        "~/.local/state/genius-control-deck/mail.json",
    )
).expanduser()

CATEGORIES = ("sales", "sponsoring", "general", "system")
CATEGORY_LABELS = {
    "sales": "Verkauf",
    "sponsoring": "Sponsoring",
    "general": "Allgemein",
    "system": "System",
}

_SPLIT_CATEGORY_HINTS = {
    "sales": "sales",
    "verkauf": "sales",
    "customer": "sales",
    "kunden": "sales",
    "lead": "sales",
    "sponsor": "sponsoring",
    "sponsoring": "sponsoring",
    "partner": "sponsoring",
    "investor": "sponsoring",
    "kooperation": "sponsoring",
    "github": "system",
    "security": "system",
    "billing": "system",
    "server": "system",
    "domain": "system",
    "system": "system",
}


def _clip(value: object, limit: int) -> str:
    return str(value or "").replace("\n", " ").replace("\r", " ")[:limit]


def _category(item: dict[str, Any]) -> str:
    explicit = str(item.get("category", "")).lower().strip()
    if explicit in CATEGORIES:
        return explicit

    split = str(item.get("split", "")).lower()
    for hint, category in _SPLIT_CATEGORY_HINTS.items():
        if hint in split:
            return category
    return "general"


def _timestamp(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _empty(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "provider": "superhuman",
        "updated_at": 0,
        "attention": "none",
        "unread": 0,
        "important": 0,
        "critical": 0,
        "categories": {
            category: {
                "label": CATEGORY_LABELS[category],
                "unread": 0,
                "important": 0,
                "critical": 0,
            }
            for category in CATEGORIES
        },
        "threads": [],
    }


def snapshot(path: Path | None = None) -> dict[str, Any]:
    source = path or DEFAULT_MAIL_SNAPSHOT
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty("offline")
    except (OSError, json.JSONDecodeError):
        return _empty("error")

    if not isinstance(raw, dict):
        return _empty("error")

    result = _empty("ready")
    result["provider"] = _clip(raw.get("provider", "superhuman"), 32) or "superhuman"
    result["updated_at"] = _timestamp(raw.get("updated_at"))

    rows = raw.get("threads", [])
    if not isinstance(rows, list):
        rows = []

    normalized: list[dict[str, Any]] = []
    for source_item in rows[:100]:
        if not isinstance(source_item, dict):
            continue
        category = _category(source_item)
        unread = bool(source_item.get("unread"))
        critical = bool(source_item.get("critical"))
        important = bool(source_item.get("important")) or critical

        item = {
            "id": _clip(source_item.get("id"), 120),
            "category": category,
            "subject": _clip(source_item.get("subject"), 160),
            "sender": _clip(source_item.get("sender"), 120),
            "unread": unread,
            "important": important,
            "critical": critical,
            "received_at": _timestamp(source_item.get("received_at")),
        }
        normalized.append(item)

        if unread:
            result["unread"] += 1
            result["categories"][category]["unread"] += 1
        if important:
            result["important"] += 1
            result["categories"][category]["important"] += 1
        if critical:
            result["critical"] += 1
            result["categories"][category]["critical"] += 1

    if result["critical"]:
        result["attention"] = "critical"
    elif result["important"]:
        result["attention"] = "important"
    elif result["unread"]:
        result["attention"] = "unread"

    normalized.sort(
        key=lambda item: (
            item["critical"],
            item["important"],
            item["unread"],
            item["received_at"],
        ),
        reverse=True,
    )
    result["threads"] = [
        item for item in normalized if item["unread"] or item["important"]
    ][:12]
    return result
