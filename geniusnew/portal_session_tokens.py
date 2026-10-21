"""Opaque Portal session and CSRF tokens; persistence remains a separate boundary."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets

from .contracts import ContractError

_TOKEN_BYTES = 32
_TOKEN_TEXT = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_CSRF_LABEL = b"geniusnew/portal/csrf/v1\x00"
_COOKIE_NAME = "__Host-geniusnew_session"
_COOKIE_ATTRIBUTES = "; Path=/; Secure; HttpOnly; SameSite=Lax"
_SESSION_SECONDS = 8 * 60 * 60


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if type(value) is not str or _TOKEN_TEXT.fullmatch(value) is None:
        raise ContractError("invalid Portal token")
    try:
        raw = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ContractError("invalid Portal token") from exc
    if len(raw) != _TOKEN_BYTES or _encode(raw) != value:
        raise ContractError("invalid Portal token")
    return raw


def new_session_token() -> str:
    """Mint an opaque token whose raw value must only reach the browser."""
    try:
        raw = secrets.token_bytes(_TOKEN_BYTES)
    except OSError:
        raw = None
    if type(raw) is not bytes or len(raw) != _TOKEN_BYTES:
        raise ContractError("session entropy unavailable")
    return _encode(raw)


def session_digest(token: str) -> str:
    """Produce the only session-token representation allowed in Portal storage."""
    return hashlib.sha256(_decode(token)).hexdigest()


def _csrf_key(key: bytes) -> bytes:
    if type(key) is not bytes or len(key) < 32:
        raise ContractError("invalid Portal CSRF key")
    return key


def csrf_token(session_token: str, key: bytes) -> str:
    """Bind a form token to the active session under a server-only key."""
    raw = _decode(session_token)
    return _encode(hmac.new(_csrf_key(key), _CSRF_LABEL + raw,
                            hashlib.sha256).digest())


def verify_csrf_token(session_token: str, key: bytes, submitted: str) -> None:
    """Refuse any state-changing form without its session-bound CSRF token."""
    supplied = _decode(submitted)
    expected = _decode(csrf_token(session_token, key))
    if not hmac.compare_digest(supplied, expected):
        raise ContractError("invalid Portal CSRF token")


def session_cookie(token: str) -> str:
    """Set a host-only, HTTPS-only cookie with an absolute eight-hour lifetime."""
    _decode(token)
    return f"{_COOKIE_NAME}={token}{_COOKIE_ATTRIBUTES}; Max-Age={_SESSION_SECONDS}"


def clear_session_cookie() -> str:
    """Expire the exact cookie name and scope used at login."""
    return f"{_COOKIE_NAME}={_COOKIE_ATTRIBUTES}; Max-Age=0"
