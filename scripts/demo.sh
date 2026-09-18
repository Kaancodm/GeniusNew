#!/bin/sh
# One command, as docs/ROADMAP-V01.md step 18 asks for.
#
# Runs a job through every layer, prints the audit chain, verifies it against
# the anchored head, and then attacks it. Exits non-zero if any claim fails, so
# it is a check and not a printout.
#
#     ./scripts/demo.sh
#
# Needs nothing but Python 3.11+ — no dependencies, no network, no state on disk.
set -eu
exec python3 "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/demo.py" "$@"
