#!/usr/bin/env python3
"""Measure how the cost of one audit event grows with the chain (P-01).

Every production event pays for the whole history before it:
`wiring._AnchoredAudit.append` snapshots and re-signs the full record list
and hands it to the anchor, whose commit verifies every record again; the
PostgreSQL path reads and re-validates the stored chain on each append and
serializes it once more for the commit-size check. Each pass is O(n) in the
chain length, so a service that commits per event does O(n^2) work over its
lifetime — and two hard ceilings end that lifetime outright: the anchor
request (64 MiB) and the anchor state file (16 MiB of head lines).

This script measures the in-process parts of that path (append, snapshot +
sign, anchor commit, commit-size serialization) with synthetic demo events,
derives the per-record coefficient of the full verification, and projects it
to the ceilings. It deliberately does **not** measure the PostgreSQL read
(`audit_store._read`, which additionally verifies every stored prefix head),
so every number here is a lower bound for the service path. Nothing is
written anywhere; the keys below are bench-only and self-describing.

Usage:
    python3 scripts/bench_audit.py                # defaults, ~30 s
    python3 scripts/bench_audit.py --quick        # smaller, a few seconds

The output is a measurement, not a gate: it exits 0 unless the arguments are
unusable. Results of record belong in a dated document, not in this file.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geniusnew import anchor_process  # noqa: E402
from geniusnew.audit import AuditAuthority, AuditEvent, event_from_handoff  # noqa: E402
from geniusnew.audit_chain import (  # noqa: E402
    _HEAD_VERSION, AuditAnchor, AuditChain, AuditHead)
from geniusnew.contracts import (  # noqa: E402
    Grant, HandoffSigner, Policy, canonical, issue, validate)

# Bench-only, like the demo's: published here, refused by any real service.
_INTEGRITY_KEY = b"bench-integrity-key-not-for-real!"
_AUDIT_KEY = b"bench-audit-key-not-for-real-use!"
_NOW = 1_700_000_000

# Checkpoint ladders. Phase 1 commits after every single append (the
# production cadence), so its total cost grows quadratically and must stay
# small; phase 2 only verifies at checkpoints and can reach further.
_PHASE1 = (100, 250, 500, 1000, 1500)
_PHASE2 = (1000, 2000, 5000, 10000, 20000)
_PROJECTIONS = (10_000, 30_000)


@dataclass(frozen=True)
class Row:
    records: int
    append_ms: float        # chain.append alone, per event in the window
    committed_ms: float     # phase 1: append+snapshot+commit per event; else 0.0
    commit_ms: float        # one anchor commit of the full chain
    commit_us_per_record: float
    size_check_ms: float    # anchor_process._check_commit_size, one call
    request_bytes: int      # canonical commit request at this length


@dataclass(frozen=True)
class Result:
    phase1: tuple[Row, ...]
    phase2: tuple[Row, ...]
    state_ceiling_records: int
    request_ceiling_records: int
    binding_ceiling_records: int
    projected_commit_ms: tuple[tuple[int, float], ...]
    projected_lifetime_hours: float


def checkpoints_upto(candidates: tuple[int, ...], limit: int) -> tuple[int, ...]:
    """The candidate checkpoints at or below `limit`, always ending at it."""
    chosen = [point for point in candidates if point < limit]
    chosen.append(limit)
    return tuple(chosen)


def project_commit_ms(us_per_record: float, records: int) -> float:
    """One full-chain commit at `records`, from the measured coefficient."""
    return us_per_record * records / 1000.0


def projected_lifetime_seconds(us_per_record: float, records: int) -> float:
    """CPU spent committing every event from 1 to `records`: c * n(n+1)/2."""
    return us_per_record / 1_000_000.0 * records * (records + 1) / 2.0


def lifetime_ceilings(request_bytes: int, records: int) -> tuple[int, int, int]:
    """(state, request, binding) record ceilings from the anchor's bounds.

    The state bound repeats the formula of `audit_store._MAX_STORED_RECORDS`
    (16 MiB of minimal head lines) without importing the PostgreSQL module;
    the request bound divides the 64 MiB frame by the measured bytes/record.
    """
    minimal = AuditHead(_HEAD_VERSION, 0, "0" * 64, "0" * 128)
    state = anchor_process._MAX_STATE_BYTES // len(anchor_process._head_line(minimal))
    per_record = max(1, request_bytes // max(1, records))
    request = anchor_process._MAX_REQUEST_BYTES // per_record
    return state, request, min(state, request)


def make_events(count: int) -> list[AuditEvent]:
    """Synthetic admitted-handoff events, built the way the tests build them."""
    signer = HandoffSigner(integrity_key=_INTEGRITY_KEY)
    authority = AuditAuthority(audit_key=_AUDIT_KEY)
    actor = authority.actor("gateway", "gw-bench")
    grant = Grant("subject-demo", "user-demo", "worker-demo", "basic",
                  ("summarize",), "isolated", False)
    policy = Policy("policy-v1", "orchestrator-demo", 60,
                    ("summarize",), ("isolated",), (grant,))
    events = []
    for step in range(count):
        wire = issue({"text": f"job {step}"}, subject="subject-demo",
                     job_id=f"job-{step}", policy=policy, signer=signer,
                     now=_NOW + step)
        handoff = validate(wire, subject="subject-demo", job_id=f"job-{step}",
                           policy=policy, verifier=signer, now=_NOW + step + 1)
        events.append(event_from_handoff(
            handoff, trace_id=f"trace-{step}", actor=actor,
            action="HANDOFF_ADMITTED", decision="ALLOWED",
            reason_code="POLICY_SATISFIED", occurred_at=_NOW + step + 1))
    return events


def _measure(checkpoints: tuple[int, ...], events: list[AuditEvent], *,
             commit_each: bool) -> tuple[Row, ...]:
    authority = AuditAuthority(audit_key=_AUDIT_KEY)
    chain = AuditChain()
    anchor = AuditAnchor()
    rows = []
    position = 0
    for target in checkpoints:
        window = events[position:target]
        started = time.perf_counter()
        for event in window:
            chain.append(event)
            if commit_each:
                head, records = chain.snapshot(authority)
                anchor.commit(head, records, authority=authority)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        head, records = chain.snapshot(authority)
        started = time.perf_counter()
        anchor.commit(head, records, authority=authority)
        commit_ms = (time.perf_counter() - started) * 1000.0
        started = time.perf_counter()
        anchor_process._check_commit_size(head, records)
        size_check_ms = (time.perf_counter() - started) * 1000.0
        request = anchor_process._commit_request(head, records)
        rows.append(Row(
            records=target,
            append_ms=0.0 if commit_each else elapsed_ms / len(window),
            committed_ms=elapsed_ms / len(window) if commit_each else 0.0,
            commit_ms=commit_ms,
            commit_us_per_record=commit_ms * 1000.0 / target,
            size_check_ms=size_check_ms,
            request_bytes=len(canonical(request)),
        ))
        position = target
    return tuple(rows)


def run_bench(*, full_commits: int, max_records: int) -> Result:
    if full_commits < 10 or max_records < full_commits:
        raise ValueError("need 10 <= full_commits <= max_records")
    events = make_events(max_records)
    phase1 = _measure(checkpoints_upto(_PHASE1, full_commits), events,
                      commit_each=True)
    phase2 = _measure(checkpoints_upto(_PHASE2, max_records), events,
                      commit_each=False)
    last = phase2[-1]
    state, request, binding = lifetime_ceilings(last.request_bytes, last.records)
    coefficient = last.commit_us_per_record
    projections = tuple(
        (n, project_commit_ms(coefficient, n))
        for n in (*_PROJECTIONS, binding))
    lifetime = projected_lifetime_seconds(coefficient, binding) / 3600.0
    return Result(phase1=phase1, phase2=phase2,
                  state_ceiling_records=state,
                  request_ceiling_records=request,
                  binding_ceiling_records=binding,
                  projected_commit_ms=projections,
                  projected_lifetime_hours=lifetime)


def _print(result: Result) -> None:
    print("Phase 1 - commit after every append (the production cadence):")
    print(f"  {'records':>8}  {'ms/event committed':>18}  {'one commit ms':>13}")
    for row in result.phase1:
        print(f"  {row.records:>8}  {row.committed_ms:>18.3f}  {row.commit_ms:>13.2f}")
    print("\nPhase 2 - append only, one full verification per checkpoint:")
    print(f"  {'records':>8}  {'append ms/event':>15}  {'commit ms':>9}  "
          f"{'us/record':>9}  {'size-check ms':>13}  {'request KiB':>11}")
    for row in result.phase2:
        print(f"  {row.records:>8}  {row.append_ms:>15.4f}  {row.commit_ms:>9.2f}  "
              f"{row.commit_us_per_record:>9.2f}  {row.size_check_ms:>13.2f}  "
              f"{row.request_bytes / 1024:>11.0f}")
    print("\nCeilings (anchor bounds, before the service refuses every event):")
    print(f"  state file 16 MiB of head lines : {result.state_ceiling_records:>8} records")
    print(f"  commit request 64 MiB frame     : {result.request_ceiling_records:>8} records")
    print(f"  binding ceiling                 : {result.binding_ceiling_records:>8} records")
    print("\nProjected cost of one committed event (anchor verification alone,")
    print("linear in the coefficient above; the PostgreSQL read adds more):")
    for records, ms in result.projected_commit_ms:
        print(f"  at {records:>8} records: {ms:>9.0f} ms per committed event")
    print(f"\nProjected anchor-verification CPU to reach the binding ceiling,")
    print(f"committing every event: {result.projected_lifetime_hours:.1f} hours.")
    print("\nReading: append stays flat; everything that re-reads or re-verifies")
    print("the chain grows with its length. That is the O(n) P-01 names, and")
    print("the ceilings are where it stops being slow and starts refusing.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure audit-chain growth costs (P-01).")
    parser.add_argument("--quick", action="store_true",
                        help="smaller run: 250 full commits, 2000 records")
    parser.add_argument("--full-commits", type=int, default=_PHASE1[-1],
                        help="phase 1 length: commit after every append")
    parser.add_argument("--max-records", type=int, default=_PHASE2[-1],
                        help="phase 2 length: verify only at checkpoints")
    args = parser.parse_args(argv)
    full = 250 if args.quick else args.full_commits
    total = 2000 if args.quick else args.max_records
    try:
        result = run_bench(full_commits=full, max_records=total)
    except ValueError as exc:
        print(f"bench_audit: {exc}", file=sys.stderr)
        return 2
    _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
