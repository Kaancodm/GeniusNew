"""Where the separated keys come from, so that separating them is not optional.

`CONSTITUTION-V1-DRAFT.md` section 8 keeps Orchestrierung, Ausführung and
Audit/Forensik apart. In practice that means each holds a key the others do not:
the handoff integrity key mints authorizations, the worker result key only
attests to what came back, and the audit key signs the record of both. Sharing
any two collapses the separation — a worker holding the handoff key can issue
itself a handoff for any tool in the policy.

`WorkerAuthority` can compare the key it is given against the handoff key and
refuse a match, but a comparison a caller may omit is a defence, not an
invariant: the separation then depends on every caller remembering to disclose
the other secret, and on being willing to pass a minting key around in order to
prove it is not being used. That is the wrong shape for the thing it protects.

So the keys are derived instead, from one root secret under distinct labels.
Two labels through HMAC-SHA-256 cannot produce the same key without breaking the
PRF assumption HMAC already rests on everywhere else in this codebase, and no
caller has to hold one key to obtain another. Nothing has to be remembered.

The root secret comes from server-side runtime configuration and is never a
signing key itself. It is the only thing a deployment has to keep, and losing it
invalidates every signature derived from it, which is the intended blast radius.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from .contracts import ContractError

_MIN_ROOT_BYTES = 32

# Distinct labels are what make the outputs unrelated. They carry a version so a
# future rotation can derive a new generation without colliding with this one.
_HANDOFF_LABEL = b"geniusnew/handoff-integrity/v1"
_RESULT_LABEL = b"geniusnew/worker-result/v1"
_AUDIT_LABEL = b"geniusnew/audit/v1"


def _fail(message: str) -> None:
    raise ContractError(message)


@dataclass(frozen=True)
class ServiceKeys:
    """The three separated keys, which by construction cannot be equal."""

    integrity_key: bytes
    result_key: bytes
    audit_key: bytes


def derive_keys(root_secret: bytes) -> ServiceKeys:
    """Derive the three role keys from one root secret.

    Deterministic, so a restart reproduces them, and there is nothing to store
    per key. A caller that needs only one still gets the others separated,
    because the separation is the point rather than a side effect.
    """
    if type(root_secret) is not bytes or len(root_secret) < _MIN_ROOT_BYTES:
        _fail(f"root_secret must be at least {_MIN_ROOT_BYTES} bytes")
    return ServiceKeys(
        integrity_key=hmac.new(root_secret, _HANDOFF_LABEL, hashlib.sha256).digest(),
        result_key=hmac.new(root_secret, _RESULT_LABEL, hashlib.sha256).digest(),
        audit_key=hmac.new(root_secret, _AUDIT_LABEL, hashlib.sha256).digest(),
    )
