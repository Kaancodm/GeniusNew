"""Server-side, process-local approval tokens for pending handoffs."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import secrets
from threading import RLock
from typing import Callable

from .contracts import ContractError, Handoff, canonical, validate_pending


_GRANTED = "GRANTED"
_CONSUMED = "CONSUMED"
_REVOKED = "REVOKED"
_EXECUTE_HANDOFF = "EXECUTE_HANDOFF"
_MAX_TTL_SECONDS = 600
_TOKEN_BYTES = 32


def _fail(message: str) -> None:
    raise ContractError(message)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{field} must be a non-empty string")
    return value


def _integer(value: object, field: str) -> int:
    if type(value) is not int:
        _fail(f"{field} must be an integer")
    return value


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class ApprovalScope:
    """Facts that must match exactly when an approval is consumed."""

    handoff_sha256: str
    handoff_expires_at: int
    job_id: str
    user_id: str
    worker_agent_id: str
    risk_tier: str
    policy_version: str
    action: str

    def __post_init__(self) -> None:
        for field in ("handoff_sha256", "job_id", "user_id", "worker_agent_id", "risk_tier", "policy_version", "action"):
            _string(getattr(self, field), field)
        if _integer(self.handoff_expires_at, "handoff_expires_at") <= 0:
            _fail("handoff_expires_at must be positive")
        if len(self.handoff_sha256) != 64 or any(char not in "0123456789abcdef" for char in self.handoff_sha256):
            _fail("handoff_sha256 must be a lowercase SHA-256 digest")
        if self.action != _EXECUTE_HANDOFF:
            _fail("action is not allowed")

    def to_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "handoff_expires_at": self.handoff_expires_at,
            "handoff_sha256": self.handoff_sha256,
            "job_id": self.job_id,
            "policy_version": self.policy_version,
            "risk_tier": self.risk_tier,
            "user_id": self.user_id,
            "worker_agent_id": self.worker_agent_id,
        }


@dataclass(frozen=True)
class ApprovalGrant:
    """A raw token returned once to the trusted approval presenter."""

    token: bytes = field(repr=False)
    scope: ApprovalScope
    issued_at: int
    expires_at: int
    record_hash: str


@dataclass(frozen=True)
class ApprovalReceipt:
    """A hash-chained state transition with no recoverable raw token."""

    scope: ApprovalScope
    state: str
    changed_at: int
    record_hash: str


@dataclass(frozen=True)
class _Record:
    token_digest: bytes
    scope: ApprovalScope
    issued_at: int
    expires_at: int
    state: str
    changed_at: int
    previous_hash: str | None
    record_hash: str


def _record_hash(*, token_digest: bytes, scope: ApprovalScope, issued_at: int, expires_at: int,
                 state: str, changed_at: int, previous_hash: str | None) -> str:
    return _sha256_hex(canonical({
        "changed_at": changed_at,
        "expires_at": expires_at,
        "issued_at": issued_at,
        "previous_hash": previous_hash,
        "scope": scope.to_dict(),
        "state": state,
        "token_digest": token_digest.hex(),
    }))


def _scope_matches(left: ApprovalScope, right: ApprovalScope) -> bool:
    return hmac.compare_digest(canonical(left.to_dict()), canonical(right.to_dict()))


def create_scope(wire: bytes, *, subject: str, job_id: str, policy: object,
                 integrity_key: bytes, now: int) -> ApprovalScope:
    """Create a scope only from a currently valid, signed pending handoff."""
    handoff: Handoff = validate_pending(
        wire, subject=subject, job_id=job_id, policy=policy,
        integrity_key=integrity_key, now=now,
    )
    return ApprovalScope(
        handoff_sha256=_sha256_hex(handoff.to_bytes()),
        handoff_expires_at=handoff.expires_at,
        job_id=handoff.job_id,
        user_id=handoff.user_id,
        worker_agent_id=handoff.worker_agent_id,
        risk_tier=handoff.tier,
        policy_version=handoff.policy_version,
        action=_EXECUTE_HANDOFF,
    )


class ApprovalStore:
    """Thread-safe in-memory store; production callers must provide durable storage."""

    def __init__(self, *, token_source: Callable[[], bytes] | None = None) -> None:
        self._token_source = token_source or (lambda: secrets.token_bytes(_TOKEN_BYTES))
        self._records: dict[bytes, _Record] = {}
        self._lock = RLock()

    def grant(self, scope: ApprovalScope, *, now: int, ttl_seconds: int) -> ApprovalGrant:
        if not isinstance(scope, ApprovalScope):
            _fail("scope is invalid")
        now = _integer(now, "now")
        ttl_seconds = _integer(ttl_seconds, "ttl_seconds")
        if ttl_seconds <= 0 or ttl_seconds > _MAX_TTL_SECONDS:
            _fail("ttl_seconds must be between 1 and 600")
        if now >= scope.handoff_expires_at:
            _fail("handoff is expired")
        token = self._token_source()
        if type(token) is not bytes or len(token) < _TOKEN_BYTES:
            _fail("approval token source must return at least 32 bytes")
        digest = hashlib.sha256(token).digest()
        expires_at = min(now + ttl_seconds, scope.handoff_expires_at)
        record_hash = _record_hash(
            token_digest=digest, scope=scope, issued_at=now, expires_at=expires_at,
            state=_GRANTED, changed_at=now, previous_hash=None,
        )
        record = _Record(digest, scope, now, expires_at, _GRANTED, now, None, record_hash)
        with self._lock:
            if digest in self._records:
                _fail("approval token collision")
            self._records[digest] = record
        return ApprovalGrant(token, scope, now, expires_at, record_hash)

    def consume(self, token: bytes, scope: ApprovalScope, *, now: int) -> ApprovalReceipt:
        return self._transition(token, scope, now=now, new_state=_CONSUMED, require_unexpired=True)

    def revoke(self, token: bytes, scope: ApprovalScope, *, now: int) -> ApprovalReceipt:
        return self._transition(token, scope, now=now, new_state=_REVOKED, require_unexpired=False)

    def _transition(self, token: bytes, scope: ApprovalScope, *, now: int, new_state: str,
                    require_unexpired: bool) -> ApprovalReceipt:
        if type(token) is not bytes or len(token) < _TOKEN_BYTES:
            _fail("approval token is invalid")
        if not isinstance(scope, ApprovalScope):
            _fail("scope is invalid")
        now = _integer(now, "now")
        digest = hashlib.sha256(token).digest()
        with self._lock:
            record = self._records.get(digest)
            if record is None or not hmac.compare_digest(record.token_digest, digest):
                _fail("approval token is unknown")
            if not _scope_matches(record.scope, scope):
                _fail("approval scope does not match")
            if record.state != _GRANTED:
                _fail("approval is not granted")
            if now < record.issued_at:
                _fail("approval is not currently valid")
            if require_unexpired and now >= record.expires_at:
                _fail("approval is expired")
            record_hash = _record_hash(
                token_digest=record.token_digest, scope=record.scope,
                issued_at=record.issued_at, expires_at=record.expires_at,
                state=new_state, changed_at=now, previous_hash=record.record_hash,
            )
            updated = _Record(record.token_digest, record.scope, record.issued_at,
                              record.expires_at, new_state, now, record.record_hash,
                              record_hash)
            self._records[digest] = updated
        return ApprovalReceipt(updated.scope, updated.state, updated.changed_at, updated.record_hash)
