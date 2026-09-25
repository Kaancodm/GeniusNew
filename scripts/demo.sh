#!/bin/sh
# One command, as docs/ROADMAP-V01.md step 18 asks for.
#
# Runs a job through every layer, prints the audit chain, verifies it against
# the anchored head, and then attacks it. Exits non-zero if any claim fails, so
# it is a check and not a printout.
#
#     ./scripts/demo.sh
#
# Needs Python 3.11+ and the one pinned dependency in requirements.txt
# (python3 -m pip install --require-hashes -r requirements.txt) — no network,
# no state on disk.
set -eu
exec python3 "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/demo.py" "$@"
