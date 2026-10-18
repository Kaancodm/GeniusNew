"""Canonical, unsigned D1 request data. This module grants no Core authority."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .contracts import ContractError

_IDENTIFIER = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_NONCE = re.compile(r"[0-9a-f]{64}\Z")
_BODY_LIMIT = 16384
_ENVELOPE_LIMIT = 4096
_UNIX_2100 = 4102444800
_FIELDS = frozenset({"version", "kind", "issuer", "audience", "key_id", "nonce",
                     "issued_at", "expires_at", "method", "path", "body_sha256"})


def _fail(message: str) -> None:
    raise ContractError(message)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in items:
        if key in result:
            _fail("duplicate JSON key")
        result[key] = value
    return result


def _forbid_number(value: str) -> None:
    _fail("non-integer JSON number")


def _bounded_int(value: str) -> int:
    # D1 timestamps fit in ten digits; bound parsing before Python's integer
    # conversion limit can turn an otherwise valid-size body into ValueError.
    if len(value) > 10:
        _fail("JSON integer exceeds D1 range")
    return int(value)


def _read_json(wire: bytes, limit: int) -> Any:
    if type(wire) is not bytes or not 0 < len(wire) <= limit:
        _fail("invalid JSON byte length")
    try:
        value = json.loads(wire.decode("ascii"), object_pairs_hook=_pairs,
                           parse_int=_bounded_int, parse_float=_forbid_number,
                           parse_constant=_forbid_number)
    except ContractError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ContractError("invalid ASCII JSON") from exc
    return value


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def _text(value: Any) -> str:
    if type(value) is not str or not value:
        _fail("text must be a non-empty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractError("text contains an invalid Unicode scalar") from exc
    return value


def encode_body(text: str) -> bytes:
    """Create the single permitted byte representation of a D1 request body."""
    wire = _canonical({"text": _text(text)})
    if len(wire) > _BODY_LIMIT:
        _fail("body exceeds D1 limit")
    return wire


def parse_body(wire: bytes) -> str:
    """Check received bytes before they can be bound to a signed digest."""
    value = _read_json(wire, _BODY_LIMIT)
    if type(value) is not dict or set(value) != {"text"}:
        _fail("body must contain only text")
    text = _text(value["text"])
    if encode_body(text) != wire:
        _fail("body is not canonical JSON")
    return text


def _identifier(value: Any, name: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        _fail(f"{name} is not a D1 identifier")
    return value


def _valid_envelope(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _FIELDS:
        _fail("envelope has the wrong fields")
    for name in ("issuer", "audience", "key_id"):
        _identifier(value[name], name)
    if (value["version"] != "geniusnew-portal-core-d1"
            or value["kind"] != "REQUEST" or value["method"] != "POST"
            or value["path"] != "/jobs"):
        _fail("envelope has an unsupported route or version")
    if type(value["nonce"]) is not str or _NONCE.fullmatch(value["nonce"]) is None:
        _fail("nonce must be 32 lowercase hex bytes")
    if type(value["body_sha256"]) is not str or _NONCE.fullmatch(value["body_sha256"]) is None:
        _fail("body digest must be lowercase SHA-256 hex")
    issued, expires = value["issued_at"], value["expires_at"]
    if (type(issued) is not int or type(expires) is not int
            or not 0 <= issued < expires < _UNIX_2100 or expires - issued > 60):
        _fail("invalid D1 validity interval")
    return value


def encode_unsigned_envelope(body: bytes, *, issuer: str, audience: str, key_id: str,
                             nonce: str, issued_at: int, expires_at: int) -> bytes:
    """Build bytes for a Portal signer after validating the exact body."""
    parse_body(body)
    value = _valid_envelope({
        "version": "geniusnew-portal-core-d1", "kind": "REQUEST",
        "issuer": issuer, "audience": audience, "key_id": key_id, "nonce": nonce,
        "issued_at": issued_at, "expires_at": expires_at,
        "method": "POST", "path": "/jobs",
        "body_sha256": hashlib.sha256(body).hexdigest(),
    })
    # The closed field set and 64-character identifier bounds keep this below
    # the wire limit; the receiver still checks the limit before parsing.
    return _canonical(value)


def parse_unsigned_envelope(wire: bytes, body: bytes, *, issuer: str, audience: str,
                            key_id: str) -> dict[str, Any]:
    """Check syntax and configured identity; this does not verify a signature."""
    parse_body(body)
    value = _valid_envelope(_read_json(wire, _ENVELOPE_LIMIT))
    if _canonical(value) != wire:
        _fail("envelope is not canonical JSON")
    if (value["issuer"] != _identifier(issuer, "issuer")
            or value["audience"] != _identifier(audience, "audience")
            or value["key_id"] != _identifier(key_id, "key_id")):
        _fail("envelope identity is not configured")
    if value["body_sha256"] != hashlib.sha256(body).hexdigest():
        _fail("body digest mismatch")
    return value
