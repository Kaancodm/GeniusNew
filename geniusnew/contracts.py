"""Strict, side-effect-free contracts at the orchestrator-to-worker boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from typing import Any


class ContractError(ValueError):
    """Raised whenever an untrusted contract cannot be accepted."""


_VERSION = "geniusnew-handoff-v1"
_PENDING = "PENDING_APPROVAL"
_NOT_REQUIRED = "NOT_REQUIRED"
_MAX_WIRE_BYTES = 16 * 1024
_HANDOFF_KEYS = frozenset(
    {
        "version",
        "job_id",
        "user_id",
        "orchestrator_id",
        "worker_agent_id",
        "tier",
        "tools",
        "sandbox_profile",
        "approval_state",
        "policy_version",
        "issued_at",
        "expires_at",
        "payload",
        "payload_sha256",
        "signature",
    }
)


def _fail(message: str) -> None:
    raise ContractError(message)


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{field} must be a non-empty string")
    return value


def _subject_bytes(value: Any) -> bytes:
    """Encode an exact server-side subject without Unicode normalization."""
    subject = _string(value, "subject")
    try:
        return subject.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractError("subject must be valid UTF-8") from exc


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        _fail(f"{field} must be an integer")
    return value


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        _fail(f"{field} must be a non-empty list of strings")
    result = tuple(_string(item, field) for item in value)
    if len(result) != len(set(result)):
        _fail(f"{field} must not contain duplicates")
    return result


def canonical(value: Any) -> bytes:
    """Return the one permitted JSON representation for contract hashing."""
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ContractError("value is not canonical JSON") from exc


def _integrity_key(value: Any) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        _fail("integrity_key must be at least 32 bytes")
    return value


def _signature(value: Any, integrity_key: bytes) -> str:
    return hmac.new(integrity_key, canonical(value), hashlib.sha256).hexdigest()


def _payload(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"text"}:
        _fail("payload must contain only text")
    text = value["text"]
    if not isinstance(text, str) or not text:
        _fail("payload text must be a non-empty string")
    return {"text": text}


@dataclass(frozen=True)
class Grant:
    """Trusted, server-owned authorization facts for one authenticated subject."""

    subject: str
    user_id: str
    worker_agent_id: str
    tier: str
    tools: tuple[str, ...]
    sandbox_profile: str
    requires_approval: bool

    def __post_init__(self) -> None:
        _subject_bytes(self.subject)
        for field in ("user_id", "worker_agent_id", "tier", "sandbox_profile"):
            _string(getattr(self, field), field)
        tools = _string_tuple(self.tools, "tools")
        if tools != self.tools:
            object.__setattr__(self, "tools", tools)
        if type(self.requires_approval) is not bool:
            _fail("requires_approval must be a boolean")


@dataclass(frozen=True)
class Policy:
    """A minimal allow-list policy. It intentionally defaults to no access."""

    version: str
    orchestrator_id: str
    handoff_ttl_seconds: int
    allowed_tools: tuple[str, ...]
    allowed_sandbox_profiles: tuple[str, ...]
    grants: tuple[Grant, ...]

    def __post_init__(self) -> None:
        _string(self.version, "version")
        _string(self.orchestrator_id, "orchestrator_id")
        ttl = _integer(self.handoff_ttl_seconds, "handoff_ttl_seconds")
        if ttl <= 0 or ttl > 300:
            _fail("handoff_ttl_seconds must be between 1 and 300")
        tools = _string_tuple(self.allowed_tools, "allowed_tools")
        profiles = _string_tuple(self.allowed_sandbox_profiles, "allowed_sandbox_profiles")
        if not isinstance(self.grants, (tuple, list)) or not self.grants:
            _fail("grants must be a non-empty collection")
        grants = tuple(self.grants)
        if not all(isinstance(grant, Grant) for grant in grants):
            _fail("grants must contain Grant values")
        subjects = tuple(grant.subject for grant in grants)
        if len(subjects) != len(set(subjects)):
            _fail("grants must have unique subjects")
        for grant in grants:
            if not set(grant.tools).issubset(tools) or grant.sandbox_profile not in profiles:
                _fail("grant exceeds policy allow-lists")
        object.__setattr__(self, "allowed_tools", tools)
        object.__setattr__(self, "allowed_sandbox_profiles", profiles)
        object.__setattr__(self, "grants", grants)

    def grant_for(self, subject: str) -> Grant:
        subject_bytes = _subject_bytes(subject)
        for grant in self.grants:
            if hmac.compare_digest(_subject_bytes(grant.subject), subject_bytes):
                return grant
        _fail("subject is not authorized")


@dataclass(frozen=True)
class Handoff:
    """A validated handoff. Construction is private to :func:`validate`."""

    version: str
    job_id: str
    user_id: str
    orchestrator_id: str
    worker_agent_id: str
    tier: str
    tools: tuple[str, ...]
    sandbox_profile: str
    approval_state: str
    policy_version: str
    issued_at: int
    expires_at: int
    payload: dict[str, str]
    payload_sha256: str
    signature: str

    def to_bytes(self) -> bytes:
        return canonical(
            {
                "version": self.version,
                "job_id": self.job_id,
                "user_id": self.user_id,
                "orchestrator_id": self.orchestrator_id,
                "worker_agent_id": self.worker_agent_id,
                "tier": self.tier,
                "tools": list(self.tools),
                "sandbox_profile": self.sandbox_profile,
                "approval_state": self.approval_state,
                "policy_version": self.policy_version,
                "issued_at": self.issued_at,
                "expires_at": self.expires_at,
                "payload": self.payload,
                "payload_sha256": self.payload_sha256,
                "signature": self.signature,
            }
        )


def _wire_object(wire: Any) -> dict[str, Any]:
    if type(wire) is not bytes or not wire or len(wire) > _MAX_WIRE_BYTES:
        _fail("handoff wire must be a bounded, non-empty bytes value")
    try:
        decoded = wire.decode("utf-8")

        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    _fail("handoff JSON contains duplicate keys")
                result[key] = value
            return result

        value = json.loads(
            decoded,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _: _fail("handoff JSON contains a non-finite value"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("handoff wire is not valid JSON") from exc
    if not isinstance(value, dict):
        _fail("handoff wire must contain an object")
    if set(value) != _HANDOFF_KEYS:
        _fail("handoff fields are not exact")
    if wire != canonical(value):
        _fail("handoff wire is not canonical JSON")
    return value


def _from_object(value: dict[str, Any], *, subject: str, job_id: str, policy: Policy, integrity_key: bytes, now: int, allow_pending: bool = False) -> Handoff:
    if not isinstance(policy, Policy):
        _fail("policy is invalid")
    _string(job_id, "job_id")
    now = _integer(now, "now")
    integrity_key = _integrity_key(integrity_key)
    grant = policy.grant_for(subject)
    for field in ("version", "job_id", "user_id", "orchestrator_id", "worker_agent_id", "tier", "sandbox_profile", "approval_state", "policy_version", "payload_sha256", "signature"):
        _string(value[field], field)
    tools = _string_tuple(value["tools"], "tools")
    issued_at = _integer(value["issued_at"], "issued_at")
    expires_at = _integer(value["expires_at"], "expires_at")
    payload = _payload(value["payload"])
    expected_hash = hashlib.sha256(canonical(payload)).hexdigest()
    if not hmac.compare_digest(value["payload_sha256"], expected_hash):
        _fail("payload hash does not match payload")
    signed = {key: field_value for key, field_value in value.items() if key != "signature"}
    if not hmac.compare_digest(value["signature"], _signature(signed, integrity_key)):
        _fail("handoff signature is invalid")
    expected = {
        "version": _VERSION,
        "job_id": job_id,
        "user_id": grant.user_id,
        "orchestrator_id": policy.orchestrator_id,
        "worker_agent_id": grant.worker_agent_id,
        "tier": grant.tier,
        "tools": grant.tools,
        "sandbox_profile": grant.sandbox_profile,
        "approval_state": _PENDING if grant.requires_approval else _NOT_REQUIRED,
        "policy_version": policy.version,
        "expires_at": issued_at + policy.handoff_ttl_seconds,
    }
    for field, expected_value in expected.items():
        actual = tools if field == "tools" else value[field]
        if actual != expected_value:
            _fail(f"handoff {field} does not match trusted policy")
    if issued_at > now or now >= expires_at:
        _fail("handoff is not currently valid")
    if value["approval_state"] == _PENDING and not allow_pending:
        _fail("pending approval handoff cannot reach a worker")
    return Handoff(
        version=value["version"], job_id=value["job_id"], user_id=value["user_id"],
        orchestrator_id=value["orchestrator_id"], worker_agent_id=value["worker_agent_id"],
        tier=value["tier"], tools=tools, sandbox_profile=value["sandbox_profile"],
        approval_state=value["approval_state"], policy_version=value["policy_version"],
        issued_at=issued_at, expires_at=expires_at, payload=payload,
        payload_sha256=value["payload_sha256"],
        signature=value["signature"],
    )


def issue(request: Any, *, subject: str, job_id: str, policy: Policy, integrity_key: bytes, now: int) -> bytes:
    """Issue a new wire contract using only trusted policy data."""
    payload = _payload(request)
    now = _integer(now, "now")
    integrity_key = _integrity_key(integrity_key)
    if not isinstance(policy, Policy):
        _fail("policy is invalid")
    grant = policy.grant_for(subject)
    _string(job_id, "job_id")
    body = {
        "version": _VERSION,
        "job_id": job_id,
        "user_id": grant.user_id,
        "orchestrator_id": policy.orchestrator_id,
        "worker_agent_id": grant.worker_agent_id,
        "tier": grant.tier,
        "tools": list(grant.tools),
        "sandbox_profile": grant.sandbox_profile,
        "approval_state": _PENDING if grant.requires_approval else _NOT_REQUIRED,
        "policy_version": policy.version,
        "issued_at": now,
        "expires_at": now + policy.handoff_ttl_seconds,
        "payload": payload,
        "payload_sha256": hashlib.sha256(canonical(payload)).hexdigest(),
    }
    body["signature"] = _signature(body, integrity_key)
    return canonical(body)


def validate(wire: Any, *, subject: str, job_id: str, policy: Policy, integrity_key: bytes, now: int) -> Handoff:
    """Deserialize, validate, and bind a handoff to the current trust context."""
    return _from_object(
        _wire_object(wire), subject=subject, job_id=job_id, policy=policy,
        integrity_key=integrity_key, now=now,
    )


def validate_pending(wire: Any, *, subject: str, job_id: str, policy: Policy, integrity_key: bytes, now: int) -> Handoff:
    """Validate a pending handoff at the server-side approval boundary only."""
    handoff = _from_object(
        _wire_object(wire), subject=subject, job_id=job_id, policy=policy,
        integrity_key=integrity_key, now=now, allow_pending=True,
    )
    if handoff.approval_state != _PENDING:
        _fail("handoff does not require approval")
    return handoff
