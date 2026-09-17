"""What a security-relevant decision may be recorded as, and nothing more.

`docs/CONSTITUTION-V1-DRAFT.md` section 7 requires audit entries to carry a
job/trace reference, the kind of decision, the policy and constitution versions,
a result code, integrity information and a time reference — and forbids secrets
and raw payloads. `SECURITY.md` repeats the prohibition.

This module makes that structural rather than conventional:

- No field accepts free text. `action` and `decision` come from closed sets,
  `reason_code` must match an uppercase code, digests must be digests, and the
  time reference must be a positive integer.
- No field accepts arbitrary data. Every remaining field is a bounded
  identifier, so an entry cannot exceed roughly 1.2 KB whatever a caller does.
- Payload content is representable only as the digest the handoff already
  carries, and `event_from_handoff` never copies payload content into an event.

What this does **not** do is make an entry incapable of holding a secret. The
identifier fields — `trace_id`, `actor`, `subject`, `job_id` and the version
fields — accept any bounded string without whitespace, so a caller that passes a
credential as a trace id will record it. The guarantee here is that payloads
cannot be bulk-leaked through an audit entry and that nothing in the derivation
path puts them there; it is not a filter against a caller determined to leak.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

from .contracts import ContractError, Handoff, canonical

CONSTITUTION_VERSION = "constitution-v1-draft"

_ACTIONS = frozenset({
    "HANDOFF_ISSUED",
    "HANDOFF_ADMITTED",
    "HANDOFF_REJECTED",
    "APPROVAL_GRANTED",
    "APPROVAL_CONSUMED",
    "APPROVAL_REVOKED",
    "EXECUTION_DISPATCHED",
    "RESULT_ACCEPTED",
    "RESULT_REJECTED",
})
_DECISIONS = frozenset({"ALLOWED", "DENIED"})
_MAX_IDENTIFIER_CHARS = 128
_REASON_CODE = re.compile(r"\A[A-Z][A-Z0-9_]{0,63}\Z")
_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")


def _fail(message: str) -> None:
    raise ContractError(message)


def _identifier(value: Any, field: str) -> str:
    if type(value) is not str or not value or len(value) > _MAX_IDENTIFIER_CHARS:
        _fail(f"{field} must be a non-empty identifier of at most {_MAX_IDENTIFIER_CHARS} characters")
    if any(character.isspace() for character in value):
        _fail(f"{field} must not contain whitespace")
    return value


def _digest(value: Any, field: str) -> str:
    if type(value) is not str or not _DIGEST.match(value):
        _fail(f"{field} must be a lowercase SHA-256 digest")
    return value


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        _fail(f"{field} must be an integer")
    return value


@dataclass(frozen=True)
class AuditEvent:
    """One security-relevant decision, recorded without its payload."""

    trace_id: str
    job_id: str
    actor: str
    subject: str
    action: str
    decision: str
    reason_code: str
    policy_version: str
    constitution_version: str
    handoff_sha256: str
    payload_sha256: str
    occurred_at: int

    def __post_init__(self) -> None:
        for field in ("trace_id", "job_id", "actor", "subject", "policy_version", "constitution_version"):
            _identifier(getattr(self, field), field)
        for field in ("handoff_sha256", "payload_sha256"):
            _digest(getattr(self, field), field)
        if self.action not in _ACTIONS:
            _fail("action is not an allowed audit action")
        if self.decision not in _DECISIONS:
            _fail("decision is not an allowed audit decision")
        if type(self.reason_code) is not str or not _REASON_CODE.match(self.reason_code):
            _fail("reason_code must be an uppercase code of at most 64 characters")
        if _integer(self.occurred_at, "occurred_at") <= 0:
            _fail("occurred_at must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "actor": self.actor,
            "constitution_version": self.constitution_version,
            "decision": self.decision,
            "handoff_sha256": self.handoff_sha256,
            "job_id": self.job_id,
            "occurred_at": self.occurred_at,
            "payload_sha256": self.payload_sha256,
            "policy_version": self.policy_version,
            "reason_code": self.reason_code,
            "subject": self.subject,
            "trace_id": self.trace_id,
        }

    def to_bytes(self) -> bytes:
        return canonical(self.to_dict())

    def event_sha256(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()


def event_from_handoff(handoff: Handoff, *, trace_id: str, actor: str, action: str,
                       decision: str, reason_code: str, occurred_at: int) -> AuditEvent:
    """Derive an event from a handoff without copying its payload.

    The job, subject and policy version come from the handoff itself, and the
    payload appears only as the digest the handoff already carries. Payload
    content is read once, by `handoff.to_bytes()`, to hash the exact artifact
    the decision was made about; it is never copied into a field.
    """
    if not isinstance(handoff, Handoff):
        _fail("handoff is invalid")
    return AuditEvent(
        trace_id=_identifier(trace_id, "trace_id"),
        job_id=handoff.job_id,
        actor=_identifier(actor, "actor"),
        subject=handoff.user_id,
        action=action,
        decision=decision,
        reason_code=reason_code,
        policy_version=handoff.policy_version,
        constitution_version=CONSTITUTION_VERSION,
        handoff_sha256=hashlib.sha256(handoff.to_bytes()).hexdigest(),
        payload_sha256=handoff.payload_sha256,
        occurred_at=_integer(occurred_at, "occurred_at"),
    )
