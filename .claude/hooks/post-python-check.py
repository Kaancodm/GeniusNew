#!/usr/bin/env python3
"""Fast PostToolUse checks after Claude edits a Python file."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

def edited_path(payload: dict) -> str | None:
    tool_input = payload.get("tool_input") or {}
    value = tool_input.get("file_path") or tool_input.get("path")
    return value if isinstance(value, str) else None

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        print("post-python-check: invalid hook input", file=sys.stderr)
        return 2

    raw = edited_path(payload)
    if not raw or not raw.endswith(".py"):
        return 0

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    checks = [
        ["python3", "-m", "compileall", "-q", "geniusnew", "scripts", "tools"],
        ["git", "diff", "--check"],
    ]
    for command in checks:
        result = subprocess.run(command, cwd=root, text=True, capture_output=True)
        if result.returncode:
            message = (result.stderr or result.stdout or "check failed").strip()
            print(f"post-python-check: {' '.join(command)}: {message}", file=sys.stderr)
            return 2

    print("post-python-check: compileall PASS; git diff --check PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
