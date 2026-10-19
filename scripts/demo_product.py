#!/usr/bin/env python3
"""The product demo: one real text in, one signed digest out.

Where `scripts/demo.py` proves the gates and prints only digests, this proves
the product behind them (`docs/PRODUCT-DIGEST.md`): a built-in public sample
text travels the whole path — API key, signed handoff, gateway, isolated
`digest` worker, signed result, verified audit chain — and the digest it
produces is printed, because here the output *is* the claim. The sample and
the keys are published demo material; nothing else is.

Needs the same host properties as the security demo (POSIX worker isolation
with Landlock ABI 4). `run()` accepts a `runner_factory` purely as a test
seam, exactly as `wiring.build` defines it; the command line never does.

    python3 scripts/demo_product.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geniusnew.audit_chain import verify  # noqa: E402
from geniusnew.config import DEMO_ROOT_SECRET  # noqa: E402
from geniusnew.contracts import Grant, Policy  # noqa: E402
from geniusnew.http_entry import serve  # noqa: E402
from geniusnew.wiring import build  # noqa: E402
from geniusnew.workers import TextDigest  # noqa: E402

NOW = 1_700_000_000
API_KEY = b"demo-api-key-not-for-real-use!!!"

# The sample says what the product is, in the language it will be fed most.
SAMPLE = (
    "GeniusNew ist ein Zero Trust Agentensystem. Ein Auftrag kommt über HTTP "
    "herein und erhält seine Identität aus einem API Schlüssel, niemals aus "
    "dem Request. Der Orchestrator signiert einen Handoff mit einer Gültigkeit "
    "von 60 Sekunden. Das Gateway prüft den Handoff unabhängig nach und stellt "
    "einen einmaligen Permit aus. Erst dann läuft ein Worker in einem isolierten "
    "Prozess mit Landlock und Seccomp. Das Ergebnis wird vom Worker signiert "
    "und von einer Prüfinstanz angenommen, die nur öffentliche Schlüssel hält. "
    "Jede Entscheidung landet in einer verketteten Audit Kette. Ein Anker in "
    "einem eigenen Prozess hält den signierten Kopf dieser Kette fest. Die Demo "
    "zeigt 15 Angriffe und jeder Angriff wird abgelehnt. Aktuell begrenzt der "
    "Anker die Kette auf 62.137 Records in 16 MiB Zustandsdatei. Darum misst ein "
    "Benchmark das Wachstum und der nächste Entwurf macht die Kette inkrementell. "
    "Sicherheit entsteht hier nicht durch ein Versprechen, sondern durch "
    "Ablehnungen, für die es jeweils einen Test gibt. Kein Baustein bestätigt "
    "seine eigene Entscheidung. Genau diese Trennung macht das System prüfbar "
    "und am Ende hoffentlich langweilig im besten Sinn."
)


def run(*, runner_factory=None) -> int:
    grant = Grant("subject-product", "user-product", "worker-digest", "basic",
                  ("digest",), "isolated", False)
    policy = Policy("policy-v1", "orchestrator-1", 60,
                    ("digest",), ("isolated",), (grant,))
    with tempfile.TemporaryDirectory() as state_dir:
        service = build(root_secret=DEMO_ROOT_SECRET, policy=policy,
                        api_keys={API_KEY: "subject-product"},
                        workers=(TextDigest(),),
                        clock=lambda: NOW, job_ids=lambda: "job-product-0001",
                        anchor_state=os.path.join(state_dir, "anchor.state"),
                        runner_factory=runner_factory)
        server = serve(service.entry)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            host, port = server.server_address[:2]
        except Exception:
            server.server_close()
            service.close()
            raise
        try:
            request = urllib.request.Request(
                f"http://{host}:{port}/jobs",
                data=json.dumps({"text": SAMPLE}).encode(),
                method="POST",
                headers={"Authorization": f"Bearer {API_KEY.decode()}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read())
            records = service.chain.records
            verified = verify(records, service.head(), authority=service.audit,
                              anchor=service.anchor)
        finally:
            server.shutdown()
            server.server_close()
            service.close()

    output = (body.get("output") or {}).get("text", "")
    if body.get("status") != "SUCCEEDED" or not output or verified != len(records):
        print(f"FAIL — status={body.get('status')} verified={verified}/"
              f"{len(records)}", file=sys.stderr)
        return 1
    print(output, end="")
    print(f"— job {body['job_id']} · result {body['result_sha256'][:16]}… · "
          f"audit chain verified against the anchored head ({verified} entries)")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
