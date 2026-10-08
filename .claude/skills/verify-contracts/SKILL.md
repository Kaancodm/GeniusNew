---
name: verify-contracts
description: Run GeniusNew's canonical tests, demo, refusal guard, and diff check.
disable-model-invocation: true
---

# Verify GeniusNew contracts

Run the repository's canonical verification workflow without modifying files.

1. Confirm repository root, branch, full HEAD SHA, and working-tree status.
2. Determine the complete change set: committed changes from the merge-base of
   `HEAD` and `origin/main` to `HEAD`, staged and unstaged changes against `HEAD`,
   and untracked files. Record the base SHA. A clean working tree does not mean
   the branch changed no guarded modules. If the base cannot be established,
   report scope UNKNOWN and run the complete guard rather than assume no changes.
   Intersect the combined paths with `scripts.refusals.GUARDED`, including tools
   and shell scripts, not only files under `geniusnew/`.
3. Run, in this order:
   - `python3 -W error::ResourceWarning -m unittest discover -s tests`
   - `./scripts/demo.sh`
   - `python3 scripts/refusals.py <all affected guarded paths>`
   - `git diff --check "$BASE_SHA" HEAD` using the recorded merge-base SHA;
     if that base is UNKNOWN, this check is UNKNOWN, never PASS.
   - `git diff --cached --check` and `git diff --check` for staged and unstaged changes.
   - List untracked paths with `git ls-files --others --exclude-standard -z`.
     For each readable regular file run `git diff --no-index --check -- /dev/null <path>`
     with the path passed as one argument. A normal added-file diff can exit 1;
     count it as clean only when both stdout and stderr are empty and the exit
     code is 0 or 1. Whitespace diagnostics or other failures are FAIL.
     Inspect symlinks and unreadable/special paths separately; unresolved cases
     remain UNKNOWN. Do not claim this is covered by the tracked diff checks.
4. Do not skip, disable, weaken, or rewrite tests to make the result green.
5. If PostgreSQL required by the tests is unavailable, do not report DB coverage as PASS.
6. Only report a check as PASS when that exact check actually ran successfully.
7. Report checks that could not run as UNKNOWN and failed checks as FAIL.
8. Only if the complete committed and uncommitted change set is known and contains
   no guarded path, report the refusal-guard step as NOT_APPLICABLE, not PASS.
9. New refusal paths must remain protected by `scripts/refusals.py`.
10. Do not change `main`, create commits, add dependencies, or modify repository files.

Finish with: HEAD SHA, changed files, affected guarded modules, each command executed,
its actual result, remaining UNKNOWN items, and any review or approval still required.
