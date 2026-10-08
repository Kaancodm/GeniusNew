#!/usr/bin/env python3
"""Fast PostToolUse checks after Claude edits a Python file."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_INPUT = 1_048_576
WATCHDOG_SECONDS = 45


class PostCheckRefused(RuntimeError):
    """A post-edit check lacks trustworthy input or cannot complete."""


def edited_path(payload: dict) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise PostCheckRefused("Invalid tool input")
    value = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(value, str) or not value or "\0" in value:
        raise PostCheckRefused("Invalid edited path")
    return value

def main() -> int:
    data = sys.stdin.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise PostCheckRefused("Oversized hook input")
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise PostCheckRefused("Invalid hook payload")

    raw = edited_path(payload)
    if not raw or not raw.endswith(".py"):
        return 0

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise PostCheckRefused("Invalid working directory")
    configured_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if configured_root is not None and Path(configured_root).resolve() != ROOT:
        raise PostCheckRefused("Hook does not belong to configured project")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(cwd) / path
    path = path.resolve(strict=True)
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise PostCheckRefused("Edited file is outside the active project")
    checks = [
        [sys.executable, "-m", "py_compile", str(path)],
        ["git", "diff", "--check"],
    ]
    for command in checks:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                                timeout=15)
        if result.returncode:
            raise PostCheckRefused("Post-edit check failed")

    print("post-python-check: edited file compiles; tracked diff whitespace check PASS")
    return 0

def timeout_handler(signum: int, frame: object) -> None:
    raise TimeoutError("Post-edit check timed out")


def run() -> int:
    try:
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(WATCHDOG_SECONDS)
        return main()
    except Exception:
        print("post-python-check failed; verification blocked.", file=sys.stderr)
        return 2
    finally:
        try:
            signal.alarm(0)
        except AttributeError:
            pass


if __name__ == "__main__":
    raise SystemExit(run())
