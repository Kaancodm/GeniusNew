"""Bounded messages between the signing core and one inference broker call.

These messages carry only execution data. They are not a Handoff, approval,
budget receipt, or authority to sign a result. The caller must obtain those
decisions at their existing boundaries before sending a request.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from .contracts import ContractError, canonical


_VERSION = 1
_MAX_MESSAGE_BYTES = 32 * 1024
_MAX_TEXT_BYTES = 8 * 1024
_MAX_TTL_SECONDS = 300
_MAX_TIME = 4102444800
_MAX_TOKENS = 1_000_000
_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")
_REQUEST_KEYS = frozenset({"version", "job_id", "handoff_sha256", "expires_at", "text"})
_RESPONSE_KEYS = frozenset({"version", "job_id", "handoff_sha256", "text", "usage"})
_USAGE_KEYS = frozenset({"input_tokens", "output_tokens"})


def _fail(message: str) -> None:
    raise ContractError(message)


def _job_id(value: Any) -> str:
    if type(value) is not str or not value:
        _fail("broker job_id must be a non-empty string")
    length = None
    try:
        length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        pass
    if length is None:
        _fail("broker job_id must be valid UTF-8")
    if length > 128:
        _fail("broker job_id is too long")
    return value


def _digest(value: Any) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _fail("broker handoff digest is invalid")
    return value


def _text(value: Any) -> str:
    if type(value) is not str or not value:
        _fail("broker text must be a non-empty string")
    length = None
    try:
        length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        pass
    if length is None:
        _fail("broker text must be valid UTF-8")
    if length > _MAX_TEXT_BYTES:
        _fail("broker text is too large")
    return value


def _decode(data: Any, keys: frozenset[str]) -> dict[str, Any]:
    if type(data) is not bytes or not data or len(data) > _MAX_MESSAGE_BYTES:
        _fail("broker message must be bounded bytes")
    value = None
    try:
        value = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        pass
    if value is None:
        _fail("broker message is invalid JSON")
    if type(value) is not dict or set(value) != keys:
        _fail("broker message fields are not exact")
    encoded = None
    try:
        encoded = canonical(value)
    except ContractError:
        pass
    if encoded != data:
        _fail("broker message is not canonical JSON")
    return value


@dataclass(frozen=True, repr=False)
class BrokerRequest:
    job_id: str
    handoff_sha256: str
    expires_at: int
    text: str

    def __post_init__(self) -> None:
        _job_id(self.job_id)
        _digest(self.handoff_sha256)
        if type(self.expires_at) is not int or not 1 <= self.expires_at <= _MAX_TIME:
            _fail("broker deadline is invalid")
        _text(self.text)

    def to_bytes(self) -> bytes:
        data = canonical({"version": _VERSION, "job_id": self.job_id,
                          "handoff_sha256": self.handoff_sha256,
                          "expires_at": self.expires_at, "text": self.text})
        if len(data) > _MAX_MESSAGE_BYTES:
            _fail("broker request is too large")
        return data

    def __repr__(self) -> str:
        return f"BrokerRequest(job_id={self.job_id!r}, text=<redacted>)"


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens):
            if type(value) is not int or not 0 <= value <= _MAX_TOKENS:
                _fail("broker usage is invalid")

    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens}


@dataclass(frozen=True, repr=False)
class BrokerResponse:
    job_id: str
    handoff_sha256: str
    text: str
    usage: Usage | None

    def __post_init__(self) -> None:
        _job_id(self.job_id)
        _digest(self.handoff_sha256)
        _text(self.text)
        if self.usage is not None and not isinstance(self.usage, Usage):
            _fail("broker usage is invalid")

    def to_bytes(self) -> bytes:
        data = canonical({"version": _VERSION, "job_id": self.job_id,
                          "handoff_sha256": self.handoff_sha256,
                          "text": self.text,
                          "usage": None if self.usage is None else self.usage.to_dict()})
        if len(data) > _MAX_MESSAGE_BYTES:
            _fail("broker response is too large")
        return data

    def __repr__(self) -> str:
        return f"BrokerResponse(job_id={self.job_id!r}, text=<redacted>)"


def decode_request(data: bytes, *, now: int) -> BrokerRequest:
    if type(now) is not int or not 1 <= now <= _MAX_TIME:
        _fail("broker time is invalid")
    value = _decode(data, _REQUEST_KEYS)
    if type(value["version"]) is not int or value["version"] != _VERSION:
        _fail("broker request version is invalid")
    request = BrokerRequest(value["job_id"], value["handoff_sha256"],
                            value["expires_at"], value["text"])
    if now >= request.expires_at:
        _fail("broker request has expired")
    if request.expires_at - now > _MAX_TTL_SECONDS:
        _fail("broker request deadline exceeds the handoff limit")
    return request


def decode_response(data: bytes, *, job_id: str,
                    handoff_sha256: str) -> BrokerResponse:
    _job_id(job_id)
    _digest(handoff_sha256)
    value = _decode(data, _RESPONSE_KEYS)
    if type(value["version"]) is not int or value["version"] != _VERSION:
        _fail("broker response version is invalid")
    usage = value["usage"]
    if usage is not None:
        if type(usage) is not dict or set(usage) != _USAGE_KEYS:
            _fail("broker usage fields are not exact")
        usage = Usage(usage["input_tokens"], usage["output_tokens"])
    response = BrokerResponse(value["job_id"], value["handoff_sha256"],
                              value["text"], usage)
    if response.job_id != job_id or response.handoff_sha256 != handoff_sha256:
        _fail("broker response belongs to another job")
    return response
