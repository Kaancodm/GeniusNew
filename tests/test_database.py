"""B1 acceptance tests against PostgreSQL, including DDL rollback and SQL denials."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

import psycopg

from geniusnew import database
from geniusnew.contracts import ContractError
from postgres_support import PostgresDatabase


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.db = PostgresDatabase()
        self.addCleanup(self.db.close)

    def install(self):
        database.migrate(self.db.owner_dsn)

    def execute(self, query, parameters=None):
        with self.db.connect() as connection:
            cursor = connection.execute(query, parameters)
            return cursor.fetchall() if cursor.description else None

    def test_migration_is_atomic_repeatable_and_has_an_exact_byte_checksum(self):
        self.install()
        before = self.execute("SELECT * FROM schema_migrations")
        database.migrate(self.db.owner_dsn)
        self.assertEqual(self.execute("SELECT * FROM schema_migrations"), before)
        raw = (database._MIGRATION_DIR / "0001_core_foundation.sql").read_bytes()
        self.assertEqual(before[0][:2], (1, sha256(raw).hexdigest()))
        self.assertGreater(before[0][2], 0)
        with database.open_database(self.db.runtime_dsn) as connection:
            self.assertFalse(connection.closed)
            self.assertEqual(connection.execute("SELECT current_user").fetchone(),
                             ("genius_core",))
        self.assertTrue(connection.closed)
        tables = self.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        self.assertEqual({r[0] for r in tables},
                         set(database._CORE_TABLES))

    def test_two_migrators_serialize_the_first_installation(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(database.migrate, self.db.owner_dsn) for _ in range(2)]
            for future in futures:
                future.result(timeout=30)
        self.assertEqual(self.execute("SELECT count(*) FROM schema_migrations"), [(len(database.migration_files()),)])

    def test_a_missing_history_table_is_refused_without_automatic_migration(self):
        with self.assertRaisesRegex(ContractError, "database connection or operation failed"):
            with database.open_database(self.db.owner_dsn):
                self.fail("an empty database was accepted")
        self.assertEqual(self.execute("SELECT to_regclass('public.schema_migrations')"), [(None,)])

    def test_missing_migration_wrong_checksum_and_newer_version_are_refused(self):
        self.install()
        for change, reason in (
            ("DELETE FROM schema_migrations", "required database migration is missing"),
            ("UPDATE schema_migrations SET checksum = repeat('0',64)", "checksum mismatch"),
            ("INSERT INTO schema_migrations VALUES (999, repeat('a',64), 1)", "unknown"),
            ("UPDATE schema_migrations SET version = 0 WHERE version = 1", "unknown"),
            ("UPDATE schema_migrations SET applied_at = -1", "timestamp is invalid"),
        ):
            with self.subTest(change=change), self.db.connect() as owner:
                with owner.transaction(force_rollback=True):
                    # Use the real connection for an uncommitted corruption fixture.
                    owner.execute(change)
                    with self.assertRaisesRegex(ContractError, reason):
                        database._check_history(database._history(owner),
                                                database.migration_files(), complete=True)

    def test_migrate_does_not_repair_unknown_or_changed_history(self):
        self.install()
        self.execute("UPDATE schema_migrations SET checksum = repeat('f',64)")
        with self.assertRaisesRegex(ContractError, "checksum mismatch"):
            database.migrate(self.db.owner_dsn)
        self.assertEqual(self.execute("SELECT checksum FROM schema_migrations"), [("f" * 64,)] * len(database.migration_files()))
        self.execute("UPDATE schema_migrations SET version = 999 WHERE version = 1")
        with self.assertRaisesRegex(ContractError, "unknown"):
            database.migrate(self.db.owner_dsn)

    def test_missing_history_in_an_occupied_database_is_not_recreated(self):
        self.install()
        self.execute("DROP TABLE schema_migrations")
        with self.assertRaisesRegex(ContractError, "history missing from a non-empty"):
            self.install()
        self.assertEqual(self.execute("SELECT to_regclass('public.schema_migrations')"), [(None,)])

    def test_each_missing_foundation_table_is_refused(self):
        self.install()
        for table in ("acceptance_ledger", "job_ledger"):
            with self.subTest(table=table):
                self.execute("DROP TABLE " + table + " CASCADE")
                with self.assertRaisesRegex(ContractError, "database connection or operation failed"):
                    with database.open_database(self.db.owner_dsn):
                        self.fail("missing foundation table accepted")

    def test_a_changed_migration_byte_is_refused(self):
        self.install()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for _, name in database._MIGRATIONS:
                (path / name).write_bytes((database._MIGRATION_DIR / name).read_bytes())
            original = database._MIGRATION_DIR / "0001_core_foundation.sql"
            (path / original.name).write_bytes(original.read_bytes() + b"\n")
            with patch.object(database, "_MIGRATION_DIR", path):
                with self.assertRaisesRegex(ContractError, "checksum mismatch"):
                    with database.open_database(self.db.owner_dsn):
                        self.fail("changed migration bytes accepted")

    def test_a_failed_migration_rolls_back_ddl_and_history_together(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for _, name in database._MIGRATIONS:
                (path / name).write_bytes((database._MIGRATION_DIR / name).read_bytes())
            original = database._MIGRATION_DIR / "0001_core_foundation.sql"
            (path / original.name).write_bytes(original.read_bytes() + b"\nSELECT 1/0;\n")
            with patch.object(database, "_MIGRATION_DIR", path):
                with self.assertRaisesRegex(ContractError, "database connection or operation failed"):
                    self.install()
        self.assertEqual(self.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'"), [])
        self.install()

    def test_runtime_cannot_install_the_schema(self):
        with self.assertRaisesRegex(ContractError, "database connection or operation failed"):
            database.migrate(self.db.runtime_dsn)
        self.assertEqual(self.execute("SELECT to_regclass('public.schema_migrations')"), [(None,)])

    def test_runtime_has_only_the_documented_table_privileges(self):
        self.install()
        with self.db.connect(runtime=True) as connection:
            for query in (
                "DELETE FROM job_ledger", "TRUNCATE job_ledger",
                "DELETE FROM acceptance_ledger", "UPDATE acceptance_ledger SET accepted_at=0",
                "INSERT INTO schema_migrations VALUES (2, repeat('0',64), 0)",
                "UPDATE schema_migrations SET applied_at=0", "DELETE FROM schema_migrations",
                "ALTER TABLE job_ledger DISABLE TRIGGER ALL", "DROP TABLE job_ledger",
                "CREATE TABLE public.unwanted (id int)",
            ):
                with self.subTest(query=query), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(query)
            for table in ("schema_migrations", "job_ledger", "acceptance_ledger"):
                owner = connection.execute(
                    "SELECT pg_get_userbyid(relowner) FROM pg_class "
                    "WHERE oid=to_regclass(%s)", ("public." + table,)).fetchone()[0]
                self.assertNotEqual(owner, "genius_core")


class StartCheckTest(unittest.TestCase):
    """Gate B2: the start refuses a runtime that could undo the database's rules."""

    def setUp(self):
        self.db = PostgresDatabase()
        self.addCleanup(self.db.close)
        database.migrate(self.db.owner_dsn)

    def owner(self, *statements):
        with self.db.connect() as connection:
            for statement in statements:
                connection.execute(statement)

    def refused(self, message, dsn=None):
        with self.assertRaisesRegex(ContractError, message):
            with database.open_database(dsn or self.db.runtime_dsn):
                self.fail("the start was not refused")

    def test_the_documented_runtime_role_starts(self):
        with database.open_database(self.db.runtime_dsn) as connection:
            self.assertEqual(connection.execute("SELECT current_user").fetchone(),
                             ("genius_core",))

    def test_a_superuser_is_refused_as_the_runtime(self):
        self.refused("must not be privileged", dsn=self.db.owner_dsn)

    def test_a_runtime_that_can_disable_triggers_is_refused(self):
        for privilege in ("SET", "ALTER SYSTEM"):
            with self.subTest(privilege=privilege):
                self.owner(f"GRANT {privilege} ON PARAMETER session_replication_role TO genius_core")
                try:
                    self.refused("must not disable triggers")
                finally:
                    self.owner(f"REVOKE {privilege} ON PARAMETER session_replication_role FROM genius_core")

    def test_trigger_bypass_through_a_non_inherited_role_is_refused(self):
        role = "geniusnew_test_parameter_" + uuid.uuid4().hex
        self.owner(f"CREATE ROLE {role}",
                   f"GRANT SET ON PARAMETER session_replication_role TO {role}",
                   f"GRANT {role} TO genius_core WITH INHERIT FALSE, SET TRUE")
        try:
            self.refused("must not disable triggers")
        finally:
            self.owner(f"REVOKE {role} FROM genius_core",
                       f"REVOKE SET ON PARAMETER session_replication_role FROM {role}",
                       f"DROP ROLE {role}")

    def test_excess_table_or_schema_rights_through_a_non_inherited_role_are_refused(self):
        role = "geniusnew_test_access_" + uuid.uuid4().hex
        self.owner(f"CREATE ROLE {role}",
                   f"GRANT {role} TO genius_core WITH INHERIT FALSE, SET TRUE")
        try:
            for grant, revoke, reason in (
                    (f"GRANT DELETE ON job_ledger TO {role}",
                     f"REVOKE DELETE ON job_ledger FROM {role}", "documented table privileges"),
                    (f"GRANT CREATE ON SCHEMA public TO {role}",
                     f"REVOKE CREATE ON SCHEMA public FROM {role}", "must not create objects")):
                with self.subTest(grant=grant):
                    self.owner(grant)
                    try:
                        self.refused(reason)
                    finally:
                        self.owner(revoke)
        finally:
            self.owner(f"REVOKE {role} FROM genius_core", f"DROP ROLE {role}")

    def test_a_runtime_with_server_file_or_program_access_is_refused(self):
        for role in ("pg_read_server_files", "pg_write_server_files",
                     "pg_execute_server_program"):
            with self.subTest(role=role):
                self.owner(f"GRANT {role} TO genius_core")
                try:
                    self.refused("must not be privileged")
                finally:
                    self.owner(f"REVOKE {role} FROM genius_core")

    def test_a_runtime_that_can_set_a_privileged_role_is_refused(self):
        role = "geniusnew_test_privileged_" + uuid.uuid4().hex
        self.owner(f"CREATE ROLE {role} CREATEROLE",
                   f"GRANT {role} TO genius_core WITH INHERIT FALSE, SET TRUE")
        try:
            self.refused("must not be privileged")
        finally:
            self.owner(f"REVOKE {role} FROM genius_core", f"DROP ROLE {role}")

    def test_membership_with_only_documented_rights_starts(self):
        role = "geniusnew_test_reader_" + uuid.uuid4().hex
        self.owner(f"CREATE ROLE {role}",
                   f"GRANT SELECT ON job_ledger TO {role}",
                   f"GRANT {role} TO genius_core")
        try:
            with database.open_database(self.db.runtime_dsn):
                pass
        finally:
            self.owner(f"REVOKE {role} FROM genius_core",
                       f"REVOKE SELECT ON job_ledger FROM {role}", f"DROP ROLE {role}")

    def test_a_runtime_that_owns_a_core_table_is_refused(self):
        # Owner privileges are revoked, so only the ownership itself is left.
        self.owner("ALTER TABLE job_ledger OWNER TO genius_core",
                   "REVOKE ALL ON job_ledger FROM genius_core",
                   "GRANT SELECT, INSERT, UPDATE ON job_ledger TO genius_core")
        self.refused("must not own the Core tables")

    def test_each_privilege_beyond_or_short_of_the_documented_set_is_refused(self):
        changes = [
                ("GRANT DELETE ON job_ledger TO genius_core",
                 "REVOKE DELETE ON job_ledger FROM genius_core"),
                ("GRANT TRIGGER ON job_ledger TO genius_core",
                 "REVOKE TRIGGER ON job_ledger FROM genius_core"),
                ("GRANT UPDATE ON acceptance_ledger TO genius_core",
                 "REVOKE UPDATE ON acceptance_ledger FROM genius_core"),
                ("GRANT INSERT ON schema_migrations TO genius_core",
                 "REVOKE INSERT ON schema_migrations FROM genius_core"),
                ("REVOKE UPDATE ON job_ledger FROM genius_core",
                 "GRANT UPDATE ON job_ledger TO genius_core")]
        with self.db.connect() as connection:
            if connection.info.server_version >= 170000:
                changes.append(("GRANT MAINTAIN ON job_ledger TO genius_core",
                                "REVOKE MAINTAIN ON job_ledger FROM genius_core"))
        for grant, revoke in changes:
            with self.subTest(grant=grant):
                self.owner(grant)
                self.refused("exactly the documented table privileges")
                self.owner(revoke)
        with database.open_database(self.db.runtime_dsn):
            pass

    def test_a_runtime_that_may_create_in_the_core_schema_is_refused(self):
        self.owner("GRANT CREATE ON SCHEMA public TO genius_core")
        self.refused("must not create objects in the Core schema")

    def test_a_formally_invalid_ledger_row_is_refused(self):
        valid = dict(job_id="'job-demo'", subject="'subject-demo'",
                     handoff_sha256="repeat('a', 64)", state="'EXECUTION_COMMITTED'",
                     created_at="100", reserved_at="100", updated_at="110", expires_at="160")
        for changes in (dict(job_id="''"), dict(job_id="repeat('j', 129)"),
                        dict(subject="''"), dict(handoff_sha256="repeat('A', 64)"),
                        dict(handoff_sha256="repeat('g', 64)"),
                        dict(created_at="0", reserved_at="0"),
                        dict(updated_at="99"), dict(updated_at="4102444801",
                                                    expires_at="4102444802"),
                        dict(expires_at="100"), dict(expires_at="4102444801"),
                        dict(reserved_at="99"), dict(reserved_at="111")):
            with self.subTest(changes=changes):
                row = {**valid, **changes}
                self.owner(f"INSERT INTO job_ledger VALUES ({row['job_id']}, "
                           f"{row['subject']}, {row['handoff_sha256']}, {row['state']}, "
                           f"{row['created_at']}, {row['reserved_at']}, "
                           f"{row['updated_at']}, {row['expires_at']})")
                self.refused("job ledger holds a formally invalid row")
                self.owner("DELETE FROM job_ledger")
        self.owner(f"INSERT INTO job_ledger VALUES ({valid['job_id']}, {valid['subject']}, "
                   f"{valid['handoff_sha256']}, {valid['state']}, {valid['created_at']}, "
                   f"{valid['reserved_at']}, {valid['updated_at']}, {valid['expires_at']})")
        with database.open_database(self.db.runtime_dsn):
            pass

    def test_the_bounds_are_the_orchestrator_s_and_the_audit_contract_s(self):
        from geniusnew import audit, orchestrator
        self.assertEqual(database._MAX_TIME, audit._MAX_OCCURRED_AT)
        self.assertEqual(database._MAX_TIME, orchestrator._MAX_OCCURRED_AT)
        self.assertEqual(database._MAX_JOB_ID_BYTES, orchestrator._MAX_JOB_ID_BYTES)


class ConnectionInputTest(unittest.TestCase):
    def test_implicit_connection_defaults_are_refused(self):
        for dsn in ("", "dbname=test", "host=localhost user=test", "host=localhost dbname=test"):
            with self.subTest(dsn=dsn), self.assertRaisesRegex(ContractError, "explicitly name"):
                with database.open_database(dsn):
                    self.fail("implicit database accepted")

    def test_invalid_dsn_does_not_echo_credentials_or_driver_errors(self):
        with self.assertRaisesRegex(ContractError, "database connection or operation failed") as caught:
            with database.open_database("DSN-SECRET-CANARY=invalid"):
                self.fail("invalid DSN accepted")
        self.assertNotIn("DSN-SECRET-CANARY", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_missing_local_migration_is_a_contract_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(database, "_MIGRATION_DIR", Path(directory)):
                with self.assertRaisesRegex(ContractError, "migration files cannot be read"):
                    database.migration_files()


class FoundationConstraintsTest(unittest.TestCase):
    def setUp(self):
        self.db = PostgresDatabase()
        self.addCleanup(self.db.close)
        database.migrate(self.db.owner_dsn)
        self.connection = self.db.connect(runtime=True)
        self.addCleanup(self.connection.close)

    def job(self, job_id="job", state="RESERVED", digest="a" * 64):
        self.connection.execute(
            "INSERT INTO public.job_ledger VALUES (%s, 'subject', %s, %s, 10, %s, 10, 100)",
            (job_id, digest, state, None if state == "PENDING_APPROVAL" else 10))

    def acceptance(self, job_id="job", digest="a" * 64, result="b" * 64):
        self.connection.execute("INSERT INTO public.acceptance_ledger VALUES (%s,%s,%s,%s,%s,20)",
                                (digest, job_id, b"\x00handoff\xff", result, b"\x00result\xfe"))

    def test_acceptance_completes_exactly_the_bound_job_and_preserves_wire_bytes(self):
        self.job(state="EXECUTION_COMMITTED")
        self.job("other", digest="c" * 64)
        self.acceptance()
        self.assertEqual(self.connection.execute(
            "SELECT job_id, state FROM job_ledger ORDER BY job_id").fetchall(),
            [("job", "COMPLETED"), ("other", "RESERVED")])
        self.assertEqual(self.connection.execute(
            "SELECT handoff_wire, result_wire FROM acceptance_ledger").fetchone(),
            (b"\x00handoff\xff", b"\x00result\xfe"))
        with self.assertRaises(psycopg.Error):
            self.acceptance()

    def test_acceptance_and_completion_rollback_together(self):
        self.job(state="EXECUTION_COMMITTED")
        with self.connection.transaction(force_rollback=True):
            self.acceptance()
            self.assertEqual(self.connection.execute("SELECT state FROM job_ledger").fetchone(),
                             ("COMPLETED",))
        self.assertEqual(self.connection.execute("SELECT state FROM job_ledger").fetchone(),
                         ("EXECUTION_COMMITTED",))
        self.assertEqual(self.connection.execute("SELECT * FROM acceptance_ledger").fetchall(), [])

    def test_acceptance_refuses_every_state_except_execution_committed(self):
        for state in ("PENDING_APPROVAL", "RESERVED", "REFUSED", "COMPLETED"):
            with self.subTest(state=state), self.connection.transaction(force_rollback=True):
                self.job(state=state)
                with self.assertRaisesRegex(psycopg.errors.CheckViolation,
                                            "acceptance requires the bound committed job"):
                    with self.connection.transaction():
                        self.acceptance()
                self.assertEqual(self.connection.execute("SELECT state FROM job_ledger").fetchone(),
                                 (state,))
                self.assertEqual(self.connection.execute("SELECT * FROM acceptance_ledger").fetchall(), [])

    def test_acceptance_refuses_a_missing_job_or_another_handoff(self):
        with self.assertRaisesRegex(psycopg.errors.CheckViolation,
                                    "acceptance requires the bound committed job"):
            self.acceptance()
        self.job(state="EXECUTION_COMMITTED")
        with self.assertRaisesRegex(psycopg.errors.CheckViolation,
                                    "acceptance requires the bound committed job"):
            self.acceptance(digest="c" * 64)

    def test_the_composite_foreign_key_independently_binds_job_and_handoff(self):
        self.job(state="EXECUTION_COMMITTED")
        with self.db.connect() as owner:
            owner.execute("ALTER TABLE acceptance_ledger DISABLE TRIGGER acceptance_job")
            owner.execute("ALTER TABLE acceptance_ledger DISABLE TRIGGER acceptance_complete")
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.acceptance(digest="c" * 64)

    def test_acceptance_cannot_succeed_if_completion_updates_zero_rows(self):
        self.job(state="EXECUTION_COMMITTED")
        with self.db.connect() as owner:
            owner.execute("CREATE FUNCTION public.suppress_update() RETURNS trigger "
                          "LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$")
            owner.execute("CREATE TRIGGER suppress BEFORE UPDATE ON job_ledger "
                          "FOR EACH ROW EXECUTE FUNCTION public.suppress_update()")
        with self.assertRaisesRegex(psycopg.errors.CheckViolation, "exactly one job"):
            self.acceptance()
        self.assertEqual(self.connection.execute("SELECT * FROM acceptance_ledger").fetchall(), [])

    def test_job_id_and_result_digest_are_unique(self):
        self.job(state="EXECUTION_COMMITTED")
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.job()
        self.acceptance()
        self.job("second", state="EXECUTION_COMMITTED", digest="c" * 64)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.acceptance("second", digest="c" * 64)
        self.assertEqual(self.connection.execute(
            "SELECT state FROM job_ledger WHERE job_id='second'").fetchone(),
            ("EXECUTION_COMMITTED",))

    def test_only_documented_forward_transitions_are_allowed(self):
        states = ("PENDING_APPROVAL", "RESERVED", "EXECUTION_COMMITTED", "COMPLETED", "REFUSED")
        allowed = {("PENDING_APPROVAL", "RESERVED"), ("PENDING_APPROVAL", "REFUSED"),
                   ("RESERVED", "EXECUTION_COMMITTED"), ("RESERVED", "REFUSED"),
                   ("EXECUTION_COMMITTED", "COMPLETED")}
        for old in states:
            for new in states:
                with self.subTest(old=old, new=new), self.connection.transaction(force_rollback=True):
                    self.job(state=old)
                    query = "UPDATE job_ledger SET state=%s"
                    if old == "PENDING_APPROVAL" and new == "RESERVED":
                        query += ", reserved_at=20"
                    if (old, new) in allowed:
                        self.connection.execute(query, (new,))
                    else:
                        with self.assertRaises(psycopg.errors.CheckViolation):
                            with self.connection.transaction():
                                self.connection.execute(query, (new,))

    def test_identity_and_reservation_time_cannot_change(self):
        self.job()
        for assignment in ("job_id='replacement'", "subject='replacement'",
                           "handoff_sha256=repeat('c',64)", "created_at=11",
                           "expires_at=101", "reserved_at=11", "reserved_at=NULL"):
            with self.subTest(assignment=assignment), self.assertRaises(psycopg.errors.CheckViolation):
                self.connection.execute("UPDATE job_ledger SET state='EXECUTION_COMMITTED', "
                                        + assignment)

    def test_reservation_timestamp_and_state_checks_are_enforced(self):
        for state, reserved in (("UNKNOWN", 10), ("PENDING_APPROVAL", 10),
                                ("RESERVED", None), ("EXECUTION_COMMITTED", None),
                                ("COMPLETED", None)):
            with self.subTest(state=state), self.assertRaises(psycopg.errors.CheckViolation):
                self.connection.execute(
                    "INSERT INTO job_ledger VALUES ('job','subject',%s,%s,10,%s,10,100)",
                    ("a" * 64, state, reserved))
        self.job(state="PENDING_APPROVAL")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute("UPDATE job_ledger SET state='RESERVED'")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.connection.execute("UPDATE job_ledger SET state='REFUSED', reserved_at=20")
