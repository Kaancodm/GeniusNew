"""Gate B1: synchronous connections and an exact, transactional schema history.

Serving never installs or repairs a schema. Only the explicit migration command
uses the migration owner's credentials; runtime gets SELECT on the history.
The ledger tables remain unused by the runtime until gates B2 and B3.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import time

import psycopg
from psycopg.conninfo import conninfo_to_dict

from .contracts import ContractError

_MIGRATIONS = ((1, "0001_core_foundation.sql"),)
_MIGRATION_DIR = Path(__file__).with_name("migrations")
# Serialize competing migration processes, including the first installation.
_MIGRATION_LOCK = 0x47454E4955534231


def _fail(message: str) -> None:
    raise ContractError(message)


def migration_files() -> tuple[tuple[int, bytes, str], ...]:
    try:
        files = tuple((version, (_MIGRATION_DIR / name).read_bytes())
                      for version, name in _MIGRATIONS)
    except OSError:
        raise ContractError("migration files cannot be read") from None
    return tuple((version, raw, sha256(raw).hexdigest()) for version, raw in files)


@contextmanager
def _connect(dsn: str):
    try:
        parameters = conninfo_to_dict(dsn)
        if not all(parameters.get(key) for key in ("host", "dbname", "user")):
            _fail("database DSN must explicitly name host, dbname and user")
        # Bound startup and lock waits, regardless of libpq/user defaults. Fixed
        # qualification prevents a caller-controlled search_path shadowing tables.
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5,
                             options="-c search_path=pg_catalog,public "
                                     "-c statement_timeout=10000 -c lock_timeout=5000") as connection:
            yield connection
    except psycopg.Error:
        # libpq errors can contain the DSN, usernames and server-supplied text.
        raise ContractError("database connection or operation failed") from None


def _history(connection):
    return connection.execute(
        "SELECT version, checksum, applied_at FROM public.schema_migrations ORDER BY version"
    ).fetchall()


def _check_history(rows, expected, *, complete: bool) -> None:
    versions = [row[0] for row in rows]
    known = [item[0] for item in expected]
    if versions != known[:len(versions)]:
        _fail("unknown or out-of-order database migration")
    if complete and versions != known:
        _fail("required database migration is missing")
    for (version, checksum, applied_at), (_, _, digest) in zip(rows, expected):
        if checksum != digest:
            _fail("database migration checksum mismatch")
        if type(applied_at) is not int or applied_at < 0:
            _fail("database migration timestamp is invalid")


def _check_tables(connection) -> None:
    # A forged history must not hide a missing foundation table or column.
    connection.execute("SELECT job_id, subject, handoff_sha256, state, created_at, "
                       "reserved_at, updated_at, expires_at FROM public.job_ledger LIMIT 0")
    connection.execute("SELECT handoff_sha256, job_id, handoff_wire, result_sha256, "
                       "result_wire, accepted_at FROM public.acceptance_ledger LIMIT 0")


@contextmanager
def open_database(dsn: str):
    """Validate the installed foundation before any listener or worker exists."""
    expected = migration_files()
    with _connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            _check_history(_history(connection), expected, complete=True)
            _check_tables(connection)
        yield connection


def migrate(dsn: str) -> None:
    """Apply only a known missing suffix; never rewrite history or repair damage."""
    expected = migration_files()
    with _connect(dsn) as connection:
        # B1 has one migration. Its DDL, grants and history commit together.
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
            exists = connection.execute(
                "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
            ).fetchone()[0]
            rows = []
            if exists:
                rows = _history(connection)
            if not exists:
                occupied = connection.execute(
                    "SELECT EXISTS (SELECT FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid = c.relnamespace WHERE n.nspname = 'public')"
                ).fetchone()[0]
                if occupied:
                    _fail("migration history missing from a non-empty database")
            _check_history(rows, expected, complete=False)
            for version, raw, digest in expected[len(rows):]:
                connection.execute(raw.decode("utf-8"))
                connection.execute(
                    "INSERT INTO public.schema_migrations (version, checksum, applied_at) "
                    "VALUES (%s, %s, %s)", (version, digest, int(time.time())))
            _check_history(_history(connection), expected, complete=True)
            _check_tables(connection)
