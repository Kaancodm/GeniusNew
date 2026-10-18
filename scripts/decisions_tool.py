"""Read the one-file-per-decision layout in docs/decisions/.

Each decision is its own file, so two pull requests never edit the same line.
`python3 scripts/decisions_tool.py` prints every decision as one document,
newest first. That is the file to hand to a tool that reads a single source
(see docs/COLLABORATION.md, NotebookLM source package). Nothing here writes.
"""
import datetime
import re
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "docs" / "decisions"
NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2,})-[a-z0-9-]+\.md$")


def load(directory=DIR):
    """Read direct *.md children of directory, newest first (date, then sequence number).

    Return a list of dictionaries with title, date, source, decision, and reason
    fields. Ordering uses the filename date and the numeric sequence; the date must match the date field.

    Raise ValueError for an empty directory, an unexpected filename, a filename
    date that differs from the record date, or a malformed decision layout.
    File read errors (OSError) and UTF-8 decoding errors (UnicodeDecodeError)
    propagate to the caller.
    """
    entries = []
    named = []
    for path in directory.glob("*.md"):
        match = NAME.match(path.name)
        if not match:
            raise ValueError(f"unexpected file name in {directory}: {path.name}")
        named.append((match.group(1), int(match.group(2)), path, match))
    if not named:
        raise ValueError(f"no decision files in {directory}")
    # Date, then the numeric sequence: decision 100 sorts above decision 99.
    named.sort(key=lambda item: item[:2], reverse=True)
    for _, _, path, match in named:
        text = path.read_text(encoding="utf-8")
        parts = re.fullmatch(
            r"# (?P<title>.+)\n\n- Datum: (?P<date>.+)\n- Quelle: (?P<source>.+)\n\n"
            r"## Entscheidung\n\n(?P<decision>.+)\n\n## Begründung\n\n(?P<reason>.+)\n",
            text, re.S)
        if not parts:
            raise ValueError(f"malformed decision file: {path.name}")
        entry = parts.groupdict()
        if entry["date"] == "—":
            expected = "0000-00-00"
        else:
            day, month, year = entry["date"].split(".")
            expected = f"{year}-{month}-{day}"
            datetime.date(int(year), int(month), int(day))  # a real calendar date
        if match.group(1) != expected:
            raise ValueError(f"file name date does not match record date: {path.name}")
        entries.append(entry)
    return entries


def render(entries):
    """Return one Markdown document from entries in the format returned by load.

    Preserve the supplied order; callers must order entries newest first to
    match the heading. Empty input returns only the heading. Missing dictionary
    fields raise KeyError.
    """
    out = ["# Entscheidungen (alle, neueste zuerst)\n"]
    for e in entries:
        out.append(f"## {e['date']} — {e['title']}\n\n{e['decision']}\n\n"
                   f"Begründung: {e['reason']}\n\nQuelle: {e['source']}\n")
    return "\n".join(out)


if __name__ == "__main__":
    sys.stdout.write(render(load()))
