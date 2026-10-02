---
name: verify-contracts
description: Run GeniusNew's canonical tests, demo, refusal guard, and diff check.
disable-model-invocation: true
---

# Verify GeniusNew contracts

Run the repository's canonical verification workflow without modifying files.

1. Confirm repository root, branch, full HEAD SHA, and working-tree status.
2. Determine changed Python modules from tracked and untracked working-tree changes.
3. Run, in this order:
   - `python3 -W error::ResourceWarning -m unittest discover -s tests`
   - `./scripts/demo.sh`
   - `python3 scripts/refusals.py <affected geniusnew modules>`
   - `git diff --check`
4. Do not skip, disable, weaken, or rewrite tests to make the result green.
5. If PostgreSQL required by the tests is unavailable, do not report DB coverage as PASS.
6. Only report a check as PASS when that exact check actually ran successfully.
7. Report checks that could not run as UNKNOWN and failed checks as FAIL.
8. If no guarded GeniusNew module changed, report the refusal-guard step as NOT_APPLICABLE, not PASS.
9. New refusal paths must remain protected by `scripts/refusals.py`.
10. Do not change `main`, create commits, add dependencies, or modify repository files.

Finish with: HEAD SHA, changed files, affected guarded modules, each command executed,
its actual result, remaining UNKNOWN items, and any review or approval still required.
