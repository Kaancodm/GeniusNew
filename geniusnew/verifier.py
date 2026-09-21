"""Independent acceptance of a result: signature, integrity, TTL, and once.

`docs/ROADMAP-V01.md` step 14 asks for an instance that takes results and is
neither the worker that produced them nor the orchestrator that asked for them.
`CONSTITUTION-V1-DRAFT.md` §8 is the reason: no instance confirms its own
security-relevant decision, and "this result is genuine" is exactly such a
decision when the party making it also produced the work.

## What makes it independent

It takes **wires**, never objects. A `Handoff` handed over in memory is a
decision someone else already made about those bytes; this instance parses and
revalidates them against trusted policy and server-side identity for itself,
the way `gateway.py` does at the other end of the path. Everything it then
checks — signature, binding to that exact handoff, output digest, the four TTL
bounds — is `results.accept`, called with a handoff this instance derived.

## What it adds that no contract can

`results.py` records the gap it cannot close: a contract holds no state, so
nothing in it stops the same result being accepted twice. That is this
instance's job, the way `approvals.py` does it for approval tokens. One
admitted handoff has exactly one accepted result, and a second presentation of
the very same bytes is refused rather than quietly repeated.

The ledger burns **only after a result has fully validated**. Burning on
presentation would let anyone who can reach this instance spend a job's one
acceptance with a forged result and permanently deny the genuine one — a denial
of service built out of the anti-replay rule.

## What it deliberately cannot do

The result HMAC is symmetric, so holding the key to verify is holding the key
to sign. The independence here is a separate instance and a separate API
boundary, not a cryptographic one; an asymmetric result signature is the
dependency decision noted against step 8 and it would change this file's
constructor, nothing else.

For an approval-bound job it is weaker still. The wire keeps `approval_state:
PENDING_APPROVAL` forever — consuming the approval does not rewrite it — so this
instance cannot tell an approved job from an unapproved one. It therefore
**requires the gateway's approval receipt hash to be handed to it** and records
it, while being unable to verify it: the store that could is the gateway's.
Refusing without it makes the evidence travel; calling that verification would
be reading UNKNOWN as PASS.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any

from .contracts import ContractError, Policy, validate, validate_pending
from .results import Result, WorkerAuthority, accept as accept_result, handoff_digest

_VERIFIER_ID = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")
_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")

# Both are in the closed action vocabulary of `audit.py`; a test checks that
# against it. An acceptance this instance cannot audit is one nobody can see.
_ACCEPTED = "RESULT_ACCEPTED"
_REJECTED = "RESULT_REJECTED"
ACTIONS = frozenset({_ACCEPTED, _REJECTED})

_VERIFIED = "RESULT_VERIFIED"

# Closed, like `results.py` keeps its own codes closed, so that a rejection
# cannot become a text channel out of the verification domain. The refusal's
# own sentence is preserved beside the code — contract messages are fixed
# strings, never payload content — but only the code is a value a caller
# branches on.
REJECTIONS = frozenset({
    "HANDOFF_NOT_VALID",
    "APPROVAL_NOT_EVIDENCED",
    "RESULT_NOT_VALID",
    "RESULT_ALREADY_ACCEPTED",
    "ACCEPTANCE_LEDGER_FULL",
})

# The dividing line, so that callers can rely on it: anything about the job or
# its artifacts is a `Rejected` carrying one of those codes and is recordable as
# an audit entry. Anything about how this instance was *called* — a clock that
# is not an integer, a policy that is not a Policy — is a plain `ContractError`,
# because a caller getting its own arguments wrong is not a verdict about a
# result and must not be audited as one.

_MAX_OCCURRED_AT = 4102444800  # 2100-01-01T00:00:00Z, as `audit.py` bounds it
_MAX_ACCEPTED = 100_000


def _fail(message: str) -> None:
    raise ContractError(message)


class Rejected(ContractError):
    """A result this instance refused, with the code an audit entry records.

    A `ContractError`, so callers that already treat any refusal as "do not use
    this result" keep working unchanged.
    """

    def __init__(self, message: str, *, reason_code: str, occurred_at: int) -> None:
        # Typed before the membership test: an unhashable value would otherwise
        # raise a bare TypeError out of the frozenset lookup, past every caller
        # that handles ContractError. Review found exactly this in
        # `orchestrator.py`; writing it correctly here is cheaper than being
        # told twice.
        if type(reason_code) is not str:
            _fail("reason_code must be a string")
        if reason_code not in REJECTIONS:
            _fail("reason_code is not a verifier rejection code")
        if type(occurred_at) is not int:
            _fail("occurred_at must be an integer")
        super().__init__(message)
        self.reason_code = reason_code
        self.action = _REJECTED
        self.decision = "DENIED"
        self.occurred_at = occurred_at


@dataclass(frozen=True)
class Acceptance:
    """One result taken, and the evidence for having taken it.

    `action`, `decision`, `reason_code` and `occurred_at` are the fields
    `audit.event_from_handoff` wants, so the acceptance can be recorded without
    anything in between reinterpreting it.
    """

    job_id: str
    worker_agent_id: str
    handoff_sha256: str
    result_sha256: str
    status: str
    result: Result
    approval_record_hash: str | None
    occurred_at: int
    action: str = _ACCEPTED
    decision: str = "ALLOWED"
    reason_code: str = _VERIFIED

    @property
    def succeeded(self) -> bool:
        """Whether the *work* succeeded — not whether the result was accepted.

        A signed `FAILED` is a genuine result and is accepted as one. Conflating
        the two would make a worker able to suppress its own failures by having
        them rejected as invalid.
        """
        return self.result.succeeded


class ResultVerifier:
    """Takes results for jobs it did not request and did not run."""

    def __init__(self, *, verifier_id: str, integrity_key: bytes,
                 result_key: bytes) -> None:
        if type(verifier_id) is not str or not _VERIFIER_ID.match(verifier_id):
            _fail("verifier_id must be a lowercase identifier of at most 63 characters")
        if type(integrity_key) is not bytes or len(integrity_key) < 32:
            _fail("integrity_key must be at least 32 bytes")
        # Passing the integrity key here is the defence `results.py` documents:
        # a deployment that derived its keys some other way gets an accidental
        # reuse refused. `keys.derive_keys` is what makes them differ.
        self._authority = WorkerAuthority(result_key=result_key,
                                          integrity_key=integrity_key)
        self._verifier_id = verifier_id
        self._integrity_key = integrity_key
        self._accepted: set[str] = set()
        self._lock = Lock()

    @property
    def verifier_id(self) -> str:
        return self._verifier_id

    def accept(self, result_wire: Any, *, handoff_wire: Any, subject: str,
               job_id: str, policy: Policy, now: int,
               approval_record_hash: str | None = None) -> Acceptance:
        """Revalidate both wires, take the result once, and record the decision."""
        if type(now) is not int:
            _fail("now must be an integer")
        if not 1 <= now <= _MAX_OCCURRED_AT:
            _fail("now is outside the range the audit contract accepts")
        if not isinstance(policy, Policy):
            _fail("policy is invalid")
        # A subject this policy does not know is a statement about whether the
        # handoff is admissible here, so it is a rejection with a code rather
        # than a bare error. `validate` below would refuse it too, one layer on.
        try:
            grant = policy.grant_for(subject)
        except ContractError as refusal:
            raise Rejected(str(refusal), reason_code="HANDOFF_NOT_VALID",
                           occurred_at=now) from None
        self._approval(approval_record_hash, required=grant.requires_approval,
                       now=now)

        handoff = self._handoff(handoff_wire, subject=subject, job_id=job_id,
                                policy=policy, now=now,
                                pending=grant.requires_approval)
        result = self._result(result_wire, handoff=handoff, now=now)

        digest = handoff_digest(handoff)
        # Burned only now, with a fully validated result in hand. Reserving
        # earlier would let a forged result spend the job's one acceptance and
        # lock out the genuine one.
        self._reserve(digest, now=now)
        return Acceptance(
            job_id=handoff.job_id,
            worker_agent_id=handoff.worker_agent_id,
            handoff_sha256=digest,
            result_sha256=hashlib.sha256(result.to_bytes()).hexdigest(),
            status=result.status,
            result=result,
            approval_record_hash=approval_record_hash,
            occurred_at=now,
        )

    def _approval(self, approval_record_hash: Any, *, required: bool,
                  now: int) -> None:
        """Require the gateway's receipt for an approval-bound job, and only then.

        This instance cannot verify the receipt — the store that could is the
        gateway's — so it refuses without one and records what it was given.
        """
        if required:
            if approval_record_hash is None:
                raise Rejected(
                    "an approval-bound handoff needs the gateway's approval receipt",
                    reason_code="APPROVAL_NOT_EVIDENCED", occurred_at=now)
            if (type(approval_record_hash) is not str
                    or not _DIGEST.match(approval_record_hash)):
                raise Rejected(
                    "approval_record_hash must be a lowercase SHA-256 digest",
                    reason_code="APPROVAL_NOT_EVIDENCED", occurred_at=now)
        else:
            if approval_record_hash is not None:
                raise Rejected(
                    "approval_record_hash is not allowed for a handoff that needs no approval",
                    reason_code="APPROVAL_NOT_EVIDENCED", occurred_at=now)

    def _handoff(self, handoff_wire: Any, *, subject: str, job_id: str,
                 policy: Policy, now: int, pending: bool):
        """Parse and revalidate the handoff here rather than be handed one."""
        revalidate = validate_pending if pending else validate
        try:
            return revalidate(handoff_wire, subject=subject, job_id=job_id,
                              policy=policy, integrity_key=self._integrity_key,
                              now=now)
        except ContractError as refusal:
            raise Rejected(str(refusal), reason_code="HANDOFF_NOT_VALID",
                           occurred_at=now) from None

    def _result(self, result_wire: Any, *, handoff, now: int) -> Result:
        try:
            return accept_result(result_wire, handoff=handoff,
                                 authority=self._authority, now=now)
        except ContractError as refusal:
            raise Rejected(str(refusal), reason_code="RESULT_NOT_VALID",
                           occurred_at=now) from None

    def _reserve(self, digest: str, *, now: int) -> None:
        """One admitted handoff, one accepted result. Atomically."""
        with self._lock:
            if digest in self._accepted:
                raise Rejected("this handoff already has an accepted result",
                               reason_code="RESULT_ALREADY_ACCEPTED", occurred_at=now)
            if len(self._accepted) >= _MAX_ACCEPTED:
                raise Rejected("the acceptance ledger is full",
                               reason_code="ACCEPTANCE_LEDGER_FULL", occurred_at=now)
            self._accepted.add(digest)
