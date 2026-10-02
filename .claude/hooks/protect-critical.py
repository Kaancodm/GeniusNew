#!/usr/bin/env python3
"""Deterministic PreToolUse guard driven by critical-paths.txt."""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path

POLICY_FILE = Path(__file__).with_name("critical-paths.txt")

def emit(kind: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": kind,
            "permissionDecisionReason": reason,
        }
    }))
    raise SystemExit(0)

def load_rules() -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    for raw in POLICY_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        mode, pattern = line.split(":", 1)
        if mode not in {"ask", "deny"} or not pattern:
            emit("deny", "Invalid critical-path policy; fail closed.")
        rules.append((mode, pattern))
    return rules

def normalize(raw: str, root: Path) -> str | None:
    try:
        path = Path(raw)
        if not path.is_absolute():
            path = root / path
        return path.resolve(strict=False).relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None

def candidate_paths(tool_input: dict) -> list[str]:
    values: list[str] = []
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str):
            values.append(value)
    extra = tool_input.get("paths")
    if isinstance(extra, list):
        values.extend(value for value in extra if isinstance(value, str))
    return values

def matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(Path(path).name, pattern)

def command_mentions(command: str, pattern: str) -> bool:
    literal = pattern.replace("**/", "").replace("**", "").replace("*", "").strip("/")
    return bool(literal) and literal.lower() in command.lower()

def main() -> int:
    try:
        payload = json.load(sys.stdin)
        rules = load_rules()
    except (json.JSONDecodeError, OSError, ValueError):
        emit("deny", "Critical-path guard could not load valid input or policy; fail closed.")

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    tool_input = payload.get("tool_input") or {}

    for raw in candidate_paths(tool_input):
        rel = normalize(raw, root)
        if rel is None:
            continue
        for mode, pattern in rules:
            if matches(rel, pattern):
                emit(mode, f"Critical-path policy requires {mode.upper()} for: {rel}")

    if payload.get("tool_name") == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            for mode, pattern in rules:
                if command_mentions(command, pattern):
                    emit(mode, "Shell command references a path governed by critical-path policy.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
