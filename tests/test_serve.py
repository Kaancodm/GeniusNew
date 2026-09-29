"""Gate C1: `python -m geniusnew serve` runs the real path from a configuration file."""

import hashlib
import json
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from geniusnew.database import migrate
from postgres_support import PostgresDatabase

from geniusnew.contracts import ContractError, Grant, Policy
from geniusnew.wiring import build
from geniusnew.workers import DeterministicSummarizer

ROOT = Path(__file__).resolve().parent.parent
ROOT_SECRET = b"ROOT-SECRET-CANARY-FOR-THE-SERVE-TEST"
API_KEY = b"API-KEY-CANARY-FOR-THE-SERVE-TEST"
PAYLOAD_CANARY = "PAYLOAD-CANARY-MUST-NOT-REACH-THE-LOG"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def configuration(*, secret_path: str, anchor_path: str, port: int, digest: str, database_dsn_file: str) -> str:
    return f"""
[service]
listen_host = "127.0.0.1"
listen_port = {port}
root_secret_file = "{secret_path}"
anchor_state = "{anchor_path}"
database_dsn_file = "{database_dsn_file}"

[policy]
version = "policy-serve"
orchestrator_id = "orchestrator-1"
handoff_ttl_seconds = 60
allowed_tools = ["summarize"]
allowed_sandbox_profiles = ["isolated"]

[[policy.grants]]
subject = "subject-serve"
user_id = "user-serve"
worker_agent_id = "worker-serve"
tier = "basic"
tools = ["summarize"]
sandbox_profile = "isolated"
requires_approval = false

[principals]
{digest} = "subject-serve"
"""


def run_module(*arguments, stdin=b"", timeout=30):
    return subprocess.run([sys.executable, "-m", "geniusnew", *arguments],
                          cwd=ROOT, input=stdin, capture_output=True, timeout=timeout)


class ServeTest(unittest.TestCase):

    def setUp(self):
        self.db = PostgresDatabase()
        self.addCleanup(self.db.close)
        migrate(self.db.owner_dsn)
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)
        secret = self.root / "root_secret"
        secret.write_bytes(ROOT_SECRET)
        os.chmod(secret, 0o600)
        self.dsn_path = self.root / "database_dsn"
        self.dsn_path.write_text(self.db.runtime_dsn)
        self.dsn_path.chmod(0o600)
        self.port = free_port()
        self.config = self.root / "geniusnew.toml"
        self.config.write_text(configuration(
            secret_path=str(secret), anchor_path=str(self.root / "anchor.state"),
            port=self.port, digest=hashlib.sha256(API_KEY).hexdigest(),
            database_dsn_file=str(self.dsn_path)))
        self.process = None
        self.stderr = b""

    def tearDown(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=10)
        if self.process is not None:
            self.process.stderr.close()
        self._directory.cleanup()

    def start(self):
        self.process = subprocess.Popen(
            [sys.executable, "-m", "geniusnew", "serve", "--config", str(self.config)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE)
        deadline = time.monotonic() + 30
        while b"listening on" not in self.stderr:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, "service did not report listening")
            ready, _, _ = select.select([self.process.stderr], [], [], remaining)
            self.assertTrue(ready, "service did not report listening")
            chunk = os.read(self.process.stderr.fileno(), 4096)
            self.assertTrue(chunk, f"service exited early: {self.stderr!r}")
            self.stderr += chunk

    def stop(self):
        self.process.send_signal(signal.SIGTERM)
        self.assertEqual(self.process.wait(timeout=30), 0)
        self.stderr += self.process.stderr.read()

    def post(self, api_key, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/jobs", data=json.dumps(payload).encode(),
            method="POST", headers={"Content-Type": "application/json",
                                    "Authorization": "Bearer " + api_key.decode()})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def assert_database_start_refused(self, reason):
        from geniusnew.__main__ import _serve

        # Only observe downstream side effects; every DB operation is real.
        with patch("geniusnew.__main__.build") as build_service, \
                patch("geniusnew.__main__.serve") as listener:
            with self.assertRaisesRegex(ContractError, reason):
                _serve(str(self.config))
            build_service.assert_not_called()
            listener.assert_not_called()
        result = run_module("serve", "--config", str(self.config), timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertIn(reason.encode(), result.stderr)
        self.assertNotIn(b"listening on", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertNotIn(self.dsn_path.read_bytes(), result.stderr)
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", self.port)), 0)

    def test_an_unreachable_database_is_refused_before_listener_and_anchor(self):
        from psycopg.conninfo import make_conninfo

        # Reserve a TCP port without listening, so no other server can take it.
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            self.dsn_path.write_text(make_conninfo(
                self.db.runtime_dsn, host="127.0.0.1", port=unused.getsockname()[1]))
            self.assert_database_start_refused("database connection or operation failed")

    def test_missing_migration_is_refused_before_listener_and_anchor(self):
        with self.db.connect() as connection:
            connection.execute("DELETE FROM schema_migrations")
        self.assert_database_start_refused("required database migration is missing")

    def test_missing_migration_table_is_refused_before_listener_and_anchor(self):
        with self.db.connect() as connection:
            connection.execute("DROP TABLE schema_migrations")
        self.assert_database_start_refused("database connection or operation failed")

    def test_wrong_checksum_is_refused_before_listener_and_anchor(self):
        with self.db.connect() as connection:
            connection.execute("UPDATE schema_migrations SET checksum=repeat('0',64)")
        self.assert_database_start_refused("database migration checksum mismatch")

    def test_newer_migration_is_refused_before_listener_and_anchor(self):
        with self.db.connect() as connection:
            connection.execute("INSERT INTO schema_migrations VALUES (2, repeat('a',64), 1)")
        self.assert_database_start_refused("unknown or out-of-order database migration")

    def test_migrate_command_uses_its_separate_private_dsn_file(self):
        owner_file = self.root / "migration_dsn"
        owner_file.write_text(self.db.owner_dsn)
        owner_file.chmod(0o600)
        result = run_module("migrate", "--dsn-file", str(owner_file))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, b"")

    def test_a_job_runs_end_to_end_and_sigterm_stops_the_service(self):
        self.start()
        status, body = self.post(API_KEY, {"text": PAYLOAD_CANARY})
        self.assertEqual(status, 202, body)
        self.assertEqual(body["status"], "SUCCEEDED")
        self.stop()
        self.assertIn(b"stopping", self.stderr)
        for canary in (ROOT_SECRET, API_KEY, PAYLOAD_CANARY.encode()):
            self.assertNotIn(canary, self.stderr)

    def test_an_unknown_key_is_refused_by_the_running_service(self):
        self.start()
        status, _ = self.post(b"UNKNOWN-KEY-CANARY-NOT-CONFIGURED", {"text": "x"})
        self.assertEqual(status, 401)
        self.stop()

    def test_a_restart_before_any_job_starts_again(self):
        self.start()
        self.stop()
        self.process.stderr.close()
        self.stderr = b""
        self.start()
        self.assertEqual(self.post(API_KEY, {"text": "after restart"})[0], 202)
        self.stop()

    def test_a_restart_after_jobs_is_refused_and_this_is_the_boundary(self):
        """Held open until the audit chain is persisted (docs/ROADMAP-V02.md, B5).

        The anchor's state file survives the restart; the chain does not. The
        restarted process refuses to start rather than run behind an anchor it
        cannot extend. When B5 lands, this test turns into "a restart continues
        the chain", together with SECURITY.md.
        """
        self.start()
        self.assertEqual(self.post(API_KEY, {"text": "first"})[0], 202)
        self.stop()
        result = run_module("serve", "--config", str(self.config))
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"refused: the anchor has committed", result.stderr)
        self.assertIn(b"not persisted yet (gate B5)", result.stderr)
        self.assertNotIn(b"listening on", result.stderr)

    def test_a_refused_configuration_exits_non_zero_without_a_traceback(self):
        self.config.write_text(self.config.read_text().replace(
            'listen_host = "127.0.0.1"', 'listen_host = "0.0.0.0"'))
        result = run_module("serve", "--config", str(self.config))
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"refused: service.listen_host must be a loopback address", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertNotIn(ROOT_SECRET, result.stderr)

    def test_a_placeholder_principal_is_refused_at_start(self):
        text = self.config.read_text()
        digest = hashlib.sha256(API_KEY).hexdigest()
        self.config.write_text(text.replace(digest, "REPLACE_WITH_SHA256_OF_THE_API_KEY"))
        result = run_module("serve", "--config", str(self.config))
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"SHA-256 digests", result.stderr)


class DigestApiKeyTest(unittest.TestCase):

    def test_the_digest_of_the_key_on_stdin_is_printed(self):
        result = run_module("digest-api-key", stdin=API_KEY)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.decode().strip(), hashlib.sha256(API_KEY).hexdigest())

    def test_one_trailing_newline_is_not_part_of_the_key(self):
        for ending in (b"\n", b"\r\n"):
            result = run_module("digest-api-key", stdin=API_KEY + ending)
            self.assertEqual(result.stdout.decode().strip(),
                             hashlib.sha256(API_KEY).hexdigest())

    def test_the_key_itself_is_never_printed(self):
        result = run_module("digest-api-key", stdin=API_KEY)
        self.assertNotIn(API_KEY, result.stdout + result.stderr)

    def test_a_short_key_is_refused(self):
        result = run_module("digest-api-key", stdin=b"short")
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"refused: api key must be between", result.stderr)


class BuildPrincipalsTest(unittest.TestCase):
    """`build` takes exactly one way of naming principals."""

    def arguments(self):
        grant = Grant("subject-a", "user-a", "worker-a", "basic",
                      ("summarize",), "isolated", False)
        policy = Policy("policy-v1", "orchestrator-1", 60, ("summarize",),
                        ("isolated",), (grant,))
        return dict(root_secret=ROOT_SECRET, policy=policy,
                    workers=(DeterministicSummarizer(),))

    def test_neither_api_keys_nor_principals_is_refused(self):
        with self.assertRaisesRegex(ContractError, "exactly one of api_keys or principals"):
            build(**self.arguments())

    def test_both_api_keys_and_principals_is_refused(self):
        digest = hashlib.sha256(API_KEY).hexdigest()
        with self.assertRaisesRegex(ContractError, "exactly one of api_keys or principals"):
            build(**self.arguments(), api_keys={API_KEY: "subject-a"},
                  principals={digest: "subject-a"})

    def test_principals_by_digest_authenticate_the_same_key(self):
        digest = hashlib.sha256(API_KEY).hexdigest()
        service = build(**self.arguments(), principals={digest: "subject-a"})
        try:
            self.assertEqual(service.entry._registry.resolve(API_KEY).subject, "subject-a")
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
