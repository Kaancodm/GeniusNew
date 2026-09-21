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

from .approvals import ApprovalStore
from .audit import AuditAuthority, event_from_handoff
from .audit_chain import AuditAnchor, AuditChain
from .contracts import ContractError, Policy, validate, validate_pending
from .gateway import Gateway
from .http_entry import HttpEntry, PrincipalRegistry
from .keys import ServiceKeys, derive_keys
from .orchestrator import Orchestrator, WorkerEndpoint
from .results import WorkerAuthority
from .verifier import ResultVerifier
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
        """The signed chain head, committed to the anchor. Cheap to ask for."""
        head = self.chain.head(self.audit)
        self.anchor.commit(head, self.chain.records, authority=self.audit)
        return head


def build(*, root_secret: bytes, policy: Policy, api_keys: Mapping[bytes, str],
          workers: Iterable[Worker], clock: Callable[[], int] | None = None,
          gateway_id: str = "gateway-1", verifier_id: str = "verifier-1",
          job_ids: Callable[[], str] | None = None,
          runner_factory: Callable[..., WorkerRunner] | None = None) -> Service:
    """Assemble one service. The only function that knows all the parts."""
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
    make_runner = runner_factory or (
        lambda worker: WorkerRunner(worker, authority=worker_authority))

    endpoints = []
    for worker in tuple(workers):
        runner = make_runner(worker)
        if not isinstance(runner, WorkerRunner):
            _fail("runner_factory must return a WorkerRunner")
        # The grant names which agent may run a subject's jobs, so a worker is
        # registered under the agent id its grant carries. A worker nobody is
        # granted is simply never routed to.
        for grant in policy.grants:
            if runner.tool in grant.tools:
                endpoints.append(WorkerEndpoint(grant.worker_agent_id, runner))
                break
        else:
            _fail(f"no grant in this policy names a worker for tool {runner.tool!r}")

    orchestrator = Orchestrator(orchestrator_id=policy.orchestrator_id,
                                integrity_key=keys.integrity_key,
                                gateway=gateway, workers=endpoints)
    verifier = ResultVerifier(verifier_id=verifier_id,
                              integrity_key=keys.integrity_key,
                              result_key=keys.result_key)
    audit = AuditAuthority(audit_key=keys.audit_key)
    chain = AuditChain()
    anchor = AuditAnchor()

    entry = HttpEntry(
        registry=PrincipalRegistry.from_api_keys(api_keys),
        submit=_submitter(orchestrator=orchestrator, verifier=verifier,
                          audit=audit, chain=chain, policy=policy, keys=keys,
                          now=now),
        job_ids=job_ids,
    )
    return Service(
        entry=entry, orchestrator=orchestrator, gateway=gateway,
        verifier=verifier, audit=audit, chain=chain, anchor=anchor,
        policy=policy, keys=keys,
    )


def _submitter(*, orchestrator: Orchestrator, verifier: ResultVerifier,
               audit: AuditAuthority, chain: AuditChain, policy: Policy,
               keys: ServiceKeys, now: Callable[[], int]):
    """Turn one authenticated request into one audited, verified job.

    The clock is read at each step rather than once: that is the difference
    between a TTL and a note about when the request arrived, and step 15's
    third control point exists because a single frozen `now` cannot see a
    deadline pass.
    """

    def submit(*, subject: str, job_id: str, payload: Mapping[str, str]) -> dict[str, Any]:
        # An approval token is a capability a client would have to present, and
        # the entrance has no field for one. The gateway would refuse anyway,
        # further in and with a message about tokens; saying it here keeps the
        # gap named rather than looking like a policy failure. Approval-bound
        # work is reachable only by calling the orchestrator directly until the
        # entrance grows a place to carry the token.
        if policy.grant_for(subject).requires_approval:
            _fail("approval-bound jobs cannot be submitted through the entrance")
        dispatched = orchestrator.submit(
            payload, subject=subject, job_id=job_id, policy=policy, now=now())
        acceptance = verifier.accept(
            dispatched.result_wire, handoff_wire=dispatched.handoff_wire,
            subject=subject, job_id=job_id, policy=policy, now=now(),
            approval_record_hash=dispatched.approval_record_hash)
        _record(orchestrator=orchestrator, verifier=verifier, audit=audit,
                chain=chain, policy=policy, keys=keys, dispatched=dispatched,
                acceptance=acceptance, subject=subject, job_id=job_id)
        return {
            "status": acceptance.status,
            "reason_code": acceptance.result.reason_code,
            "output": acceptance.result.output,
            "handoff_sha256": acceptance.handoff_sha256,
            "result_sha256": acceptance.result_sha256,
        }

    return submit


def _record(*, orchestrator: Orchestrator, verifier: ResultVerifier,
            audit: AuditAuthority, chain: AuditChain, policy: Policy,
            keys: ServiceKeys, dispatched, acceptance, subject: str,
            job_id: str) -> None:
    """Write what each instance decided into the chain, as its own actor.

    The audit role revalidates the wire rather than taking the handoff object
    from the component whose decision it is recording. In one process that is a
    formality; it is also the only version of this that survives the components
    being split apart, which is the point of writing it this way now.
    """
    handoff = _revalidate(policy=policy, keys=keys, wire=dispatched.handoff_wire,
                          subject=subject, job_id=job_id,
                          now=acceptance.occurred_at)
    trace_id = f"{_TRACE_PREFIX}{dispatched.handoff_sha256[:16]}"
    entries = [
        (audit.actor("orchestrator", orchestrator.orchestrator_id),
         decision.action, decision.decision, decision.reason_code,
         decision.occurred_at)
        for decision in dispatched.decisions
    ]
    entries.append((audit.actor("monitor", verifier.verifier_id),
                    acceptance.action, acceptance.decision, acceptance.reason_code,
                    acceptance.occurred_at))
    for actor, action, decision, reason_code, occurred_at in entries:
        chain.append(event_from_handoff(
            handoff, trace_id=trace_id, actor=actor, action=action,
            decision=decision, reason_code=reason_code, occurred_at=occurred_at))


def _revalidate(*, policy: Policy, keys: ServiceKeys, wire: bytes, subject: str,
                job_id: str, now: int):
    """Parse the wire again for the audit role, pending approval included.

    `validate` refuses a handoff that still reads PENDING_APPROVAL, which every
    approval-bound wire does for ever. Audit has to be able to record those jobs
    too, so it uses the pending path when the grant says so — and records the
    gateway's receipt hash as the evidence it is, rather than inferring approval
    from the fact that a result exists.
    """
    grant = policy.grant_for(subject)
    revalidate = validate_pending if grant.requires_approval else validate
    return revalidate(wire, subject=subject, job_id=job_id, policy=policy,
                      integrity_key=keys.integrity_key, now=now)
