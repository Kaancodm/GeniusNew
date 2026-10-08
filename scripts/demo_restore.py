#!/usr/bin/env python3
"""Exercise pg_dump/pg_restore with the CI PostgreSQL service and an anchor copy.

The Docker container ID and admin DSN must identify the disposable CI service.
Nothing here can select a production database or the active host anchor.
"""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import crash_service as harness  # noqa: E402
from geniusnew import database  # noqa: E402
from geniusnew.audit_chain import verify  # noqa: E402
from geniusnew.contracts import ContractError  # noqa: E402
from postgres_support import PostgresDatabase  # noqa: E402

_CONTAINER_ID = re.compile(r"\A[0-9a-f]{64}\Z")
_ADMIN_DSN = {
    "host": "127.0.0.1",
    "dbname": "geniusnew_test_admin", "user": "postgres",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def ci_container() -> str:
    container = os.environ.get("GENIUSNEW_TEST_POSTGRES_CONTAINER", "")
    dsn = os.environ.get("GENIUSNEW_TEST_ADMIN_DSN", "")
    parameters = conninfo_to_dict(dsn)
    port = parameters.pop("port", "")
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or not _CONTAINER_ID.fullmatch(container)
            or parameters != _ADMIN_DSN
            or not re.fullmatch(r"[1-9][0-9]{0,4}", port)
            or int(port) > 65535):
        raise ContractError("restore drill requires the disposable CI PostgreSQL service")
    # Bind the DSN to this exact container, rather than trusting a host port
    # that could point at a different database. Never fall back to libpq defaults.
    bindings = None
    try:
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", container],
            capture_output=True, text=True, check=True, timeout=10)
        bindings = json.loads(inspected.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    if (not isinstance(bindings, dict)
            or bindings.get("5432/tcp") != [{"HostIp": "127.0.0.1", "HostPort": port}]):
        raise ContractError("restore drill DSN does not match the loopback CI container")
    return container


def postgres(container: str, command: str, db_name: str, *, archive: bytes | None = None) -> bytes:
    require(bool(re.fullmatch(r"geniusnew_test_[0-9a-f]{32}", db_name)),
            "restore drill database name is not disposable")
    args = ["docker", "exec"]
    if archive is not None:
        args.append("-i")
    args.extend([container, command, "-h", "127.0.0.1", "-U", "postgres"])
    if command == "pg_dump":
        args.extend(["-Fc", "--no-owner", "-d", db_name])
    elif command == "pg_restore":
        args.extend(["--exit-on-error", "--no-owner", "-d", db_name])
    else:
        raise AssertionError("unsupported PostgreSQL command")
    try:
        result = subprocess.run(args, input=archive, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise AssertionError("CI PostgreSQL backup tool is unavailable") from None
    require(result.returncode == 0, f"{command} failed in the CI service")
    return result.stdout


def run() -> None:
    container = ci_container()
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        source = PostgresDatabase()
        stack.callback(source.close)
        database.migrate(source.owner_dsn)
        older_dump = postgres(container, "pg_dump", source.name)
        require(bool(older_dump), "pre-job dump is empty")

        anchor_state = str(Path(directory) / "source.anchor")
        log_path = str(Path(directory) / "worker.exec")
        service, connection = harness.build_service(
            source.runtime_dsn, anchor_state, log_path,
            requires_approval=False, job_id=harness.CRASH_JOB_ID)
        try:
            response = harness.run_job(service, mode="direct", text=harness.CRASH_TEXT)[-1]
            require(response.status == 202 and response.body.get("status") == "SUCCEEDED",
                    "source job did not complete")
            head = service.head()
            require(head.count > 0, "source anchor did not advance")
        finally:
            service.close()
            connection.close()
        current_dump = postgres(container, "pg_dump", source.name)
        require(bool(current_dump), "post-job dump is empty")

        restored = PostgresDatabase()
        stack.callback(restored.close)
        postgres(container, "pg_restore", restored.name, archive=current_dump)
        with database.open_database(restored.runtime_dsn):
            pass
        restored_anchor = str(Path(directory) / "restored.anchor")
        shutil.copyfile(anchor_state, restored_anchor)
        service, connection = harness.build_service(
            restored.runtime_dsn, restored_anchor, log_path,
            requires_approval=False, job_id=harness.CRASH_JOB_ID)
        try:
            require(service.anchor.committed == (head.count, head.head_hash),
                    "restored anchor does not match the signed source head")
            require(verify(service.chain.records, service.head(), authority=service.audit,
                           anchor=service.anchor) == head.count,
                    "restored chain does not verify")
            response = harness.run_job(service, mode="direct", text=harness.CRASH_TEXT)[-1]
            require(response.status == 409 and response.body == {"error": "REJECTED"},
                    "restored completed job id was accepted again")
            require(harness.executions(log_path, harness.CRASH_TEXT) == 1,
                    "restored replay ran the worker")
        finally:
            service.close()
            connection.close()
        print("[ok] pg_restore with matching anchor verified and replay refused", flush=True)

        older = PostgresDatabase()
        stack.callback(older.close)
        postgres(container, "pg_restore", older.name, archive=older_dump)
        with database.open_database(older.runtime_dsn):
            pass
        older_anchor = str(Path(directory) / "older.anchor")
        shutil.copyfile(anchor_state, older_anchor)
        try:
            service, connection = harness.build_service(
                older.runtime_dsn, older_anchor, log_path,
                requires_approval=False, job_id=harness.CRASH_JOB_ID)
        except ContractError as refusal:
            require(str(refusal) ==
                    f"anchor already committed {head.count} records; head claims 0",
                    "older restore met an unrelated refusal")
        else:
            service.close()
            connection.close()
            raise AssertionError("older restored database started behind its anchor")
        print("[ok] pg_restore behind anchor refused at startup", flush=True)


def main() -> int:
    try:
        run()
    except Exception as failure:  # noqa: BLE001 - never print a DSN or archive
        print(f"FAIL — C5 restore drill ({type(failure).__name__})")
        return 1
    print("PASS — C5 disposable PostgreSQL and anchor restore drill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
