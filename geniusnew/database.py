"""Gates B1–B3: an exact schema history and persistent job/acceptance ledgers.

Serving never installs or repairs a schema. Only the explicit migration command
uses the migration owner's credentials; runtime gets SELECT on the history.
The runtime burns job ids and accepts signed results in the database; the
acceptance trigger completes the bound committed job atomically.

The start check refuses a runtime role that could undo what the database is
there to enforce. Triggers that forbid deleting or rewinding a job are worth
nothing to a superuser or to the tables' owner, so connecting as either is a
configuration error, not a convenience.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import threading
import time
from weakref import WeakKeyDictionary

import psycopg
from psycopg.conninfo import conninfo_to_dict

from .contracts import ContractError, HandoffVerifier, Policy, validate, validate_pending
from .orchestrator import JobLedger, Reservation
from .results import WorkerVerifier, accept as accept_result
from .verifier import AcceptanceLedger

_MIGRATIONS = ((1, "0001_core_foundation.sql"),)
_MIGRATION_DIR = Path(__file__).with_name("migrations")
# Serialize competing migration processes, including the first installation.
_MIGRATION_LOCK = 0x47454E4955534231

# The audit contract's upper bound for a timestamp, as `orchestrator.py` and
# `audit.py` use it; a test pins the three together.
_MAX_TIME = 4102444800
_MAX_JOB_ID_BYTES = 128

_CONNECTION_LOCKS = WeakKeyDictionary()
_CONNECTION_LOCKS_LOCK = threading.Lock()


def connection_lock(connection):
    """Share transaction exclusion among every store using this connection."""
    with _CONNECTION_LOCKS_LOCK:
        return _CONNECTION_LOCKS.setdefault(connection, threading.RLock())

_CORE_TABLES = ("schema_migrations", "job_ledger", "acceptance_ledger")
# What the runtime role holds on each Core table, and nothing else
# (docs/DATABASE.md §10). An owner or a superuser holds every one of them.
_RUNTIME_PRIVILEGES = {
    "schema_migrations": frozenset({"SELECT"}),
    "job_ledger": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "acceptance_ledger": frozenset({"SELECT", "INSERT"}),
}
_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
                     "REFERENCES", "TRIGGER")


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


def _check_runtime_role(connection) -> None:
    """Refuse a runtime role that could undo what the database enforces."""
    privileged = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles "
        "WHERE (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication "
        "OR rolname IN ('pg_read_server_files', 'pg_write_server_files', "
        "'pg_execute_server_program')) "
        "AND pg_catalog.pg_has_role(current_user, oid, 'MEMBER'))").fetchone()[0]
    if privileged:
        _fail("database runtime role must not be privileged")
    # These grants bypass the triggers without any privileged role flag.
    # Include memberships that need SET ROLE rather than inheriting rights.
    if connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles "
            "WHERE pg_catalog.pg_has_role(current_user, oid, 'MEMBER') "
            "AND pg_catalog.has_parameter_privilege("
            "oid, 'session_replication_role', 'SET, ALTER SYSTEM'))").fetchone()[0]:
        _fail("database runtime role must not disable triggers")
    # Membership counts: a member of the owning role can alter the table and
    # disable its triggers even after the owner revoked its own privileges.
    owned = connection.execute(
        "SELECT count(*) FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY(%s) "
        "AND pg_catalog.pg_has_role(current_user, c.relowner, 'MEMBER')",
        (list(_CORE_TABLES),)).fetchone()[0]
    if owned:
        _fail("database runtime role must not own the Core tables")
    table_privileges = _TABLE_PRIVILEGES
    if connection.info.server_version >= 170000:
        table_privileges += ("MAINTAIN",)
    for table, expected in _RUNTIME_PRIVILEGES.items():
        held = frozenset(
            privilege for privilege in table_privileges
            if connection.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles "
                "WHERE pg_catalog.pg_has_role(current_user, oid, 'MEMBER') "
                "AND pg_catalog.has_table_privilege(oid, %s, %s))",
                ("public." + table, privilege)).fetchone()[0])
        if held != expected:
            _fail("database runtime role must hold exactly the documented table privileges")
    if connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles "
            "WHERE pg_catalog.pg_has_role(current_user, oid, 'MEMBER') "
            "AND pg_catalog.has_schema_privilege(oid, 'public', 'CREATE'))"
    ).fetchone()[0]:
        _fail("database runtime role must not create objects in the Core schema")


def _check_job_ledger(connection) -> None:
    """Re-check every stored row formally (docs/DATABASE.md §3 rule 5, §8 item 3).

    A row this code could not have written means someone else wrote it, and a
    start on top of it would trust whatever they meant by it.
    """
    invalid = connection.execute(
        "SELECT count(*) FROM public.job_ledger WHERE NOT ("
        "job_id <> '' AND octet_length(job_id) <= %s AND subject <> '' "
        "AND handoff_sha256 ~ '^[0-9a-f]{64}$' "
        "AND created_at BETWEEN 1 AND %s "
        "AND updated_at BETWEEN created_at AND %s "
        "AND expires_at > created_at AND expires_at <= %s "
        "AND (reserved_at IS NULL OR reserved_at BETWEEN created_at AND updated_at))",
        (_MAX_JOB_ID_BYTES, _MAX_TIME, _MAX_TIME, _MAX_TIME)).fetchone()[0]
    if invalid:
        _fail("job ledger holds a formally invalid row")


def _check_acceptance_bindings(connection) -> None:
    """The durable state must agree in both directions, in one snapshot."""
    mismatch = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM public.acceptance_ledger a "
        "FULL JOIN public.job_ledger j ON j.job_id = a.job_id "
        "AND j.handoff_sha256 = a.handoff_sha256 "
        "WHERE (a.job_id IS NOT NULL AND j.state IS DISTINCT FROM 'COMPLETED') "
        "OR (j.state = 'COMPLETED' AND a.job_id IS NULL))").fetchone()[0]
    if mismatch:
        _fail("acceptance ledger and completed jobs do not match")


@contextmanager
def open_database(dsn: str):
    """Validate the installed foundation before any listener or worker exists."""
    expected = migration_files()
    with _connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            _check_history(_history(connection), expected, complete=True)
            _check_tables(connection)
            _check_runtime_role(connection)
            _check_job_ledger(connection)
            _check_acceptance_bindings(connection)
        yield connection


class PostgresJobLedger(JobLedger):
    """Gate B2: burned job ids in `public.job_ledger`, shared by every instance.

    Each call is one statement on an autocommit connection, so it is its own
    transaction and nothing here holds one open while a worker runs
    (docs/DATABASE.md §7). The primary key decides a race between instances.
    A database error is a refusal: there is no in-memory fallback, because a
    fallback is a ledger the next instance cannot see.
    """

    def __init__(self, connection) -> None:
        if not isinstance(connection, psycopg.Connection):
            _fail("job ledger needs a psycopg connection")
        # Inside a caller's open transaction a reservation would stay invisible
        # to every other instance until that caller commits.
        if not connection.autocommit:
            _fail("job ledger connection must be in autocommit mode")
        self._connection = connection
        # The connection is shared by the service's request threads.
        self._lock = connection_lock(connection)

    def is_burned(self, job_id: str) -> bool:
        return self._execute("SELECT 1 FROM public.job_ledger WHERE job_id = %s",
                             (job_id,), fetch=True) is not None

    def reserve(self, reservation: Reservation) -> bool:
        if not isinstance(reservation, Reservation):
            _fail("job ledger reserves only a Reservation")
        rowcount = self._execute(
            "INSERT INTO public.job_ledger (job_id, subject, handoff_sha256, state, "
            "created_at, reserved_at, updated_at, expires_at) "
            "VALUES (%s, %s, %s, 'RESERVED', %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (reservation.job_id, reservation.subject, reservation.handoff_sha256,
             reservation.reserved_at, reservation.reserved_at, reservation.reserved_at,
             reservation.expires_at))
        return rowcount == 1

    def commit_execution(self, reservation: Reservation, *, now: int) -> None:
        if not isinstance(reservation, Reservation):
            _fail("job ledger commits only a Reservation")
        # Only the row for this handoff, and only from RESERVED: of two callers
        # holding the same reservation, one moves it and the other is refused.
        rowcount = self._execute(
            "UPDATE public.job_ledger SET state = 'EXECUTION_COMMITTED', updated_at = %s "
            "WHERE job_id = %s AND handoff_sha256 = %s AND state = 'RESERVED'",
            (now, reservation.job_id, reservation.handoff_sha256))
        if rowcount != 1:
            _fail("job reservation could not be committed to execution")

    def _execute(self, query: str, parameters: tuple, *, fetch: bool = False):
        try:
            with self._lock:
                cursor = self._connection.execute(query, parameters)
                return cursor.fetchone() if fetch else cursor.rowcount
        except psycopg.Error:
            # The same reason as `_connect`: driver errors carry server text.
            raise ContractError("job ledger is unavailable") from None


class PostgresAcceptanceLedger(AcceptanceLedger):
    """Gate B3: once across restarts/replicas, with the database's state trigger.

    Use a dedicated autocommit connection: its short transaction must not
    include another ledger's mutations or any worker execution.
    """

    def __init__(self, connection) -> None:
        if not isinstance(connection, psycopg.Connection):
            _fail("acceptance ledger needs a psycopg connection")
        if not connection.autocommit:
            _fail("acceptance ledger connection must be in autocommit mode")
        self._connection = connection
        self._lock = connection_lock(connection)

    def reserve(self, *, job_id: str, handoff_wire: bytes, result_wire: bytes,
                now: int) -> bool:
        digest = sha256(handoff_wire).hexdigest()
        try:
            with self._lock:
                # UNKNOWN belongs to a closed connection; let its first SQL
                # operation fail through the sanitized driver-error path.
                if self._connection.info.transaction_status not in (
                        psycopg.pq.TransactionStatus.IDLE, psycopg.pq.TransactionStatus.UNKNOWN):
                    _fail("acceptance ledger cannot join an existing transaction")
                with self._connection.transaction():
                    # Serialize replays on the job before inspecting acceptance;
                    # otherwise a racing INSERT meets the BEFORE trigger first.
                    self._connection.execute(
                        "SELECT 1 FROM public.job_ledger "
                        "WHERE job_id = %s AND handoff_sha256 = %s FOR UPDATE",
                        (job_id, digest)).fetchone()
                    existing = self._connection.execute(
                        "SELECT 1 FROM public.acceptance_ledger WHERE handoff_sha256 = %s",
                        (digest,)).fetchone()
                    if existing is not None:
                        return False
                    # BEFORE INSERT requires EXECUTION_COMMITTED; AFTER INSERT
                    # completes exactly this job. Failure rolls both back.
                    self._connection.execute(
                        "INSERT INTO public.acceptance_ledger "
                        "(handoff_sha256, job_id, handoff_wire, result_sha256, result_wire, accepted_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (digest, job_id, handoff_wire, sha256(result_wire).hexdigest(), result_wire, now))
                    completed = self._connection.execute(
                        "SELECT state, updated_at FROM public.job_ledger "
                        "WHERE job_id = %s AND handoff_sha256 = %s", (job_id, digest)).fetchone()
                    if completed != ("COMPLETED", now):
                        _fail("acceptance did not complete the bound job")
            return True
        except psycopg.Error:
            raise ContractError("acceptance ledger is unavailable") from None

    def check(self, *, policy: Policy, handoff_verifier: HandoffVerifier,
              worker_verifier: WorkerVerifier) -> None:
        # A private authority here would collapse the independent verifier.
        if not isinstance(policy, Policy):
            _fail("acceptance start check needs a Policy")
        if type(handoff_verifier) is not HandoffVerifier:
            _fail("acceptance start check needs a HandoffVerifier")
        if type(worker_verifier) is not WorkerVerifier:
            _fail("acceptance start check needs a WorkerVerifier")
        try:
            with self._lock, self._connection.transaction():
                self._connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                _check_job_ledger(self._connection)
                _check_acceptance_bindings(self._connection)
                rows = self._connection.execute(
                    "SELECT a.job_id, a.handoff_sha256, a.handoff_wire, a.result_sha256, "
                    "a.result_wire, a.accepted_at, j.subject, j.expires_at, j.reserved_at, j.updated_at "
                    "FROM public.acceptance_ledger a JOIN public.job_ledger j "
                    "ON j.job_id = a.job_id AND j.handoff_sha256 = a.handoff_sha256")
                for job_id, digest, wire, result_digest, result_wire, accepted_at, subject, expires, reserved, updated in rows:
                    if (type(accepted_at) is not int or not 1 <= accepted_at <= _MAX_TIME
                            or not reserved <= accepted_at < expires or updated != accepted_at):
                        _fail("acceptance ledger holds an invalid acceptance time")
                    if (sha256(wire).hexdigest() != digest
                            or sha256(result_wire).hexdigest() != result_digest):
                        _fail("acceptance ledger wire digest mismatch")
                    # Reconstruct from the stored bytes at the original time,
                    # not today's clock: historical expiration is not damage.
                    pending = policy.grant_for(subject).requires_approval
                    revalidate = validate_pending if pending else validate
                    handoff = revalidate(wire, subject=subject, job_id=job_id,
                                         policy=policy, verifier=handoff_verifier, now=accepted_at)
                    if handoff.expires_at != expires:
                        _fail("acceptance handoff does not match the job expiration")
                    accept_result(result_wire, handoff=handoff,
                                  verifier=worker_verifier, now=accepted_at)
        except psycopg.Error:
            raise ContractError("acceptance ledger is unavailable") from None


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
