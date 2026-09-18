"""Admission, assignment and dispatch — decisions this component does not confirm.

`docs/ROADMAP-V01.md` step 13 asks for an orchestrator that is deterministic,
fails closed, holds no shared mutable authority, and — the sentence that shapes
the whole module — "trifft Entscheidungen, er bestätigt sie nicht selbst".

## What it decides, and what it must not

It decides three things and records each one: whether a request is admissible at
all, which worker the policy names for it, and that a dispatch happened. It then
hands the signed result wire back **unjudged**. It never calls `accept`, holds no
`WorkerAuthority`, and so has no way to declare its own job a success. That is
step 14's role, and `CONSTITUTION-V1-DRAFT.md` §8 requires it to stay a different
one.

Symmetrically, it cannot admit its own handoff. `DispatchPermit` can only be
minted inside `gateway.py`, and `WorkerRunner.execute` accepts nothing else, so
the orchestrator must go through the independent gateway to reach a worker —
default deny before dispatch is structural here, not a convention.

## Assignment is a decision, not a lookup

A grant names both the tools a subject may use **and** the `worker_agent_id` that
may run them. Nothing downstream checks the second half: the worker boundary
verifies that its tool is in the handoff's tool list, and every tool in the grant
passes that. So an orchestrator that dispatched `summarize` to some other
registered worker would be sending the job to an agent the policy never named,
and every layer after it would agree. This module refuses that
(`WORKER_NOT_GRANTED`), because it is the only place that can.

## Deterministic

The assignment is a function of the request and the policy alone: a dict keyed by
tool, so registration order cannot change it; one worker per tool, so there is
never a choice to make; and no failover, because a second attempt after a refusal
is precisely the fallback that turns default deny into default retry. The module
reads no clock — `now` is an argument — and draws no randomness.

## The ledger is the one piece of state

A job id may be submitted once. A retry needs a new id: releasing a burned one on
failure would make the ledger a replay window rather than a record. It is bounded,
because an unbounded set that a caller can grow is a memory exhaustion
with a paper trail.

## What a refusal carries

Denials use a closed set of reason codes, like `results.py` does, so that a
refusal cannot become a text channel out of the admission domain. Every action
recorded here is in the audit event vocabulary of `audit.py`
(`tests/test_orchestrator.py` checks that against it): a decision this component
cannot audit is a decision nobody can see.

Refusals from another role — the gateway's, the worker boundary's — are passed
through unchanged rather than relabelled as orchestrator decisions. Recording
another instance's refusal as its own is the same error in the other direction.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Iterable, Mapping

from .contracts import ContractError, Policy, issue
from .gateway import DispatchPermit, Gateway
from .results import handoff_digest

_INSTANCE_ID = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")

# Every action below is in the closed vocabulary of `audit.py`. HANDOFF_ADMITTED
# is deliberately absent: that is the gateway's decision, not this one's.
_ISSUED = "HANDOFF_ISSUED"
_REJECTED = "HANDOFF_REJECTED"
_DISPATCHED = "EXECUTION_DISPATCHED"
ACTIONS = frozenset({_ISSUED, _REJECTED, _DISPATCHED})

_SATISFIED = "POLICY_SATISFIED"
DENIAL_REASONS = frozenset({
    "POLICY_NOT_FOR_THIS_ORCHESTRATOR",
    "SUBJECT_NOT_AUTHORIZED",
    "TOOL_NOT_IN_POLICY",
    "TOOL_NOT_GRANTED",
    "NO_WORKER_FOR_TOOL",
    "WORKER_NOT_GRANTED",
    "JOB_ID_REUSED",
    "JOB_LEDGER_FULL",
})

_MAX_JOB_ID_BYTES = 128
_MAX_JOBS = 100_000


def _fail(message: str) -> None:
    raise ContractError(message)


@dataclass(frozen=True)
class Decision:
    """One admission decision, in a shape `audit.py` can turn into an event."""

    action: str
    decision: str
    reason_code: str
    occurred_at: int

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            _fail("action is not an orchestrator action")
        if self.decision not in ("ALLOWED", "DENIED"):
            _fail("decision must be ALLOWED or DENIED")
        if self.reason_code != _SATISFIED and self.reason_code not in DENIAL_REASONS:
            _fail("reason_code is not an orchestrator reason code")
        if type(self.occurred_at) is not int:
            _fail("occurred_at must be an integer")


class Denied(ContractError):
    """A refusal by this component, carrying the decision that can be audited.

    It is a `ContractError`, so every caller that already handles refusals keeps
    handling this one. The reason code is from the closed set above; the message
    is built from it and nothing else.
    """

    def __init__(self, decision: Decision) -> None:
        super().__init__(f"{decision.action} denied: {decision.reason_code}")
        self.decision = decision


def _deny(reason_code: str, *, action: str, now: int) -> None:
    raise Denied(Decision(action=action, decision="DENIED",
                          reason_code=reason_code, occurred_at=now))


@dataclass(frozen=True)
class WorkerEntry:
    """One registered worker: the agent identity, its tool, and the way in."""

    worker_id: str
    tool: str
    dispatch: Callable[..., bytes]

    def __post_init__(self) -> None:
        if type(self.worker_id) is not str or not _INSTANCE_ID.match(self.worker_id):
            _fail("worker_id must be a lowercase identifier of at most 63 characters")
        if type(self.tool) is not str or not self.tool:
            _fail("tool must be a non-empty string")
        if not callable(self.dispatch):
            _fail("dispatch must be callable")


@dataclass(frozen=True)
class Dispatch:
    """What one submitted job produced — evidence, not a verdict.

    `result_wire` is exactly what the worker signed. This component has not
    checked it and cannot: `handoff_wire` is included so the independent result
    verifier of step 14 can revalidate the handoff for itself rather than trust
    an object handed over by the instance that requested the work.
    """

    job_id: str
    tool: str
    worker_id: str
    handoff_sha256: str
    handoff_wire: bytes
    result_wire: bytes
    decisions: tuple[Decision, ...]


class Orchestrator:
    """Admission, assignment and dispatch for one policy-named orchestrator."""

    def __init__(self, *, orchestrator_id: str, integrity_key: bytes,
                 gateway: Gateway, workers: Iterable[WorkerEntry]) -> None:
        if type(orchestrator_id) is not str or not _INSTANCE_ID.match(orchestrator_id):
            _fail("orchestrator_id must be a lowercase identifier of at most 63 characters")
        if type(integrity_key) is not bytes or len(integrity_key) < 32:
            _fail("integrity_key must be at least 32 bytes")
        if not isinstance(gateway, Gateway):
            _fail("gateway must be a Gateway")
        registry: dict[str, WorkerEntry] = {}
        for entry in tuple(workers):
            if not isinstance(entry, WorkerEntry):
                _fail("workers must be WorkerEntry values")
            # One worker per tool. Two would make the assignment a choice, and a
            # choice made here is a choice no test can pin down.
            if entry.tool in registry:
                _fail("two workers registered for the same tool")
            registry[entry.tool] = entry
        if not registry:
            _fail("at least one worker must be registered")
        self._orchestrator_id = orchestrator_id
        self._integrity_key = integrity_key
        self._gateway = gateway
        self._workers: Mapping[str, WorkerEntry] = registry
        self._jobs: set[str] = set()
        self._lock = Lock()

    @property
    def orchestrator_id(self) -> str:
        return self._orchestrator_id

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(sorted(self._workers))

    def assign(self, *, subject: str, tool: str, policy: Policy, now: int) -> WorkerEntry:
        """Decide which registered worker the policy names for this request.

        Pure: same request and policy, same answer, whatever the registration
        order was and whenever it is called.
        """
        if type(now) is not int:
            _fail("now must be an integer")
        if not isinstance(policy, Policy):
            _fail("policy is invalid")
        if type(tool) is not str or not tool:
            _fail("tool must be a non-empty string")
        if policy.orchestrator_id != self._orchestrator_id:
            _deny("POLICY_NOT_FOR_THIS_ORCHESTRATOR", action=_REJECTED, now=now)
        try:
            grant = policy.grant_for(subject)
        except ContractError:
            # The lookup is a pure policy function, so its refusal is this
            # component's own admission decision and is recorded as one.
            _deny("SUBJECT_NOT_AUTHORIZED", action=_REJECTED, now=now)
        if tool not in policy.allowed_tools:
            _deny("TOOL_NOT_IN_POLICY", action=_REJECTED, now=now)
        if tool not in grant.tools:
            _deny("TOOL_NOT_GRANTED", action=_REJECTED, now=now)
        entry = self._workers.get(tool)
        if entry is None:
            _deny("NO_WORKER_FOR_TOOL", action=_REJECTED, now=now)
        # The grant names the agent, not just the capability. Nothing downstream
        # rechecks this, so failing to check it here means never checking it.
        if entry.worker_id != grant.worker_agent_id:
            _deny("WORKER_NOT_GRANTED", action=_REJECTED, now=now)
        return entry

    def submit(self, request: Any, *, subject: str, job_id: str, tool: str,
               policy: Policy, now: int,
               approval_token: bytes | None = None) -> Dispatch:
        """Admit one job, assign its worker, and dispatch it through the gateway."""
        if type(job_id) is not str or not job_id:
            _fail("job_id must be a non-empty string")
        if len(job_id.encode("utf-8", "surrogatepass")) > _MAX_JOB_ID_BYTES:
            _fail(f"job_id must be at most {_MAX_JOB_ID_BYTES} bytes")
        entry = self.assign(subject=subject, tool=tool, policy=policy, now=now)
        self._reserve(job_id, now=now)

        wire = issue(request, subject=subject, job_id=job_id, policy=policy,
                     integrity_key=self._integrity_key, now=now)
        decisions = [Decision(action=_ISSUED, decision="ALLOWED",
                              reason_code=_SATISFIED, occurred_at=now)]

        # The gateway revalidates these bytes on its own and mints the only
        # capability the worker boundary accepts. Its refusals are its own and
        # travel unchanged.
        permit = self._gateway.admit(wire, subject=subject, job_id=job_id,
                                     policy=policy, now=now,
                                     approval_token=approval_token)
        if not isinstance(permit, DispatchPermit):
            _fail("gateway did not return a dispatch permit")
        # The permit has to be for the job that was sent. A gateway handing back
        # a permit for some other handoff would have the worker run a job this
        # orchestrator never issued, while `handoff_wire` below still described
        # the one it did — the result verifier would then be checking a result
        # against the wrong contract.
        if not hmac.compare_digest(permit.handoff.to_bytes(), wire):
            _fail("gateway admitted a different handoff than the one issued")

        result_wire = self._dispatch(entry, permit, now=now)
        decisions.append(Decision(action=_DISPATCHED, decision="ALLOWED",
                                  reason_code=_SATISFIED, occurred_at=now))
        return Dispatch(
            job_id=job_id,
            tool=tool,
            worker_id=entry.worker_id,
            handoff_sha256=handoff_digest(permit.handoff),
            handoff_wire=wire,
            result_wire=result_wire,
            decisions=tuple(decisions),
        )

    def _reserve(self, job_id: str, *, now: int) -> None:
        """Burn one job id, atomically, and keep it burned."""
        with self._lock:
            if job_id in self._jobs:
                _deny("JOB_ID_REUSED", action=_REJECTED, now=now)
            if len(self._jobs) >= _MAX_JOBS:
                _deny("JOB_LEDGER_FULL", action=_REJECTED, now=now)
            self._jobs.add(job_id)

    def _dispatch(self, entry: WorkerEntry, permit: DispatchPermit, *,
                  now: int) -> bytes:
        """Call the assigned worker once. No failover, no second attempt."""
        try:
            result_wire = entry.dispatch(permit, now=now)
        except ContractError:
            raise
        except Exception as exc:  # noqa: BLE001 - a dispatcher is not trusted to be tidy
            # Deliberately not `str(exc)`: a dispatcher's exception text must not
            # become a channel out of the execution domain, the same reason
            # `workers.py` keeps reason_code closed.
            raise ContractError("dispatch failed") from exc
        if type(result_wire) is not bytes or not result_wire:
            _fail("dispatch must return the signed result wire")
        return result_wire
