"""PostgreSQL schema history and durable job, acceptance and approval state.

Serving never installs or repairs a schema. Only the explicit migration command
uses the migration owner's credentials; runtime gets SELECT on the history.
Pending wires retain their exact signed bytes. Approval consumption, pointer
advance, pending removal and job reservation share one database transaction.
The acceptance trigger completes the bound committed job atomically.

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

from .approvals import (ApprovalGrant, ApprovalReceipt, ApprovalScope, ApprovalStore,
                        _Record, _record_hash, _scope_matches)
from .contracts import (ContractError, HandoffVerifier, Policy, _MAX_WIRE_BYTES,
                        _wire_object, canonical, decode_wire, validate, validate_pending)
from .orchestrator import JobLedger, Reservation
from .results import WorkerVerifier, accept as accept_result
from .verifier import AcceptanceLedger
from .wiring import PendingJobs, _Waiting, _MAX_PENDING

_SCOPE_KEYS = frozenset({"handoff_sha256", "handoff_expires_at", "job_id", "user_id",
                         "worker_agent_id", "risk_tier", "policy_version", "action"})
_MIGRATIONS = ((1, "0001_core_foundation.sql"), (2, "0002_pending_jobs.sql"),
               (3, "0003_approval_store.sql"), (4, "0004_audit_chain.sql"))
_MIGRATION_DIR = Path(__file__).with_name("migrations")
# Serialize competing migration processes, including the first installation.
_MIGRATION_LOCK = 0x47454E4955534231

# The audit contract's upper bound for a timestamp, as `orchestrator.py` and
# `audit.py` use it; a test pins the three together.
_MAX_TIME = 4102444800
_MAX_JOB_ID_BYTES = 128
_MAX_TRACE_BYTES = 64

_CORE_TABLES = ("schema_migrations", "job_ledger", "acceptance_ledger", "pending_jobs",
                "approval_records", "approval_tokens", "audit_chain", "audit_heads")
# What the runtime role holds on each Core table, and nothing else
# (docs/DATABASE.md §10). An owner or a superuser holds every one of them.
_RUNTIME_PRIVILEGES = {
    "schema_migrations": frozenset({"SELECT"}),
    "job_ledger": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "acceptance_ledger": frozenset({"SELECT", "INSERT"}),
    "pending_jobs": frozenset({"SELECT", "INSERT", "DELETE"}),
    "approval_records": frozenset({"SELECT", "INSERT"}),
    "approval_tokens": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "audit_chain": frozenset({"SELECT", "INSERT"}),
    "audit_heads": frozenset({"SELECT", "INSERT"}),
}
_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
                     "REFERENCES", "TRIGGER")


_CONNECTION_LOCKS = WeakKeyDictionary()
_CONNECTION_LOCKS_LOCK = threading.Lock()


def connection_lock(connection):
    """Serialize all users of a shared connection, including outer transactions."""
    with _CONNECTION_LOCKS_LOCK:
        return _CONNECTION_LOCKS.setdefault(connection, threading.RLock())


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
    connection.execute("SELECT job_id, subject, wire, trace_id, expires_at "
                       "FROM public.pending_jobs LIMIT 0")
    connection.execute("SELECT " + _APPROVAL_COLUMNS + " FROM public.approval_records LIMIT 0")
    connection.execute("SELECT token_digest, current_record_hash FROM public.approval_tokens LIMIT 0")
    connection.execute("SELECT index, previous_hash, record_hash, event "
                       "FROM public.audit_chain LIMIT 0")
    connection.execute("SELECT count, version, head_hash, signature, created_at "
                       "FROM public.audit_heads LIMIT 0")


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
            _check_approvals(connection)
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

    def dispatch_available(self, job_id: str, *, subject: str, handoff_sha256: str | None) -> bool:
        row = self._execute("SELECT state,subject,handoff_sha256 FROM public.job_ledger "
                            "WHERE job_id=%s", (job_id,), fetch=True)
        return row is None or row == ("PENDING_APPROVAL", subject, handoff_sha256)

    def reserve_admitted(self, reservation: Reservation, *,
                         approval_record_hash: str | None,
                         transaction=None) -> bool:
        if approval_record_hash is None:
            return self.reserve(reservation, transaction=transaction)
        row = self._execute(
            "SELECT CASE WHEN octet_length(r.scope) <= %s THEN r.scope ELSE NULL END,"
            "r.changed_at FROM public.job_ledger j "
            "JOIN public.approval_records r ON r.record_hash=%s "
            "JOIN public.approval_tokens t ON t.token_digest=r.token_digest "
            "AND t.current_record_hash=r.record_hash "
            "WHERE j.job_id=%s AND j.subject=%s AND j.handoff_sha256=%s "
            "AND j.expires_at=%s AND j.state='RESERVED' AND j.reserved_at=%s "
            "AND r.state='CONSUMED'", (_MAX_WIRE_BYTES, approval_record_hash,
            reservation.job_id,
            reservation.subject, reservation.handoff_sha256, reservation.expires_at,
            reservation.reserved_at), fetch=True, transaction=transaction)
        if row is None:
            return False
        scope = ApprovalScope(**decode_wire(row[0], keys=_SCOPE_KEYS, noun="approval scope"))
        return (scope.job_id == reservation.job_id
                and scope.handoff_sha256 == reservation.handoff_sha256
                and scope.handoff_expires_at == reservation.expires_at
                and row[1] == reservation.reserved_at)

    def reserve(self, reservation: Reservation, *, transaction=None) -> bool:
        if not isinstance(reservation, Reservation):
            _fail("job ledger reserves only a Reservation")
        rowcount = self._execute(
            "INSERT INTO public.job_ledger (job_id, subject, handoff_sha256, state, "
            "created_at, reserved_at, updated_at, expires_at) "
            "VALUES (%s, %s, %s, 'RESERVED', %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (reservation.job_id, reservation.subject, reservation.handoff_sha256,
             reservation.reserved_at, reservation.reserved_at, reservation.reserved_at,
             reservation.expires_at), transaction=transaction)
        return rowcount == 1

    def commit_execution(self, reservation: Reservation, *, now: int,
                         transaction=None) -> None:
        if not isinstance(reservation, Reservation):
            _fail("job ledger commits only a Reservation")
        # Only the row for this handoff, and only from RESERVED: of two callers
        # holding the same reservation, one moves it and the other is refused.
        rowcount = self._execute(
            "UPDATE public.job_ledger SET state = 'EXECUTION_COMMITTED', updated_at = %s "
            "WHERE job_id = %s AND handoff_sha256 = %s AND state = 'RESERVED'",
            (now, reservation.job_id, reservation.handoff_sha256),
            transaction=transaction)
        if rowcount != 1:
            _fail("job reservation could not be committed to execution")

    def _execute(self, query: str, parameters: tuple, *, fetch: bool = False,
                 transaction=None):
        try:
            with self._lock:
                if transaction is None:
                    if self._connection.info.transaction_status not in (
                            psycopg.pq.TransactionStatus.IDLE,
                            psycopg.pq.TransactionStatus.UNKNOWN):
                        _fail("job ledger cannot join an existing transaction")
                elif (transaction is not self._connection
                      or self._connection.info.transaction_status
                      != psycopg.pq.TransactionStatus.INTRANS):
                    _fail("job ledger needs its own active audit transaction")
                cursor = self._connection.execute(query, parameters)
                return cursor.fetchone() if fetch else cursor.rowcount
        except psycopg.Error:
            # The same reason as `_connect`: driver errors carry server text.
            raise ContractError("job ledger is unavailable") from None


class PostgresAcceptanceLedger(AcceptanceLedger):
    """Gate B3: once across restarts/replicas, with the database's state trigger.

    Standalone calls use a short autocommit transaction. B6 callers may pass
    the chain's exact active connection to include the signed result event;
    neither path holds a transaction while a worker executes.
    """

    def __init__(self, connection) -> None:
        if not isinstance(connection, psycopg.Connection):
            _fail("acceptance ledger needs a psycopg connection")
        if not connection.autocommit:
            _fail("acceptance ledger connection must be in autocommit mode")
        self._connection = connection
        self._lock = connection_lock(connection)

    def reserve(self, *, job_id: str, handoff_wire: bytes, result_wire: bytes,
                now: int, transaction=None) -> bool:
        digest = sha256(handoff_wire).hexdigest()
        try:
            with _store_transaction(self._connection, self._lock,
                                    transaction=transaction,
                                    unavailable="acceptance ledger is unavailable") as connection:
                # Serialize replays on the job before inspecting acceptance;
                # otherwise a racing INSERT meets the BEFORE trigger first.
                connection.execute(
                    "SELECT 1 FROM public.job_ledger "
                    "WHERE job_id = %s AND handoff_sha256 = %s FOR UPDATE",
                    (job_id, digest)).fetchone()
                existing = connection.execute(
                    "SELECT 1 FROM public.acceptance_ledger WHERE handoff_sha256 = %s",
                    (digest,)).fetchone()
                if existing is not None:
                    return False
                # BEFORE INSERT requires EXECUTION_COMMITTED; AFTER INSERT
                # completes exactly this job. Failure rolls both back.
                connection.execute(
                    "INSERT INTO public.acceptance_ledger "
                    "(handoff_sha256, job_id, handoff_wire, result_sha256, result_wire, accepted_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (digest, job_id, handoff_wire, sha256(result_wire).hexdigest(), result_wire, now))
                completed = connection.execute(
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
        # Install the known suffix under the migration lock; DDL, grants and
        # history commit together.
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


def _store_connection(connection):
    if not isinstance(connection, psycopg.Connection) or not connection.autocommit:
        _fail("durable store needs an autocommit psycopg connection")
    return connection, connection_lock(connection)


@contextmanager
def _store_transaction(connection, lock, *, transaction=None,
                       unavailable="durable store is unavailable"):
    try:
        with lock:
            if transaction is not None:
                if (transaction is not connection
                        or connection.info.transaction_status
                        != psycopg.pq.TransactionStatus.INTRANS):
                    _fail("durable store needs its own active audit transaction")
                yield connection
            else:
                if connection.info.transaction_status not in (
                        psycopg.pq.TransactionStatus.IDLE,
                        psycopg.pq.TransactionStatus.UNKNOWN):
                    _fail("durable store cannot join an existing transaction")
                with connection.transaction():
                    yield connection
    except psycopg.Error:
        raise ContractError(unavailable) from None


_APPROVAL_COLUMNS = ("token_digest, scope, issued_at, expires_at, state, "
                     "changed_at, previous_hash, record_hash")
_APPROVAL_READ_COLUMNS = (
    "token_digest, CASE WHEN octet_length(scope) <= %s THEN scope ELSE NULL END, "
    "issued_at, expires_at, state, changed_at, previous_hash, record_hash"
)


def _approval_record(row):
    digest, raw_scope, issued, expires, state, changed, previous, record_hash = row
    if raw_scope is None:
        _fail("stored approval scope exceeds the maximum size")
    scope = ApprovalScope(**decode_wire(raw_scope, keys=_SCOPE_KEYS, noun="approval scope"))
    record = _Record(bytes.fromhex(digest) if _digest(digest) else b"", scope,
                     issued, expires, state, changed, previous, record_hash)
    expected = _record_hash(token_digest=record.token_digest, scope=scope,
                            issued_at=issued, expires_at=expires, state=state,
                            changed_at=changed, previous_hash=previous)
    if (not _digest(digest) or not _digest(record_hash)
            or (previous is not None and not _digest(previous))
            or any(type(value) is not int for value in (issued, expires, changed))
            or not 1 <= issued < expires <= scope.handoff_expires_at <= _MAX_TIME
            or not issued <= changed <= _MAX_TIME
            or (state == "CONSUMED" and changed >= expires)
            or state not in ("GRANTED", "CONSUMED", "REVOKED")
            or expected != record_hash):
        _fail("approval record is invalid")
    return record


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _check_approvals(connection):
    records = {}
    for row in connection.execute(
            "SELECT " + _APPROVAL_READ_COLUMNS + " FROM public.approval_records",
            (_MAX_WIRE_BYTES,)):
        record = _approval_record(row)
        records.setdefault(record.token_digest.hex(), []).append(record)
    pointers = dict(connection.execute(
        "SELECT token_digest, current_record_hash FROM public.approval_tokens").fetchall())
    if set(records) != set(pointers):
        _fail("approval records and pointers do not match")
    consumed_jobs = set()
    for digest, history in records.items():
        roots = [record for record in history if record.previous_hash is None]
        if len(roots) != 1 or roots[0].state != "GRANTED" or roots[0].changed_at != roots[0].issued_at:
            _fail("approval history must have one granted root")
        root = roots[0]
        successors = [record for record in history if record.previous_hash is not None]
        if (len(successors) > 1 or (successors and (
                successors[0].previous_hash != root.record_hash
                or successors[0].state not in ("CONSUMED", "REVOKED")
                or successors[0].scope != root.scope
                or successors[0].issued_at != root.issued_at
                or successors[0].expires_at != root.expires_at
                or successors[0].changed_at < root.changed_at))):
            _fail("approval history is not an immutable one-way chain")
        tip = successors[0] if successors else root
        if pointers[digest] != tip.record_hash:
            _fail("approval pointer must name the only history tip")
        if tip.state == "CONSUMED":
            scope = tip.scope
            if scope.job_id in consumed_jobs:
                _fail("consumed approval does not match its reserved job")
            job = connection.execute(
                "SELECT j.handoff_sha256,j.state,j.reserved_at,j.expires_at,p.job_id "
                "FROM public.job_ledger j LEFT JOIN public.pending_jobs p ON p.job_id=j.job_id "
                "WHERE j.job_id=%s", (scope.job_id,)).fetchone()
            if job is None:
                _fail("consumed approval does not match its reserved job")
            handoff_sha256, state, reserved_at, expires_at, pending_id = job
            if (handoff_sha256 != scope.handoff_sha256
                    or state not in ("RESERVED", "EXECUTION_COMMITTED", "COMPLETED")
                    or reserved_at != tip.changed_at
                    or expires_at != scope.handoff_expires_at
                    or pending_id is not None):
                _fail("consumed approval does not match its reserved job")
            consumed_jobs.add(scope.job_id)


class PostgresPendingJobs(PendingJobs):
    """Gate B4: canonical signed wires, never an in-memory recovery fallback."""

    durable = True

    def __init__(self, connection, *, policy, verifier):
        self._connection, self._lock = _store_connection(connection)
        if not isinstance(policy, Policy) or type(verifier) is not HandoffVerifier:
            _fail("pending store needs trusted policy and a public handoff verifier")
        self._policy, self._verifier = policy, verifier
        with _store_transaction(self._connection, self._lock) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self._check(connection)

    def _waiting(self, row):
        job_id, subject, wire, trace, expires, state, ledger_subject, digest, created, ledger_expires = row
        if wire is None:
            _fail("stored pending wire exceeds the maximum size")
        if (state != "PENDING_APPROVAL" or subject != ledger_subject or expires != ledger_expires
                or sha256(wire).hexdigest() != digest or trace != "trace-" + digest[:16]):
            _fail("pending job does not match its bound ledger")
        # Check the historical issuance, including its signature and exact bytes;
        # current expiry is handled by the atomic refusal path, never by replay.
        handoff = validate_pending(wire, subject=subject, job_id=job_id,
                                   policy=self._policy, verifier=self._verifier, now=created)
        if handoff.issued_at != created or handoff.to_bytes() != wire or handoff.expires_at != expires:
            _fail("pending wire does not bind its issuance")
        return _Waiting(subject, wire, handoff, trace)

    def _check(self, connection):
        missing = connection.execute(
            "SELECT 1 FROM public.job_ledger j FULL JOIN public.pending_jobs p "
            "ON p.job_id=j.job_id WHERE "
            "(j.state='PENDING_APPROVAL' AND p.job_id IS NULL) "
            "OR (p.job_id IS NOT NULL AND j.state IS DISTINCT FROM 'PENDING_APPROVAL') LIMIT 1"
        ).fetchone()
        if missing:
            _fail("pending rows and pending ledger states do not match")
        rows = connection.execute(
            "SELECT p.job_id,p.subject,"
            "CASE WHEN octet_length(p.wire) <= %s THEN p.wire ELSE NULL END,"
            "CASE WHEN octet_length(p.trace_id) <= %s THEN p.trace_id ELSE NULL END,"
            "p.expires_at,j.state,j.subject,"
            "j.handoff_sha256,j.created_at,j.expires_at FROM public.pending_jobs p "
            "JOIN public.job_ledger j ON j.job_id=p.job_id",
            (_MAX_WIRE_BYTES, _MAX_TRACE_BYTES)).fetchall()
        for row in rows:
            self._waiting(row)

    def add(self, job_id, waiting, *, now, transaction=None):
        if not isinstance(waiting, _Waiting):
            _fail("pending store accepts only a waiting signed handoff")
        handoff = validate_pending(waiting.wire, subject=waiting.subject, job_id=job_id,
                                   policy=self._policy, verifier=self._verifier, now=now)
        digest = sha256(waiting.wire).hexdigest()
        if waiting.handoff != handoff or waiting.trace_id != "trace-" + digest[:16]:
            _fail("pending waiting metadata does not match the signed wire")
        with _store_transaction(self._connection, self._lock,
                                transaction=transaction) as connection:
            # Serializes admission against the global queue bound across replicas.
            # Expiry is not swept here: changing an existing job to REFUSED
            # without its audit event would violate the B6 atomicity boundary.
            # Expired rows stay but hold no slot, so abandoned jobs cannot fill
            # the bound for good.
            connection.execute("SELECT pg_advisory_xact_lock(513812742)")
            if connection.execute("SELECT count(*) FROM public.pending_jobs WHERE expires_at > %s",
                                  (now,)).fetchone()[0] >= _MAX_PENDING:
                _fail("too many jobs are waiting for approval")
            inserted = connection.execute(
                "INSERT INTO public.job_ledger (job_id,subject,handoff_sha256,state,"
                "created_at,reserved_at,updated_at,expires_at) "
                "VALUES (%s,%s,%s,'PENDING_APPROVAL',%s,NULL,%s,%s) ON CONFLICT DO NOTHING",
                (job_id, waiting.subject, digest, handoff.issued_at, now, handoff.expires_at)).rowcount
            if inserted != 1:
                _fail("a job with this id is already burned")
            connection.execute("INSERT INTO public.pending_jobs VALUES (%s,%s,%s,%s,%s)",
                               (job_id, waiting.subject, waiting.wire, waiting.trace_id, handoff.expires_at))

    def peek(self, job_id):
        with _store_transaction(self._connection, self._lock) as connection:
            row = connection.execute(
                "SELECT p.job_id,p.subject,"
                "CASE WHEN octet_length(p.wire) <= %s THEN p.wire ELSE NULL END,"
                "CASE WHEN octet_length(p.trace_id) <= %s THEN p.trace_id ELSE NULL END,"
                "p.expires_at,j.state,j.subject,"
                "j.handoff_sha256,j.created_at,j.expires_at FROM public.pending_jobs p "
                "JOIN public.job_ledger j ON j.job_id=p.job_id WHERE p.job_id=%s",
                (_MAX_WIRE_BYTES, _MAX_TRACE_BYTES, job_id)).fetchone()
            if row is None:
                _fail("no job is waiting for approval under this id")
            return self._waiting(row)

    def take(self, job_id, subject):
        # Reading does not remove authorization evidence. The gateway consumes
        # it atomically with the token and reservation once all checks succeeded.
        waiting = self.peek(job_id)
        if waiting.subject != subject:
            _fail("no job of this subject is waiting for approval under this id")
        return waiting

    def restore(self, job_id, waiting):
        # Wrong tokens cannot have changed durable pending state in the first place.
        return None

    def refuse(self, job_id, subject, *, now, transaction=None):
        if type(now) is not int or not 1 <= now <= _MAX_TIME:
            _fail("refusal time is invalid")
        with _store_transaction(self._connection, self._lock,
                                transaction=transaction) as connection:
            row = connection.execute(
                "SELECT state,subject,created_at FROM public.job_ledger "
                "WHERE job_id=%s FOR UPDATE",
                (job_id,)).fetchone()
            if row is None or row[:2] != ("PENDING_APPROVAL", subject):
                _fail("only this subject's pending job may be refused")
            if now < row[2]:
                _fail("refusal time precedes job creation")
            connection.execute("DELETE FROM public.pending_jobs WHERE job_id=%s", (job_id,))
            connection.execute("UPDATE public.job_ledger SET state='REFUSED',updated_at=%s "
                               "WHERE job_id=%s", (now, job_id))



class PostgresApprovalStore(ApprovalStore):
    """Append-only grant records; consume also resolves its exact pending job."""

    def __init__(self, connection, *, token_source=None):
        super().__init__(token_source=token_source)
        self._connection, self._lock = _store_connection(connection)
        with _store_transaction(self._connection, self._lock) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            _check_approvals(connection)

    def _insert(self, connection, record):
        connection.execute(
            "INSERT INTO public.approval_records (" + _APPROVAL_COLUMNS + ") "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (record.token_digest.hex(), canonical(record.scope.to_dict()), record.issued_at,
             record.expires_at, record.state, record.changed_at, record.previous_hash, record.record_hash))

    def grant(self, scope, *, now, ttl_seconds, transaction=None):
        token, record = self._new_grant(scope, now=now, ttl_seconds=ttl_seconds)
        _approval_record((record.token_digest.hex(), canonical(scope.to_dict()), record.issued_at,
                          record.expires_at, record.state, record.changed_at, None, record.record_hash))
        with _store_transaction(self._connection, self._lock,
                                transaction=transaction) as connection:
            job = connection.execute(
                "SELECT j.state,j.subject,j.handoff_sha256,j.expires_at,"
                "p.subject,CASE WHEN octet_length(p.wire) <= %s THEN p.wire ELSE NULL END,"
                "p.expires_at "
                "FROM public.job_ledger j LEFT JOIN public.pending_jobs p ON p.job_id=j.job_id "
                "WHERE j.job_id=%s FOR UPDATE OF j",
                (_MAX_WIRE_BYTES, scope.job_id)).fetchone()
            if (job is None or job[0] != "PENDING_APPROVAL"
                    or job[1] != job[4] or job[2] != scope.handoff_sha256
                    or job[3] != scope.handoff_expires_at or job[6] != job[3]
                    or job[5] is None or sha256(job[5]).hexdigest() != job[2]):
                _fail("approval grant needs its exact pending job")
            handoff = _wire_object(job[5])
            if (handoff["job_id"] != scope.job_id or handoff["user_id"] != scope.user_id
                    or handoff["worker_agent_id"] != scope.worker_agent_id
                    or handoff["tier"] != scope.risk_tier
                    or handoff["policy_version"] != scope.policy_version):
                _fail("approval grant needs its exact pending job")
            if connection.execute("SELECT 1 FROM public.approval_tokens WHERE token_digest=%s",
                                  (record.token_digest.hex(),)).fetchone():
                _fail("approval token collision")
            # The job row lock serializes approvers across service instances.
            # A lost response may be replaced only after its raw token expires.
            if connection.execute(
                    "SELECT 1 FROM public.approval_tokens t "
                    "JOIN public.approval_records r ON r.token_digest=t.token_digest "
                    "AND r.record_hash=t.current_record_hash "
                    "WHERE r.scope=%s AND r.state='GRANTED' AND r.expires_at>%s LIMIT 1",
                    (canonical(scope.to_dict()), now)).fetchone():
                _fail("pending job already has an active approval grant")
            self._insert(connection, record)
            connection.execute("INSERT INTO public.approval_tokens VALUES (%s,%s)",
                               (record.token_digest.hex(), record.record_hash))
        return ApprovalGrant(token, scope, record.issued_at, record.expires_at, record.record_hash)

    def consume(self, token, scope, *, now, subject, transaction=None):
        return self._change(token, scope, now=now, state="CONSUMED", subject=subject,
                            transaction=transaction)

    def revoke(self, token, scope, *, now, transaction=None):
        return self._change(token, scope, now=now, state="REVOKED",
                            transaction=transaction)

    def _change(self, token, scope, *, now, state, subject=None, transaction=None):
        if type(token) is not bytes or len(token) < 32 or not isinstance(scope, ApprovalScope):
            _fail("approval token or scope is invalid")
        if type(now) is not int or not 1 <= now <= _MAX_TIME:
            _fail("approval time is invalid")
        digest = sha256(token).hexdigest()
        with _store_transaction(self._connection, self._lock,
                                transaction=transaction) as connection:
            if state == "CONSUMED":
                job = connection.execute(
                    "SELECT j.state,j.subject,j.handoff_sha256,j.expires_at,"
                    "CASE WHEN octet_length(p.wire) <= %s THEN p.wire ELSE NULL END "
                    "FROM public.job_ledger j LEFT JOIN public.pending_jobs p ON p.job_id=j.job_id "
                    "WHERE j.job_id=%s FOR UPDATE OF j",
                    (_MAX_WIRE_BYTES, scope.job_id)).fetchone()
            pointer = connection.execute(
                "SELECT current_record_hash FROM public.approval_tokens "
                "WHERE token_digest=%s FOR UPDATE", (digest,)).fetchone()
            if pointer is None:
                _fail("approval token is unknown")
            row = connection.execute(
                "SELECT " + _APPROVAL_READ_COLUMNS + " FROM public.approval_records "
                "WHERE token_digest=%s AND record_hash=%s",
                (_MAX_WIRE_BYTES, digest, pointer[0])).fetchone()
            record = _approval_record(row)
            if record.state != "GRANTED":
                _fail("approval is not granted")
            if not _scope_matches(record.scope, scope):
                _fail("approval scope does not match")
            if now < record.issued_at or (state == "CONSUMED" and now >= record.expires_at):
                _fail("approval is not currently valid")
            if state == "CONSUMED" and (
                    job is None or job[:4] != ("PENDING_APPROVAL", subject,
                                              scope.handoff_sha256, scope.handoff_expires_at)
                    or job[4] is None or sha256(job[4]).hexdigest() != scope.handoff_sha256):
                _fail("approval does not bind this subject's pending job")
            record_hash = _record_hash(token_digest=record.token_digest, scope=record.scope,
                                       issued_at=record.issued_at, expires_at=record.expires_at,
                                       state=state, changed_at=now, previous_hash=record.record_hash)
            updated = _Record(record.token_digest, record.scope, record.issued_at, record.expires_at,
                              state, now, record.record_hash, record_hash)
            self._insert(connection, updated)
            connection.execute("UPDATE public.approval_tokens SET current_record_hash=%s "
                               "WHERE token_digest=%s", (record_hash, digest))
            if state == "CONSUMED":
                connection.execute("DELETE FROM public.pending_jobs WHERE job_id=%s", (scope.job_id,))
                connection.execute("UPDATE public.job_ledger SET state='RESERVED',"
                                   "reserved_at=%s,updated_at=%s WHERE job_id=%s",
                                   (now, now, scope.job_id))
        return ApprovalReceipt(updated.scope, state, now, record_hash)
