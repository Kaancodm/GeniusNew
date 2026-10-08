"""Gate B7 harness: one job against the durable service, in a process that dies on cue.

Not a test module. `test_b7_crash_recovery.py` runs this file as a child and kills
it with SIGKILL at a chosen boundary, then restarts the service on what was left
behind. Nothing here changes production code: the boundaries are observed by
wrapping the seams the durable path already goes through in this one process.

A boundary is any point where the process could die between two durable effects:
around each audit append, around each anchor acknowledgement, inside the
transaction after its mutation, and either side of the worker. A dry run
(`GENIUSNEW_CRASH_AT=0`) records every crossing together with the state a
second connection can see at that moment and whether the operator already holds
the approval token. That committed state is exactly what
a crash leaves behind, so the test needs one kill per distinct state, and
a boundary added to the flow later is found without editing a list here.

Environment: `GENIUSNEW_CRASH_DSN` (runtime role), `..._ANCHOR_STATE`,
`..._EXEC_LOG`, `..._MODE` (`direct`, `approval` or `expired`), `..._AT` (crossing number to
die at, 0 to run through and report).
"""

from __future__ import annotations

import json
import os
import signal
import sys

import psycopg

from geniusnew import audit_store, database, orchestrator, wiring, workers
from geniusnew.contracts import ContractError, Grant, Policy
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority

# Zero-entropy and self-describing, like every other test constant here.
ROOT_SECRET = b"NOT-A-SECRET-" + b"0" * 24
API_KEY = b"API-KEY-CANARY-MUST-NOT-BE-DISCLOSED"
APPROVER_KEY = b"APPROVER-KEY-CANARY-B7-ONLY"
SUBJECT = "subject-demo"
APPROVER_SUBJECT = "subject-approver"
NOW = 1_700_000_000
EXPIRED_AT = NOW + 61  # the handoff's lifetime is 60 seconds
CRASH_JOB_ID = "job-" + "b7" * 16
FRESH_JOB_ID = "job-" + "f0" * 16
CRASH_TEXT = "b7 crash job"
FRESH_TEXT = "b7 fresh job"
# Information that left the process. Two crashes in the same committed state are
# still different if the operator did or did not receive the approval token.
DELIVERED = {"token": 0}


class Clock:
    """A clock the scenario can move, so a pending job can run out."""

    def __init__(self, now: int = NOW) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def policy(*, requires_approval: bool) -> Policy:
    grant = Grant(SUBJECT, "user-demo", "worker-demo", "basic", ("summarize",),
                  "isolated", requires_approval)
    return Policy("policy-v1", "orchestrator-1", 60, ("summarize",), ("isolated",),
                  (grant,))


class CountingSummarizer(workers.DeterministicSummarizer):
    """Appends one line per execution. The kernel keeps it when this process dies."""

    def __init__(self, log_path: str) -> None:
        self._log_path = log_path

    def run(self, payload):
        with open(self._log_path, "a") as log:
            log.write(payload["text"] + "\n")
        return super().run(payload)


def executions(log_path: str, text: str) -> int:
    try:
        with open(log_path) as log:
            return sum(1 for line in log if line.rstrip("\n") == text)
    except FileNotFoundError:
        return 0


def build_service(dsn: str, anchor_state: str, log_path: str, *,
                  requires_approval: bool, job_id: str, clock=None):
    """The durable composition: PostgreSQL ledgers and chain, an anchor with state."""
    connection = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
    try:
        keys = derive_keys(ROOT_SECRET)
        authority = WorkerAuthority(result_key=keys.result_key,
                                    integrity_key=keys.integrity_key)
        service = wiring.build(
            root_secret=ROOT_SECRET, policy=policy(requires_approval=requires_approval),
            api_keys={API_KEY: SUBJECT, APPROVER_KEY: APPROVER_SUBJECT},
            approvers={APPROVER_SUBJECT: "user-approver"},
            workers=(CountingSummarizer(log_path),),
            clock=clock or Clock(), job_ids=lambda: job_id, anchor_state=anchor_state,
            job_ledger=database.PostgresJobLedger(connection),
            acceptance_ledger=database.PostgresAcceptanceLedger(connection),
            database_connection=connection,
            audit_chain_factory=lambda audit: audit_store.PostgresAuditChain(
                connection, authority=audit),
            runner_factory=lambda worker: workers.WorkerRunner(worker, authority=authority))
    except BaseException:
        connection.close()
        raise
    return service, connection


def headers(**extra: str) -> dict[str, str]:
    return {"Content-Type": "application/json",
            "Authorization": "Bearer " + API_KEY.decode(), **extra}


def run_job(service, *, mode: str, text: str, announce=lambda event: None, clock=None):
    """Drive one job the way a client and, if needed, an operator would.

    `direct` needs no approval. `approval` is approved and completed. `expired`
    lets the pending job run out before the operator reaches it, which makes the
    service refuse it durably. Returns the responses in order.
    """
    body = json.dumps({"text": text}).encode()
    submitted = service.entry.handle(method="POST", path="/jobs", headers=headers(),
                                     body=body)
    if mode == "direct":
        return [submitted]
    job_id = submitted.body["job_id"]
    if mode == "expired":
        clock.now = EXPIRED_AT
        try:
            service.approve(job_id, approver_subject=APPROVER_SUBJECT)
        except ContractError:
            pass
        return [submitted]
    token = service.approve(job_id, approver_subject=APPROVER_SUBJECT)
    announce({"event": "token", "job_id": job_id, "token": token.hex()})
    return [submitted, complete(service, job_id, token)]


def complete(service, job_id: str, token: bytes):
    return service.entry.handle(
        method="POST", path=f"/jobs/{job_id}/approve",
        headers=headers(**{"X-Approval-Token": token.hex()}), body=b"{}")


class Crossings:
    """Counts boundary crossings and dies at the chosen one."""

    def __init__(self, target: int, observe) -> None:
        self.count = 0
        self.target = target
        self.observe = observe
        self.log: list[dict] = []

    def cross(self, kind: str, phase: str, action: str | None = None) -> dict:
        self.count += 1
        entry = {"n": self.count, "kind": kind, "phase": phase, "action": action}
        if self.observe is not None:
            entry["state"] = self.observe()
        self.log.append(entry)
        if self.count == self.target:
            os.kill(os.getpid(), signal.SIGKILL)
        return entry


def observer(dsn: str, log_path: str, anchor):
    """What a second connection sees: committed rows only, never this transaction."""
    connection = psycopg.connect(dsn, autocommit=True, connect_timeout=5)

    def observe() -> list:
        row = connection.execute(
            "SELECT (SELECT count(*) FROM audit_chain), (SELECT count(*) FROM audit_heads), "
            "(SELECT coalesce(string_agg(state, ',' ORDER BY job_id), '') FROM job_ledger), "
            "(SELECT count(*) FROM approval_records), (SELECT count(*) FROM pending_jobs), "
            "(SELECT count(*) FROM acceptance_ledger)").fetchone()
        return [*row, anchor.committed[0], executions(log_path, CRASH_TEXT),
                DELIVERED["token"]]

    return observe, connection


def install(crossings: Crossings, announce) -> None:
    """Wrap the seams after startup, so only the job's own crossings are counted."""
    atomic = wiring._AnchoredAudit.atomic
    acknowledge = wiring._AnchoredAudit._ack_locked
    append = audit_store.PostgresAuditChain.append
    dispatch = orchestrator.Orchestrator.dispatch
    execute = workers.WorkerRunner.execute

    def wrapped_atomic(self, mutate, event):
        entries = [crossings.cross("atomic", "before")]

        def wrapped_mutate(transaction):
            result = mutate(transaction)
            entries.append(crossings.cross("atomic", "mutated"))
            return result

        def wrapped_event(result):
            built = event(result)
            for entry in entries:
                entry["action"] = built.action
            return built

        return atomic(self, wrapped_mutate, wrapped_event)

    def wrapped_append(self, event, *, transaction=None):
        where = "in transaction" if transaction is not None else "committed"
        crossings.cross("chain.append", "before " + where, event.action)
        record = append(self, event, transaction=transaction)
        crossings.cross("chain.append", "after " + where, event.action)
        return record

    def wrapped_acknowledge(self):
        crossings.cross("anchor.ack", "before")
        head = acknowledge(self)
        crossings.cross("anchor.ack", "after")
        return head

    def wrapped_dispatch(self, wire, **arguments):
        announce({"event": "dispatch", "wire": wire.hex()})
        return dispatch(self, wire, **arguments)

    def wrapped_execute(self, permit, *, now):
        crossings.cross("worker", "before")
        result = execute(self, permit, now=now)
        announce({"event": "result", "wire": result.hex()})
        crossings.cross("worker", "after")
        return result

    wiring._AnchoredAudit.atomic = wrapped_atomic
    wiring._AnchoredAudit._ack_locked = wrapped_acknowledge
    audit_store.PostgresAuditChain.append = wrapped_append
    orchestrator.Orchestrator.dispatch = wrapped_dispatch
    workers.WorkerRunner.execute = wrapped_execute


def main() -> int:
    environment = os.environ
    mode = environment["GENIUSNEW_CRASH_MODE"]
    clock = Clock()
    target = int(environment["GENIUSNEW_CRASH_AT"])
    log_path = environment["GENIUSNEW_CRASH_EXEC_LOG"]
    dsn = environment["GENIUSNEW_CRASH_DSN"]

    def announce(event: dict) -> None:
        if event["event"] == "token":
            DELIVERED["token"] = 1
        print(json.dumps(event), flush=True)

    service, connection = build_service(
        dsn, environment["GENIUSNEW_CRASH_ANCHOR_STATE"], log_path,
        requires_approval=mode != "direct", job_id=CRASH_JOB_ID, clock=clock)
    observe, watcher = (None, None)
    if target == 0:
        observe, watcher = observer(dsn, log_path, service.anchor)
    crossings = Crossings(target, observe)
    try:
        install(crossings, announce)
        announce({"event": "ready", "anchor_pid": service.anchor.pid})
        run_job(service, mode=mode, text=CRASH_TEXT, announce=announce, clock=clock)
        announce({"event": "done", "crossings": crossings.log})
    finally:
        service.close()
        connection.close()
        if watcher is not None:
            watcher.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
