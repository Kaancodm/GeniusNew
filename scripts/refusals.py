#!/usr/bin/env python3
"""Disable each refusal in turn and require a test to notice.

A test that passes is not the same as a test that tests something. Three times
in three days a test in this repository passed for a reason other than the one
it claimed: an assertion satisfied by a path other than the one under test.
Each time it was found by hand, after the fact, and only because someone went
looking. `docs/ROADMAP-V01.md` says claims belong in checks rather than in
sentences, and "the tests cover the refusals" was still a sentence.

This makes it a check. Every refusal in the guarded modules has the same shape:

    if <the thing that is wrong>:
        _fail("...")

So the tool finds each one, rewrites its condition to `False` — the refusal is
now unreachable, exactly as if it had been deleted — runs the suite, and
requires the suite to fail. A refusal nobody notices the loss of is a refusal
with no test behind it, whatever the coverage report says.

This is deliberately not general-purpose mutation testing. Flipping arbitrary
operators produces mutants that change nothing observable, and the survivors are
then argued about rather than fixed. Here every mutant has one meaning — this
check no longer happens — so a survivor is never a false positive. It is either
a missing test or a check that was never needed.

Usage:
    python3 scripts/refusals.py              # all guarded modules
    python3 scripts/refusals.py geniusnew/contracts.py
    python3 scripts/refusals.py --list       # show them without running

Exit status is non-zero if any refusal survives, so CI fails on it.
"""

from __future__ import annotations

import argparse
import ast
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Modules whose refusals must each be covered. A module belongs here once it
# makes a security decision; leaving one out is a decision to be made openly
# rather than by forgetting to add it.
GUARDED = ("geniusnew/contracts.py", "geniusnew/approvals.py")

_REFUSAL_CALLS = {"_fail"}
_REFUSAL_RAISES = {"ContractError"}


@dataclass(frozen=True)
class Refusal:
    """One `if <condition>: _fail(...)` and where its condition sits."""

    path: str
    line: int
    condition: str
    message: str

    def label(self) -> str:
        return f"{self.path}:{self.line}"


def _is_refusal_body(node: ast.If) -> str | None:
    """Return the refusal's message if this `if` body is a refusal, else None."""
    for statement in node.body:
        call = None
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
        elif isinstance(statement, ast.Raise) and isinstance(statement.exc, ast.Call):
            call = statement.exc
        if call is None:
            continue
        name = call.func.id if isinstance(call.func, ast.Name) else None
        if name in _REFUSAL_CALLS or name in _REFUSAL_RAISES:
            if call.args and isinstance(call.args[0], ast.Constant):
                return str(call.args[0].value)
            return "(no message)"
    return None


def find_refusals(path: Path) -> list[Refusal]:
    source = path.read_text()
    tree = ast.parse(source)
    lines = source.splitlines()
    found: list[Refusal] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        message = _is_refusal_body(node)
        if message is None:
            continue
        # `elif` chains and refusals inside `else` are not reached by rewriting
        # this test alone, so they are skipped rather than reported as covered.
        if node.orelse:
            continue
        found.append(Refusal(
            path=str(path.relative_to(ROOT)),
            line=node.test.lineno,
            condition=ast.get_source_segment(source, node.test) or lines[node.test.lineno - 1].strip(),
            message=message,
        ))
    return sorted(found, key=lambda refusal: refusal.line)


def _disable(source: str, refusal: Refusal) -> str:
    """Rewrite the refusal's condition to `False`, leaving everything else."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and node.test.lineno == refusal.line and not node.orelse:
            if _is_refusal_body(node) is None:
                continue
            lines = source.splitlines(keepends=True)
            start, end = node.test.lineno - 1, node.test.end_lineno - 1
            if start == end:
                line = lines[start]
                lines[start] = (line[:node.test.col_offset] + "False"
                                + line[node.test.end_col_offset:])
            else:
                first, last = lines[start], lines[end]
                lines[start] = first[:node.test.col_offset] + "False" + last[node.test.end_col_offset:]
                del lines[start + 1:end + 1]
            return "".join(lines)
    raise SystemExit(f"could not locate the refusal at {refusal.label()}")


def _suite_passes(cwd: Path) -> bool:
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=cwd, capture_output=True, text=True,
    )
    return completed.returncode == 0


def check(paths: list[str]) -> int:
    refusals = [(Path(ROOT / path), refusal)
                for path in paths for refusal in find_refusals(ROOT / path)]
    if not refusals:
        print("no refusals found — check the module list", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory) / "repo"
        shutil.copytree(ROOT, workspace, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", ".venv"))

        if not _suite_passes(workspace):
            print("the suite does not pass unmutated — fix that first", file=sys.stderr)
            return 1

        survivors = []
        for index, (path, refusal) in enumerate(refusals, start=1):
            target = workspace / refusal.path
            original = path.read_text()
            target.write_text(_disable(original, refusal))
            survived = _suite_passes(workspace)
            target.write_text(original)

            mark = "SURVIVED" if survived else "caught  "
            print(f"[{index:>3}/{len(refusals)}] {mark}  {refusal.label():<32} {refusal.message}")
            if survived:
                survivors.append(refusal)

    print()
    if survivors:
        print(f"{len(survivors)} of {len(refusals)} refusals can be deleted "
              f"without any test failing:")
        for refusal in survivors:
            print(f"  {refusal.label():<32} if {refusal.condition}:")
            print(f"  {'':<32}     {refusal.message}")
        print("\nEach one is either a missing test or a check that is not needed.")
        return 1

    print(f"all {len(refusals)} refusals are covered: deleting any one fails the suite")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", default=None,
                        help="modules to check (default: the guarded set)")
    parser.add_argument("--list", action="store_true",
                        help="list the refusals without running the suite")
    arguments = parser.parse_args()
    paths = arguments.paths or list(GUARDED)

    if arguments.list:
        for path in paths:
            for refusal in find_refusals(ROOT / path):
                print(f"{refusal.label():<32} if {refusal.condition}:")
        return 0
    return check(paths)


if __name__ == "__main__":
    raise SystemExit(main())
