"""What a worker may hand back, and what the acceptance boundary will take.

`docs/ROADMAP-V01.md` step 9 asks for a result that is signed by the worker and
checked on acceptance, symmetrically to the incoming handoff. Without it the
project goal — "das Ergebnis kommt **signiert** zurück und wird von einer
weiteren unabhängigen Instanz angenommen" — has nothing to stand on.

Symmetry is the point, so this mirrors `contracts.py` deliberately: `produce`
is to `issue` what `accept` is to `validate`, the wire is decoded by the same
`decode_wire`, and the same refusals apply.

## The key is not the handoff key

`CONSTITUTION-V1-DRAFT.md` section 8 keeps Ausführung separate from
Orchestrierung. The handoff integrity key is what mints authorizations: a
worker holding it could issue itself a handoff granting any tool in the policy.
So the worker signs results with its own key, held by `WorkerAuthority`.

That separation is guaranteed by `keys.derive_keys`, not by this module. The
optional `integrity_key` comparison here catches an accidental reuse when a
caller provisions keys by hand; it is a defence rather than an invariant,
because a caller who omits it is not checked at all.

## What the signature is bound to

A signature over the output alone would let a result be lifted from one job and
presented as the answer to another. The signed body therefore carries the digest
of the exact handoff artifact it answers, derived the same way `audit.py`
derives `handoff_sha256`, so a result, its audit entry and its handoff all
reference the same artifact.

## A failed result carries nothing

`status` is `FAILED` or `SUCCEEDED`. A failure has no output at all — not an
empty one. Otherwise "failure" becomes a channel that returns data while
skipping every check that applies to output, which is the shape of an
exfiltration path rather than an error path. `reason_code` comes from a closed set for the
same reason: a field on a boundary between trust domains is somewhere to put
things that do not belong there, and an uppercase *shape* is not a closed set —
sixty characters of `[A-Z0-9_]` is sixty characters of base32.

## What this does not do

- **HMAC is symmetric.** Anything that can verify a result could also sign one,
  so `accept` is not cryptographically distinguishable from `produce`. The
  independence required by step 14 is organisational here, not cryptographic.
  A private signing key against a public verification key needs a primitive
  outside the standard library and is a dependency decision.
- **One worker key, not one per worker.** `worker_agent_id` is inside the signed
  body, so a result names its producer and that name cannot be edited in
  flight. With a single shared key, however, one worker can still forge
  another's result. Per-worker keys are the strengthening, and a key-management
  decision rather than a change here.
- **Acceptance is not single-use.** A result is bound to its handoff, but
  replaying the *same* result twice is not prevented by a contract, which holds
  no state. That is the job of the accepting instance, in the way
  `approvals.py` makes an approval single-use.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
from typing import Any

from .contracts import ContractError, Handoff, canonical, decode_wire

_RESULT_VERSION = "geniusnew-result-v1"
_SUCCEEDED = "SUCCEEDED"
_FAILED = "FAILED"
_STATUSES = frozenset({_SUCCEEDED, _FAILED})
_MAX_PRODUCED_AT = 4102444800  # 2100-01-01T00:00:00Z
# A closed set, not a shape. An uppercase pattern still leaves sixty-odd
# characters a compromised worker can fill with base32 and hand back under
# `FAILED`, where no output check applies — which is the exfiltration path the
# empty-output rule exists to close, reopened one field along. Adding a code is
# a deliberate change here; encoding data in one is not possible.
_REASON_CODES = frozenset({
    "WORK_COMPLETED",
    "TOOL_NOT_GRANTED",
    "WORKER_FAILED",
    "OUTPUT_REJECTED",
    "PAYLOAD_MUTATED",
    "HANDOFF_EXPIRED",
    "RESOURCE_EXHAUSTED",
    "ISOLATION_VIOLATED",
})
_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")
_RESULT_KEYS = frozenset({
    "version",
    "job_id",
    "worker_agent_id",
    "handoff_sha256",
    "status",
    "reason_code",
    "output",
    "output_sha256",
    "produced_at",
    "signature",
})


def _fail(message: str) -> None:
    raise ContractError(message)


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{field} must be a non-empty string")
    return value


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        _fail(f"{field} must be an integer")
    return value


def _digest(value: Any, field: str) -> str:
    if type(value) is not str or not _DIGEST.match(value):
        _fail(f"{field} must be a lowercase SHA-256 digest")
    return value


def _output(value: Any) -> dict[str, str]:
    """The same shape a handoff payload has, held to the same rule.

    `test_results.py` pins that this and the handoff payload accept and refuse
    exactly the same values, so the symmetry is a check rather than a comment.
    """
    if not isinstance(value, dict) or set(value) != {"text"}:
        _fail("output must contain only text")
    text = value["text"]
    if not isinstance(text, str) or not text:
        _fail("output text must be a non-empty string")
    return {"text": text}


def _reason_code(value: Any) -> str:
    if type(value) is not str or value not in _REASON_CODES:
        _fail("reason_code is not one of the allowed result reason codes")
    return value


def handoff_digest(handoff: Any) -> str:
    """Digest the exact handoff artifact a result answers.

    Derived identically in `audit.py`, so an audit entry and a result that
    reference the same job reference the same bytes.
    """
    if not isinstance(handoff, Handoff):
        _fail("handoff is invalid")
    # `Handoff.payload` is still a mutable dict, and `to_bytes()` serializes
    # whatever it holds now. Mutate it after validation and both sides compute
    # the same digest over the same mutated object, so the result binds to a
    # payload nobody ever signed. `payload_sha256` is inside the signed body and
    # does not move, so comparing against it is what makes the binding mean
    # something. `audit.py` does the same, for the same reason.
    if not hmac.compare_digest(hashlib.sha256(canonical(handoff.payload)).hexdigest(),
                               handoff.payload_sha256):
        _fail("handoff payload no longer matches the digest it was signed with")
    return hashlib.sha256(handoff.to_bytes()).hexdigest()


class WorkerAuthority:
    """Holds the result-signing key, which is **not** the handoff integrity key.

    Separating them is what stops a worker from authorizing its own execution:
    the handoff key mints authorizations, this one only attests to what came
    back.

    Where that separation is actually guaranteed is `keys.derive_keys`, which
    produces both from one root secret under distinct labels so they cannot be
    equal and no caller has to hold one to obtain the other. The `integrity_key`
    argument here is a **defence, not the invariant**: a caller who provisions
    keys some other way can disclose the handoff key and have an accidental
    reuse refused, but a caller who omits it is not checked at all. Presenting
    an optional comparison as an invariant is how it was first written, and it
    made the separation depend on every caller remembering — and on passing a
    minting key around to prove it was not being used.
    """

    def __init__(self, *, result_key: bytes, integrity_key: bytes | None = None) -> None:
        if type(result_key) is not bytes or len(result_key) < 32:
            _fail("result_key must be at least 32 bytes")
        if integrity_key is not None:
            if type(integrity_key) is not bytes:
                _fail("integrity_key must be bytes")
            if hmac.compare_digest(result_key, integrity_key):
                _fail("result_key must not be the handoff integrity key")
        self._result_key = result_key

    @property
    def result_key(self) -> bytes:
        return self._result_key

    def sign(self, body: dict[str, Any]) -> str:
        return hmac.new(self._result_key, canonical(body), hashlib.sha256).hexdigest()


def _authority(value: Any) -> WorkerAuthority:
    if not isinstance(value, WorkerAuthority):
        _fail("authority must be a WorkerAuthority")
    return value


@dataclass(frozen=True)
class Result:
    """A validated result. Construction is private to :func:`accept`."""

    version: str
    job_id: str
    worker_agent_id: str
    handoff_sha256: str
    status: str
    reason_code: str
    output: dict[str, str] | None
    output_sha256: str | None
    produced_at: int
    signature: str

    @property
    def succeeded(self) -> bool:
        return self.status == _SUCCEEDED

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "job_id": self.job_id,
            "worker_agent_id": self.worker_agent_id,
            "handoff_sha256": self.handoff_sha256,
            "status": self.status,
            "reason_code": self.reason_code,
            "output": self.output,
            "output_sha256": self.output_sha256,
            "produced_at": self.produced_at,
            "signature": self.signature,
        }

    def to_bytes(self) -> bytes:
        return canonical(self.to_dict())


def produce(output: Any, *, handoff: Handoff, status: str, reason_code: str,
            authority: WorkerAuthority, now: int) -> bytes:
    """Sign what a worker hands back, bound to the handoff it answers.

    The wire is decoded through `decode_wire` before it is returned. That is not
    a formality: `issue` could emit a handoff too large for `validate` to accept
    until commit 2525475 fixed it. Checking against a second constant would let
    the two drift again, so this checks against the decoder the acceptance side
    actually uses.

    That check is structural only — a well-formed wire can still be refused on
    its content — so the time bounds `accept` enforces are repeated here rather
    than left to it. A worker that cannot sign a result acceptance would throw
    away learns so at the point of signing.
    """
    if not isinstance(handoff, Handoff):
        _fail("handoff is invalid")
    key_holder = _authority(authority)
    now = _integer(now, "now")
    # The same bounds `accept` applies. Without them `produce` signs a result
    # that acceptance will refuse — the wire-level self-check below does not
    # catch it, because the wire is perfectly well formed.
    if not 0 < now <= _MAX_PRODUCED_AT:
        _fail(f"now must be between 1 and {_MAX_PRODUCED_AT}")
    if now < handoff.issued_at:
        _fail("result predates the handoff it answers")
    if now >= handoff.expires_at:
        _fail("result was produced after its handoff expired")
    if type(status) is not str or status not in _STATUSES:
        _fail("status is not an allowed result status")
    body: dict[str, Any] = {
        "version": _RESULT_VERSION,
        "job_id": handoff.job_id,
        "worker_agent_id": handoff.worker_agent_id,
        "handoff_sha256": handoff_digest(handoff),
        "status": status,
        "reason_code": _reason_code(reason_code),
        "output": None,
        "output_sha256": None,
        "produced_at": now,
    }
    if status == _SUCCEEDED:
        produced = _output(output)
        body["output"] = produced
        body["output_sha256"] = hashlib.sha256(canonical(produced)).hexdigest()
    elif output is not None:
        _fail("a failed result must not carry output")
    body["signature"] = key_holder.sign(body)
    wire = canonical(body)
    decode_wire(wire, keys=_RESULT_KEYS, noun="result")
    return wire


def accept(wire: Any, *, handoff: Handoff, authority: WorkerAuthority, now: int) -> Result:
    """Take a result only if it answers this handoff, unaltered and in time.

    Raises `ContractError` on the first problem found, so a caller that treats
    any refusal as "do not use this result" needs no further interpretation.
    """
    if not isinstance(handoff, Handoff):
        _fail("handoff is invalid")
    key_holder = _authority(authority)
    now = _integer(now, "now")
    value = decode_wire(wire, keys=_RESULT_KEYS, noun="result")

    for field in ("version", "job_id", "worker_agent_id", "status", "reason_code", "signature"):
        _string(value[field], field)
    _digest(value["handoff_sha256"], "handoff_sha256")
    _reason_code(value["reason_code"])
    produced_at = _integer(value["produced_at"], "produced_at")

    signed = {key: field_value for key, field_value in value.items() if key != "signature"}
    if not hmac.compare_digest(value["signature"], key_holder.sign(signed)):
        _fail("result signature is invalid")

    if value["version"] != _RESULT_VERSION:
        _fail("result version is not recognised")
    if value["status"] not in _STATUSES:
        _fail("status is not an allowed result status")

    expected = {
        "job_id": handoff.job_id,
        "worker_agent_id": handoff.worker_agent_id,
        "handoff_sha256": handoff_digest(handoff),
    }
    for field, expected_value in expected.items():
        if not hmac.compare_digest(value[field], expected_value):
            _fail(f"result {field} does not match the handoff it claims to answer")

    if value["status"] == _SUCCEEDED:
        output = _output(value["output"])
        if not hmac.compare_digest(
            _digest(value["output_sha256"], "output_sha256"),
            hashlib.sha256(canonical(output)).hexdigest(),
        ):
            _fail("output hash does not match output")
    else:
        output = None
        if value["output"] is not None or value["output_sha256"] is not None:
            _fail("a failed result must not carry output")

    # A result cannot predate the job it answers, cannot come from the future,
    # and cannot be produced after the handoff it answers had already expired.
    if not 0 < produced_at <= _MAX_PRODUCED_AT:
        _fail(f"produced_at must be between 1 and {_MAX_PRODUCED_AT}")
    if produced_at < handoff.issued_at:
        _fail("result predates the handoff it answers")
    if produced_at > now:
        _fail("result is dated in the future")
    if produced_at >= handoff.expires_at:
        _fail("result was produced after its handoff expired")
    # The fourth TTL control point of roadmap step 15: a handoff that was valid
    # at admission and expired during execution must not have its result taken.
    if now >= handoff.expires_at:
        _fail("handoff expired before its result was accepted")

    return Result(
        version=value["version"], job_id=value["job_id"],
        worker_agent_id=value["worker_agent_id"], handoff_sha256=value["handoff_sha256"],
        status=value["status"], reason_code=value["reason_code"], output=output,
        output_sha256=value["output_sha256"], produced_at=produced_at,
        signature=value["signature"],
    )
