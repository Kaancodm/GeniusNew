"""Independent default-deny enforcement before worker dispatch.

The gateway is the second trust boundary in the v0.1 path. It accepts the raw
handoff wire, revalidates it against trusted policy and server-side identity,
consumes a one-time approval when policy requires one, and mints the only
capability a WorkerRunner will accept.

An already validated Handoff is intentionally not an input. The constitution
requires an independent serialization boundary; accepting an in-memory object
would let the orchestrator substitute its own earlier decision for the
gateway's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import re
from threading import Lock
from typing import Any

from .approvals import ApprovalStore, create_scope
from .contracts import ContractError, Handoff, Policy, validate, validate_pending
from .results import handoff_digest

_GATEWAY_ID = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")
_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")
_PERMIT_PROVENANCE = object()


class _PermitUse:
    """Atomic one-shot state shared by every reference to one permit."""

    def __init__(self) -> None:
        self._used = False
        self._lock = Lock()

    def consume(self) -> None:
        with self._lock:
            if self._used:
                _fail("dispatch permit has already been consumed")
            self._used = True


def _fail(message: str) -> None:
    raise ContractError(message)


def _gateway_id(value: Any) -> str:
    if type(value) is not str or not _GATEWAY_ID.match(value):
        _fail("gateway_id must be a lowercase identifier of at most 63 characters")
    return value


def _integrity_key(value: Any) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        _fail("integrity_key must be at least 32 bytes")
    return value


@dataclass(frozen=True)
class DispatchPermit:
    """A gateway-minted capability required by the worker boundary."""

    handoff: Handoff
    handoff_sha256: str
    gateway_id: str
    admitted_at: int
    approval_record_hash: str | None
    use: object = field(default=None, repr=False, compare=False)
    origin: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.origin is not _PERMIT_PROVENANCE:
            _fail("dispatch permit must be minted by a Gateway")
        if not isinstance(self.use, _PermitUse):
            _fail("dispatch permit use state is invalid")
        if not isinstance(self.handoff, Handoff):
            _fail("dispatch permit handoff is invalid")
        if type(self.handoff_sha256) is not str or not _DIGEST.match(self.handoff_sha256):
            _fail("dispatch permit digest must be a lowercase SHA-256 digest")
        _gateway_id(self.gateway_id)
        if type(self.admitted_at) is not int:
            _fail("dispatch permit admitted_at must be an integer")
        if not self.handoff.issued_at <= self.admitted_at < self.handoff.expires_at:
            _fail("dispatch permit admission is outside the handoff lifetime")
        if self.approval_record_hash is not None:
            if (type(self.approval_record_hash) is not str
                    or not _DIGEST.match(self.approval_record_hash)):
                _fail("approval_record_hash must be a lowercase SHA-256 digest")


def handoff_from_permit(permit: Any) -> Handoff:
    """Inspect the admitted handoff while the capability still binds to it."""
    if not isinstance(permit, DispatchPermit) or permit.origin is not _PERMIT_PROVENANCE:
        _fail("dispatch requires a gateway-minted DispatchPermit")
    current = handoff_digest(permit.handoff)
    if not hmac.compare_digest(current, permit.handoff_sha256):
        _fail("dispatch permit no longer matches its handoff")
    return permit.handoff


def consume_handoff_from_permit(permit: Any) -> Handoff:
    """Atomically consume one dispatch capability and return its handoff."""
    handoff = handoff_from_permit(permit)
    if not isinstance(permit.use, _PermitUse):
        _fail("dispatch permit use state is invalid")
    permit.use.consume()
    return handoff


class Gateway:
    """Independent validation and approval consumption before dispatch."""

    def __init__(self, *, gateway_id: str, integrity_key: bytes,
                 approval_store: ApprovalStore) -> None:
        self._gateway_id = _gateway_id(gateway_id)
        self._integrity_key = _integrity_key(integrity_key)
        if not isinstance(approval_store, ApprovalStore):
            _fail("approval_store must be an ApprovalStore")
        self._approvals = approval_store

    @property
    def gateway_id(self) -> str:
        return self._gateway_id

    def admit(self, wire: Any, *, subject: str, job_id: str, policy: Policy,
              now: int, approval_token: bytes | None = None) -> DispatchPermit:
        """Revalidate the raw wire and mint a one-job dispatch capability.

        Policy, identity, job id and time are trusted server-side inputs. The
        wire is always parsed again here; a Handoff object is never accepted as
        a shortcut around independent validation.
        """
        if not isinstance(policy, Policy):
            _fail("policy is invalid")
        grant = policy.grant_for(subject)

        if grant.requires_approval:
            if approval_token is None:
                _fail("approval token is required")
            handoff = validate_pending(
                wire, subject=subject, job_id=job_id, policy=policy,
                integrity_key=self._integrity_key, now=now,
            )
            scope = create_scope(
                wire, subject=subject, job_id=job_id, policy=policy,
                integrity_key=self._integrity_key, now=now,
            )
            receipt = self._approvals.consume(approval_token, scope, now=now)
            approval_record_hash: str | None = receipt.record_hash
        else:
            if approval_token is not None:
                _fail("approval token is not allowed when policy does not require approval")
            handoff = validate(
                wire, subject=subject, job_id=job_id, policy=policy,
                integrity_key=self._integrity_key, now=now,
            )
            approval_record_hash = None

        return DispatchPermit(
            handoff=handoff,
            handoff_sha256=handoff_digest(handoff),
            gateway_id=self._gateway_id,
            admitted_at=now,
            approval_record_hash=approval_record_hash,
            use=_PermitUse(),
            origin=_PERMIT_PROVENANCE,
        )
