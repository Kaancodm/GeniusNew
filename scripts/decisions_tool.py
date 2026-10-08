"""Read the one-file-per-decision layout in docs/decisions/.

Each decision is its own file, so two pull requests never edit the same line.
`python3 scripts/decisions_tool.py` prints every decision as one document,
newest first. That is the file to hand to a tool that reads a single source
(see docs/COLLABORATION.md, NotebookLM source package). Nothing here writes.
"""
import re
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "docs" / "decisions"
NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})-[a-z0-9-]+\.md$")


def load(directory=DIR):
    """Return every decision as a dict, newest first; refuse a malformed file."""
    entries = []
    for path in sorted(directory.glob("*.md"), reverse=True):
        match = NAME.match(path.name)
        if not match:
            raise ValueError(f"unexpected file name in {directory}: {path.name}")
        text = path.read_text(encoding="utf-8")
        parts = re.fullmatch(
            r"# (?P<title>.+)\n\n- Datum: (?P<date>.+)\n- Quelle: (?P<source>.+)\n\n"
            r"## Entscheidung\n\n(?P<decision>.+)\n\n## Begründung\n\n(?P<reason>.+)\n",
            text, re.S)
        if not parts:
            raise ValueError(f"malformed decision file: {path.name}")
        entries.append(parts.groupdict())
    return entries


def render(entries):
    """Join the decisions into one Markdown document."""
    out = ["# Entscheidungen (alle, neueste zuerst)\n"]
    for e in entries:
        out.append(f"## {e['date']} — {e['title']}\n\n{e['decision']}\n\n"
                   f"Begründung: {e['reason']}\n\nQuelle: {e['source']}\n")
    return "\n".join(out)


if __name__ == "__main__":
    sys.stdout.write(render(load()))
