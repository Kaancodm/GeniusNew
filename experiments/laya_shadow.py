"""Shadow-only decision adapter for Jev-compatible local services.

This module never authorizes, dispatches, denies, or mutates a GeniusNew job.  It
serializes a bounded routing question, sends it to an explicitly configured local
decision service, validates the returned choice, and emits a comparison record.

The caller owns the authoritative decision.  A model failure therefore produces an
UNKNOWN observation, never a fallback authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable
from urllib import request


_ALLOWED_AGENTS = ("claude", "gemini", "codex", "zen")
_MAX_TEXT = 4096
_MAX_RESPONSE = 64 * 1024


class ShadowError(ValueError):
    """Raised when shadow input or output violates the experiment contract."""


@dataclass(frozen=True)
class ShadowObservation:
    model_choice: str | None
    authoritative_choice: str
    matched: bool | None
    status: str

    def as_dict(self) -> dict[str, object]:
        return {
            "model_choice": self.model_choice,
            "authoritative_choice": self.authoritative_choice,
            "matched": self.matched,
            "status": self.status,
        }


def _validate_choice(value: object) -> str:
    if not isinstance(value, str) or value not in _ALLOWED_AGENTS:
        raise ShadowError("choice must be one of the configured agents")
    return value


def _validate_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
        raise ShadowError(f"{field} must be non-empty text up to {_MAX_TEXT} characters")
    return value


def build_request(task: str) -> bytes:
    task = _validate_text(task, "task")
    payload = {
        "state": task,
        "questions": {
            "agent": {
                "type": "choice",
                "instructions": "Which agent should handle this GeniusNew task?",
                "criteria": {
                    "claude": "security, architecture, and difficult integration review",
                    "gemini": "knowledge synthesis, documentation, and contradiction review",
                    "codex": "implementation, tests, and focused code changes",
                    "zen": "general planning and coordination",
                },
            }
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_choice(raw: bytes) -> str:
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_RESPONSE:
        raise ShadowError("response must be bounded non-empty bytes")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShadowError("response must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ShadowError("response must be an object")

    answers = decoded.get("answers")
    if not isinstance(answers, dict):
        raise ShadowError("response must contain answers")
    agent = answers.get("agent")
    if not isinstance(agent, dict):
        raise ShadowError("response must contain agent answer")
    if agent.get("type") != "choice":
        raise ShadowError("agent answer must be a choice")
    return _validate_choice(agent.get("choice"))


def observe(
    task: str,
    authoritative_choice: str,
    decide: Callable[[bytes], bytes],
) -> ShadowObservation:
    authoritative_choice = _validate_choice(authoritative_choice)
    wire = build_request(task)
    try:
        model_choice = parse_choice(decide(wire))
    except (ShadowError, OSError, TimeoutError):
        return ShadowObservation(None, authoritative_choice, None, "UNKNOWN")
    return ShadowObservation(
        model_choice,
        authoritative_choice,
        model_choice == authoritative_choice,
        "OBSERVED",
    )


def http_decider(endpoint: str, timeout: float = 2.0) -> Callable[[bytes], bytes]:
    if not isinstance(endpoint, str) or not endpoint.startswith(
        ("http://127.0.0.1:", "http://localhost:")
    ):
        raise ShadowError("shadow endpoint must be loopback HTTP")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ShadowError("timeout must be positive")

    def decide(wire: bytes) -> bytes:
        req = request.Request(
            endpoint,
            data=wire,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=float(timeout)) as response:
            if response.status != 200:
                raise OSError("shadow service returned non-200")
            raw = response.read(_MAX_RESPONSE + 1)
        if len(raw) > _MAX_RESPONSE:
            raise ShadowError("shadow response exceeded limit")
        return raw

    return decide
