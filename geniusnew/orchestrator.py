"""Deterministic admission, assignment and dispatch.

The orchestrator decides *where* a trusted request should go. It does not confirm
its own decision: every raw Handoff wire is independently revalidated by the
Gateway, and execution is performed behind a WorkerEndpoint.

The worker registry is copied into an immutable mapping at construction. Runtime
requests cannot add workers, change routing or smuggle an alternate runner into
the dispatch path.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Iterable

from .contracts import ContractError, Policy, issue
from .gateway import Gateway
from .workers import WorkerRunner

_INSTANCE_ID = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")


def _fail(message: str) -> None:
    raise ContractError(message)


def _instance_id(value: Any, field: str) -> str:
    if type(value) is not str or not _INSTANCE_ID.match(value):
        _fail(f"{field} must be a lowercase identifier of at most 63 characters")
    return value


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

    def dispatch(self, permit: object, *, now: int) -> bytes:
        return self.runner.execute(permit, now=now)


class Orchestrator:
    """Admission and deterministic routing without self-confirmation."""

    def __init__(self, *, orchestrator_id: str, gateway: Gateway,
                 workers: Iterable[WorkerEndpoint]) -> None:
        self._orchestrator_id = _instance_id(orchestrator_id, "orchestrator_id")
        if not isinstance(gateway, Gateway):
            _fail("gateway must be a Gateway")
        self._gateway = gateway

        try:
            endpoints = tuple(workers)
        except TypeError as exc:
            raise ContractError("workers must be an iterable of WorkerEndpoint") from exc
        if not endpoints:
            _fail("workers must not be empty")
        if not all(isinstance(endpoint, WorkerEndpoint) for endpoint in endpoints):
            _fail("workers must contain only WorkerEndpoint values")
        ids = tuple(endpoint.worker_agent_id for endpoint in endpoints)
        if len(ids) != len(set(ids)):
            _fail("worker_agent_id values must be unique")
        self._workers = MappingProxyType({
            endpoint.worker_agent_id: endpoint for endpoint in endpoints
        })

    @property
    def orchestrator_id(self) -> str:
        return self._orchestrator_id

    @property
    def worker_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._workers))

    def _policy(self, policy: Any) -> Policy:
        if not isinstance(policy, Policy):
            _fail("policy is invalid")
        if policy.orchestrator_id != self._orchestrator_id:
            _fail("policy orchestrator_id does not name this orchestrator")
        return policy

    def admit(self, request: Any, *, subject: str, job_id: str,
              policy: Policy, now: int) -> bytes:
        """Create the raw Handoff wire from trusted server-side routing facts."""
        trusted = self._policy(policy)
        return issue(
            request,
            subject=subject,
            job_id=job_id,
            policy=trusted,
            integrity_key=self._gateway_integrity_key(),
            now=now,
        )

    def _gateway_integrity_key(self) -> bytes:
        """Return the v0.1 shared Handoff key used by issuer and verifier.

        Handoff v1 is HMAC-based, so issuance and verification cannot yet use
        separate cryptographic capabilities. Keeping this access in one method
        makes the limitation explicit and local until the contract becomes
        asymmetric.
        """
        return self._gateway._integrity_key  # noqa: SLF001 - explicit v0.1 limitation

    def dispatch(self, wire: Any, *, subject: str, job_id: str,
                 policy: Policy, now: int,
                 approval_token: bytes | None = None) -> bytes:
        """Select one configured endpoint, then let Gateway confirm the wire."""
        trusted = self._policy(policy)
        grant = trusted.grant_for(subject)
        endpoint = self._workers.get(grant.worker_agent_id)
        if endpoint is None:
            _fail("trusted policy names an unconfigured worker")
        if endpoint.tool not in grant.tools:
            _fail("configured worker tool is not granted by policy")

        permit = self._gateway.admit(
            wire,
            subject=subject,
            job_id=job_id,
            policy=trusted,
            now=now,
            approval_token=approval_token,
        )
        return endpoint.dispatch(permit, now=now)
