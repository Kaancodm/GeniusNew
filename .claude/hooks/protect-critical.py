#!/usr/bin/env python3
"""Approval guard for tool calls; this is not a shell or filesystem sandbox."""
from __future__ import annotations

import fnmatch
import json
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_FILE = Path(__file__).with_name("critical-paths.txt")
READ_ONLY_TOOLS = {"Read", "Grep", "Glob"}
PATH_WRITE_TOOLS = {"Write", "Edit", "NotebookEdit"}
WATCHDOG_SECONDS = 5
MAX_INPUT = 1_048_576


def emit(kind: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": kind,
            "permissionDecisionReason": reason,
        }
    }))


def load_rules() -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    for raw in POLICY_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        mode, pattern = line.split(":", 1)
        if mode not in {"ask", "deny"} or not pattern or pattern != pattern.strip():
            raise ValueError("Invalid critical-path policy")
        rules.append((mode, pattern))
    if not rules:
        raise ValueError("Empty critical-path policy")
    return rules


def candidate_paths(tool_input: dict) -> list[str]:
    values: list[str] = []
    for key in ("file_path", "path", "notebook_path"):
        if key in tool_input:
            values.append(tool_input[key])
    if "paths" in tool_input:
        if not isinstance(tool_input["paths"], list):
            raise ValueError("Invalid paths")
        values.extend(tool_input["paths"])
    if any(not isinstance(value, str) or not value or "\0" in value for value in values):
        raise ValueError("Invalid path")
    return values


def matches(path: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(Path(path).name, pattern):
        return True
    # A leading **/ also matches zero directories (fnmatch alone does not).
    return pattern.startswith("**/") and matches(path, pattern[3:])


def command_mentions(command: str, pattern: str) -> bool:
    # Keep the existing deny heuristic as an additional refusal, never as
    # evidence that another shell command is safe to execute without approval.
    literal = pattern.replace("**/", "").replace("**", "").replace("*", "").strip("/")
    return bool(literal) and literal.lower() in command.lower()


def path_decisions(raw: str, cwd: Path, rules: list[tuple[str, str]]) -> set[str]:
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    decisions: set[str] = set()
    # Check both the named path and the symlink target. Resolving alone would
    # forget that a protected filename can itself be a symlink.
    for candidate in (Path(os.path.abspath(path)), path.resolve(strict=False)):
        try:
            rel = candidate.relative_to(ROOT).as_posix()
        except ValueError:
            decisions.add("ask")
            continue
        if rel in {".", ".claude", ".git"} or rel.startswith((".claude/", ".git/")):
            decisions.add("deny")
        for mode, pattern in rules:
            if matches(rel, pattern):
                decisions.add(mode)
    return decisions


def main() -> int:
    data = sys.stdin.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ValueError("Oversized hook input")
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError("Invalid hook payload")
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str) or not tool_name or not isinstance(tool_input, dict):
        raise ValueError("Invalid tool input")
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("Invalid working directory")
    configured_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if configured_root is not None and Path(configured_root).resolve() != ROOT:
        raise ValueError("Hook does not belong to configured project")
    rules = load_rules()
    if tool_name in READ_ONLY_TOOLS:
        return 0

    paths = candidate_paths(tool_input)
    if tool_name in PATH_WRITE_TOOLS and not paths:
        raise ValueError("Missing write path")
    decisions: set[str] = set()
    for raw in paths:
        decisions.update(path_decisions(raw, Path(cwd), rules))
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Missing shell command")
        if any(command_mentions(command, pattern) for mode, pattern in rules if mode == "deny"):
            decisions.add("deny")
        if command_mentions(command, ".claude") or command_mentions(command, ".git/"):
            decisions.add("deny")
    if tool_name not in PATH_WRITE_TOOLS:
        # Shell indirection, git flags, interpreters and MCP tools cannot be
        # classified as read-only from command text or tool names alone.
        decisions.add("ask")
    if "deny" in decisions:
        emit("deny", "Protected path; tool call denied.")
    elif "ask" in decisions:
        emit("ask", "Critical path or unclassified execution requires approval.")
    return 0


def timeout_handler(signum: int, frame: object) -> None:
    raise TimeoutError("Critical-path guard timed out")


def run() -> int:
    try:
        # Unsupported watchdog platforms also refuse, before any tool executes.
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(WATCHDOG_SECONDS)
        result = main()
        signal.alarm(0)
        return result
    except Exception:
        # Claude treats an ordinary exit 1 as nonblocking; exit 2 is required.
        print("Critical-path guard failed; tool call blocked.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
