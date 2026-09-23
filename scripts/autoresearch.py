#!/usr/bin/env python3
"""A keep-or-discard loop for one file, after karpathy/autoresearch.

The pattern (MIT, github.com/karpathy/autoresearch): an agent may change exactly
one file, a fixed measurement decides, an improvement is committed and anything
else is reverted — then again. There the measurement is validation loss after
five minutes of training. Here it is how long the test suite takes, because the
refusal guard runs that suite once per refusal and CI time is what it costs.

A number an agent is paid to lower is a number it will learn to game, so the
number counts only when every gate holds, and the gates are checked by this
script, not by the agent:

1. Only the target file changed. A change anywhere else is reverted unseen.
2. The suite passes with at least as many tests as the best state so far —
   deleting a slow test is not a speed-up.
3. If the target is guarded, every refusal in it is still caught, and there are
   at least as many.
4. The demo still passes with at least as many attacks refused.
5. It is faster by more than the noise threshold.

Nothing here pushes. Kept commits stay on a local `autoresearch/<tag>` branch
until a person picks what is worth a pull request. `docs/AUTORESEARCH.md` is
what the agent is told.

Usage:
    python3 scripts/autoresearch.py start --target tests/test_demo.py --tag demo-speed
    python3 scripts/autoresearch.py step --note "memoize the second demo run"
    python3 scripts/autoresearch.py status
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = "autoresearch"
NOISE = 0.03
RUNS = 3
_COLUMNS = ("commit", "seconds", "tests", "refusals", "attacks", "status", "note")


class GateFailed(Exception):
    """The measurement could not be taken because something is red."""


@dataclass(frozen=True)
class Measurement:
    seconds: float
    tests: int
    refusals: int
    attacks: int


Measure = Callable[[Path, str], Measurement]


def _run(root: Path, *command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=root, capture_output=True, text=True)


def _git(root: Path, *args: str) -> str:
    completed = _run(root, "git", *args)
    if completed.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def changed_files(root: Path) -> tuple[set[str], set[str]]:
    """Modified tracked files and untracked files, both relative to the root."""
    tracked = set(_git(root, "diff", "--name-only", "HEAD").split())
    untracked = set(_git(root, "ls-files", "--others", "--exclude-standard").split())
    return tracked, untracked


def measure_repository(root: Path, target: str) -> Measurement:
    """The real measurement: suite time over RUNS runs, and every gate's count."""
    durations, tests = [], None
    for _ in range(RUNS):
        started = time.monotonic()
        completed = _run(root, sys.executable, "-m", "unittest", "discover", "-s", "tests")
        durations.append(time.monotonic() - started)
        ran = re.search(r"Ran (\d+) tests?", completed.stderr)
        if completed.returncode != 0 or ran is None:
            raise GateFailed("the test suite failed")
        tests = int(ran.group(1))

    sys.path.insert(0, str(root))
    from scripts.refusals import GUARDED

    refusals = -1
    if target in GUARDED:
        completed = _run(root, sys.executable, "scripts/refusals.py", target)
        caught = re.search(r"all (\d+) refusal mutations are caught", completed.stdout)
        if completed.returncode != 0 or caught is None:
            raise GateFailed("a refusal in the target is no longer caught")
        refusals = int(caught.group(1))

    completed = _run(root, "./scripts/demo.sh")
    passed = re.search(r"^PASS .* (\d+)/\1 attacks refused", completed.stdout, re.MULTILINE)
    if completed.returncode != 0 or passed is None:
        raise GateFailed("the demo did not pass")
    return Measurement(round(statistics.median(durations), 3), tests, refusals,
                       int(passed.group(1)))


class Loop:
    def __init__(self, root: Path, measure: Measure = measure_repository) -> None:
        self.root = root
        self.measure = measure
        self.state_path = root / STATE_DIR / "state.json"

    # --- state ---------------------------------------------------------------

    def _load(self) -> dict:
        if not self.state_path.exists():
            raise SystemExit("no loop started here; run `start` first")
        return json.loads(self.state_path.read_text())

    def _save(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True))

    def _log(self, state: dict, status: str, measured: Measurement | None, note: str) -> None:
        values = asdict(measured) if measured else {"seconds": "", "tests": "",
                                                    "refusals": "", "attacks": ""}
        row = {"commit": _git(self.root, "rev-parse", "--short", "HEAD").strip(),
               **values, "status": status, "note": " ".join(note.split())}
        with (self.root / STATE_DIR / f"{state['tag']}.tsv").open("a") as log:
            log.write("\t".join(str(row[column]) for column in _COLUMNS) + "\n")

    # --- the loop ------------------------------------------------------------

    def start(self, target: str, tag: str) -> Measurement:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", tag):
            raise SystemExit("tag must be lowercase letters, digits and dashes")
        if _run(self.root, "git", "ls-files", "--error-unmatch", target).returncode != 0:
            raise SystemExit(f"{target} is not a tracked file")
        tracked, untracked = changed_files(self.root)
        if tracked or untracked:
            raise SystemExit("the working tree must be clean before a loop starts")
        _git(self.root, "checkout", "-q", "-b", f"autoresearch/{tag}")
        try:
            baseline = self.measure(self.root, target)
        except GateFailed as failure:
            raise SystemExit(f"the baseline is not green: {failure}") from None
        (self.root / STATE_DIR).mkdir(exist_ok=True)
        state = {"tag": tag, "target": target, "best": asdict(baseline)}
        self._save(state)
        with (self.root / STATE_DIR / f"{tag}.tsv").open("w") as log:
            log.write("\t".join(_COLUMNS) + "\n")
        self._log(state, "baseline", baseline, "baseline")
        return baseline

    def step(self, note: str) -> str:
        state = self._load()
        target = state["target"]
        best = Measurement(**state["best"])
        tracked, untracked = changed_files(self.root)

        foreign = tracked - {target}
        if foreign or untracked:
            # Everything tracked goes back, the target included: a change that
            # needed another file is not a change to the target. Untracked files
            # are named, never deleted — they might not be the agent's.
            for path in tracked:
                _git(self.root, "checkout", "--", path)
            self._log(state, "discard", None,
                      f"outside the target: {', '.join(sorted(foreign | untracked))}; {note}")
            return "discard"
        if target not in tracked:
            self._log(state, "discard", None, f"no change; {note}")
            return "discard"

        try:
            measured = self.measure(self.root, target)
        except GateFailed as failure:
            _git(self.root, "checkout", "--", target)
            self._log(state, "crash", None, f"{failure}; {note}")
            return "crash"

        weaker = (measured.tests < best.tests or measured.refusals < best.refusals
                  or measured.attacks < best.attacks)
        if weaker or measured.seconds >= best.seconds * (1 - NOISE):
            _git(self.root, "checkout", "--", target)
            reason = "fewer checks" if weaker else "not faster beyond noise"
            self._log(state, "discard", measured, f"{reason}; {note}")
            return "discard"

        _git(self.root, "add", "--", target)
        _git(self.root, "commit", "-q", "-m",
             f"autoresearch: {' '.join(note.split())}\n\n"
             f"{measured.seconds}s (was {best.seconds}s), {measured.tests} tests, "
             f"{measured.refusals} refusals, {measured.attacks} attacks.")
        state["best"] = asdict(measured)
        self._save(state)
        self._log(state, "keep", measured, note)
        return "keep"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--target", required=True)
    start.add_argument("--tag", required=True)
    step = commands.add_parser("step")
    step.add_argument("--note", required=True)
    commands.add_parser("status")
    arguments = parser.parse_args()

    loop = Loop(ROOT)
    if arguments.command == "start":
        print(f"baseline: {loop.start(arguments.target, arguments.tag)}")
    elif arguments.command == "step":
        print(loop.step(arguments.note))
    else:
        state = loop._load()
        print((ROOT / STATE_DIR / f"{state['tag']}.tsv").read_text(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
