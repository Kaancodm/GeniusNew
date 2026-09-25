"""What a security-relevant decision may be recorded as, and nothing more.

`docs/CONSTITUTION-V1-DRAFT.md` section 7 requires audit entries to carry a
job/trace reference, the kind of decision, the policy and constitution versions,
a result code, integrity information and a time reference — and forbids secrets
and raw payloads. `SECURITY.md` repeats the prohibition.

This module makes that structural rather than conventional:

- No field accepts free text. `action` and `decision` come from closed sets,
  `reason_code` must match an uppercase code, digests must be digests, and the
  time reference must be a positive integer.
- No field accepts arbitrary data. Every remaining field is an identifier
  bounded by its *serialized* size and the time reference has an upper bound, so
  an entry stays small whatever a caller does. Bounding identifiers by code
  points would not have been enough: `canonical()` escapes non-ASCII, so 128
  emoji serialize to over 1.5 KB each.
- Payload content is representable only as the digest the handoff already
  carries, and `event_from_handoff` never copies payload content into an event.
- The acting component is proven, not claimed. `actor` is a `ComponentActor`
  that only an `AuditAuthority` can mint, so a caller cannot record a decision
  as `gateway` without holding the server-side audit authority.

What this does **not** do is make an entry incapable of holding a secret. The
identifier fields — `trace_id`, `subject`, `job_id` and the version fields —
accept any bounded string without whitespace, so a caller that passes a
credential as a trace id will record it. The guarantee here is that payloads
cannot be bulk-leaked through an audit entry and that nothing in the derivation
path puts them there; it is not a filter against a caller determined to leak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (Ed25519PrivateKey,
                                                              Ed25519PublicKey)

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
_COMPONENTS = frozenset({"orchestrator", "gateway", "worker", "monitor", "forensics"})
_ACTOR_PROVENANCE = object()
_INSTANCE_ID = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")
_MAX_IDENTIFIER_BYTES = 160
_MAX_OCCURRED_AT = 4102444800  # 2100-01-01T00:00:00Z
_REASON_CODE = re.compile(r"\A[A-Z][A-Z0-9_]{0,63}\Z")
_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")
_SIGNING_LABEL = b"geniusnew/audit-head-signing/ed25519/v1"
_EVENT_KEYS = frozenset({
    "action", "actor", "constitution_version", "decision", "handoff_sha256",
    "job_id", "occurred_at", "payload_sha256", "policy_version", "reason_code",
    "subject", "trace_id",
})


def _fail(message: str) -> None:
    raise ContractError(message)


def _identifier(value: Any, field: str) -> str:
    """Bound an identifier by what it costs once serialized, not by code points.

    `canonical()` escapes non-ASCII, so a single emoji becomes twelve bytes. A
    code-point bound would let a caller put nine kilobytes into an entry that
    claims to be bounded at one.
    """
    if type(value) is not str or not value:
        _fail(f"{field} must be a non-empty identifier")
    if any(character.isspace() for character in value):
        _fail(f"{field} must not contain whitespace")
    if len(canonical(value)) > _MAX_IDENTIFIER_BYTES:
        _fail(f"{field} must serialize to at most {_MAX_IDENTIFIER_BYTES} bytes")
    return value


def _audit_safe(value: Any, field: str) -> str:
    """Project a handoff identifier into one an entry can always carry.

    The handoff contract accepts identifiers this module would refuse, so a
    decision about an unusual but perfectly valid handoff would otherwise be
    impossible to audit. Refusing to record a security decision is the worse
    failure, so such a value is recorded as a digest of itself instead.
    """
    if type(value) is not str or not value:
        _fail(f"{field} must be a non-empty identifier")
    try:
        return _identifier(value, field)
    except ContractError:
        return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest(value: Any, field: str) -> str:
    if type(value) is not str or not _DIGEST.match(value):
        _fail(f"{field} must be a lowercase SHA-256 digest")
    return value


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        _fail(f"{field} must be an integer")
    return value


@dataclass(frozen=True)
class ComponentActor:
    """Which component acted, proven rather than claimed.

    `docs/MIGRATION-MATRIX.md` requires actor binding for the audit chain, and
    `CONSTITUTION-V1-DRAFT.md` section 8 keeps the roles logically separate. A
    free string defeats both: any caller could record a decision as `gateway`.

    Only `AuditAuthority.actor()` can mint one, so attributing an event to a
    component means holding the server-side audit authority for it.
    """

    component: str
    instance_id: str
    origin: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.origin is not _ACTOR_PROVENANCE:
            _fail("component actor must be minted by an AuditAuthority")
        if type(self.component) is not str or self.component not in _COMPONENTS:
            _fail("component is not one of the constitution's roles")
        if type(self.instance_id) is not str or not _INSTANCE_ID.match(self.instance_id):
            _fail("instance_id must be a lowercase identifier of at most 63 characters")

    def identifier(self) -> str:
        return f"{self.component}:{self.instance_id}"


class AuditVerifier:
    """The public half of the audit authority: it checks heads and makes none.

    Everything that only needs to *check* the log — `audit_chain.verify`, the
    anchor, a forensic reader — is given this and nothing else. It holds a
    32-byte Ed25519 public key and has no signing method; there is no private
    key anywhere in it to reach for. With HMAC the same object had to hold the
    key that signs, which is the gap this closes for audit heads.
    """

    def __init__(self, *, public_key: bytes) -> None:
        if type(public_key) is not bytes or len(public_key) != 32:
            _fail("public_key must be 32 bytes")
        try:
            self._key = Ed25519PublicKey.from_public_bytes(public_key)
        except ValueError:
            raise ContractError("public_key is not an Ed25519 public key") from None
        self._public_key = public_key

    @property
    def public_key(self) -> bytes:
        return self._public_key

    def verifies(self, message: bytes, signature: bytes) -> bool:
        try:
            self._key.verify(signature, message)
        except InvalidSignature:
            return False
        return True


class AuditAuthority:
    """The Audit/Forensics role of `CONSTITUTION-V1-DRAFT.md` section 8.

    Holds the audit signing key, which is deliberately **not** the handoff
    integrity key. A component that can issue or validate handoffs therefore
    cannot mint an audit actor or sign an audit head, which is what keeps the
    roles separate in practice rather than only on paper.

    The signing key is Ed25519, derived from the audit key under its own label,
    so the root secret stays the only secret to manage. What the key signs can
    be checked by an `AuditVerifier` that never holds it.

    Constructed once from server-side runtime configuration. A request handler
    that does not hold it cannot attribute a decision to any component.
    """

    def __init__(self, *, audit_key: bytes) -> None:
        if type(audit_key) is not bytes or len(audit_key) < 32:
            _fail("audit_key must be at least 32 bytes")
        seed = hmac.new(audit_key, _SIGNING_LABEL, hashlib.sha256).digest()
        self._signing_key = Ed25519PrivateKey.from_private_bytes(seed)
        self._verifier = AuditVerifier(public_key=self._signing_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw))

    def verifier(self) -> AuditVerifier:
        return self._verifier

    def sign(self, message: bytes) -> bytes:
        if type(message) is not bytes:
            _fail("message must be bytes")
        return self._signing_key.sign(message)

    def actor(self, component: str, instance_id: str) -> ComponentActor:
        return ComponentActor(component, instance_id, _ACTOR_PROVENANCE)


@dataclass(frozen=True)
class AuditEvent:
    """One security-relevant decision, recorded without its payload."""

    trace_id: str
    job_id: str
    actor: ComponentActor
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
        for field in ("trace_id", "job_id", "subject", "policy_version", "constitution_version"):
            _identifier(getattr(self, field), field)
        if not isinstance(self.actor, ComponentActor):
            _fail("actor must be a ComponentActor minted by an AuditAuthority")
        for field in ("handoff_sha256", "payload_sha256"):
            _digest(getattr(self, field), field)
        if type(self.action) is not str or self.action not in _ACTIONS:
            _fail("action is not an allowed audit action")
        if type(self.decision) is not str or self.decision not in _DECISIONS:
            _fail("decision is not an allowed audit decision")
        if type(self.reason_code) is not str or not _REASON_CODE.match(self.reason_code):
            _fail("reason_code must be an uppercase code of at most 64 characters")
        if not 0 < _integer(self.occurred_at, "occurred_at") <= _MAX_OCCURRED_AT:
            _fail(f"occurred_at must be between 1 and {_MAX_OCCURRED_AT}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "actor": self.actor.identifier(),
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


def event_from_handoff(handoff: Handoff, *, trace_id: str, actor: ComponentActor, action: str,
                       decision: str, reason_code: str, occurred_at: int) -> AuditEvent:
    """Derive an event from a handoff without copying its payload.

    The job, subject and policy version come from the handoff itself, and the
    payload appears only as the digest the handoff already carries. Payload
    content is read once, by `handoff.to_bytes()`, to hash the exact artifact
    the decision was made about; it is never copied into a field.
    """
    if not isinstance(handoff, Handoff):
        _fail("handoff is invalid")
    if not hmac.compare_digest(hashlib.sha256(canonical(handoff.payload)).hexdigest(),
                               handoff.payload_sha256):
        _fail("handoff payload no longer matches its digest")
    return AuditEvent(
        trace_id=_identifier(trace_id, "trace_id"),
        job_id=_audit_safe(handoff.job_id, "job_id"),
        actor=actor,
        subject=_audit_safe(handoff.user_id, "subject"),
        action=action,
        decision=decision,
        reason_code=reason_code,
        policy_version=_audit_safe(handoff.policy_version, "policy_version"),
        constitution_version=CONSTITUTION_VERSION,
        handoff_sha256=hashlib.sha256(handoff.to_bytes()).hexdigest(),
        payload_sha256=handoff.payload_sha256,
        occurred_at=_integer(occurred_at, "occurred_at"),
    )


def rehydrate_event(value: Any) -> AuditEvent:
    """Rebuild an event read back as data, so a log can be checked, not written.

    An actor normally proves that the audit authority stood behind an event.
    An event read back from storage or a pipe has no such proof of its own; its
    attribution is exactly as trustworthy as the signed head that covers the
    record it sits in. That is why a verifier may rebuild one without holding
    the signing key: nothing rebuilt here counts until `audit_chain.verify` has
    checked the chain against a head signed by the authority.
    """
    if not isinstance(value, dict) or set(value) != _EVENT_KEYS:
        _fail("record carries a malformed event")
    actor = value["actor"]
    if type(actor) is not str or actor.count(":") != 1:
        _fail("record carries a malformed actor")
    component, instance_id = actor.split(":")
    fields = {name: value[name] for name in _EVENT_KEYS - {"actor"}}
    return AuditEvent(actor=ComponentActor(component, instance_id, _ACTOR_PROVENANCE),
                      **fields)
