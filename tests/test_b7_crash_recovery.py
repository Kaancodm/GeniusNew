"""Gate B7: the durable service is killed at every durable boundary and restarted.

A child process (`crash_service.py`) runs one job against PostgreSQL ledgers, the
PostgreSQL audit chain and an anchor with a state file, and dies by SIGKILL at a
chosen boundary. This test then starts the service again on what the crash left
behind and requires, at every boundary:

- the restart succeeds and the whole chain verifies against the anchor;
- the job ran at most once, and never without its committed `EXECUTION_COMMITTED`
  row, even when the same id is driven again after the restart;
- a result is accepted at most once, an approval token is spent at most once,
  and no audit event of the job appears twice;
- a job that was still waiting can still be completed: with the token the operator
  already holds, or, if the crash cost the operator the token, by approving the
  job again;
- the service still takes and completes a new job.

The boundaries are not listed here. A dry run records every crossing with the
state a second connection sees and whether the operator already holds the approval
token, and each distinct combination is killed once: the committed state, and what
reached the outside, is all a crash leaves behind. A boundary added to the flow
later is therefore covered without touching this file.

Recovery rule for a lost token: the operator approves the waiting job again. That
issues a second token for the same job and leaves the first record GRANTED until
it expires. The decision is made when the job is consumed, so whichever token
comes first completes the job once and the other is refused
(`RegrantTest`). Allowing a second grant is a property C3 has to keep or replace
deliberately; it is not a side effect.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import crash_service as harness
from geniusnew import database
from geniusnew.audit_chain import verify
from geniusnew.contracts import ContractError
from geniusnew.orchestrator import Denied
from postgres_support import PostgresDatabase

ROOT = Path(__file__).resolve().parent.parent
BURNED = ("RESERVED", "EXECUTION_COMMITTED", "COMPLETED")
ONCE_PER_JOB = ("HANDOFF_ADMITTED", "EXECUTION_DISPATCHED", "RESULT_ACCEPTED")
WORKERS = max(1, min(4, os.cpu_count() or 1))
# state = [chain records, signed heads, job states, approval records, pending
# rows, acceptances, anchored count, executions, approval token delivered]
DB_AHEAD = {"ahead of the anchor": lambda state: state[0] > state[6]}
REACHED = {
    "direct": {
        **DB_AHEAD,
        "RESERVED": lambda state: state[2] == "RESERVED",
        "EXECUTION_COMMITTED before the worker ran":
            lambda state: state[2] == "EXECUTION_COMMITTED" and state[7] == 0,
        "EXECUTION_COMMITTED after the worker ran":
            lambda state: state[2] == "EXECUTION_COMMITTED" and state[7] == 1,
        "COMPLETED": lambda state: state[2] == "COMPLETED",
    },
    "approval": {
        **DB_AHEAD,
        "PENDING_APPROVAL with a grant the operator never received":
            lambda state: state[2] == "PENDING_APPROVAL" and state[3] == 1 and state[8] == 0,
        "PENDING_APPROVAL with a grant the operator holds":
            lambda state: state[2] == "PENDING_APPROVAL" and state[3] == 1 and state[8] == 1,
        "RESERVED": lambda state: state[2] == "RESERVED",
        "EXECUTION_COMMITTED after the worker ran":
            lambda state: state[2] == "EXECUTION_COMMITTED" and state[7] == 1,
        "COMPLETED": lambda state: state[2] == "COMPLETED",
    },
    "expired": {
        **DB_AHEAD,
        "PENDING_APPROVAL": lambda state: state[2] == "PENDING_APPROVAL" and state[4] == 1,
        "REFUSED": lambda state: state[2] == "REFUSED" and state[4] == 0,
    },
}


def run_child(db, directory, mode, target):
    """Run the harness; returns (exit code, announced events, stderr)."""
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "tests")]),
        "GENIUSNEW_CRASH_DSN": db.runtime_dsn,
        "GENIUSNEW_CRASH_ANCHOR_STATE": os.path.join(directory, "anchor.state"),
        "GENIUSNEW_CRASH_EXEC_LOG": os.path.join(directory, "exec.log"),
        "GENIUSNEW_CRASH_MODE": mode,
        "GENIUSNEW_CRASH_AT": str(target),
    }
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "crash_service.py")], cwd=ROOT,
        env=environment, capture_output=True, text=True, timeout=120)
    events = [json.loads(line) for line in completed.stdout.splitlines()
              if line.startswith("{")]
    return completed.returncode, events, completed.stderr


def gone(pid, timeout=15.0):
    """True once the process is dead or a zombie that nobody has reaped."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/stat") as stat:
                state = stat.read().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return True
        if state in ("Z", "X"):
            return True
        time.sleep(0.02)
    return False


def distinct_states(crossings):
    """The first crossing of every committed state, in order."""
    seen, chosen = set(), []
    for crossing in crossings:
        key = tuple(crossing["state"])
        if key not in seen:
            seen.add(key)
            chosen.append(crossing)
    return chosen


def label(crossing):
    return (f"crossing {crossing['n']} ({crossing['kind']} {crossing['phase']}"
            f"{' ' + crossing['action'] if crossing['action'] else ''}) "
            f"state {crossing['state']}")


def trace(mode):
    """Dry run: every crossing with the committed state seen at that moment."""
    db = PostgresDatabase()
    try:
        database.migrate(db.owner_dsn)
        with tempfile.TemporaryDirectory() as directory:
            code, events, stderr = run_child(db, directory, mode, 0)
    finally:
        db.close()
    if code != 0:
        raise AssertionError(f"the dry run failed with {code}: {stderr[-500:]}")
    return next(event["crossings"] for event in events if event["event"] == "done")


def kill_and_restart(mode, crossing):
    """Kill the child at one crossing, restart, and return what went wrong."""
    db = PostgresDatabase()
    try:
        database.migrate(db.owner_dsn)
        with tempfile.TemporaryDirectory() as directory:
            code, events, stderr = run_child(db, directory, mode, crossing["n"])
            if code != -signal.SIGKILL:
                return [f"the child exited with {code} instead of dying: {stderr[-300:]}"]
            ready = next((event for event in events if event["event"] == "ready"), None)
            if ready is None or not gone(ready["anchor_pid"]):
                return ["the anchor outlived the killed service and holds its state"]
            return restart(db, directory, events, mode)
    finally:
        db.close()


def restart(db, directory, events, mode):
    anchor_state = os.path.join(directory, "anchor.state")
    log_path = os.path.join(directory, "exec.log")
    # An expired job is only expired if the restarted service's clock agrees.
    clock = harness.Clock(harness.EXPIRED_AT if mode == "expired" else harness.NOW)
    try:
        service, connection = harness.build_service(
            db.runtime_dsn, anchor_state, log_path, requires_approval=mode != "direct",
            job_id=harness.FRESH_JOB_ID, clock=clock)
    except ContractError as refusal:
        return [f"the restart was refused: {refusal}"]
    except Exception as error:  # noqa: BLE001 - any other failure is a finding too
        return [f"the restart raised {type(error).__name__}: {error}"]
    try:
        return check(service, db, log_path, events, mode)
    finally:
        service.close()
        connection.close()


def check(service, db, log_path, events, mode):
    problems = []

    def ran():
        return harness.executions(log_path, harness.CRASH_TEXT)

    def state():
        with db.connect() as owner:
            row = owner.execute("SELECT state FROM job_ledger WHERE job_id = %s",
                                (harness.CRASH_JOB_ID,)).fetchone()
        return None if row is None else row[0]

    def accepted_rows():
        with db.connect() as owner:
            return owner.execute("SELECT count(*) FROM acceptance_ledger WHERE job_id = %s",
                                 (harness.CRASH_JOB_ID,)).fetchone()[0]

    def actions():
        return Counter(record.event.action for record in service.chain.records
                       if record.event.job_id == harness.CRASH_JOB_ID)

    def chain_verifies():
        records = service.chain.records
        return verify(records, service.head(), authority=service.audit,
                      anchor=service.anchor) == len(records)

    def safe():
        found = []
        before = state()
        if ran() > 1:
            found.append(f"the job ran {ran()} times")
        if ran() == 1 and before not in ("EXECUTION_COMMITTED", "COMPLETED"):
            found.append(f"the job ran while its row was {before}")
        for action, count in actions().items():
            if action in ONCE_PER_JOB and count > 1:
                found.append(f"{action} was recorded {count} times")
        if before == "COMPLETED" and (ran() != 1 or accepted_rows() != 1
                                      or actions()["RESULT_ACCEPTED"] != 1):
            found.append("a completed job lacks its single run or its single acceptance")
        if mode == "expired" and ran() != 0:
            found.append("a job that expired before its approval ran")
        return found

    # Before anything asks for the head, which would bring the anchor up itself.
    anchored, stored = service.anchor.committed[0], len(service.chain.records)
    if anchored != stored:
        problems.append(f"the restart left the anchor at {anchored} of {stored} records")
    try:
        if not chain_verifies():
            problems.append("the chain does not verify against the anchor after the restart")
    except Exception as error:  # noqa: BLE001
        problems.append(f"verifying the chain raised {type(error).__name__}: {error}")
    problems += safe()

    announced = {event["event"]: event for event in events}
    before = state()
    if before in BURNED and "dispatch" in announced and mode == "direct":
        wire = bytes.fromhex(announced["dispatch"]["wire"])
        try:
            service.orchestrator.dispatch(
                wire, subject=harness.SUBJECT, job_id=harness.CRASH_JOB_ID,
                policy=service.policy, now=harness.NOW)
            problems.append("a burned job id was dispatched again")
        except Denied as denied:
            if denied.decision.reason_code != "JOB_ID_REUSED":
                problems.append(f"driving a burned id again gave {denied.decision.reason_code}")
        except Exception as error:  # noqa: BLE001
            problems.append(f"driving a burned id again raised {type(error).__name__}")
        if before == "COMPLETED" and "result" in announced:
            try:
                service.verifier.accept(
                    bytes.fromhex(announced["result"]["wire"]), handoff_wire=wire,
                    subject=harness.SUBJECT, job_id=harness.CRASH_JOB_ID,
                    policy=service.policy, now=harness.NOW, approval_record_hash=None)
                problems.append("a result was accepted twice")
            except ContractError:
                pass
            except Exception as error:  # noqa: BLE001
                problems.append(f"accepting a result again raised {type(error).__name__}")
    if mode == "expired" and before is not None:
        # Whether or not the crash cut the refusal short, the job ends REFUSED,
        # once, and is never approved.
        try:
            service.approve(harness.CRASH_JOB_ID)
            problems.append("an expired job was approved")
        except ContractError:
            pass
        if state() != "REFUSED":
            problems.append(f"the expired job ended {state()} instead of REFUSED")
        if actions()["HANDOFF_REJECTED"] != 1:
            problems.append(f"the expired job has {actions()['HANDOFF_REJECTED']} "
                            "rejection events instead of one")
    if mode == "approval":
        token = None
        if "token" in announced:
            token = bytes.fromhex(announced["token"]["token"])
        elif before == "PENDING_APPROVAL":
            # The crash cost the operator the token. The recovery rule is to
            # approve the waiting job again, so no waiting job is lost.
            try:
                token = service.approve(harness.CRASH_JOB_ID)
            except ContractError as refusal:
                problems.append(f"a waiting job could not be approved again: {refusal}")
        if token is not None:
            first = harness.complete(service, harness.CRASH_JOB_ID, token)
            second = harness.complete(service, harness.CRASH_JOB_ID, token)
            if before in BURNED and first.status == 202:
                problems.append(f"a token was spent again for a job that was already {before}")
            if before == "PENDING_APPROVAL" and (
                    first.status != 202 or first.body.get("status") != "SUCCEEDED"
                    or ran() != 1):
                problems.append("a waiting job was lost: its approval did not complete it")
            if second.status == 202:
                problems.append("the same approval token completed a job twice")
    problems += safe()

    try:
        responses = harness.run_job(
            service, mode="direct" if mode == "direct" else "approval",
            text=harness.FRESH_TEXT)
        if responses[-1].status != 202 or responses[-1].body.get("status") != "SUCCEEDED":
            problems.append(f"a new job after the restart gave {responses[-1].status}")
        elif harness.executions(log_path, harness.FRESH_TEXT) != 1:
            problems.append("a new job after the restart did not run exactly once")
        if not chain_verifies():
            problems.append("the chain does not verify after a new job")
    except Exception as error:  # noqa: BLE001
        problems.append(f"a new job after the restart raised {type(error).__name__}: {error}")
    return problems


class CrashRecoveryTest(unittest.TestCase):
    def exercise(self, mode):
        crossings = trace(mode)
        points = distinct_states(crossings)
        # A harness that silently found nothing would prove nothing: every
        # ledger state the mode reaches, and a database ahead of the anchor,
        # must be among the points it kills at.
        states = [point["state"] for point in points]
        for name, reached in REACHED[mode].items():
            self.assertTrue(any(reached(state) for state in states),
                            f"no kill point leaves the job {name}")
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            outcomes = list(pool.map(lambda point: kill_and_restart(mode, point), points))
        for point, problems in zip(points, outcomes):
            with self.subTest(label(point)):
                self.assertEqual(problems, [])

    def test_a_job_without_approval_survives_a_kill_at_every_boundary(self):
        self.exercise("direct")

    def test_an_approved_job_survives_a_kill_at_every_boundary(self):
        self.exercise("approval")

    def test_a_pending_job_that_expires_is_refused_once_whatever_the_kill(self):
        self.exercise("expired")


class RegrantTest(unittest.TestCase):
    """A waiting job with two live tokens is decided once, whichever token comes first."""

    def run_order(self, new_token_first):
        db = PostgresDatabase()
        try:
            database.migrate(db.owner_dsn)
            with tempfile.TemporaryDirectory() as directory:
                log_path = os.path.join(directory, "exec.log")
                service, connection = harness.build_service(
                    db.runtime_dsn, os.path.join(directory, "anchor.state"), log_path,
                    requires_approval=True, job_id=harness.CRASH_JOB_ID)
                try:
                    body = json.dumps({"text": harness.CRASH_TEXT}).encode()
                    waiting = service.entry.handle(
                        method="POST", path="/jobs", headers=harness.headers(), body=body)
                    self.assertEqual(waiting.body["status"], "PENDING_APPROVAL")
                    lost = service.approve(harness.CRASH_JOB_ID)
                    again = service.approve(harness.CRASH_JOB_ID)
                    self.assertNotEqual(lost, again)
                    first, second = (again, lost) if new_token_first else (lost, again)
                    won = harness.complete(service, harness.CRASH_JOB_ID, first)
                    refused = harness.complete(service, harness.CRASH_JOB_ID, second)
                    self.assertEqual((won.status, won.body["status"]), (202, "SUCCEEDED"))
                    self.assertEqual(refused.status, 409)
                    self.assertEqual(harness.executions(log_path, harness.CRASH_TEXT), 1)
                    records = service.chain.records
                    self.assertEqual(verify(records, service.head(), authority=service.audit,
                                            anchor=service.anchor), len(records))
                finally:
                    service.close()
                    connection.close()
        finally:
            db.close()

    def test_the_new_token_first_completes_the_job_and_the_lost_one_is_refused(self):
        self.run_order(new_token_first=True)

    def test_the_old_token_first_completes_the_job_and_the_new_one_is_refused(self):
        self.run_order(new_token_first=False)


if __name__ == "__main__":
    unittest.main()
