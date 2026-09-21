#!/usr/bin/env python3
"""Disable each refusal in turn and require a test to notice.

A test that passes is not the same as a test that tests something. Three times
in three days a test in this repository passed for a reason other than the one
it claimed: an assertion satisfied by a path other than the one under test.
Each time it was found by hand, after the fact, and only because someone went
looking. `docs/ROADMAP-V01.md` says claims belong in checks rather than in
sentences, and "the tests cover the refusals" was still a sentence.

The original refusal shape is:

    if <the thing that is wrong>:
        _fail("...")

So the tool finds each one, rewrites its condition to `False` — the refusal is
now unreachable, exactly as if it had been deleted — runs the suite, and
requires the suite to fail. A refusal nobody notices the loss of is a refusal
with no test behind it, whatever the coverage report says.

Gateway refusals also translate a ContractError inside an except block into a
GatewayRejected carrying audit metadata. For each such direct raise, replace
the translation with a bare re-raise: rejection must not quietly lose its
structured evidence while tests still pass because ContractError was raised.

This is deliberately not general-purpose mutation testing. Flipping arbitrary
operators produces mutants that change nothing observable, and the survivors are
then argued about rather than fixed. Here every mutant has one meaning — this
check or gateway audit translation no longer happens. Only these shapes are
enumerated; the count is not a claim about every possible refusal control flow.

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
GUARDED = ("geniusnew/contracts.py", "geniusnew/approvals.py",
           "geniusnew/audit.py", "geniusnew/audit_chain.py",
           "geniusnew/results.py", "geniusnew/keys.py",
           "geniusnew/workers.py", "geniusnew/isolation.py",
           "geniusnew/isolation_child.py", "geniusnew/gateway.py",
           "geniusnew/orchestrator.py", "geniusnew/verifier.py",
           "geniusnew/http_entry.py", "geniusnew/wiring.py")

# Each module's own way of refusing counts. `_deny` is `orchestrator.py`'s
# helper, `Rejected` is `verifier.py`'s exception type, `GatewayRejected`
# is `gateway.py`'s recordable denial type, and `http_entry.py`
# *returns* its refusals — a boundary that answers a stranger cannot raise at
# one. Leaving any of them out would have hidden that module's decisions from
# this check while it sat in the guarded list looking covered.
#
# Three additions in three modules is a pattern: whatever a module refuses
# with belongs here the same day the module joins GUARDED.
_REFUSAL_CALLS = {"_fail", "_deny"}
_REFUSAL_RAISES = {"ContractError", "Rejected", "GatewayRejected"}
_REFUSAL_RETURNS = {"_refusal"}


@dataclass(frozen=True)
class Refusal:
    """One conditional refusal or except-based gateway metadata translation."""

    path: str
    line: int
    condition: str
    message: str
    kind: str = "condition"

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
        elif isinstance(statement, ast.Return) and isinstance(statement.value, ast.Call):
            call = statement.value
        if call is None:
            continue
        name = call.func.id if isinstance(call.func, ast.Name) else None
        if name in _REFUSAL_CALLS or name in _REFUSAL_RAISES or name in _REFUSAL_RETURNS:
            if call.args and isinstance(call.args[0], ast.Constant):
                return str(call.args[0].value)
            return "(no message)"
    return None


def _gateway_wrappers(handler: ast.ExceptHandler) -> list[ast.Raise]:
    """Direct metadata translations only; nested scopes need their own analysis."""
    return [statement for statement in handler.body
            if isinstance(statement, ast.Raise)
            and isinstance(statement.exc, ast.Call)
            and isinstance(statement.exc.func, ast.Name)
            and statement.exc.func.id == "GatewayRejected"]


def find_refusals(path: Path) -> list[Refusal]:
    source = path.read_text()
    tree = ast.parse(source)
    lines = source.splitlines()
    found: list[Refusal] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            for statement in _gateway_wrappers(node):
                code = next((keyword.value.value for keyword in statement.exc.keywords
                             if keyword.arg == "reason_code"
                             and isinstance(keyword.value, ast.Constant)), "(no code)")
                found.append(Refusal(
                    path=str(path.relative_to(ROOT)), line=statement.lineno,
                    condition="except gateway metadata translation",
                    message=f"GatewayRejected {code}", kind="gateway_metadata",
                ))
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


def _replace_node(source: str, node: ast.AST, replacement: str) -> str:
    # AST columns count UTF-8 bytes, not Python string characters.
    lines = source.encode("utf-8").splitlines(keepends=True)
    start, end = node.lineno - 1, node.end_lineno - 1
    lines[start:end + 1] = [lines[start][:node.col_offset]
                            + replacement.encode("utf-8")
                            + lines[end][node.end_col_offset:]]
    return b"".join(lines).decode("utf-8")


def _disable(source: str, refusal: Refusal) -> str:
    """Disable a condition or strip only a gateway refusal's audit metadata."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if refusal.kind == "gateway_metadata" and isinstance(node, ast.ExceptHandler):
            for statement in _gateway_wrappers(node):
                if statement.lineno == refusal.line:
                    return _replace_node(source, statement, "raise")
        if (refusal.kind == "condition" and isinstance(node, ast.If)
                and node.test.lineno == refusal.line and not node.orelse):
            if _is_refusal_body(node) is None:
                continue
            return _replace_node(source, node.test, "False")
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
            # Read from the frozen copy, not the live file. A run takes minutes;
            # reading the original each time meant an edit made while it ran
            # shifted the line numbers under it and the run died with "could
            # not locate the refusal" after ten minutes of work.
            original = target.read_text()
            target.write_text(_disable(original, refusal))
            survived = _suite_passes(workspace)
            target.write_text(original)

            mark = "SURVIVED" if survived else "caught  "
            print(f"[{index:>3}/{len(refusals)}] {mark}  {refusal.label():<32} {refusal.message}")
            if survived:
                survivors.append(refusal)

    print()
    if survivors:
        print(f"{len(survivors)} of {len(refusals)} refusal mutations survive "
              f"without any test failing:")
        for refusal in survivors:
            print(f"  {refusal.label():<32} {refusal.kind}: {refusal.condition}")
            print(f"  {'':<32}     {refusal.message}")
        print("\nEach one is either a missing test or a check that is not needed.")
        return 1

    print(f"all {len(refusals)} refusal mutations are caught: each one fails the suite")
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
                print(f"{refusal.label():<32} {refusal.kind}: {refusal.condition} [{refusal.message}]")
        return 0
    return check(paths)


if __name__ == "__main__":
    raise SystemExit(main())
