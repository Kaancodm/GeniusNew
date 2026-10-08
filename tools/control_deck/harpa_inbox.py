from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


MAX_ITEMS = 50
_EVENT_ID = re.compile(r"[A-Za-z0-9._:-]{1,80}\Z")
_KINDS = frozenset({"monitor", "report", "research"})
_STORE_LOCK = threading.Lock()


class HarpaPayloadError(ValueError):
    """Reject malformed or overpowered HARPA input."""


def _text(payload: dict[str, object], name: str, limit: int) -> str:
    value = payload.get(name)
    if not isinstance(value, str):
        raise HarpaPayloadError(f"{name} must be text")
    value = value.strip()
    if not value or len(value) > limit or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise HarpaPayloadError(f"invalid {name}")
    return value


def normalize(payload: object, *, received_at: int | None = None) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {
        "event_id", "kind", "title", "summary", "source_url"
    }:
        raise HarpaPayloadError("unexpected payload shape")
    event_id = _text(payload, "event_id", 80)
    if not _EVENT_ID.fullmatch(event_id):
        raise HarpaPayloadError("invalid event_id")
    kind = _text(payload, "kind", 16)
    if kind not in _KINDS:
        raise HarpaPayloadError("invalid kind")
    source_url = _text(payload, "source_url", 2048)
    parsed = urlparse(source_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise HarpaPayloadError("invalid source_url")
    return {
        "event_id": event_id,
        "kind": kind,
        "title": _text(payload, "title", 160),
        "summary": _text(payload, "summary", 8000),
        "source_url": source_url,
        "received_at": int(time.time()) if received_at is None else received_at,
    }


def _items(path: Path, *, strict: bool = False) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        return []
    items: list[dict[str, Any]] = []
    for line in lines[-MAX_ITEMS:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            if strict:
                raise OSError("invalid HARPA inbox")
            continue
        if not isinstance(item, dict):
            if strict:
                raise OSError("invalid HARPA inbox")
            continue
        items.append(item)
    return items


def store(payload: object, path: Path) -> dict[str, object]:
    item = normalize(payload)
    with _STORE_LOCK:
        existing = _items(path, strict=True)
        if any(value.get("event_id") == item["event_id"] for value in existing):
            return {"accepted": True, "duplicate": True, "event_id": item["event_id"]}
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".harpa-", dir=path.parent)
        try:
            try:
                os.fchmod(descriptor, 0o600)
                values = existing[-(MAX_ITEMS - 1):] + [item]
                content = "".join(
                    json.dumps(value, separators=(",", ":")) + "\n"
                    for value in values
                )
                remaining = memoryview(content.encode())
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("HARPA inbox write failed")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    return {"accepted": True, "duplicate": False, "event_id": item["event_id"]}


def snapshot(path: Path | None) -> dict[str, object]:
    if path is None:
        return {"status": "disabled", "items": []}
    items = _items(path)
    items.reverse()
    return {"status": "ready", "items": items[:20]}
