"""The boundary a worker sits behind, and a reference worker to prove it.

`docs/ROADMAP-V01.md` step 10 asks for a worker interface and a deterministic
trivial worker. The interface is the interesting half: it decides what the
least trustworthy component in the system is allowed to touch.

## The work function is not the worker

`CONSTITUTION-V1-DRAFT.md` section 8 names Ausführung as its own role, and a
result is signed by it. That does not mean the code doing the work holds the
signing key. `WorkerRunner` is the boundary: it holds the `WorkerAuthority`,
checks the grant, calls the work function, validates what comes back, and signs.
A `Worker` implementation never sees the key, the handoff, or anything it could
sign with.

That split matters because the work function is where arbitrary logic lives —
in v0.1 a pure function, later something that runs a model or a tool. Anything
it can reach, an attacker who subverts it can reach. So it gets a copy of the
payload and nothing else.

## Default deny at the boundary, not just upstream

A handoff carries the tools its grant allows. The runner refuses to execute a
worker whose tool is not among them, and records that refusal as a signed
`FAILED` result rather than staying silent. Step 12 now makes the upstream
gateway mandatory as well: `execute` accepts only a gateway-minted
`DispatchPermit`, never a raw or already validated `Handoff`. The local tool
check remains defence in depth after that independent admission.

## Failure is an outcome, not a crash

A work function that raises, returns the wrong shape, or returns nothing usable
produces a signed `FAILED` result. The exception is never allowed to escape and
its text never reaches the result: `results.py` keeps `reason_code` to an
uppercase code precisely so a failure path cannot become a free-text channel out
of the execution domain. A worker that could put `str(exc)` into an audited
artifact would be a leak with an error message wrapped around it.

## Expiry has no signed answer

If the handoff has expired there is nothing to hand back. `produce` refuses to
sign a result dated after `expires_at`, so an expired handoff cannot even yield
a signed `FAILED`. The runner raises instead. That is the third of the four TTL
control points roadmap step 15 collects, and the one place here where a caller
gets an exception rather than a result: silence would be indistinguishable from
a worker that never ran.
"""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import ContractError, Handoff
from .gateway import DispatchPermit, consume_handoff_from_permit
from .results import WorkerAuthority, handoff_digest, produce

_COMPLETED = "WORK_COMPLETED"
_TOOL_NOT_GRANTED = "TOOL_NOT_GRANTED"
_WORKER_FAILED = "WORKER_FAILED"
_OUTPUT_REJECTED = "OUTPUT_REJECTED"
_PAYLOAD_MUTATED = "PAYLOAD_MUTATED"
_ISOLATION_VIOLATED = "ISOLATION_VIOLATED"
_RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"


class _WorkerIsolationViolation(Exception):
    """The isolated worker attempted an operation the sandbox forbids."""


class _WorkerResourceExhausted(Exception):
    """The isolated worker exceeded a wall-clock or process resource limit."""



def _fail(message: str) -> None:
    raise ContractError(message)


class Worker:
    """One tool, one pure function, no access to anything else.

    Subclasses set `tool` to the single tool they implement and override `run`.
    `run` receives a copy of the handoff payload and returns an output mapping.
    It is given no key, no handoff and no identity: whatever it can reach is
    what an attacker who subverts it can reach.

    Raising from `run` is allowed and is not an error in the runner. It becomes
    a signed `FAILED` result, and the exception's text is discarded.
    """

    tool: str = ""

    def run(self, payload: Mapping[str, str]) -> Mapping[str, str]:
        raise NotImplementedError


class DeterministicSummarizer(Worker):
    """The reference worker: same input, same bytes, no clock and no entropy.

    It exists to make the path testable end to end, not to be useful. The output
    is a fact about the input that can be recomputed by hand, so a test can
    assert the exact result rather than that "something came back".
    """

    tool = "summarize"

    def run(self, payload: Mapping[str, str]) -> Mapping[str, str]:
        import hashlib

        text = payload["text"]
        words = len(text.split())
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return {"text": f"{words} words; sha256:{digest[:16]}"}


class WorkerRunner:
    """Executes one worker behind the contract boundary, and signs for it."""

    def __init__(self, worker: Worker, *, authority: WorkerAuthority) -> None:
        if not isinstance(worker, Worker):
            _fail("worker must be a Worker")
        worker_type = type(worker)
        tool = type.__getattribute__(worker_type, "tool")
        if type(tool) is not str or not tool:
            _fail("worker must declare the tool it implements")
        if not isinstance(authority, WorkerAuthority):
            _fail("authority must be a WorkerAuthority")
        self._worker = worker
        self._tool = tool
        self._authority = authority

    @property
    def tool(self) -> str:
        return self._tool

    def execute(self, permit: DispatchPermit, *, now: int) -> bytes:
        """Run a gateway-admitted job and hand back a signed result wire.

        A raw or already validated Handoff is deliberately insufficient. The
        worker boundary accepts only a capability minted by the independent
        gateway after revalidation and any required approval consumption.
        """
        handoff = consume_handoff_from_permit(permit)
        if type(now) is not int:
            _fail("now must be an integer")
        if now < permit.admitted_at:
            _fail("dispatch predates gateway admission")
        # Checked before anything runs: past `expires_at` there is no signable
        # answer at all, so a signed FAILED is not available as a fallback.
        if now >= handoff.expires_at:
            _fail("handoff expired before execution")
        if self._tool not in handoff.tools:
            return self._refuse(handoff, _TOOL_NOT_GRANTED, now=now)

        # The work function gets a copy. `Handoff.payload` is still mutable, and
        # mutating it would change the digest the result is bound to — so the
        # copy keeps a misbehaving worker from invalidating its own result, and
        # the check afterwards catches one that found another way.
        before = handoff_digest(handoff)
        try:
            output = self._run_worker(dict(handoff.payload))
        except _WorkerIsolationViolation:
            return self._refuse(handoff, _ISOLATION_VIOLATED, now=now)
        except _WorkerResourceExhausted:
            return self._refuse(handoff, _RESOURCE_EXHAUSTED, now=now)
        except Exception:  # noqa: BLE001 - a worker failing is an outcome here
            # Deliberately not `str(exc)`: reason_code is a closed shape so that
            # a failure cannot carry text out of the execution domain.
            return self._refuse(handoff, _WORKER_FAILED, now=now)
        if handoff_digest(handoff) != before:
            return self._refuse(handoff, _PAYLOAD_MUTATED, now=now)

        try:
            return produce(dict(output) if isinstance(output, Mapping) else output,
                           handoff=handoff, status="SUCCEEDED", reason_code=_COMPLETED,
                           authority=self._authority, now=now)
        except ContractError:
            return self._refuse(handoff, _OUTPUT_REJECTED, now=now)

    def _run_worker(self, payload: Mapping[str, str]) -> Mapping[str, str]:
        """Execute the untrusted work function.

        Step 11 overrides this single seam to cross a process boundary while
        keeping the signing authority in this parent-side runner.
        """
        return self._worker.run(payload)

    def _refuse(self, handoff: Handoff, reason_code: str, *, now: int) -> bytes:
        return produce(None, handoff=handoff, status="FAILED", reason_code=reason_code,
                       authority=self._authority, now=now)
