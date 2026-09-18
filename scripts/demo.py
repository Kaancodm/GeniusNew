#!/usr/bin/env python3
"""One job through every layer, and the tampering that each layer refuses.

`docs/ROADMAP-V01.md` step 18 asks for one command that starts a job, prints the
audit chain, verifies it against the anchored head, and says plainly whether the
check passed. This is that command. It exits non-zero if anything it claims
fails, so it is a check rather than a printout — a demo that cannot fail proves
nothing.

## What it does not print

The roadmap requires the output to contain only what step 7 permits, and step 7
is the audit event contract: no secrets, no raw payloads. So nothing here prints
key material — not even a truncated prefix, since sixteen hex characters is
eight real bytes of a thirty-two byte key and demo output gets pasted into
issues. Nor does it print payload or result text, only the digests that stand
for them. `tests/test_demo.py` puts a canary in both the root secret and the
payload and fails if either reaches this output.

That makes the demo less immediately impressive and more honest about what the
system is for. Seeing the summary text would tell you the worker ran; seeing the
digest it is bound to tells you nobody could have swapped it.

## The second half is the point

Any pipeline can print success. The refusals are what the contracts are for, so
the demo performs seven attacks and requires every one to be refused. Five were
real holes at some point — three found by review, two by adversarial probing of
this repository's own modules. The other two are what the gateway buys: dispatch
without its permit, and a permit used twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geniusnew.approvals import ApprovalStore  # noqa: E402
from geniusnew.audit import AuditAuthority, event_from_handoff  # noqa: E402
from geniusnew.audit_chain import AuditAnchor, AuditChain, verify  # noqa: E402
from geniusnew.contracts import ContractError, Grant, Policy, issue  # noqa: E402
from geniusnew.gateway import Gateway  # noqa: E402
from geniusnew.keys import derive_keys  # noqa: E402
from geniusnew.results import WorkerAuthority, accept, handoff_digest  # noqa: E402
from geniusnew.workers import DeterministicSummarizer, WorkerRunner  # noqa: E402

# A fixed instant, so two runs of the demo produce the same digests and a reader
# can diff them. Real callers pass a real clock.
NOW = 1_700_000_000
JOB_ID = "job-demo-001"
TRACE_ID = "trace-demo-001"


def line(text: str = "") -> None:
    print(text, flush=True)


def step(number: int, text: str) -> None:
    line()
    line(f"[{number}] {text}")


def main(root_secret: bytes, request_text: str) -> int:
    keys = derive_keys(root_secret)
    step(0, "Three role keys derived from one root secret")
    line("    handoff integrity · worker result · audit — no two equal by construction")
    line(f"    distinct: {len({keys.integrity_key, keys.result_key, keys.audit_key}) == 3}")
    # The keys themselves are never printed. Eight bytes of a key is eight bytes
    # an attacker did not have, and demo output ends up in issue threads.

    grant = Grant("api-key-hash-demo", "user-demo", "worker-summarize-1", "basic",
                  ("summarize",), "isolated", False)
    policy = Policy("policy-v1", "orchestrator-1", 60,
                    ("summarize",), ("isolated",), (grant,))
    step(1, "Policy loaded — allow-list, default deny")
    line(f"    tools {policy.allowed_tools}   sandbox {policy.allowed_sandbox_profiles}")

    wire = issue({"text": request_text}, subject="api-key-hash-demo", job_id=JOB_ID,
                 policy=policy, integrity_key=keys.integrity_key, now=NOW)
    step(2, f"Handoff issued — {len(wire)} bytes of canonical JSON, HMAC signed")

    gateway = Gateway(gateway_id="gateway-1", integrity_key=keys.integrity_key,
                      approval_store=ApprovalStore())
    permit = gateway.admit(wire, subject="api-key-hash-demo", job_id=JOB_ID,
                           policy=policy, now=NOW + 1)
    handoff = permit.handoff
    step(3, "Gateway admitted the job — it revalidates the raw wire itself")
    line("    the orchestrator's word is not taken: the gateway checks the bytes again")
    line(f"    permit minted by {permit.gateway_id}, single use, one job only")
    line(f"    user {handoff.user_id}   tier {handoff.tier}   tools {handoff.tools}")
    line(f"    valid until {handoff.expires_at} (TTL {policy.handoff_ttl_seconds}s)")
    line(f"    artifact digest {handoff_digest(handoff)}")
    line(f"    payload digest  {handoff.payload_sha256}")

    worker_authority = WorkerAuthority(result_key=keys.result_key,
                                       integrity_key=keys.integrity_key)
    runner = WorkerRunner(DeterministicSummarizer(), authority=worker_authority)
    result_wire = runner.execute(permit, now=NOW + 5)
    step(4, f"Worker ran behind the boundary — {len(result_wire)} bytes, signed")
    line("    dispatch required the gateway's permit; a bare handoff is refused")
    line("    the work function received a copy of the payload and nothing else:")
    line("    no key, no handoff, no identity, no clock")

    result = accept(result_wire, handoff=handoff, authority=worker_authority, now=NOW + 10)
    step(5, "Result accepted")
    line(f"    status {result.status}   reason {result.reason_code}")
    line(f"    output digest {result.output_sha256}")
    line(f"    bound to handoff {result.handoff_sha256}")

    audit = AuditAuthority(audit_key=keys.audit_key)
    gateway_actor = audit.actor("gateway", "gw-1")
    chain = AuditChain()
    for action, occurred_at in (("HANDOFF_ADMITTED", NOW + 1),
                                ("EXECUTION_DISPATCHED", NOW + 5),
                                ("RESULT_ACCEPTED", NOW + 10)):
        chain.append(event_from_handoff(
            handoff, trace_id=TRACE_ID, actor=gateway_actor, action=action,
            decision="ALLOWED", reason_code="POLICY_SATISFIED", occurred_at=occurred_at))
    step(6, f"Audit chain — {len(chain.records)} entries, each linked to the one before")
    for record in chain.records:
        line(f"    [{record.index}] {record.event.action:22s} {record.record_hash}")

    head = chain.head(audit)
    anchor = AuditAnchor()
    anchor.commit(head, chain.records, authority=audit)
    verified = verify(chain.records, head, authority=audit, anchor=anchor)
    step(7, "Head signed with the audit key and committed to the anchor")
    line(f"    count {head.count}   head {head.head_hash}")
    line(f"    VERIFIED against the anchored head: {verified} entries")

    def swap_payload():
        original = handoff.payload["text"]
        try:
            handoff.payload["text"] = "something else"
            return handoff_digest(handoff)
        finally:
            handoff.payload["text"] = original

    step(8, "Now the tampering — every one of these must be refused")
    attacks = [
        ("Replay the result against a different job",
         lambda: accept(result_wire, handoff=gateway.admit(
             issue({"text": request_text}, subject="api-key-hash-demo", job_id="job-other",
                   policy=policy, integrity_key=keys.integrity_key, now=NOW),
             subject="api-key-hash-demo", job_id="job-other", policy=policy,
             now=NOW + 1).handoff,
             authority=worker_authority, now=NOW + 10)),
        ("Accept a result after its handoff expired",
         lambda: accept(result_wire, handoff=handoff, authority=worker_authority,
                        now=handoff.expires_at)),
        ("Truncate the audit chain by one entry",
         lambda: verify(chain.records[:-1], head, authority=audit, anchor=anchor)),
        ("Sign results with the handoff key",
         lambda: WorkerAuthority(result_key=keys.integrity_key,
                                 integrity_key=keys.integrity_key)),
        # This one mutates shared state, so it puts it back before the next
        # attack runs. Leaving it dirty made the permit-reuse attack below fail
        # with "payload no longer matches" — refused, but by the wrong check.
        ("Swap the payload after validation", swap_payload),
        ("Dispatch without a gateway permit",
         lambda: runner.execute(handoff, now=NOW + 5)),
        ("Reuse the permit for a second dispatch",
         lambda: runner.execute(permit, now=NOW + 6)),
    ]

    refused = 0
    for name, attempt in attacks:
        try:
            attempt()
            line(f"    [!!] {name:44s} NOT REFUSED")
        except ContractError as refusal:
            refused += 1
            line(f"    [ok] {name:44s} {refusal}")

    line()
    line("=" * 78)
    if refused == len(attacks) and verified == len(chain.records):
        line(f"PASS — chain verified against the anchored head, "
             f"{refused}/{len(attacks)} attacks refused.")
        return 0
    line(f"FAIL — {len(attacks) - refused} attack(s) not refused.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(
        root_secret=b"demo-root-secret-not-for-real-use!!!",
        request_text="Zero trust means never trust, always verify.",
    ))
