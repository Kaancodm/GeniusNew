"""The composition root: where the independent instances are put together.

`docs/ROADMAP-V01.md` step 17 asks for the wiring plus an end-to-end test that
walks the whole path, checks the result signature and verifies the audit chain
against the held head. This is the wiring. It is the only file that knows all
the parts exist, which is deliberate: every module below it is written as though
it will be handed inputs by someone it does not trust, and this is the someone.

## What a composition root owes the things it composes

Three things, and it is worth being explicit because getting them wrong here
would quietly undo the separations the rest of the repository spends its effort
on.

**Keys are derived once and handed out by role.** `keys.derive_keys` produces
the handoff, result and audit keys from one root secret under distinct labels,
and each instance is given only its own. Nothing here reaches into another
component for a key it did not receive — that reach-through was a real finding
on the orchestrator, and this file is where it would come back.

**Policy comes from here, not from a component.** The orchestrator and the
gateway both receive the same `Policy` object from this root. Step 13 records
the open point: the gateway revalidating a wire against a policy handed to it by
the instance whose decision it is checking is not full independence. What this
root fixes is the *source* — neither component obtains the policy from the other
— and what it does not fix is that they are still the same object in one
process. A deployment splits them; this makes the split a configuration change
rather than a rewrite.

**The clock is real.** Every module takes `now` as an argument and reads no
clock, which is what makes them testable. Somebody has to actually look at one,
and it is this file. The TTL control points are only as honest as this call.

## The order is the argument

A request arrives, the entrance turns a key into a subject, the orchestrator
admits and routes it, the gateway revalidates the wire it was handed and mints
the one capability the worker boundary accepts, the worker runs behind that
boundary, the verifier takes the result without having asked for it, and the
audit role records what each of them decided. No step takes another's word for
a decision it can make itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Iterable, Mapping

from .anchor_process import AnchorProcess
from .approvals import ApprovalStore, create_scope
from .audit import AuditAuthority, AuditEvent, event_from_handoff
from .audit_chain import AuditAnchor, AuditChain
from .contracts import (ContractError, HandoffSigner, HandoffVerifier, Policy, validate,
                        validate_pending)
from .gateway import (ADMISSION_REASON_CODE, DispatchPermit, Gateway, GatewayRejected,
                      handoff_from_permit)
from .http_entry import HttpEntry, PrincipalRegistry
from .isolation import IsolatedWorkerRunner
from .keys import ServiceKeys, derive_keys
from .orchestrator import Denied, DispatchAttempted, Orchestrator, WorkerEndpoint
from .results import WorkerAuthority, handoff_digest
from .verifier import Rejected, ResultVerifier
from .workers import Worker, WorkerRunner

_TRACE_PREFIX = "trace-"
_MAX_PENDING = 1000
_APPROVAL_TTL_SECONDS = 60


def _fail(message: str) -> None:
    raise ContractError(message)


@dataclass(frozen=True)
class _Waiting:
    subject: str
    wire: bytes
    handoff: Any
    trace_id: str


class PendingJobs:
    """Approval-bound jobs issued over HTTP and not yet run.

    Process-local and bounded, like every ledger in v0.1. An entry expires with
    its handoff: nothing could run it afterwards, so it is dropped the next time
    one is added rather than counted against the bound for ever.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, _Waiting] = {}
        self._lock = Lock()

    def add(self, job_id: str, waiting: _Waiting, *, now: int) -> None:
        with self._lock:
            for stale in [key for key, value in self._jobs.items()
                          if value.handoff.expires_at <= now]:
                del self._jobs[stale]
            if job_id in self._jobs:
                _fail("a job with this id is already waiting for approval")
            if len(self._jobs) >= _MAX_PENDING:
                _fail("too many jobs are waiting for approval")
            self._jobs[job_id] = waiting

    def peek(self, job_id: str) -> _Waiting:
        with self._lock:
            waiting = self._jobs.get(job_id)
        if waiting is None:
            _fail("no job is waiting for approval under this id")
        return waiting

    def take(self, job_id: str, subject: str) -> _Waiting:
        """Remove the job for its own subject. Anyone else gets the same refusal."""
        with self._lock:
            waiting = self._jobs.get(job_id)
            if waiting is None or waiting.subject != subject:
                _fail("no job of this subject is waiting for approval under this id")
            return self._jobs.pop(job_id)

    def restore(self, job_id: str, waiting: _Waiting) -> None:
        with self._lock:
            self._jobs.setdefault(job_id, waiting)


class _AnchoredAudit:
    """Serialize event append and anchor acknowledgement, never worker execution."""

    def __init__(self, audit: AuditAuthority, chain: AuditChain, anchor: AuditAnchor) -> None:
        self.audit = audit
        self.chain = chain
        self.anchor = anchor
        self._lock = Lock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self.chain.append(event)
            self._commit_locked()

    def head(self):
        with self._lock:
            return self._commit_locked()

    def _commit_locked(self):
        head, records = self.chain.snapshot(self.audit)
        acknowledged = self.anchor.commit(head, records, authority=self.audit)
        if acknowledged != (head.count, head.head_hash):
            _fail("anchor acknowledgement does not match the submitted head")
        return head


@dataclass(frozen=True)
class Service:
    """Every instance, still separate, with one object that can reach them all.

    Holding them together is this object's whole job; nothing here decides
    anything. The audit chain and anchor live here because forensics is its own
    role and no other component may hold them — a writer that also holds the
    anchor is not anchored to anything outside itself.
    """

    entry: HttpEntry
    orchestrator: Orchestrator
    gateway: Gateway
    verifier: ResultVerifier
    audit: AuditAuthority
    chain: AuditChain
    anchor: AuditAnchor
    policy: Policy
    keys: ServiceKeys
    handoff_verifier: HandoffVerifier
    approvals: ApprovalStore
    pending: PendingJobs
    clock: Callable[[], int]
    _recorder: _AnchoredAudit

    def approve(self, job_id: str, *, ttl_seconds: int = _APPROVAL_TTL_SECONDS) -> bytes:
        """Grant the one-time approval for a job waiting over HTTP.

        Server-side only, and deliberately without an HTTP route: who may
        approve is a decision about people, and v0.1 has no principal type for
        an approver. The token reaches the client out of band and comes back on
        `POST /jobs/<job_id>/approve`, where the gateway consumes it once.
        """
        waiting = self.pending.peek(job_id)
        now = self.clock()
        scope = create_scope(waiting.wire, subject=waiting.subject, job_id=job_id,
                             policy=self.policy, verifier=self.handoff_verifier,
                             now=now)
        grant = self.approvals.grant(scope, now=now, ttl_seconds=ttl_seconds)
        _append_event(
            recorder=self._recorder, handoff=waiting.handoff,
            trace_id=waiting.trace_id, component="gateway",
            instance_id=self.gateway.gateway_id, action="APPROVAL_GRANTED",
            decision="ALLOWED", reason_code="OPERATOR_APPROVED", occurred_at=now)
        return grant.token

    def head(self):
        """The signed chain head, committed to the anchor."""
        return self._recorder.head()

    def close(self) -> None:
        """End the anchor process, if this service started one."""
        if isinstance(self.anchor, AnchorProcess):
            self.anchor.close()


def build(*, root_secret: bytes, policy: Policy, api_keys: Mapping[bytes, str],
          workers: Iterable[Worker], clock: Callable[[], int] | None = None,
          gateway_id: str = "gateway-1", verifier_id: str = "verifier-1",
          job_ids: Callable[[], str] | None = None,
          runner_factory: Callable[..., WorkerRunner] | None = None,
          anchor: AuditAnchor | None = None,
          anchor_state: str | None = None) -> Service:
    """Assemble one service. The only function that knows all the parts.

    The default anchor is an `AnchorProcess`, persisted to `anchor_state` when
    one is given so that a restarted service resumes from what it committed.
    Passing an in-process `AuditAnchor` is a test seam, the same way
    `runner_factory` is: it puts the anchor back inside the writer's memory.
    """
    if anchor is not None and not isinstance(anchor, AuditAnchor):
        _fail("anchor must be an AuditAnchor")
    if anchor is not None and anchor_state is not None:
        _fail("anchor_state configures the default anchor; pass one or the other")
    if not isinstance(policy, Policy):
        _fail("policy is invalid")
    keys = derive_keys(root_secret)
    if clock is not None and not callable(clock):
        _fail("clock must be callable")
    now = clock or (lambda: int(time.time()))

    # The orchestrator alone signs handoffs; everything that checks one gets
    # the public half and could not issue a handoff if it tried.
    handoff_signer = HandoffSigner(integrity_key=keys.integrity_key)
    handoff_verifier = handoff_signer.verifier()
    approvals = ApprovalStore()
    gateway = Gateway(gateway_id=gateway_id, handoff_verifier=handoff_verifier,
                      approval_store=approvals)
    worker_authority = WorkerAuthority(result_key=keys.result_key,
                                       integrity_key=keys.integrity_key)
    # The production/default path is fail-closed isolated execution. Tests may
    # inject a runner_factory deliberately, but a host without the required
    # POSIX isolation primitives must fail here rather than silently fall back
    # to same-process worker execution.
    make_runner = runner_factory or (
        lambda worker: IsolatedWorkerRunner(worker, authority=worker_authority))

    endpoints = []
    for worker in tuple(workers):
        runner = make_runner(worker)
        if not isinstance(runner, WorkerRunner):
            _fail("runner_factory must return a WorkerRunner")
        # The grant names which agent may run a subject's jobs, so a worker is
        # registered under the agent id its grant carries. Taking the first of
        # several would leave the other agents' subjects unroutable, found only
        # per request as WORKER_NOT_CONFIGURED.
        agents = {grant.worker_agent_id for grant in policy.grants
                  if runner.tool in grant.tools}
        if not agents:
            _fail(f"no grant in this policy names a worker for tool {runner.tool!r}")
        if len(agents) > 1:
            _fail(f"grants name more than one worker agent for tool {runner.tool!r}; "
                  "one worker cannot be registered as several agents")
        endpoints.append(WorkerEndpoint(agents.pop(), runner))

    audit = AuditAuthority(audit_key=keys.audit_key)
    chain = AuditChain()
    # Construction is lazy: invalid later configuration does not start a child.
    if anchor is None:
        anchor = AnchorProcess(verifier=audit.verifier(), state_path=anchor_state)
    recorder = _AnchoredAudit(audit, chain, anchor)
    orchestrator = Orchestrator(orchestrator_id=policy.orchestrator_id,
                                signer=handoff_signer,
                                gateway=gateway, workers=endpoints,
                                on_admitted=_admission_recorder(
                                    gateway=gateway, recorder=recorder))
    verifier = ResultVerifier(verifier_id=verifier_id,
                              handoff_verifier=handoff_verifier,
                              worker_verifier=worker_authority.verifier())
    pending = PendingJobs()

    submit, complete = _submitter(
        orchestrator=orchestrator, verifier=verifier,
        recorder=recorder, policy=policy,
        handoff_verifier=handoff_verifier, now=now, pending=pending)
    entry = HttpEntry(
        registry=PrincipalRegistry.from_api_keys(api_keys),
        submit=submit, complete=complete, job_ids=job_ids,
    )
    return Service(
        entry=entry, orchestrator=orchestrator, gateway=gateway,
        verifier=verifier, audit=audit, chain=chain, anchor=anchor,
        policy=policy, keys=keys, handoff_verifier=handoff_verifier,
        approvals=approvals, pending=pending,
        clock=now, _recorder=recorder,
    )


def _admission_recorder(*, gateway: Gateway,
                        recorder: _AnchoredAudit) -> Callable[[DispatchPermit], None]:
    """Record the gateway's admission from the permit, when it is minted.

    The orchestrator calls this, so it must not be able to say anything the
    gateway did not: it hands over a permit and nothing else. Action, decision
    and reason are fixed; instance, time and handoff are read from the permit,
    which only a `Gateway` can mint. It used to be appended after the worker
    returned, so a lost race for the job id left a minted permit — and possibly
    a spent approval — with no admission on the in-memory chain. This ordering
    does not make the chain durable across process crashes.
    """

    def record(permit: DispatchPermit) -> None:
        handoff = handoff_from_permit(permit)
        # One gateway per service. A permit another one minted is not evidence
        # of an admission this service made.
        if permit.gateway_id != gateway.gateway_id:
            _fail("admission names a gateway this service did not wire")
        _append_event(
            recorder=recorder, handoff=handoff,
            trace_id=_trace_id(handoff), component="gateway",
            instance_id=permit.gateway_id, action="HANDOFF_ADMITTED",
            decision="ALLOWED", reason_code=ADMISSION_REASON_CODE,
            occurred_at=permit.admitted_at)

    return record


def _submitter(*, orchestrator: Orchestrator,
               verifier: ResultVerifier, recorder: _AnchoredAudit, policy: Policy,
               handoff_verifier: HandoffVerifier, now: Callable[[], int], pending: PendingJobs):
    """Turn one authenticated request into one audited, verified job.

    Evidence is appended as each security-relevant decision happens. That is
    intentionally incremental: a later refusal must not erase the fact that an
    earlier component issued, admitted, or dispatched the job. The gateway's
    admission is not appended here at all but by `_admission_recorder`, while
    `dispatch` is still running and before the worker is.

    Returns two callables. `submit` issues a job and, unless its grant requires
    approval, runs it. `complete` runs an approval-bound job once the client
    presents its token. Both go through the same `run`, so an approved job is
    checked by exactly the path every other job is.
    """

    def submit(*, subject: str, job_id: str, payload: Mapping[str, str]) -> dict[str, Any]:
        wire, handoff, trace_id = issue_job(subject=subject, job_id=job_id,
                                            payload=payload)
        if policy.grant_for(subject).requires_approval:
            pending.add(job_id, _Waiting(subject, wire, handoff, trace_id), now=now())
            return {"status": "PENDING_APPROVAL",
                    "handoff_sha256": handoff_digest(handoff)}
        return run(subject=subject, job_id=job_id, wire=wire, handoff=handoff,
                   trace_id=trace_id)

    def complete(*, subject: str, job_id: str, approval_token: bytes) -> dict[str, Any]:
        waiting = pending.take(job_id, subject)
        try:
            return run(subject=subject, job_id=job_id, wire=waiting.wire,
                       handoff=waiting.handoff, trace_id=waiting.trace_id,
                       approval_token=approval_token)
        except GatewayRejected:
            # Refused before anything ran — a wrong token, or one for another
            # job. The job's id is not burned and its own approval is not
            # spent, so it stays waiting for the right one. If anchoring the
            # refusal failed instead, this handler is not reached and the
            # pending entry remains consumed conservatively.
            pending.restore(job_id, waiting)
            raise

    def issue_job(*, subject: str, job_id: str, payload: Mapping[str, str]):
        admitted_at = now()
        # Route before issuing so an impossible route produces no signed
        # authorization artifact.
        orchestrator.route(subject=subject, policy=policy, now=admitted_at)
        admission = orchestrator.admit(
            payload, subject=subject, job_id=job_id, policy=policy,
            now=admitted_at)
        handoff = _revalidate(
            policy=policy, handoff_verifier=handoff_verifier, wire=admission.wire,
            subject=subject, job_id=job_id, now=admitted_at)
        trace_id = _trace_id(handoff)
        _append_event(
            recorder=recorder, handoff=handoff, trace_id=trace_id,
            component="orchestrator", instance_id=orchestrator.orchestrator_id,
            action=admission.decision.action,
            decision=admission.decision.decision,
            reason_code=admission.decision.reason_code,
            occurred_at=admission.decision.occurred_at)
        return admission.wire, handoff, trace_id

    def run(*, subject: str, job_id: str, wire: bytes, handoff, trace_id: str,
            approval_token: bytes | None = None) -> dict[str, Any]:
        dispatch_at = now()
        try:
            dispatched = orchestrator.dispatch(
                wire, subject=subject, job_id=job_id, policy=policy,
                now=dispatch_at, approval_token=approval_token)
        except GatewayRejected as refusal:
            _append_event(
                recorder=recorder, handoff=handoff, trace_id=trace_id,
                component="gateway", instance_id=refusal.gateway_id,
                action="HANDOFF_REJECTED", decision="DENIED",
                reason_code=refusal.reason_code,
                occurred_at=refusal.occurred_at)
            raise
        except Denied as refusal:
            _append_event(
                recorder=recorder, handoff=handoff, trace_id=trace_id,
                component="orchestrator", instance_id=orchestrator.orchestrator_id,
                action=refusal.decision.action,
                decision=refusal.decision.decision,
                reason_code=refusal.decision.reason_code,
                occurred_at=refusal.decision.occurred_at)
            raise
        except DispatchAttempted as refusal:
            # A DispatchAttempted means gateway admission succeeded — already
            # on the chain — and the orchestrator committed to execution before
            # the worker boundary refused or failed. Preserve both of those.
            _append_event(
                recorder=recorder, handoff=handoff, trace_id=trace_id,
                component="orchestrator", instance_id=orchestrator.orchestrator_id,
                action=refusal.decision.action,
                decision=refusal.decision.decision,
                reason_code=refusal.decision.reason_code,
                occurred_at=refusal.decision.occurred_at)
            _append_event(
                recorder=recorder, handoff=handoff, trace_id=trace_id,
                component="worker", instance_id=handoff.worker_agent_id,
                action="HANDOFF_REJECTED", decision="DENIED",
                reason_code="EXECUTION_REFUSED", occurred_at=dispatch_at)
            raise

        for decision in dispatched.decisions:
            _append_event(
                recorder=recorder, handoff=handoff, trace_id=trace_id,
                component="orchestrator", instance_id=orchestrator.orchestrator_id,
                action=decision.action, decision=decision.decision,
                reason_code=decision.reason_code,
                occurred_at=decision.occurred_at)

        verify_at = now()
        try:
            acceptance = verifier.accept(
                dispatched.result_wire, handoff_wire=dispatched.handoff_wire,
                subject=subject, job_id=job_id, policy=policy, now=verify_at,
                approval_record_hash=dispatched.approval_record_hash)
        except Rejected as refusal:
            _append_event(
                recorder=recorder, handoff=handoff, trace_id=trace_id,
                component="monitor", instance_id=verifier.verifier_id,
                action="RESULT_REJECTED", decision="DENIED",
                reason_code=refusal.reason_code,
                occurred_at=refusal.occurred_at)
            raise

        _append_event(
            recorder=recorder, handoff=handoff, trace_id=trace_id,
            component="monitor", instance_id=verifier.verifier_id,
            action=acceptance.action, decision=acceptance.decision,
            reason_code=acceptance.reason_code,
            occurred_at=acceptance.occurred_at)
        return {
            "status": acceptance.status,
            "reason_code": acceptance.result.reason_code,
            "output": acceptance.result.output,
            "handoff_sha256": acceptance.handoff_sha256,
            "result_sha256": acceptance.result_sha256,
        }

    return submit, complete


def _trace_id(handoff) -> str:
    return f"{_TRACE_PREFIX}{handoff_digest(handoff)[:16]}"


def _append_event(*, recorder: _AnchoredAudit, handoff,
                  trace_id: str, component: str, instance_id: str,
                  action: str, decision: str, reason_code: str,
                  occurred_at: int) -> None:
    recorder.append(event_from_handoff(
        handoff, trace_id=trace_id,
        actor=recorder.audit.actor(component, instance_id), action=action,
        decision=decision, reason_code=reason_code,
        occurred_at=occurred_at))


def _revalidate(*, policy: Policy, handoff_verifier: HandoffVerifier, wire: bytes, subject: str,
                job_id: str, now: int):
    """Parse the wire again for the audit role, pending approval included.

    `validate` refuses a handoff that still reads PENDING_APPROVAL, which every
    approval-bound wire does for ever. Audit has to be able to derive the same
    handoff identity for those jobs too, so it uses the pending path when the
    grant says so. The gateway receipt remains separate evidence carried to the
    verifier.
    """
    grant = policy.grant_for(subject)
    revalidate = validate_pending if grant.requires_approval else validate
    return revalidate(wire, subject=subject, job_id=job_id, policy=policy,
                      verifier=handoff_verifier, now=now)
