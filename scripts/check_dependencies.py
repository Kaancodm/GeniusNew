#!/usr/bin/env python3
"""Ask PyPI whether the exact pinned versions carry known advisories.

Hash-pinning freezes the bytes in `requirements.txt`; nothing in the pin
watches them. The scheduled watcher (`docs/examples/audit-workflow.yml`,
to be moved to `.github/workflows/audit.yml` by the repository owner — the
automation that wrote it holds no `workflows` permission) uses pip-audit.
This script is the same question with only the standard library, so it can
run anywhere today: it reads the pinned `name==version` set, fetches each
release's PyPI JSON record, and prints its published `vulnerabilities`.

Unknown is never reported as clean. A network error, a truncated answer or
an unparsable pin exits 2 (UNKNOWN); advisories exit 1; only an answer for
every pin with no advisories exits 0. Responses are bounded in size and
summaries in length — advisory text is untrusted third-party content that
ends up in a terminal.

Usage:
    python3 scripts/check_dependencies.py [requirements.txt]
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable

_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")
_HASH_LINE = re.compile(r"^\s*--hash=sha256:[0-9a-fA-F]+\s*\\?\s*$")
# Dependency-specifier grammar, restricted to environment fields available here.
# Extras/group fields require a containing layer this standalone audit lacks.
_MARKER_FIELD = (
    r"(?:python_version|python_full_version|os_name|sys_platform|platform_release|"
    r"platform_system|platform_version|platform_machine|"
    r"platform_python_implementation|implementation_name|implementation_version)\b")
_MARKER_CHARS = r"A-Za-z0-9 \t().{}\-_*#:;,/?\[\]!~`@$%^&=+|<>"
_MARKER_STRING = rf'''(?:'[{_MARKER_CHARS}"]*'|"[{_MARKER_CHARS}']*")'''
_MARKER_OP = (r"(?:[ \t]*(?:===|==|!=|<=|>=|<|>|~=)[ \t]*|"
              r"[ \t]+(?:not[ \t]+)?in[ \t]+)")
_MARKER_COMPARISON = re.compile(
    rf"(?:{_MARKER_FIELD}{_MARKER_OP}{_MARKER_STRING}|"
    rf"{_MARKER_STRING}{_MARKER_OP}{_MARKER_FIELD})")
_MARKER_BOOLEAN = re.compile(r"(?:and|or)\b")
_WITHDRAWAL_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt]"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    r"(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_SUMMARY_CHARS = 200
_TIMEOUT_SECONDS = 20
_PYPI = "https://pypi.org/pypi/{name}/{version}/json"

Fetch = Callable[[str], bytes]


def _valid_marker(marker: str) -> bool:
    """Validate the whole marker without evaluating it or skipping any pin.

    Support field/string comparisons, and/or and balanced groups. Constant-only
    and field/field comparisons are unsupported. Comments after markers are
    unsupported too; '#' inside a quoted value is ordinary marker text.
    Iteration avoids a recursion limit for nested untrusted expressions.
    """
    if marker.endswith("\\"):
        marker = marker[:-1]
    position, depth = 0, 0
    expect_expression = True
    while position < len(marker):
        char = marker[position]
        if char in " \t":
            position += 1
            continue
        if expect_expression:
            if char == "(":
                depth += 1
                position += 1
                continue
            match = _MARKER_COMPARISON.match(marker, position)
            if match is None:
                return False
            position = match.end()
            expect_expression = False
        elif char == ")" and depth:
            depth -= 1
            position += 1
        else:
            match = _MARKER_BOOLEAN.match(marker, position)
            if match is None:
                return False
            position = match.end()
            expect_expression = True
    return not expect_expression and depth == 0


def parse_pins(text: str) -> list[tuple[str, str]]:
    """Extract pins; unsupported requirement syntax is UNKNOWN, never clean."""
    pins = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _PIN.match(line)
        if match:
            suffix = line[match.end():].strip()
            if suffix.startswith(";"):
                if not _valid_marker(suffix[1:]):
                    raise ValueError(f"unsupported requirement marker on line {number}")
            elif suffix not in ("", "\\") and not suffix.startswith("#"):
                raise ValueError(f"unsupported requirement syntax on line {number}")
            pins.append((match.group(1), match.group(2)))
        elif pins and _HASH_LINE.fullmatch(line):
            continue
        else:
            raise ValueError(f"unsupported requirement syntax on line {number}")
    return pins


def advisories(record: bytes) -> list[tuple[str, str]]:
    """(id, one-line summary) pairs from one PyPI release record."""
    value = json.loads(record.decode("utf-8"))
    listed = value.get("vulnerabilities") if isinstance(value, dict) else None
    if not isinstance(listed, list):
        raise ValueError("release record carries no vulnerabilities list")
    found = []
    for entry in listed:
        if not isinstance(entry, dict):
            raise ValueError("release record contains malformed advisory")
        withdrawn = entry.get("withdrawn")
        if withdrawn is not None:
            if (type(withdrawn) is not str
                    or _WITHDRAWAL_TIMESTAMP.fullmatch(withdrawn) is None):
                raise ValueError("release record contains malformed withdrawal")
            # PyPI specifies RFC 3339, not merely a nonempty string. The
            # calendar check also keeps impossible dates UNKNOWN, never clean.
            datetime.fromisoformat(withdrawn.upper())
            continue
        identifier = str(entry.get("id") or "UNKNOWN-ID")
        summary = " ".join(str(entry.get("summary") or "").split())
        found.append((identifier, summary[:_MAX_SUMMARY_CHARS]))
    return found


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        data = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(data) > _MAX_RESPONSE_BYTES:
        raise ValueError("release record exceeds the size this check reads")
    return data


def check(pins: list[tuple[str, str]], *, fetch: Fetch = _fetch) -> tuple[int, list[str]]:
    """0 clean, 1 advisories found, 2 unknown — with printable evidence."""
    if not pins:
        return 2, ["no pins found; nothing was checked (UNKNOWN)"]
    lines: list[str] = []
    total = 0
    for name, version in pins:
        try:
            found = advisories(fetch(_PYPI.format(name=name, version=version)))
        except (OSError, ValueError) as exc:
            return 2, lines + [f"{name}=={version}: UNKNOWN ({type(exc).__name__})"]
        total += len(found)
        for identifier, summary in found:
            lines.append(f"{name}=={version}: {identifier} {summary}".rstrip())
        if not found:
            lines.append(f"{name}=={version}: no known advisories")
    if total:
        lines.append(f"{total} known advisor{'y' if total == 1 else 'ies'} "
                     f"across {len(pins)} pinned packages — bump deliberately "
                     "(AGENTS.md rule 7, procedure in requirements.txt).")
        return 1, lines
    lines.append(f"No known vulnerabilities in {len(pins)} pinned packages (PyPI).")
    return 0, lines


def main(argv: list[str] | None = None, *, fetch: Fetch = _fetch) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    path = Path(arguments[0]) if arguments else Path("requirements.txt")
    try:
        pins = parse_pins(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"check_dependencies: UNKNOWN ({type(exc).__name__}): {exc}",
              file=sys.stderr)
        return 2
    code, lines = check(pins, fetch=fetch)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
