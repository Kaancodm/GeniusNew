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

import re
import time
from collections import Counter
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


class WorkerBoundaryError(ContractError):
    """Trusted transport or lifecycle failed; there is no signable worker outcome."""


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


# --- TextDigest: the first worker that is meant to be useful -----------------
#
# Everything below is a pure function of the text: no clock, no entropy, no
# network (the isolation boundary would refuse it anyway), no model. The
# methods are deliberately simple statistics — extractive sentence scoring,
# keyword frequencies, a Flesch estimate — whose output a reader can recompute
# and a test can assert exactly. A learned summarizer can replace the scoring
# later without changing the tool's contract: one text in, one digest out.
#
# Output sizes are bounded by construction (at most three shortened
# sentences, ten keywords, eight figures, fixed statistics), so a maximal
# 16 KiB payload still produces a result that fits the result wire instead
# of dying as OUTPUT_REJECTED at the signing boundary.

_WORD = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")
_VOWEL_GROUPS = re.compile(r"[aeiouäöü]+", re.IGNORECASE)
_FIGURE = re.compile(
    r"\d+(?:[.\u00a0 ]\d{3})*(?:,\d+)?"
    r"(?:\s?(?:%|€|\$|CHF|EUR|USD|Prozent|ms|MiB|GiB|MB|GB|TB|km|kg|"
    r"Minuten|Stunden|Tage|Monate|Jahre|Records?|Jobs?))?")

_MAX_SUMMARY_SENTENCES = 3
_MAX_SENTENCE_CHARS = 320
_MAX_KEYWORDS = 10
_MAX_FIGURES = 8
_MAX_OUTPUT_CHARS = 6000
_WORDS_PER_MINUTE = 200

_STOP_DE = frozenset("""
aber als am an auch auf aus bei bin bis bist da damit dann das dass dem den
der des dich die dies dir doch dort du durch ein eine einem einen einer eines
er es etwas für haben hat hatte haben hier ich ihr im in ins ist ja jede
jeder kann kein keine man mehr mein mich mir mit muss nach nicht noch nun nur
oder ohne schon sehr sein seine sich sie sind so soll und uns unter vom von
vor war warum was weil wenn wer wie wieder wir wird wo zu zum zur über
""".split())
_STOP_EN = frozenset("""
a about above after again all am an and any are as at be because been before
being below between both but by could did do does down during each few for
from further had has have having he her here hers herself him himself his how
i if in into is it its itself let me more most my nor of on once only or other
ought our ours out over own same she should so some such than that the their
theirs them then there these they this those through to too under until up
very was we were what when where which while who whom why with would you your
""".split())

_LABELS = {
    "de": {
        "title": "Text-Digest", "language": "Sprache", "unknown": "nicht sicher erkannt",
        "words": "Wörter", "sentences": "Sätze", "reading_time": "Lesezeit",
        "readability": "Lesbarkeit (Flesch, ca.)", "core": "Kern in Sätzen",
        "keywords": "Schlüsselbegriffe", "figures": "Zahlen im Text",
        "stats": "Kennzahlen", "avg_sentence": "Ø Wörter pro Satz",
        "avg_word": "Ø Zeichen pro Wort", "truncated": "[gekürzt]",
        "bands": ("leicht", "mittel", "anspruchsvoll", "schwer"),
    },
    "en": {
        "title": "Text digest", "language": "Language", "unknown": "not detected with confidence",
        "words": "words", "sentences": "sentences", "reading_time": "Reading time",
        "readability": "Readability (Flesch, approx.)", "core": "Core sentences",
        "keywords": "Keywords", "figures": "Figures in the text",
        "stats": "Statistics", "avg_sentence": "Avg. words per sentence",
        "avg_word": "Avg. characters per word", "truncated": "[truncated]",
        "bands": ("easy", "medium", "demanding", "hard"),
    },
}


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text)]
    return [part for part in parts if _WORD.search(part)]


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


def _syllables(word: str) -> int:
    # Vowel groups, at least one per word: an estimate, labelled as such.
    return max(1, len(_VOWEL_GROUPS.findall(word)))


def _language(lowered: list[str]) -> str:
    de = sum(word in _STOP_DE for word in lowered)
    en = sum(word in _STOP_EN for word in lowered)
    if de >= 3 and de >= 1.5 * en:
        return "de"
    if en >= 3 and en >= 1.5 * de:
        return "en"
    return "unknown"


def _flesch(words: int, sentences: int, syllables: int, language: str) -> int:
    per_sentence = words / sentences
    per_word = syllables / words
    if language == "en":
        score = 206.835 - 1.015 * per_sentence - 84.6 * per_word
    else:  # Amstad's German adaptation; also the fallback for "unknown".
        score = 180 - per_sentence - 58.5 * per_word
    return max(0, min(100, round(score)))


def _band(score: int, bands: tuple[str, ...]) -> str:
    if score >= 70:
        return bands[0]
    if score >= 50:
        return bands[1]
    if score >= 30:
        return bands[2]
    return bands[3]


def _keywords(lowered: list[str]) -> list[tuple[str, int]]:
    content = [word for word in lowered
               if len(word) >= 4 and word not in _STOP_DE and word not in _STOP_EN]
    counts = Counter(content)
    # (-count, word): frequency decides, alphabet breaks ties, nothing else can.
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:_MAX_KEYWORDS]


def _core_sentences(sentences: list[str],
                    keywords: list[tuple[str, int]]) -> list[str]:
    if not keywords or not sentences:
        return []
    strongest = keywords[0][1]
    weight = {word: count / strongest for word, count in keywords}
    scored = []
    for index, sentence in enumerate(sentences):
        words = [word.lower() for word in _words(sentence)]
        if not words:
            continue
        score = sum(weight.get(word, 0.0) for word in words) / len(words) ** 0.5
        scored.append((-score, index))
    keep = sorted(index for _, index in sorted(scored)[:_MAX_SUMMARY_SENTENCES])
    chosen = []
    for index in keep:
        sentence = sentences[index]
        if len(sentence) > _MAX_SENTENCE_CHARS:
            sentence = sentence[:_MAX_SENTENCE_CHARS - 2].rstrip() + " …"
        chosen.append(sentence)
    return chosen


def _figures(text: str) -> list[str]:
    seen: list[str] = []
    for match in _FIGURE.findall(text):
        figure = " ".join(match.split())
        if figure and figure not in seen:
            seen.append(figure)
        if len(seen) >= _MAX_FIGURES:
            break
    return seen


def _number(value: int, language: str) -> str:
    rendered = f"{value:,}"
    return rendered.replace(",", ".") if language != "en" else rendered


def _reading_time(words: int, language: str) -> str:
    seconds = round(words * 60 / _WORDS_PER_MINUTE)
    minutes, rest = divmod(seconds, 60)
    if language == "en":
        return f"{minutes} min {rest} s" if minutes else f"{rest} s"
    return f"{minutes} Min {rest} Sek" if minutes else f"{rest} Sek"


def render_digest(text: str) -> str:
    """The digest as Markdown: core sentences, keywords, figures, statistics."""
    sentences = _sentences(text)
    words = _words(text)
    if not words or not sentences:
        raise ValueError("text contains no words")
    lowered = [word.lower() for word in words]
    language = _language(lowered)
    labels = _LABELS["en" if language == "en" else "de"]
    syllables = sum(_syllables(word) for word in words)
    score = _flesch(len(words), len(sentences), syllables, language)
    keywords = _keywords(lowered)
    shown_language = language if language != "unknown" else labels["unknown"]

    lines = [
        f"# {labels['title']}",
        "",
        f"{labels['language']}: {shown_language} · "
        f"{_number(len(words), language)} {labels['words']} · "
        f"{len(sentences)} {labels['sentences']} · "
        f"{labels['reading_time']}: {_reading_time(len(words), language)} · "
        f"{labels['readability']}: {score} ({_band(score, labels['bands'])})",
        "",
        f"## {labels['core']}",
        "",
    ]
    fallback = sentences[0]
    if len(fallback) > _MAX_SENTENCE_CHARS:
        fallback = fallback[:_MAX_SENTENCE_CHARS - 2].rstrip() + " …"
    lines.extend(_core_sentences(sentences, keywords) or [fallback])
    if keywords:
        lines += ["", f"## {labels['keywords']}", "",
                  ", ".join(f"{word} ({count})" for word, count in keywords)]
    figures = _figures(text)
    if figures:
        lines += ["", f"## {labels['figures']}", ""]
        lines.extend(f"- {figure}" for figure in figures)
    lines += [
        "",
        f"## {labels['stats']}",
        "",
        f"- {labels['words']}: {_number(len(words), language)}",
        f"- {labels['sentences']}: {len(sentences)}",
        f"- {labels['avg_sentence']}: {len(words) / len(sentences):.1f}",
        f"- {labels['avg_word']}: {sum(len(word) for word in words) / len(words):.1f}",
    ]
    rendered = "\n".join(lines) + "\n"
    if len(rendered) > _MAX_OUTPUT_CHARS:  # unreachable by construction; held anyway
        rendered = rendered[:_MAX_OUTPUT_CHARS - 16].rstrip() + f"\n{labels['truncated']}\n"
    return rendered


class TextDigest(Worker):
    """Turn one text into a compact, verifiable digest — the first product tool.

    Extractive and deterministic by design (see the notes above
    `render_digest`): it quotes the text's own core sentences instead of
    inventing new ones, so every line of the digest can be checked against
    the input by hand. German and English are recognised for labels,
    stopwords and the readability variant; any other text still gets
    statistics and sentence scores, with the language reported as not
    detected rather than guessed.
    """

    tool = "digest"

    def run(self, payload: Mapping[str, str]) -> Mapping[str, str]:
        return {"text": render_digest(payload["text"])}


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
        started = self._monotonic()
        failure = None
        try:
            output = self._run_worker(dict(handoff.payload))
        except WorkerBoundaryError:
            raise
        except _WorkerIsolationViolation:
            failure = _ISOLATION_VIOLATED
        except _WorkerResourceExhausted:
            failure = _RESOURCE_EXHAUSTED
        except Exception:  # noqa: BLE001 - a worker failing is an outcome here
            # Deliberately not `str(exc)`: reason_code is a closed shape so that
            # a failure cannot carry text out of the execution domain. Recorded
            # rather than signed here, so the deadline below is checked first —
            # and, because the refusal is then raised outside the handler, the
            # worker's exception cannot ride out on `__context__` either.
            failure = _WORKER_FAILED

        # Roadmap step 15, and the one control point that was missing: `now` is
        # the dispatch clock and never advances, so a worker that ran past
        # `expires_at` produced a result nothing here could tell from a prompt
        # one. Measured, a summarizer sleeping 1.5s under a one-second TTL got
        # its result signed and accepted. Elapsed time is measured across the
        # work function and compared against the deadline in real seconds — no
        # rounding, because rounding up refuses valid short jobs and rounding
        # down lets a job overrun.
        #
        # Checked before any outcome is signed, success or failure alike: past
        # `expires_at` there is no signable answer at all, and a signed FAILED
        # for a dead authorization is the same lie as a signed SUCCEEDED.
        #
        # The signed artifact is untouched: `produced_at` stays the dispatch
        # clock, so the same job still produces the same bytes. Only the
        # decision to sign at all depends on how long the work took.
        if now + (self._monotonic() - started) >= handoff.expires_at:
            _fail("handoff expired while the worker was running")
        if failure is not None:
            return self._refuse(handoff, failure, now=now)
        if handoff_digest(handoff) != before:
            return self._refuse(handoff, _PAYLOAD_MUTATED, now=now)

        try:
            return produce(dict(output) if isinstance(output, Mapping) else output,
                           handoff=handoff, status="SUCCEEDED", reason_code=_COMPLETED,
                           authority=self._authority, now=now)
        except ContractError:
            return self._refuse(handoff, _OUTPUT_REJECTED, now=now)

    def _monotonic(self) -> float:
        """Elapsed-time source, as a seam.

        A test that proved this by sleeping would be honest and expensive:
        `scripts/refusals.py` runs the whole suite once per refusal, so one
        second of sleeping costs three minutes of CI. Overriding this is how
        the tests make a worker take an hour without taking an hour.
        """
        return time.monotonic()

    def _run_worker(self, payload: Mapping[str, str]) -> Mapping[str, str]:
        """Execute the untrusted work function.

        Step 11 overrides this single seam to cross a process boundary while
        keeping the signing authority in this parent-side runner.
        """
        return self._worker.run(payload)

    def _refuse(self, handoff: Handoff, reason_code: str, *, now: int) -> bytes:
        return produce(None, handoff=handoff, status="FAILED", reason_code=reason_code,
                       authority=self._authority, now=now)
