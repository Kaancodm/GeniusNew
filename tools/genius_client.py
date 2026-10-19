"""Submit one text job to a GeniusNew service and print what comes back.

The client half of the first product (`docs/PRODUCT-DIGEST.md`). Which tool
runs is the server-side grant's decision, never the request's: the body is
exactly `{"text": ...}` and the API key says who is asking. With a `digest`
grant the answer's output is the Markdown digest, printed here or saved.

Standard library only. The key comes from a file (`--api-key-file`, one
trailing newline tolerated, like `geniusnew digest-api-key` expects) or from
`GENIUSNEW_API_KEY`; it is sent as a header and never printed. Oversized
texts are refused before the wire — the handoff contract caps a job at
16 KiB, so failing here beats a `PAYLOAD_TOO_LARGE` there.

    python3 -m tools.genius_client --url https://geniusnew.example.org \
        --api-key-file ~/.config/geniusnew/api-key --file bericht.txt
    cat bericht.txt | python3 -m tools.genius_client --api-key-file … --out digest.md

Exit codes: 0 digest produced, 1 job refused or failed (reason on stderr),
2 unusable input, 3 the service could not be reached.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

_MAX_BODY_BYTES = 15_000  # under the 16 KiB wire cap, room for its framing
_TIMEOUT_SECONDS = 60

Opener = Callable[..., tuple[int, bytes]]


@dataclass(frozen=True)
class Outcome:
    kind: str          # "ok", "failed", "pending", "refused"
    text: str = ""
    reason: str = ""
    job_id: str = ""


def read_api_key(*, key_file: Path | None, environ: dict[str, str]) -> bytes:
    if key_file is not None:
        key = key_file.read_bytes().removesuffix(b"\n").removesuffix(b"\r")
    elif environ.get("GENIUSNEW_API_KEY"):
        key = environ["GENIUSNEW_API_KEY"].encode("utf-8")
    else:
        raise ValueError("no API key: pass --api-key-file or set GENIUSNEW_API_KEY")
    if not 16 <= len(key) <= 256:
        raise ValueError("API key must be between 16 and 256 bytes")
    try:
        key.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("API key must be ASCII (it travels in a header)") from None
    return key


def build_request(base_url: str, api_key: bytes, text: str) -> urllib.request.Request:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username:
        raise ValueError("--url must look like http://127.0.0.1:8080 or https://host")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("--url names the service, not a route; /jobs is added here")
    body = json.dumps({"text": text}, ensure_ascii=True,
                      separators=(",", ":")).encode("ascii")
    if len(body) > _MAX_BODY_BYTES:
        raise ValueError(f"text too large for one job ({len(body)} bytes as sent; "
                         f"the wire cap leaves {_MAX_BODY_BYTES}) — split the text")
    return urllib.request.Request(
        f"{base_url.rstrip('/')}/jobs", data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key.decode('ascii')}",
                 "Content-Type": "application/json"})


def parse_outcome(status_code: int, raw: bytes) -> Outcome:
    """Map the entrance's closed response shapes onto one client outcome."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return Outcome("refused", reason=f"unreadable answer (HTTP {status_code})")
    if not isinstance(body, dict):
        return Outcome("refused", reason=f"unexpected answer (HTTP {status_code})")
    job_id = str(body.get("job_id") or "")
    if status_code != 202:
        return Outcome("refused", reason=str(body.get("error") or f"HTTP {status_code}"),
                       job_id=job_id)
    status = body.get("status")
    if status == "SUCCEEDED":
        output = body.get("output") or {}
        text = output.get("text") if isinstance(output, dict) else None
        if type(text) is str and text:
            return Outcome("ok", text=text, job_id=job_id)
        return Outcome("failed", reason="succeeded without output", job_id=job_id)
    if status == "PENDING_APPROVAL":
        return Outcome("pending", job_id=job_id)
    return Outcome("failed", reason=str(body.get("reason_code") or status or "FAILED"),
                   job_id=job_id)


def _open(request: urllib.request.Request, *, timeout: int) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:  # a refusal is still an answer
        return error.code, error.read()


def main(argv: list[str] | None = None, *, opener: Opener = _open,
         environ: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Submit a text to GeniusNew and print the digest it produces.")
    parser.add_argument("--url", default="http://127.0.0.1:8080",
                        help="service base URL (default: local entrance)")
    parser.add_argument("--api-key-file", type=Path,
                        help="file holding the API key (or set GENIUSNEW_API_KEY)")
    parser.add_argument("--file", type=Path, help="UTF-8 text file (default: stdin)")
    parser.add_argument("--out", type=Path, help="write the digest here instead of stdout")
    parser.add_argument("--json", action="store_true",
                        help="print job id, status and digest as JSON")
    args = parser.parse_args(argv)
    try:
        api_key = read_api_key(key_file=args.api_key_file,
                               environ=dict(os.environ) if environ is None else environ)
        raw_text = (args.file.read_text(encoding="utf-8") if args.file
                    else sys.stdin.read())
        request = build_request(args.url, api_key, raw_text)
    except (OSError, ValueError) as exc:
        print(f"genius_client: {exc}", file=sys.stderr)
        return 2
    try:
        status_code, raw = opener(request, timeout=_TIMEOUT_SECONDS)
    except OSError as exc:
        print(f"genius_client: service unreachable: {exc}", file=sys.stderr)
        return 3
    outcome = parse_outcome(status_code, raw)
    if outcome.kind == "ok":
        if args.json:
            print(json.dumps({"job_id": outcome.job_id, "status": "SUCCEEDED",
                              "digest": outcome.text}, ensure_ascii=False, indent=2))
        elif args.out:
            args.out.write_text(outcome.text, encoding="utf-8")
            print(f"digest written to {args.out} (job {outcome.job_id})",
                  file=sys.stderr)
        else:
            print(outcome.text, end="")
        return 0
    if outcome.kind == "pending":
        print(f"genius_client: job {outcome.job_id} waits for an approver; "
              "an approver grants it, then the token completes the job.",
              file=sys.stderr)
        return 1
    print(f"genius_client: {outcome.kind}: {outcome.reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
