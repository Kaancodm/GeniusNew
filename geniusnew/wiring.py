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
from typing import Any, Callable, Iterable, Mapping

from .anchor_process import AnchorProcess
from .approvals import ApprovalStore
from .audit import AuditAuthority, event_from_handoff
from .audit_chain import AuditAnchor, AuditChain
from .contracts import ContractError, Policy, validate, validate_pending
from .gateway import ADMISSION_REASON_CODE, Gateway, GatewayRejected
from .http_entry import HttpEntry, PrincipalRegistry
from .isolation import IsolatedWorkerRunner
from .keys import ServiceKeys, derive_keys
from .orchestrator import Denied, DispatchAttempted, Orchestrator, WorkerEndpoint
from .results import WorkerAuthority, handoff_digest
from .verifier import Rejected, ResultVerifier
from .workers import Worker, WorkerRunner

_TRACE_PREFIX = "trace-"


def _fail(message: str) -> None:
    raise ContractError(message)


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

    def head(self):
        """The signed chain head, committed to the anchor."""
        head = self.chain.head(self.audit)
        self.anchor.commit(head, self.chain.records, authority=self.audit)
        return head

    def close(self) -> None:
        """End the anchor process, if this service started one."""
        if isinstance(self.anchor, AnchorProcess):
            self.anchor.close()


def build(*, root_secret: bytes, policy: Policy, api_keys: Mapping[bytes, str],
          workers: Iterable[Worker], clock: Callable[[], int] | None = None,
          gateway_id: str = "gateway-1", verifier_id: str = "verifier-1",
          job_ids: Callable[[], str] | None = None,
          runner_factory: Callable[..., WorkerRunner] | None = None,
          anchor: AuditAnchor | None = None) -> Service:
    """Assemble one service. The only function that knows all the parts.

    The default anchor is an `AnchorProcess`. Passing an in-process
    `AuditAnchor` is a test seam, the same way `runner_factory` is: it puts the
    anchor back inside the writer's memory.
    """
    if anchor is not None and not isinstance(anchor, AuditAnchor):
        _fail("anchor must be an AuditAnchor")
    if not isinstance(policy, Policy):
        _fail("policy is invalid")
    keys = derive_keys(root_secret)
    if clock is not None and not callable(clock):
        _fail("clock must be callable")
    now = clock or (lambda: int(time.time()))

    gateway = Gateway(gateway_id=gateway_id, integrity_key=keys.integrity_key,
                      approval_store=ApprovalStore())
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

    orchestrator = Orchestrator(orchestrator_id=policy.orchestrator_id,
                                integrity_key=keys.integrity_key,
                                gateway=gateway, workers=endpoints)
    verifier = ResultVerifier(verifier_id=verifier_id,
                              integrity_key=keys.integrity_key,
                              result_key=keys.result_key)
    audit = AuditAuthority(audit_key=keys.audit_key)
    chain = AuditChain()

    entry = HttpEntry(
        registry=PrincipalRegistry.from_api_keys(api_keys),
        submit=_submitter(orchestrator=orchestrator, gateway=gateway,
                          verifier=verifier, audit=audit, chain=chain,
                          policy=policy, keys=keys, now=now),
        job_ids=job_ids,
    )
    # Started last, so a refusal above cannot leave a process behind.
    if anchor is None:
        anchor = AnchorProcess(audit_key=keys.audit_key)
    return Service(
        entry=entry, orchestrator=orchestrator, gateway=gateway,
        verifier=verifier, audit=audit, chain=chain, anchor=anchor,
        policy=policy, keys=keys,
    )


def _submitter(*, orchestrator: Orchestrator, gateway: Gateway,
               verifier: ResultVerifier, audit: AuditAuthority,
               chain: AuditChain, policy: Policy, keys: ServiceKeys,
               now: Callable[[], int]):
    """Turn one authenticated request into one audited, verified job.

    Evidence is appended as each security-relevant decision happens. That is
    intentionally incremental: a later refusal must not erase the fact that an
    earlier component issued, admitted, or dispatched the job.
    """

    def submit(*, subject: str, job_id: str, payload: Mapping[str, str]) -> dict[str, Any]:
        # An approval token is a capability a client would have to present, and
        # the entrance has no field for one. Keep that gap explicit until the
        # HTTP contract grows a safe place to carry it.
        if policy.grant_for(subject).requires_approval:
            _fail("approval-bound jobs cannot be submitted through the entrance")

        admitted_at = now()
        # Route before issuing so an impossible route produces no signed
        # authorization artifact.
        orchestrator.route(subject=subject, policy=policy, now=admitted_at)
        admission = orchestrator.admit(
            payload, subject=subject, job_id=job_id, policy=policy,
            now=admitted_at)
        handoff = _revalidate(
            policy=policy, keys=keys, wire=admission.wire,
            subject=subject, job_id=job_id, now=admitted_at)
        trace_id = _trace_id(handoff)
        _append_event(
            chain=chain, audit=audit, handoff=handoff, trace_id=trace_id,
            component="orchestrator", instance_id=orchestrator.orchestrator_id,
            action=admission.decision.action,
            decision=admission.decision.decision,
            reason_code=admission.decision.reason_code,
            occurred_at=admission.decision.occurred_at)

        dispatch_at = now()
        try:
            dispatched = orchestrator.dispatch(
                admission.wire, subject=subject, job_id=job_id, policy=policy,
                now=dispatch_at)
        except GatewayRejected as refusal:
            _append_event(
                chain=chain, audit=audit, handoff=handoff, trace_id=trace_id,
                component="gateway", instance_id=refusal.gateway_id,
                action="HANDOFF_REJECTED", decision="DENIED",
                reason_code=refusal.reason_code,
                occurred_at=refusal.occurred_at)
            raise
        except Denied as refusal:
            _append_event(
                chain=chain, audit=audit, handoff=handoff, trace_id=trace_id,
                component="orchestrator", instance_id=orchestrator.orchestrator_id,
                action=refusal.decision.action,
                decision=refusal.decision.decision,
                reason_code=refusal.decision.reason_code,
                occurred_at=refusal.decision.occurred_at)
            raise
        except DispatchAttempted as refusal:
            # A DispatchAttempted means gateway admission succeeded and the
            # orchestrator committed to execution before the worker boundary
            # refused or failed. Preserve every one of those decisions.
            _append_event(
                chain=chain, audit=audit, handoff=handoff, trace_id=trace_id,
                component="gateway", instance_id=gateway.gateway_id,
                action="HANDOFF_ADMITTED", decision="ALLOWED",
                reason_code=ADMISSION_REASON_CODE, occurred_at=dispatch_at)
            _append_event(
                chain=chain, audit=audit, handoff=handoff, trace_id=trace_id,
                component="orchestrator", instance_id=orchestrator.orchestrator_id,
                action=refusal.decision.action,
                decision=refusal.decision.decision,
                reason_code=refusal.decision.reason_code,
                occurred_at=refusal.decision.occurred_at)
            _append_event(
                chain=chain, audit=audit, handoff=handoff, trace_id=trace_id,
                component="worker", instance_id=handoff.worker_agent_id,
                action="HANDOFF_REJECTED", decision="DENIED",
                reason_code="EXECUTION_REFUSED", occurred_at=dispatch_at)
            raise

        _append_event(
            chain=chain, audit=audit, handoff=handoff, trace_id=trace_id,
            component="gateway", instance_id=gateway.gateway_id,
            action="HANDOFF_ADMITTED", decision="ALLOWED",
            reason_code=ADMISSION_REASON_CODE, occurred_at=dispatch_at)
        for decision in dispatched.decisions:
            _append_event(
                chain=chain, audit=audit, handoff=handoff, trace_id=trace_id,
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
                chain=chain, audit=audit, handoff=handoff, trace_id=trace_id,
                component="monitor", instance_id=verifier.verifier_id,
                action="RESULT_REJECTED", decision="DENIED",
                reason_code=refusal.reason_code,
                occurred_at=refusal.occurred_at)
            raise

        _append_event(
            chain=chain, audit=audit, handoff=handoff, trace_id=trace_id,
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

    return submit


def _trace_id(handoff) -> str:
    return f"{_TRACE_PREFIX}{handoff_digest(handoff)[:16]}"


def _append_event(*, chain: AuditChain, audit: AuditAuthority, handoff,
                  trace_id: str, component: str, instance_id: str,
                  action: str, decision: str, reason_code: str,
                  occurred_at: int) -> None:
    chain.append(event_from_handoff(
        handoff, trace_id=trace_id,
        actor=audit.actor(component, instance_id), action=action,
        decision=decision, reason_code=reason_code,
        occurred_at=occurred_at))


def _revalidate(*, policy: Policy, keys: ServiceKeys, wire: bytes, subject: str,
                job_id: str, now: int):
    """Parse the wire again for the audit role, pending approval included.

    `validate` refuses a handoff that still reads PENDING_APPROVAL, which every
    approval-bound wire does for ever. Audit has to be able to derive the same
    handoff identity for those jobs too, so it uses the pending path when the
    grant says so. The gateway receipt remains separate evidence carried to the
    verifier; approval over the HTTP entrance is still explicitly out of scope.
    """
    grant = policy.grant_for(subject)
    revalidate = validate_pending if grant.requires_approval else validate
    return revalidate(wire, subject=subject, job_id=job_id, policy=policy,
                      integrity_key=keys.integrity_key, now=now)
