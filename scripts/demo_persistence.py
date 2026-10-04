#!/usr/bin/env python3
"""Refuse durable replay, owner SQL tampering, and a database behind its anchor.

The only database input is GENIUSNEW_TEST_ADMIN_DSN. PostgresDatabase creates
random disposable databases and drops them; this command never targets the
database named by that DSN for mutations.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import crash_service as harness  # noqa: E402
from geniusnew import database  # noqa: E402
from geniusnew.audit_chain import verify  # noqa: E402
from geniusnew.contracts import ContractError  # noqa: E402
from geniusnew.workers import WorkerRunner  # noqa: E402
from postgres_support import PostgresDatabase  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def start(db: PostgresDatabase, anchor_state: str, log_path: str, *, approval=False):
    return harness.build_service(
        db.runtime_dsn, anchor_state, log_path, requires_approval=approval,
        job_id=harness.CRASH_JOB_ID)


def replay_after_restart(directory: str) -> None:
    db = PostgresDatabase()
    try:
        database.migrate(db.owner_dsn)
        anchor_state = os.path.join(directory, "replay.anchor")
        log_path = os.path.join(directory, "replay.exec")
        service, connection = start(db, anchor_state, log_path)
        try:
            captured = []
            execute = WorkerRunner.execute

            def capture_result(runner, permit, *, now):
                result_wire = execute(runner, permit, now=now)
                captured.append((permit.handoff.to_bytes(), result_wire))
                return result_wire

            with patch.object(WorkerRunner, "execute", capture_result):
                result = harness.run_job(
                    service, mode="direct", text=harness.CRASH_TEXT)[-1]
            require(result.status == 202 and result.body.get("status") == "SUCCEEDED",
                    "the first job did not complete")
            require(len(captured) == 1, "the first job did not yield one signed result")
            handoff_wire, result_wire = captured[0]
            old_head = service.head()
        finally:
            service.close()
            connection.close()

        service, connection = start(db, anchor_state, log_path)
        try:
            result = harness.run_job(service, mode="direct", text=harness.CRASH_TEXT)[-1]
            require(result.status == 409 and result.body == {"error": "REJECTED"},
                    "a completed job id was accepted after restart")
            try:
                service.verifier.accept(
                    result_wire, handoff_wire=handoff_wire,
                    subject=harness.SUBJECT, job_id=harness.CRASH_JOB_ID,
                    policy=service.policy, now=harness.NOW)
            except ContractError as refusal:
                require("already has an accepted result" in str(refusal),
                        "the signed result met an unrelated refusal")
            else:
                raise AssertionError("the signed result was accepted again after restart")
            require(harness.executions(log_path, harness.CRASH_TEXT) == 1,
                    "the replay ran the worker")
            records = service.chain.records
            require(records[old_head.count - 1].record_hash == old_head.head_hash,
                    "the restart lost the previously anchored prefix")
            require(sum(record.event.action == "RESULT_ACCEPTED"
                        for record in records) == 1,
                    "the replay accepted a second result")
            require(verify(records, service.head(), authority=service.audit,
                           anchor=service.anchor) == len(records),
                    "the restarted chain did not verify")
        finally:
            service.close()
            connection.close()
    finally:
        db.close()


def sql_deletion_on_restart(directory: str) -> None:
    db = PostgresDatabase()
    try:
        database.migrate(db.owner_dsn)
        anchor_state = os.path.join(directory, "sql.anchor")
        log_path = os.path.join(directory, "sql.exec")
        service, connection = start(db, anchor_state, log_path, approval=True)
        try:
            submitted = service.entry.handle(
                method="POST", path="/jobs", headers=harness.headers(),
                body=b'{"text":"pending persistence demo"}')
            require(submitted.status == 202
                    and submitted.body.get("status") == "PENDING_APPROVAL",
                    "the approval job was not pending")
        finally:
            service.close()
            connection.close()

        with db.connect() as owner:
            pending = owner.execute(
                "DELETE FROM public.pending_jobs WHERE job_id=%s",
                (harness.CRASH_JOB_ID,)).rowcount
            ledger = owner.execute(
                "DELETE FROM public.job_ledger WHERE job_id=%s",
                (harness.CRASH_JOB_ID,)).rowcount
        require((pending, ledger) == (1, 1), "the owner SQL attack did not remove both rows")
        try:
            service, connection = start(db, anchor_state, log_path, approval=True)
        except ContractError as refusal:
            require("pending issuance has no job ledger row" in str(refusal),
                    "the SQL attack met an unrelated refusal")
        else:
            service.close()
            connection.close()
            raise AssertionError("the service started after owner SQL deletion")
    finally:
        db.close()


def anchor_ahead_of_database(directory: str) -> None:
    current = PostgresDatabase()
    try:
        database.migrate(current.owner_dsn)
        anchor_state = os.path.join(directory, "ahead.anchor")
        log_path = os.path.join(directory, "ahead.exec")
        service, connection = start(current, anchor_state, log_path)
        try:
            result = harness.run_job(service, mode="direct", text=harness.CRASH_TEXT)[-1]
            anchor_count = service.anchor.committed[0]
            require(result.status == 202 and anchor_count > 0,
                    "the anchor was not advanced")
        finally:
            service.close()
            connection.close()

        older = PostgresDatabase()
        try:
            database.migrate(older.owner_dsn)
            try:
                service, connection = start(older, anchor_state, log_path)
            except ContractError as refusal:
                require(str(refusal) ==
                        f"anchor committed {anchor_count} records; this chain has 0",
                        "the older database met an unrelated refusal")
            else:
                service.close()
                connection.close()
                raise AssertionError("the service started behind its anchor")
        finally:
            older.close()
    finally:
        current.close()


def main() -> int:
    if not os.environ.get("GENIUSNEW_TEST_ADMIN_DSN"):
        print("FAIL — GENIUSNEW_TEST_ADMIN_DSN is required for the persistence demo")
        return 1
    with tempfile.TemporaryDirectory() as directory:
        try:
            replay_after_restart(directory)
            print("[ok] replay after restart refused", flush=True)
            sql_deletion_on_restart(directory)
            print("[ok] owner SQL deletion refused at startup", flush=True)
            anchor_ahead_of_database(directory)
            print("[ok] database behind anchor refused at startup", flush=True)
        except Exception as failure:  # noqa: BLE001 - no DSN or payload in output
            print(f"FAIL — persistence demo failed ({type(failure).__name__})")
            return 1
    print("PASS — 3/3 persistence attacks refused.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
