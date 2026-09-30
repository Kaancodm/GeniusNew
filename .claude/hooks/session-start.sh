#!/bin/bash
# SessionStart hook for Claude Code on the web: the hash-pinned venv and a
# disposable local PostgreSQL cluster, so tests, demo and refusal guard run
# without manual setup (docs/POSTGRES-B1.md). Local machines are left alone.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# Hash-pinned install, as in AGENTS.md; a no-op once the cached venv matches.
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet --disable-pip-version-check \
  --require-hashes -r requirements.txt

# Throwaway test cluster only: trust auth, loopback, fixed port. It holds no
# real data and no credentials; never point it at anything that does.
PG_PORT=55432
PG_DIR=/var/tmp/geniusnew-test-pg
PG_BIN=$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -n 1 || true)
if [ -z "$PG_BIN" ] || ! id postgres >/dev/null 2>&1; then
  echo "session-start: no PostgreSQL server found; database tests will fail" >&2
  exit 0
fi
if [ ! -f "$PG_DIR/data/PG_VERSION" ]; then
  rm -rf "$PG_DIR"
  install -d -o postgres -m 700 "$PG_DIR"
  su postgres -c "$PG_BIN/initdb -D $PG_DIR/data -A trust -U postgres" >/dev/null
fi
if ! su postgres -c "$PG_BIN/pg_ctl -D $PG_DIR/data status" >/dev/null 2>&1; then
  su postgres -c "$PG_BIN/pg_ctl -D $PG_DIR/data -l $PG_DIR/log -w \
    -o '-p $PG_PORT -k $PG_DIR -c listen_addresses=127.0.0.1' start" >/dev/null
fi
ADMIN_DSN="host=127.0.0.1 port=$PG_PORT dbname=geniusnew_test_admin user=postgres"
if ! "$PG_BIN/psql" "host=127.0.0.1 port=$PG_PORT dbname=postgres user=postgres" -tAc \
    "SELECT 1 FROM pg_database WHERE datname = 'geniusnew_test_admin'" | grep -q 1; then
  "$PG_BIN/psql" "host=127.0.0.1 port=$PG_PORT dbname=postgres user=postgres" -qc \
    "CREATE DATABASE geniusnew_test_admin"
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export VIRTUAL_ENV=\"$PWD/.venv\""
    echo "export PATH=\"$PWD/.venv/bin:\$PATH\""
    echo "export GENIUSNEW_TEST_ADMIN_DSN=\"$ADMIN_DSN\""
  } >> "$CLAUDE_ENV_FILE"
fi
