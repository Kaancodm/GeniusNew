"""Real PostgreSQL only: each test owns a disposable database, never a schema reset."""

import os
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


class PostgresDatabase:
    def __init__(self):
        # Missing infrastructure is a test failure, never a skipped DB test.
        admin_dsn = os.environ["GENIUSNEW_TEST_ADMIN_DSN"]
        self.admin = psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5)
        self.name = "geniusnew_test_" + uuid.uuid4().hex
        try:
            self.admin.execute("SELECT pg_advisory_lock(813781)")
            try:
                if not self.admin.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = 'genius_core'"
                ).fetchone():
                    self.admin.execute("CREATE ROLE genius_core LOGIN")
            finally:
                self.admin.execute("SELECT pg_advisory_unlock(813781)")
            self.admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(self.name)))
        except BaseException:
            self.admin.close()
            raise
        self.owner_dsn = make_conninfo(admin_dsn, dbname=self.name)
        self.runtime_dsn = make_conninfo(admin_dsn, dbname=self.name, user="genius_core")

    def connect(self, *, runtime=False):
        return psycopg.connect(self.runtime_dsn if runtime else self.owner_dsn,
                               autocommit=True, connect_timeout=5)

    def close(self):
        try:
            self.admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                sql.Identifier(self.name)))
        finally:
            self.admin.close()
