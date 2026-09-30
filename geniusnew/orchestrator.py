"""Admission, routing and dispatch — decisions this component does not confirm.

`docs/ROADMAP-V01.md` step 13 asks for an orchestrator that is deterministic,
fails closed, holds no shared mutable authority, and — the sentence that shapes
the module — "trifft Entscheidungen, er bestätigt sie nicht selbst".

This file reconciles two independent implementations of that step. The routing
model is the one from `feat/orchestrator`: the grant names the worker, so there
is nothing to choose and nothing to check afterwards. The admission state,
decision records and permit binding come from `feat/orchestrator-decisions`.

## What it decides, and what it must not

It decides whether a request is admissible, which configured worker the trusted
policy names for it, and that a dispatch happened — and records each. It hands
the signed result wire back **unjudged**: it holds no result key and never calls
`accept`, so it cannot declare its own job a success. That is step 14's role, and
`CONSTITUTION-V1-DRAFT.md` §8 requires it to stay a different one.

Symmetrically it cannot admit its own handoff. `DispatchPermit` is minted only
inside `gateway.py` and `WorkerRunner.execute` accepts nothing else, so default
deny before dispatch is structural here rather than conventional. What it can
check — and does — is that the permit it got back binds the exact wire it sent.

## Routing is a fact of the grant

A grant names the `worker_agent_id` that may run a subject's jobs, so routing is
a dictionary lookup on that id and never a choice between candidates. The
configured endpoint's tool must still be in the grant: a worker wired under the
right agent id but implementing another tool would otherwise reach a capability
the policy never granted. Both checks happen **before** the gateway is called,
so a misrouted job cannot burn a one-time approval on the way to being refused.

## Deterministic

No clock and no entropy — `now` is an argument. The registry is copied into an
immutable mapping at construction, so a caller cannot add workers, change
routing, or smuggle a different runner into the dispatch path afterwards. There
is no failover: a second attempt after a refusal is exactly the fallback that
turns default deny into default retry.

## The ledger is the one piece of state

A job id is burned when a permit exists and the work is about to run, not before:
a request refused by the gateway leaves nothing behind and can be retried under
its own id, while a dispatch that was reached stays burned even if it failed.
Releasing a burned id on failure would make the ledger a replay window rather
than a record.

Where it is burned is a `JobLedger` (gate B2). `python -m geniusnew serve` uses
the PostgreSQL ledger in `database.py`, so a restart or a second instance sees
every burned id; the process-local one below is kept for the demo and for tests
without a database, and it is bounded, because an unbounded set a caller can grow
is memory exhaustion with a paper trail. Either way the job is committed to
execution in the ledger before the worker sees the permit, and nothing here ever
runs a committed job again: after a crash its effect may or may not have
happened, and a second run is the one outcome that is certainly wrong.

## What a refusal carries

Denials use a closed set of reason codes with fixed sentences, like `results.py`
does, so a refusal cannot become a text channel out of the admission domain.
Every action recorded here is in the audit event vocabulary of `audit.py`
(`tests/test_orchestrator.py` checks that against it): a decision this component
cannot audit is a decision nobody can see.

Refusals from another role — the gateway's, the worker boundary's — are passed
through unchanged rather than relabelled as orchestrator decisions. Recording
another instance's refusal as one's own is the same error in the other direction.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from .contracts import ContractError, HandoffSigner, Policy, issue
from .gateway import DispatchPermit, Gateway
from .results import handoff_digest
from .workers import WorkerRunner

_INSTANCE_ID = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")

# Every action below is in the closed vocabulary of `audit.py`. HANDOFF_ADMITTED
# is deliberately absent: that is the gateway's decision, not this one's.
_ISSUED = "HANDOFF_ISSUED"
_REJECTED = "HANDOFF_REJECTED"
_DISPATCHED = "EXECUTION_DISPATCHED"
ACTIONS = frozenset({_ISSUED, _REJECTED, _DISPATCHED})

_SATISFIED = "POLICY_SATISFIED"

# Closed set, with one fixed sentence each. Nothing from a request, a payload or
# another component's exception is ever interpolated into these.
DENIALS = MappingProxyType({
    "POLICY_NOT_FOR_THIS_ORCHESTRATOR":
        "policy orchestrator_id does not name this orchestrator",
    "SUBJECT_NOT_AUTHORIZED": "subject is not authorized by the trusted policy",
    "WORKER_NOT_CONFIGURED": "trusted policy names an unconfigured worker",
    "TOOL_NOT_GRANTED": "configured worker tool is not granted by policy",
    "JOB_ID_REUSED": "job_id has already been dispatched",
    "JOB_LEDGER_FULL": "job ledger is full",
})

_MAX_JOB_ID_BYTES = 128
_MAX_JOBS = 100_000

# The same window `audit.py` accepts. A decision it cannot record is a decision
# nobody can see, so a timestamp outside this range is refused where it enters
# rather than at the point someone tries to audit it. A test pins this to the
# audit module's own bound.
_MAX_OCCURRED_AT = 4102444800  # 2100-01-01T00:00:00Z

# Marks "the runner raised something that is not a refusal" without carrying the
# exception out of the handler. See `_run`.
_EXECUTION_FAILED = object()


def _fail(message: str) -> None:
    raise ContractError(message)


def _instance_id(value: Any, field: str) -> str:
    if type(value) is not str or not _INSTANCE_ID.match(value):
        _fail(f"{field} must be a lowercase identifier of at most 63 characters")
    return value


@dataclass(frozen=True)
class Decision:
    """One decision by this component, shaped so `audit.py` can record it."""

    action: str
    decision: str
    reason_code: str
    occurred_at: int

    def __post_init__(self) -> None:
        # Typed before any membership test: an unhashable action would otherwise
        # raise a bare TypeError out of the frozenset lookup, and a caller that
        # handles ContractError would not catch it.
        for field in ("action", "decision", "reason_code"):
            if type(getattr(self, field)) is not str:
                _fail(f"{field} must be a string")
        if self.action not in ACTIONS:
            _fail("action is not an orchestrator action")
        if self.decision not in ("ALLOWED", "DENIED"):
            _fail("decision must be ALLOWED or DENIED")
        # The three fields have to agree. Independent membership checks accept
        # HANDOFF_REJECTED/ALLOWED/POLICY_SATISFIED, which is a lie in a shape
        # the audit layer would happily record.
        if self.decision == "DENIED":
            if self.action != _REJECTED:
                _fail("a denial must be recorded as HANDOFF_REJECTED")
            if self.reason_code not in DENIALS:
                _fail("a denial must carry a denial reason code")
        else:
            if self.action == _REJECTED:
                _fail("a rejection cannot be recorded as ALLOWED")
            if self.reason_code != _SATISFIED:
                _fail("an allowed decision must carry POLICY_SATISFIED")
        if type(self.occurred_at) is not int:
            _fail("occurred_at must be an integer")
        if not 1 <= self.occurred_at <= _MAX_OCCURRED_AT:
            _fail("occurred_at is outside the range the audit contract accepts")


class Denied(ContractError):
    """A refusal by this component, carrying the decision that gets audited.

    It is a `ContractError`, so every caller that already handles refusals keeps
    handling this one. The message is the fixed sentence for its reason code and
    nothing else.
    """

    def __init__(self, decision: Decision) -> None:
        super().__init__(f"{DENIALS[decision.reason_code]} ({decision.reason_code})")
        self.decision = decision


class DispatchAttempted(ContractError):
    """Execution was decided and reached, and then refused or failed.

    The job id is burned and the permit is consumed by then, so the attempt
    happened whatever came back. Without this the caller would hold no record of
    a security-relevant dispatch at all. The refusal's own sentence is preserved
    verbatim; only the decision is attached.
    """

    def __init__(self, message: str, *, decision: Decision) -> None:
        super().__init__(message)
        self.decision = decision


def _deny(reason_code: str, *, now: int) -> None:
    raise Denied(Decision(action=_REJECTED, decision="DENIED",
                          reason_code=reason_code, occurred_at=now))


@dataclass(frozen=True)
class Reservation:
    """What a ledger records when a job id is burned.

    Every field comes from the permit the gateway minted and the subject the
    caller already proved, never from the request. Checked here as well as at
    the database's start check: a row this refuses to write is a row the next
    start would refuse to read.
    """

    job_id: str
    subject: str
    handoff_sha256: str
    expires_at: int
    reserved_at: int

    def __post_init__(self) -> None:
        for field in ("job_id", "subject"):
            value = getattr(self, field)
            if type(value) is not str or not value:
                _fail(f"reservation {field} must be a non-empty string")
        if len(self.job_id.encode("utf-8", "surrogatepass")) > _MAX_JOB_ID_BYTES:
            _fail(f"reservation job_id must be at most {_MAX_JOB_ID_BYTES} bytes")
        if type(self.handoff_sha256) is not str or not _SHA256.match(self.handoff_sha256):
            _fail("reservation handoff_sha256 must be a lowercase SHA-256 digest")
        for field in ("expires_at", "reserved_at"):
            value = getattr(self, field)
            if type(value) is not int or not 1 <= value <= _MAX_OCCURRED_AT:
                _fail(f"reservation {field} is outside the range the audit contract accepts")
        # The gateway admits only an unexpired handoff, so a reservation at or
        # after its expiry would describe a dispatch that cannot have happened.
        if self.expires_at <= self.reserved_at:
            _fail("reservation must be made before its handoff expires")


class JobLedger(ABC):
    """Where burned job ids live. The orchestrator decides; the ledger remembers.

    `reserve` must be atomic on its own and return False when the id is already
    burned, including by another orchestrator that checked at the same moment.
    `commit_execution` moves exactly that reservation from RESERVED to
    EXECUTION_COMMITTED, and refuses when it cannot.
    """

    @abstractmethod
    def is_burned(self, job_id: str) -> bool:
        """Whether the id was ever reserved, in any state."""

    def dispatch_available(self, job_id: str, *, subject: str,
                           handoff_sha256: str | None) -> bool:
        """Whether this exact dispatch may reach the gateway."""
        return not self.is_burned(job_id)

    def reserve_admitted(self, reservation: Reservation, *,
                         approval_record_hash: str | None) -> bool:
        """Reserve once, or confirm the reservation made by durable consume."""
        return self.reserve(reservation)

    def is_full(self) -> bool:
        """Whether this ledger would refuse any new reservation."""
        return False

    @abstractmethod
    def reserve(self, reservation: Reservation) -> bool:
        """Burn the id, or return False if it is already burned."""

    @abstractmethod
    def commit_execution(self, reservation: Reservation, *, now: int) -> None:
        """Record that the worker is about to see the permit."""


class ProcessLocalJobLedger(JobLedger):
    """The v0.1 ledger: this process's memory, bounded, forgotten on restart.

    For the demo and for tests without a database. A restart or a second replica
    with this ledger dispatches the same unexpired handoff again, which is why
    `python -m geniusnew serve` never uses it.
    """

    def __init__(self) -> None:
        self._states: dict[str, str] = {}
        self._lock = Lock()

    def job_ids(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._states)

    def is_burned(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._states

    def is_full(self) -> bool:
        with self._lock:
            return len(self._states) >= _MAX_JOBS

    def reserve(self, reservation: Reservation) -> bool:
        with self._lock:
            if reservation.job_id in self._states:
                return False
            self._states[reservation.job_id] = "RESERVED"
            return True

    def commit_execution(self, reservation: Reservation, *, now: int) -> None:
        with self._lock:
            # The same rule the database's trigger enforces: only a reserved
            # job moves forward, and only once.
            if self._states.get(reservation.job_id) != "RESERVED":
                _fail("only a reserved job can be committed to execution")
            self._states[reservation.job_id] = "EXECUTION_COMMITTED"


@dataclass(frozen=True)
class WorkerEndpoint:
    """Trusted routing metadata plus the execution boundary for one worker."""

    worker_agent_id: str
    runner: WorkerRunner

    def __post_init__(self) -> None:
        _instance_id(self.worker_agent_id, "worker_agent_id")
        if not isinstance(self.runner, WorkerRunner):
            _fail("runner must be a WorkerRunner")

    @property
    def tool(self) -> str:
        return self.runner.tool

    def dispatch(self, permit: DispatchPermit, *, now: int) -> bytes:
        return self.runner.execute(permit, now=now)


@dataclass(frozen=True)
class Admission:
    """One issued handoff and the decision that issued it.

    `admit` returns both because the approval flow is necessarily two steps — a
    wire has to exist before an approval can be scoped to it — and a signed
    authorization artifact that produced no recordable decision is exactly the
    kind of silence §7 exists to prevent.
    """

    wire: bytes
    decision: Decision


@dataclass(frozen=True)
class Dispatch:
    """What one dispatched job produced — evidence, not a verdict.

    `result_wire` is exactly what the worker signed; this component has not
    checked it and cannot. `handoff_wire` travels with it so the independent
    result verifier of step 14 can revalidate the contract for itself instead of
    trusting an object handed over by the instance that requested the work.
    """

    job_id: str
    worker_agent_id: str
    handoff_sha256: str
    handoff_wire: bytes
    result_wire: bytes
    decisions: tuple[Decision, ...]
    # Carried, not used here. An approval-bound wire keeps `PENDING_APPROVAL`
    # for ever, so the result verifier cannot tell an approved job from an
    # unapproved one and refuses without the gateway's receipt. Dropping it
    # here made those two components impossible to wire together — found by
    # wiring them, which is what step 17 is for.
    approval_record_hash: str | None = None


class Orchestrator:
    """Admission, routing and dispatch for one policy-named orchestrator."""

    def __init__(self, *, orchestrator_id: str, signer: HandoffSigner,
                 gateway: Gateway, workers: Iterable[WorkerEndpoint],
                 on_admitted: Callable[[DispatchPermit], None] | None = None,
                 job_ledger: JobLedger | None = None) -> None:
        self._orchestrator_id = _instance_id(orchestrator_id, "orchestrator_id")
        # The only component that holds the handoff signing key. The gateway
        # holds the public half, so it can check what this issues but not issue.
        if not isinstance(signer, HandoffSigner):
            _fail("signer must be a HandoffSigner")
        if not isinstance(gateway, Gateway):
            _fail("gateway must be a Gateway")
        self._signer = signer
        self._gateway = gateway
        # Handed the permit the moment it exists, before anything can run or be
        # refused on this side. Admission is the gateway's decision, so this
        # component does not record it; it only passes on the evidence the
        # gateway minted, and whoever receives it reads every field from there.
        if on_admitted is not None and not callable(on_admitted):
            _fail("on_admitted must be callable")
        self._on_admitted = on_admitted

        # Checked on the type, not by catching TypeError from `tuple()`: a
        # storage object whose iterator raises TypeError would otherwise be
        # reported as a configuration error. `audit_chain.py` had this exact bug.
        kind = type(workers)
        if not hasattr(kind, "__iter__") and not hasattr(kind, "__getitem__"):
            _fail("workers must be an iterable of WorkerEndpoint")
        endpoints = tuple(workers)
        if not endpoints:
            _fail("workers must not be empty")
        if not all(isinstance(endpoint, WorkerEndpoint) for endpoint in endpoints):
            _fail("workers must contain only WorkerEndpoint values")
        ids = tuple(endpoint.worker_agent_id for endpoint in endpoints)
        if len(ids) != len(set(ids)):
            _fail("worker_agent_id values must be unique")
        self._workers: Mapping[str, WorkerEndpoint] = MappingProxyType({
            endpoint.worker_agent_id: endpoint for endpoint in endpoints
        })
        # Process-local unless the caller hands over a durable ledger; the
        # server entry always does (`__main__.py`).
        if job_ledger is None:
            job_ledger = ProcessLocalJobLedger()
        if not isinstance(job_ledger, JobLedger):
            _fail("job_ledger must be a JobLedger")
        self._ledger = job_ledger
        # Orders this process's check-then-reserve. Another instance on the
        # same database is ordered by the ledger's own atomic reservation.
        self._lock = Lock()

    @property
    def orchestrator_id(self) -> str:
        return self._orchestrator_id

    @property
    def worker_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._workers))

    def route(self, *, subject: str, policy: Policy, now: int) -> WorkerEndpoint:
        """Decide which configured worker the trusted policy names.

        Pure: same subject and policy, same answer, whenever it is called and
        whatever order the endpoints were configured in.
        """
        self._now(now)
        trusted = self._trusted(policy, now=now)
        try:
            grant = trusted.grant_for(subject)
        except ContractError:
            # A policy lookup is a pure function, so its refusal is this
            # component's own admission decision and is recorded as one.
            _deny("SUBJECT_NOT_AUTHORIZED", now=now)
        endpoint = self._workers.get(grant.worker_agent_id)
        if endpoint is None:
            _deny("WORKER_NOT_CONFIGURED", now=now)
        # A worker wired under the right agent id but implementing another tool
        # would reach a capability the grant never carried.
        if endpoint.tool not in grant.tools:
            _deny("TOOL_NOT_GRANTED", now=now)
        return endpoint

    def admit(self, request: Any, *, subject: str, job_id: str, policy: Policy,
              now: int) -> Admission:
        """Issue one handoff wire from trusted server-side facts, and record it."""
        self._now(now)
        trusted = self._trusted(policy, now=now)
        self._job_id(job_id)
        wire = issue(request, subject=subject, job_id=job_id, policy=trusted,
                     signer=self._signer, now=now)
        return Admission(wire=wire, decision=Decision(
            action=_ISSUED, decision="ALLOWED", reason_code=_SATISFIED,
            occurred_at=now))

    def dispatch(self, wire: Any, *, subject: str, job_id: str, policy: Policy,
                 now: int, approval_token: bytes | None = None) -> Dispatch:
        """Route one admitted wire, let the gateway confirm it, and run it once."""
        self._job_id(job_id)
        endpoint = self.route(subject=subject, policy=policy, now=now)
        # Asked before the gateway is called, because admission there consumes a
        # one-time approval. Finding out afterwards that the id was unavailable
        # spends an approval on a job that then does no work at all. The atomic
        # reservation below stays the authority; this only refuses early what it
        # would refuse anyway.
        digest = hashlib.sha256(wire).hexdigest() if type(wire) is bytes else None
        self._available(job_id, now=now, subject=subject, handoff_sha256=digest)

        # The gateway revalidates these bytes independently and mints the only
        # capability the worker boundary accepts. Its refusals are its own and
        # travel unchanged.
        permit = self._gateway.admit(wire, subject=subject, job_id=job_id,
                                     policy=policy, now=now,
                                     approval_token=approval_token)
        if not isinstance(permit, DispatchPermit):
            _fail("gateway did not return a dispatch permit")
        # The permit has to be for the job that was sent. A permit for some other
        # handoff would have the worker run a contract this orchestrator never
        # submitted, while the wire reported below still described the one it did.
        if not hmac.compare_digest(permit.handoff.to_bytes(), wire):
            _fail("gateway admitted a different handoff than the one submitted")
        # Before the reservation and before the runner. The permit exists and a
        # one-time approval may be spent, so the in-memory admission precedes
        # a lost race for the job id or an execution failure. If it cannot be
        # recorded, nothing runs. Durable evidence needs persistent chain storage.
        if self._on_admitted is not None:
            self._on_admitted(permit)

        # Burned here: a permit exists and the work is about to run. Earlier, and
        # a job the gateway refused would lose its id for good; later, and two
        # callers could each hold a valid permit for the same job.
        reservation = Reservation(job_id=job_id, subject=subject,
                                  handoff_sha256=handoff_digest(permit.handoff),
                                  expires_at=permit.handoff.expires_at,
                                  reserved_at=now)
        self._reserve(reservation, now=now, approval_record_hash=permit.approval_record_hash)
        # Made before the runner is called, not after it returns: the decision
        # to dispatch is what this component decided, and it stands whether or
        # not the execution then succeeded.
        decided = Decision(action=_DISPATCHED, decision="ALLOWED",
                           reason_code=_SATISFIED, occurred_at=now)
        # Recorded before the worker sees the permit. If this fails, nothing
        # runs and the id stays burned in RESERVED; once it succeeds, a crash
        # leaves the effect unknown and the id is never dispatched again.
        self._ledger.commit_execution(reservation, now=now)
        try:
            result_wire = self._run(endpoint, permit, now=now)
        except ContractError as refusal:
            raise DispatchAttempted(str(refusal), decision=decided) from None
        return Dispatch(
            job_id=job_id,
            worker_agent_id=endpoint.worker_agent_id,
            handoff_sha256=reservation.handoff_sha256,
            handoff_wire=permit.handoff.to_bytes(),
            result_wire=result_wire,
            decisions=(decided,),
            approval_record_hash=permit.approval_record_hash,
        )

    def submit(self, request: Any, *, subject: str, job_id: str, policy: Policy,
               now: int, approval_token: bytes | None = None) -> Dispatch:
        """Admit and dispatch one job — the whole path a caller normally wants."""
        # Routed before anything is issued, so a job that cannot run produces no
        # signed artifact at all.
        self.route(subject=subject, policy=policy, now=now)
        admission = self.admit(request, subject=subject, job_id=job_id,
                               policy=policy, now=now)
        dispatched = self.dispatch(admission.wire, subject=subject, job_id=job_id,
                                   policy=policy, now=now,
                                   approval_token=approval_token)
        return Dispatch(
            job_id=dispatched.job_id,
            worker_agent_id=dispatched.worker_agent_id,
            handoff_sha256=dispatched.handoff_sha256,
            handoff_wire=dispatched.handoff_wire,
            result_wire=dispatched.result_wire,
            decisions=(admission.decision,) + dispatched.decisions,
            approval_record_hash=dispatched.approval_record_hash,
        )

    def _trusted(self, policy: Any, *, now: int) -> Policy:
        if not isinstance(policy, Policy):
            _fail("policy is invalid")
        if policy.orchestrator_id != self._orchestrator_id:
            _deny("POLICY_NOT_FOR_THIS_ORCHESTRATOR", now=now)
        return policy

    def _now(self, now: Any) -> int:
        if type(now) is not int:
            _fail("now must be an integer")
        # Bounded here rather than only inside Decision, so that a denial is
        # always constructible: a refusal that cannot be recorded because of the
        # clock it was given would be a refusal nobody can audit.
        if not 1 <= now <= _MAX_OCCURRED_AT:
            _fail("now is outside the range the audit contract accepts")
        return now

    def _job_id(self, job_id: Any) -> str:
        if type(job_id) is not str or not job_id:
            _fail("job_id must be a non-empty string")
        # Bounded before the ledger can hold it: `contracts._string` does not
        # limit length, and the ledger keeps what it is given.
        if len(job_id.encode("utf-8", "surrogatepass")) > _MAX_JOB_ID_BYTES:
            _fail(f"job_id must be at most {_MAX_JOB_ID_BYTES} bytes")
        return job_id

    def _available(self, job_id: str, *, now: int, subject: str,
                   handoff_sha256: str | None = None) -> None:
        """Refuse an unavailable dispatch before anything is spent."""
        with self._lock:
            if not self._ledger.dispatch_available(
                    job_id, subject=subject, handoff_sha256=handoff_sha256):
                _deny("JOB_ID_REUSED", now=now)
            if self._ledger.is_full():
                _deny("JOB_LEDGER_FULL", now=now)

    def _reserve(self, reservation: Reservation, *, now: int,
                 approval_record_hash: str | None = None) -> None:
        """Burn one job id, or confirm the gateway's durable reservation."""
        with self._lock:
            # Decides the race this lock cannot see: another orchestrator on
            # the same database may have burned the id since the check above.
            if not self._ledger.reserve_admitted(
                    reservation, approval_record_hash=approval_record_hash):
                _deny("JOB_ID_REUSED", now=now)

    def _run(self, endpoint: WorkerEndpoint, permit: DispatchPermit, *,
             now: int) -> bytes:
        """Call the routed worker once. No failover, no second attempt."""
        try:
            result_wire = endpoint.dispatch(permit, now=now)
        except ContractError:
            raise
        except Exception:  # noqa: BLE001 - a runner is not trusted to be tidy
            result_wire = _EXECUTION_FAILED
        # Deliberately not `str(exc)`: an execution-side exception text must not
        # become a channel out of that domain, the same reason `workers.py`
        # keeps reason_code closed. And raised outside the handler, because
        # inside one Python attaches the original as `__context__` even after
        # `from None` — that only sets `__suppress_context__`, which hides the
        # text from the traceback formatter while leaving it one attribute away
        # for anything that walks the chain. Sanitizing the message alone is not
        # sanitizing. The price is the lost stack trace, which is the right
        # trade when the alternative is worker text crossing the boundary.
        if result_wire is _EXECUTION_FAILED:
            _fail("dispatch failed")
        if type(result_wire) is not bytes or not result_wire:
            _fail("dispatch must return the signed result wire")
        return result_wire
