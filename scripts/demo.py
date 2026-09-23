#!/usr/bin/env python3
"""One job over HTTP through every layer, and the tampering each layer refuses.

`docs/ROADMAP-V01.md` step 18 asks for one command that starts a job, prints the
audit chain, verifies it against the anchored head, and says plainly whether the
check passed. The goal statement asks for more: a job that goes in **over HTTP**,
an identity determined server-side, an instance independent of the orchestrator
checking policy, isolated execution, a **signed** result taken by yet another
independent instance, and a chain verifiable against an externally held head.

This is that command, and since step 17 wired the instances together it is the
real path rather than a sketch of one. It exits non-zero if anything it claims
fails, because a demo that cannot fail proves nothing.

## What it does not print

Step 18 requires the output to contain only what step 7 permits, and step 7 is
the audit event contract: no secrets, no raw payloads. So nothing here prints
key material — not even a truncated prefix, since sixteen hex characters is
eight real bytes of a thirty-two byte key and demo output gets pasted into
issues. Nor payload or result text, only the digests that stand for them. The
HTTP response carries the worker's output; the demo prints its digest and drops
the text. `tests/test_demo.py` puts a canary in the root secret, the API key and
the payload, and fails if any of them reaches this output.

## The second half is the point

Any pipeline can print success. The refusals are what the contracts are for, so
the demo performs thirteen attacks and requires every one to be refused. Seven
were real holes at some point — found by review, by adversarial probing, and one
by wiring two finished components together and discovering they did not fit.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geniusnew.audit_chain import sign_head, verify  # noqa: E402
from geniusnew.contracts import ContractError, Grant, Policy, validate  # noqa: E402
from geniusnew.http_entry import serve  # noqa: E402
from geniusnew.results import WorkerAuthority, produce  # noqa: E402
from geniusnew.verifier import ResultVerifier  # noqa: E402
from geniusnew.wiring import build  # noqa: E402
from geniusnew.workers import DeterministicSummarizer  # noqa: E402

# A fixed instant and fixed job ids, so two runs produce the same digests and a
# reader can diff them. Real callers pass a real clock and mint random ids.
NOW = 1_700_000_000
API_KEY = b"demo-api-key-not-for-real-use!!!"


def line(text: str = "") -> None:
    print(text, flush=True)


def step(number: int, text: str) -> None:
    line()
    line(f"[{number}] {text}")


def main(root_secret: bytes, request_text: str, api_key: bytes = API_KEY) -> int:
    grant = Grant("subject-demo", "user-demo", "worker-demo", "basic",
                  ("summarize",), "isolated", False)
    policy = Policy("policy-v1", "orchestrator-1", 60,
                    ("summarize",), ("isolated",), (grant,))
    ids = iter(f"job-demo-{index:04d}" for index in range(100))

    service = build(root_secret=root_secret, policy=policy,
                    api_keys={api_key: "subject-demo"},
                    workers=(DeterministicSummarizer(),),
                    clock=lambda: NOW, job_ids=lambda: next(ids))

    step(0, "Three role keys derived from one root secret")
    line("    handoff integrity · worker result · audit — no two equal by construction")
    line(f"    distinct: {len({service.keys.integrity_key, service.keys.result_key, service.keys.audit_key}) == 3}")
    # The keys themselves are never printed. Eight bytes of a key is eight bytes
    # an attacker did not have, and demo output ends up in issue threads.

    step(1, "Policy loaded — allow-list, default deny")
    line(f"    tools {policy.allowed_tools}   sandbox {policy.allowed_sandbox_profiles}")
    line(f"    grant: subject-demo → worker-demo, tier {grant.tier}, TTL {policy.handoff_ttl_seconds}s")

    server = serve(service.entry)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/jobs"
    try:
        step(2, f"HTTP entrance listening on {host}:{port}")
        line("    the request carries a key and a payload, and nothing else is read")

        status, body = post(url, api_key, {"text": request_text})
        step(3, f"Job submitted — HTTP {status}")
        line("    identity came from the key, server-side: the request never named it")
        line(f"    job {body['job_id']}   status {body['status']}   {body['reason_code']}")
        line(f"    handoff digest {body['handoff_sha256']}")
        line(f"    result digest  {body['result_sha256']}")
        line("    the worker's output is in the response; only its digest is printed here")

        records = service.chain.records
        step(4, f"Audit chain — {len(records)} entries, each linked to the one before")
        for record in records:
            line(f"    [{record.index}] {record.event.actor.component:12s} "
                 f"{record.event.action:22s} {record.record_hash}")

        head = service.head()
        verified = verify(records, head, authority=service.audit,
                          anchor=service.anchor)
        step(5, "Head signed with the audit key and committed to the anchor")
        line(f"    anchor runs in its own process: {service.anchor.pid != os.getpid()}")
        line(f"    count {head.count}   head {head.head_hash}")
        line(f"    VERIFIED against the anchored head: {verified} entries")

        step(6, "Now the tampering — every one of these must be refused")
        refused = 0
        attacks = build_attacks(service, url, api_key, request_text, records, head)
        for name, attempt in attacks:
            try:
                attempt()
                line(f"    [!!] {name:44s} NOT REFUSED")
            except ContractError as refusal:
                refused += 1
                line(f"    [ok] {name:44s} {refusal}")
            except Refused as refusal:
                refused += 1
                line(f"    [ok] {name:44s} {refusal}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        service.close()

    job_ok = body["status"] == "SUCCEEDED" and body["reason_code"] == "WORK_COMPLETED"
    chain_ok = verified == len(records) == 4
    attacks_ok = refused == len(attacks)

    line()
    line("=" * 78)
    if job_ok and chain_ok and attacks_ok:
        line(f"PASS — job succeeded over HTTP, chain verified against the anchored "
             f"head, {refused}/{len(attacks)} attacks refused.")
        return 0
    reasons = []
    if not job_ok:
        reasons.append(f"job ended {body['status']}/{body['reason_code']}")
    if not chain_ok:
        reasons.append(f"chain verified {verified} of {len(records)} entries")
    if not attacks_ok:
        reasons.append(f"{len(attacks) - refused} attack(s) not refused")
    line(f"FAIL — {'; '.join(reasons)}.")
    return 1


class Refused(Exception):
    """An HTTP-level refusal, so the attack table can treat it like any other."""


def post(url: str, api_key: bytes, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key.decode()})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise Refused(f"HTTP {error.code} {json.loads(error.read())['error']}") from None


def build_attacks(service, url, api_key, request_text, records, head):
    """Thirteen attempts, each refused by a different check.

    Four go in over HTTP, because the entrance is the only part a stranger can
    reach. The rest hold the objects an insider would have.
    """
    policy = service.policy
    keys = service.keys

    def dispatch(job_id):
        return service.orchestrator.submit(
            {"text": request_text}, subject="subject-demo", job_id=job_id,
            policy=policy, now=NOW)

    spare = dispatch("job-demo-spare")
    handoff = validate(spare.handoff_wire, subject="subject-demo",
                       job_id="job-demo-spare", policy=policy,
                       integrity_key=keys.integrity_key, now=NOW)
    authority = WorkerAuthority(result_key=keys.result_key,
                                integrity_key=keys.integrity_key)

    def accept_spare(now=NOW, verifier=None):
        return (verifier or service.verifier).accept(
            spare.result_wire, handoff_wire=spare.handoff_wire,
            subject="subject-demo", job_id="job-demo-spare", policy=policy,
            now=now)

    def swap_payload():
        # Puts the payload back: leaving it dirty made a later attack fail on
        # the digest instead of on the check it was aimed at.
        original = handoff.payload["text"]
        try:
            handoff.payload["text"] = "something else"
            return produce({"text": "x"}, handoff=handoff, status="SUCCEEDED",
                           reason_code="WORK_COMPLETED", authority=authority,
                           now=NOW)
        finally:
            handoff.payload["text"] = original

    def rewind_anchor():
        vars(service.anchor).update(_count=0, _head_hash="0" * 64)
        return verify(records[:-1],
                      sign_head(count=len(records) - 1,
                                head_hash=records[-2].record_hash,
                                authority=service.audit),
                      authority=service.audit, anchor=service.anchor)

    fresh = ResultVerifier(verifier_id="verifier-2",
                           integrity_key=keys.integrity_key,
                           result_key=keys.result_key)

    return [
        # --- what a stranger at the socket can try ---------------------------
        ("Submit with a key that is not registered",
         lambda: post(url, b"THIS-KEY-IS-NOT-REGISTERED-AT-ALL", {"text": "x"})),
        ("Claim a tier in the request body",
         lambda: post_raw(url, api_key, {"text": "x", "tier": "admin"})),
        ("Choose your own job id",
         lambda: post_raw(url, api_key, {"text": "x", "job_id": "job-demo-0000"})),
        ("Knock on a door that is not there",
         lambda: post(url.replace("/jobs", "/admin"), api_key, {"text": "x"})),

        # --- what someone holding the objects can try ------------------------
        ("Replay the result against a different job",
         lambda: service.verifier.accept(
             spare.result_wire, handoff_wire=dispatch("job-demo-other").handoff_wire,
             subject="subject-demo", job_id="job-demo-other", policy=policy, now=NOW)),
        ("Accept a result after its handoff expired",
         lambda: accept_spare(now=handoff.expires_at)),
        ("Accept the same result twice",
         lambda: (accept_spare(verifier=fresh), accept_spare(verifier=fresh))),
        ("Sign results with the handoff key",
         lambda: WorkerAuthority(result_key=keys.integrity_key,
                                 integrity_key=keys.integrity_key)),
        ("Swap the payload after validation", swap_payload),
        ("Dispatch without a gateway permit",
         lambda: service.orchestrator._workers["worker-demo"].dispatch(
             handoff, now=NOW)),

        # --- what the anchor is for ------------------------------------------
        # Handing the original head to a short chain is refused by the count
        # inside that signed head — it would still be refused with anchor
        # enforcement deleted entirely.
        ("Truncate the chain, keep the old head",
         lambda: verify(records[:-1], head, authority=service.audit,
                        anchor=service.anchor)),
        # This is the attack the anchor exists for: shorten the log and sign a
        # fresh, entirely valid head over it. Nothing inside the chain can tell.
        ("Truncate the chain and re-sign the head",
         lambda: verify(records[:-1],
                        sign_head(count=len(records) - 1,
                                  head_hash=records[-2].record_hash,
                                  authority=service.audit),
                        authority=service.audit, anchor=service.anchor)),
        # What a writer can do to an anchor in its own memory: set it back to
        # nothing, then present the shortened chain. Against an in-process
        # AuditAnchor this is accepted; the anchor process keeps its state
        # where these assignments cannot reach.
        ("Rewind the anchor from inside the writer", rewind_anchor),
    ]


def post_raw(url: str, api_key: bytes, payload: dict) -> tuple[int, dict]:
    return post(url, api_key, payload)


if __name__ == "__main__":
    raise SystemExit(main(
        root_secret=b"demo-root-secret-not-for-real-use!!!",
        request_text="Zero trust means never trust, always verify.",
    ))
