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

_NAME_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_NAME = re.compile(_NAME_PATTERN)
# A conservative PEP 440 subset: unsupported exact pins remain UNKNOWN.
_VERSION = re.compile(
    r"(?:[0-9]+!)?[0-9]+(?:\.[0-9]+)*"
    r"(?:[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)[-_.]?[0-9]+)?"
    r"(?:-[0-9]+|[-_.]?(?:post|rev|r)[-_.]?[0-9]+)?"
    r"(?:[-_.]?dev[-_.]?[0-9]+)?"
    r"(?:\+[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*)?",
    re.IGNORECASE | re.ASCII,
)
_PIN = re.compile(rf"^\s*({_NAME_PATTERN})==([^\s;\\]+)")
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


def _valid_pin(name: str, version: str) -> bool:
    return (type(name) is str and type(version) is str
            and _NAME.fullmatch(name) is not None
            and _VERSION.fullmatch(version) is not None)


def _normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


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
    hash_continuation = False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        # Only an explicit continuation may attach hashes to the current pin.
        # Other continuation forms (including intervening comments) are unsupported.
        if hash_continuation:
            if not _HASH_LINE.fullmatch(line):
                raise ValueError(f"unsupported hash continuation on line {number}")
            hash_continuation = stripped.endswith("\\")
            continue
        if not stripped or stripped.startswith("#"):
            continue
        match = _PIN.match(line)
        if match:
            if not _valid_pin(match.group(1), match.group(2)):
                raise ValueError(f"unsupported requirement syntax on line {number}")
            raw_suffix = line[match.end():]
            suffix = raw_suffix.strip()
            if suffix.startswith(";"):
                if not _valid_marker(suffix[1:]):
                    raise ValueError(f"unsupported requirement marker on line {number}")
            elif (suffix not in ("", "\\")
                  and not (raw_suffix[:1].isspace() and suffix.startswith("#"))):
                raise ValueError(f"unsupported requirement syntax on line {number}")
            # A backslash in a comment must not grant a hash continuation.
            hash_continuation = (suffix == "\\" or
                                 (suffix.startswith(";") and suffix.endswith("\\")))
            pins.append((match.group(1), match.group(2)))
        else:
            raise ValueError(f"unsupported requirement syntax on line {number}")
    if hash_continuation:
        raise ValueError("unsupported unterminated hash continuation at end of file")
    return pins


def advisories(record: bytes, name: str, version: str) -> list[tuple[str, str]]:
    """(id, one-line summary) pairs from one PyPI release record."""
    if type(record) is not bytes or len(record) > _MAX_RESPONSE_BYTES:
        raise ValueError("release record exceeds the size this check reads")
    try:
        value = json.loads(record.decode("utf-8"))
    except RecursionError:
        raise ValueError("release record exceeds nesting limit") from None
    if type(value) is not dict:
        raise ValueError("release record is not an object")
    info = value.get("info")
    if (type(info) is not dict or type(info.get("name")) is not str
            or type(info.get("version")) is not str
            or _normalized_name(info["name"]) != _normalized_name(name)
            or info["version"] != version):
        raise ValueError("release record does not match requested pin")
    listed = value.get("vulnerabilities")
    if type(listed) is not list:
        raise ValueError("release record carries no vulnerabilities list")
    found = []
    for entry in listed:
        if type(entry) is not dict:
            raise ValueError("release record contains malformed advisory")
        identifier = entry.get("id")
        summary = entry.get("summary")
        if (type(identifier) is not str or not identifier.strip()
                or (summary is not None and type(summary) is not str)):
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
        identifier = " ".join(identifier.split())
        summary = " ".join((summary or "").split())
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
        if not _valid_pin(name, version):
            return 2, lines + ["invalid pinned package or version: UNKNOWN"]
        try:
            found = advisories(fetch(_PYPI.format(name=name, version=version)),
                               name, version)
        except (OSError, ValueError) as exc:
            return 2, lines + [f"{name}=={version}: UNKNOWN ({type(exc).__name__})"]
        total += len(found)
        for identifier, summary in found:
            # Advisory text must remain visible data, never terminal control sequences.
            identifier = json.dumps(identifier, ensure_ascii=True)[1:-1]
            summary = json.dumps(summary, ensure_ascii=True)[1:-1]
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
