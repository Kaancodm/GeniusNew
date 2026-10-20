"""The advisory boundary: the Core may ask a local model for text, nothing more.

`docs/ROADMAP-V02.md` excludes an LLM worker from v0.2, and the pilot in
`tools/ai_pilot/` is an operator tool that deliberately stands outside this
package. This module is the one place where the two meet: a caller inside the
Core hands a bounded task to a local model and receives untrusted text back.
Nothing else crosses in either direction.

## What this boundary guarantees

**No authority.** The bridge holds no key, signs nothing, mints no permit,
writes no audit event and dispatches no job. Its whole result is a string and
that string's digest. A model answer is data, exactly like a request body: the
components that decide something never read it.

**The engine is not trusted.** Everything the engine returns is re-validated
here against limits this module owns, because the pilot's own checks belong to
the pilot, not to the Core — the same reason the gateway revalidates a handoff
the orchestrator signed. Engine failures become one refusal with a fixed
message; only KeyboardInterrupt propagates to preserve operator cancellation.
The engine's own error text is not passed through, so it cannot speak for the
Core. The pilot still writes its specific reason code to its own JSONL
log, which is where an operator looks for the detail.

**Off means off.** The bridge is disabled unless a configuration explicitly
enables it, and an enabled bridge additionally needs an engine handed to it.
There is no default engine and no environment override: `geniusnew/config.py`
reads no environment at all, and this module keeps to that.

**Refuse, never degrade.** Unclear input, an engine that fails, an oversized
answer or a second concurrent caller each end in a `ContractError`. There is no
fallback model, no retry and no partial answer, mirroring the pilot it wraps.

## What it deliberately does not do

There is no HTTP route here. Publishing advisory text over the entrance is a
new network surface with its own limits and its own review, not a side effect
of this module; until that gate exists the bridge is reachable programmatically
through `wiring.Service.advisory` only.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Any

from .contracts import ContractError

# The two task kinds of P1. Closed on purpose: a new kind is a decision about
# what the Core may ask a model for, not a string someone passes in.
ADVISORY_KINDS = frozenset({"summary", "code_draft"})

# Re-validated here rather than inherited from the pilot: this boundary must
# hold on its own even if the engine beside it stops checking.
MAX_ADVISORY_INPUT_BYTES = 4096
MAX_ADVISORY_TEXT_BYTES = 4096


def _fail(message: str) -> None:
    raise ContractError(message)


@dataclass(frozen=True)
class AdvisorySettings:
    """The one configuration value: whether advisory inference is switched on."""

    enabled: bool = False

    def __post_init__(self) -> None:
        # Anything that is not exactly True stays off. A truthy string, an
        # integer or a missing value must not enable a model call.
        if type(self.enabled) is not bool:
            _fail("ai_advisory.enabled must be a boolean")


@dataclass(frozen=True)
class AdvisoryResult:
    """Advisory text and its digest. No task id, no model id, no metadata.

    The digest lets a caller record that an answer existed without storing the
    answer; the text is all a caller may use, and only as untrusted data.
    """

    kind: str
    text: str
    text_sha256: str


class AdvisoryBridge:
    """The only path from the Core to a local model, and it carries no authority.

    An engine is any object with `run(kind, text) -> str`; `default_engine`
    supplies the P1 pilot. The bridge is constructed disabled unless a caller
    passes `enabled=True` together with an engine, so a service that never
    configures advisory inference neither imports nor runs one.
    """

    def __init__(self, engine: Any = None, *, enabled: bool = False):
        self._enabled = enabled is True
        if self._enabled and engine is None:
            _fail("an enabled advisory bridge needs an engine")
        if engine is not None and not callable(getattr(engine, "run", None)):
            _fail("advisory engine must provide run(kind, text)")
        self._engine = engine
        # One advisory call at a time, in this process. The pilot adds its own
        # cross-process slot; this lock keeps two threads of one service from
        # queueing model calls behind each other.
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def request(self, kind: Any, text: Any) -> AdvisoryResult:
        """Refuse failures without their details; preserve operator cancellation."""
        if not self._enabled:
            _fail("the advisory bridge is disabled")
        _check_kind(kind)
        _check_text(kind, text)
        if not self._lock.acquire(blocking=False):
            _fail("another advisory request holds this bridge")
        try:
            failed = False
            try:
                answer = self._engine.run(kind, text)
            except KeyboardInterrupt:
                raise
            except BaseException:  # noqa: BLE001 - untrusted engine failures
                # The engine stands outside this boundary's trust: its failures
                # — including a refusal of its own — become this one
                # refusal. Forwarding its message would let it speak for the
                # Core. Refuse outside the handler so the engine exception
                # cannot follow the refusal through __context__.
                failed = True
            if failed:
                _fail("the advisory engine failed")
            return _result(kind, answer)
        finally:
            self._lock.release()


def default_engine() -> Any:
    """The P1 pilot, imported lazily and with its own opt-in already set.

    Lazy so that a service which leaves advisory disabled starts without the
    `tools` package being importable at all; an enabled service that cannot
    import the pilot refuses to start instead of falling back to anything.
    """
    failed = False
    try:
        from tools.ai_pilot.pilot import Pilot
    except ImportError:
        failed = True
    if failed:
        _fail("the advisory engine tools/ai_pilot is not importable")
    # The pilot's own switch stays on: the configuration that enabled the
    # bridge is the outer gate, and a pilot reached any other way refuses.
    return Pilot(enabled=True)


def build_advisory(settings: AdvisorySettings | None = None) -> AdvisoryBridge:
    """The bridge for one configuration; a disabled setting imports nothing."""
    if settings is None:
        settings = AdvisorySettings()
    if not isinstance(settings, AdvisorySettings):
        _fail("advisory settings must be AdvisorySettings")
    if not settings.enabled:
        return AdvisoryBridge()
    return AdvisoryBridge(engine=default_engine(), enabled=True)


def _check_kind(kind: Any) -> None:
    if type(kind) is not str or kind not in ADVISORY_KINDS:
        _fail(f"advisory kind must be one of: {', '.join(sorted(ADVISORY_KINDS))}")


def _check_text(kind: Any, text: Any) -> None:
    if type(text) is not str or not text.strip() or "\x00" in text:
        _fail(f"advisory input for {kind} must be non-empty text without NUL")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        size = None
    if size is None:
        _fail(f"advisory input for {kind} must be encodable as UTF-8")
    if size > MAX_ADVISORY_INPUT_BYTES:
        _fail(f"advisory input for {kind} must be at most "
              f"{MAX_ADVISORY_INPUT_BYTES} bytes")


def _result(kind: str, answer: Any) -> AdvisoryResult:
    if type(answer) is not str:
        _fail("advisory output must be text")
    if not answer.strip() or "\x00" in answer:
        _fail("advisory output must be non-empty text without NUL")
    try:
        encoded = answer.encode("utf-8")
    except UnicodeError:
        encoded = None
    if encoded is None:
        _fail("advisory output must be encodable as UTF-8")
    if len(encoded) > MAX_ADVISORY_TEXT_BYTES:
        _fail(f"advisory output must be at most {MAX_ADVISORY_TEXT_BYTES} bytes")
    return AdvisoryResult(kind=kind, text=answer,
                          text_sha256=hashlib.sha256(encoded).hexdigest())
