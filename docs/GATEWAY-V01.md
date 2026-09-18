# Gateway Boundary v0.1

The gateway is the independent default-deny boundary immediately before worker
dispatch.

## Input boundary

The gateway accepts the **raw canonical Handoff wire**, plus trusted server-side
context: subject, job id, policy, current time, and (only when required) an
approval token. It does not accept an already validated `Handoff` object as a
shortcut. The raw wire is re-decoded, its signature and payload digest are checked
again, and every identity/capability field is rebound to the trusted policy.

## Approval

For a grant with `requires_approval=True`, the signed Handoff intentionally remains
`PENDING_APPROVAL`. The gateway:

1. validates it with the pending-only contract path;
2. derives the exact `ApprovalScope` from that same wire;
3. consumes the one-time token from `ApprovalStore`;
4. mints a `DispatchPermit` carrying only the approval receipt hash.

The raw approval token is not stored in the permit. Reuse fails twice: the approval
record is already `CONSUMED`, and the resulting `DispatchPermit` itself carries
atomic one-shot state. The first worker dispatch consumes it; a second dispatch through
the same or another runner is refused.

For a grant that does not require approval, supplying an approval token is itself
rejected. Security-relevant extra input is not silently ignored.

## Dispatch capability

`WorkerRunner.execute` accepts a `DispatchPermit`, not a `Handoff`. A permit binds:

- the validated Handoff;
- its SHA-256 digest;
- the gateway instance id;
- the admission time;
- the approval receipt hash when approval was required.

The worker boundary recomputes the Handoff digest before execution and atomically
consumes the permit. Mutating the payload after gateway admission invalidates the
permit, and a permit that has already been dispatched cannot be replayed.

## Separation and limitation

The gateway is a distinct logical/runtime role from orchestration and execution. Its
instance id is carried on the dispatch capability, and its API accepts serialized
input rather than orchestrator-owned in-memory validation results.

Handoff signatures are still HMAC. Verification therefore requires a symmetric key,
so a gateway holding that key could cryptographically mint a Handoff too. v0.1 does
not claim asymmetric verifier-only authority; solving that requires a later key and
deployment design.

## Proof

`tests/test_gateway.py` covers independent revalidation, tamper/wrong-identity/expiry
refusals, mandatory and single-use gateway permits, one-time approval consumption,
approval scope binding, unexpected-token refusal, post-admission mutation, and
construction/config fail-closed behavior.

`geniusnew/gateway.py` is part of `scripts/refusals.py`, so deleting any explicit
security refusal must make CI fail.
