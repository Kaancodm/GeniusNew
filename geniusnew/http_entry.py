"""The request boundary: an API key becomes a principal, and nothing else does.

`docs/ROADMAP-V01.md` step 16 states the rule as a prohibition rather than a
feature: identity, tier and rights come **never** from the request. A client
sends a key and a payload. Everything about who it is comes from a server-side
table, and everything about what it may do comes from the policy grant keyed on
that subject.

## Why this is the smallest file with the most say

Every other boundary in this repository checks something about bytes that were
already authorized. This one decides *whose* authorization applies at all, and
it is the only place a stranger can reach. A field read from the request here —
a `tier`, a `user_id`, a `subject`, even a convenient `job_id` — silently
becomes trusted at every layer after it, because those layers check against the
policy grant this decision selected.

So the request body is a closed shape containing one thing: the payload. Not
ignored-if-present, refused: a client that sends `{"text": ..., "tier": "admin"}`
gets a refusal rather than a quiet demotion, because silently dropping a field
tells an attacker nothing and tells an honest caller nothing either.

## Keys are never held in the clear

The registry stores SHA-256 digests and resolves by dictionary lookup on the
digest of what was presented. There is no comparison loop to leak timing, no
plaintext key in memory after construction, and a dump of this object discloses
no credential. The digest is unsalted on purpose: this is a lookup table for
high-entropy machine keys, not a password store, and salting would force the
comparison loop back.

## The job id is minted here

A client that chooses its own job id chooses which job id to collide with. The
orchestrator burns each id exactly once, so a caller supplying them could deny
service to a job it does not own, or replay a name. The entrance mints one from
`secrets`; the generator is a seam only so tests can be deterministic.

## Approval travels as a header, not as a field

An approval-bound job takes two requests, because a one-time approval is bound
to one signed wire and that wire has to exist before anyone can approve it.
`POST /jobs` answers `PENDING_APPROVAL`; the token is granted server-side and
handed to the client out of band; `POST /jobs/<job_id>/approve` presents it in
`X-Approval-Token` with a body of exactly `{}`. The token is a capability, not
payload, so it stays out of the one-field body — and an approval request that
carries anything else in its body is refused like any other extra field.

Every refusal from behind the entrance is the same `409 REJECTED`: an unknown
job, another subject's job, a wrong token and a used one cannot be told apart
from outside, so the route answers nothing about which jobs exist.

## What this is not

There is no rate limiting, no authentication beyond the key, no TLS termination,
and no session. Those belong to a deployment, and pretending otherwise inside
this file would be the kind of claim `SECURITY.md` exists to prevent. What is
here is the mapping and the refusals around it.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contracts import ContractError

_MAX_BODY_BYTES = 16 * 1024
_MAX_KEY_BYTES = 256
_MIN_KEY_BYTES = 16
_CONTENT_TYPE = "application/json"
_PATH = "/jobs"
_APPROVE_PATH = re.compile(r"\A/jobs/([A-Za-z0-9-]{1,64})/approve\Z")
_APPROVAL_TOKEN = re.compile(r"\A[0-9a-f]{64}\Z")
_METHOD = "POST"
_AUTH_SCHEME = "Bearer "

# Closed, like every other refusal vocabulary here, so that a refusal cannot
# carry text out of the boundary it refused at. A reason a client is told is a
# reason an attacker is told, so these say what is wrong with the *request*,
# never what is known about the key.
REASONS = frozenset({
    "NOT_FOUND",
    "METHOD_NOT_ALLOWED",
    "UNSUPPORTED_MEDIA_TYPE",
    "PAYLOAD_TOO_LARGE",
    "MALFORMED_REQUEST",
    "UNAUTHENTICATED",
    "REJECTED",
    "ACCEPTED",
})


def _fail(message: str) -> None:
    raise ContractError(message)


@dataclass(frozen=True)
class Principal:
    """Who the caller is. Deliberately only that.

    No tier, no tools, no user id: those live in the policy grant keyed on
    `subject`. Carrying them here would create a second source of truth about
    authorization, and the first thing that happens to a second source of truth
    is that it disagrees.
    """

    subject: str

    def __post_init__(self) -> None:
        if type(self.subject) is not str or not self.subject:
            _fail("subject must be a non-empty string")
        if len(self.subject.encode("utf-8", "surrogatepass")) > 160:
            _fail("subject must be at most 160 bytes")


class PrincipalRegistry:
    """API key digests to principals. The keys themselves are not kept."""

    def __init__(self, principals: Mapping[str, str]) -> None:
        if not isinstance(principals, Mapping) or not principals:
            _fail("principals must be a non-empty mapping of key digest to subject")
        table: dict[str, Principal] = {}
        for digest, subject in principals.items():
            if type(digest) is not str or len(digest) != 64:
                _fail("principal keys must be SHA-256 digests as 64 hex characters")
            try:
                bytes.fromhex(digest)
            except ValueError as exc:
                raise ContractError(
                    "principal keys must be SHA-256 digests as 64 hex characters") from exc
            table[digest.lower()] = Principal(subject)
        self._table = table

    @classmethod
    def from_api_keys(cls, api_keys: Mapping[bytes, str]) -> "PrincipalRegistry":
        """Build from plaintext keys, hashing them here so callers need not.

        The keys are not retained: what this object holds afterwards is the
        table below, which discloses nothing if it is dumped or logged.
        """
        if not isinstance(api_keys, Mapping) or not api_keys:
            _fail("api_keys must be a non-empty mapping of key to subject")
        # No "two keys collided" check: a Mapping cannot hold one key twice,
        # and two different keys sharing a SHA-256 digest is not a case to
        # handle. The refusal guard flagged it as unreachable, which is exactly
        # what it is for.
        return cls({cls.digest(key): subject for key, subject in api_keys.items()})

    @staticmethod
    def digest(api_key: Any) -> str:
        if type(api_key) is not bytes:
            _fail("api key must be bytes")
        if not _MIN_KEY_BYTES <= len(api_key) <= _MAX_KEY_BYTES:
            _fail(f"api key must be between {_MIN_KEY_BYTES} and {_MAX_KEY_BYTES} bytes")
        return hashlib.sha256(api_key).hexdigest()

    def resolve(self, api_key: Any) -> Principal | None:
        """The presented key's principal, or None. No exception either way.

        A lookup, not a comparison: there is no loop over candidates whose
        duration could say how much of a key was right. An unknown key and a
        malformed one return the same nothing, so the caller cannot learn which
        of the two it sent.
        """
        try:
            digest = self.digest(api_key)
        except ContractError:
            return None
        return self._table.get(digest)

    def __len__(self) -> int:
        return len(self._table)


@dataclass(frozen=True)
class Response:
    status: int
    reason: str
    body: dict[str, Any]

    def __post_init__(self) -> None:
        if type(self.status) is not int or not 100 <= self.status <= 599:
            _fail("status must be an HTTP status code")
        if self.reason not in REASONS:
            _fail("reason is not an entrance reason code")

    def to_bytes(self) -> bytes:
        return json.dumps(self.body, sort_keys=True, separators=(",", ":")).encode()


def _refusal(status: int, reason: str) -> Response:
    return Response(status=status, reason=reason, body={"error": reason})


class HttpEntry:
    """Maps one authenticated request onto one job, and refuses everything else.

    `submit` is called with a server-side subject and a server-minted job id. It
    is the only way out of this object, and it is given nothing the request
    could have influenced except the payload itself.
    """

    def __init__(self, *, registry: PrincipalRegistry,
                 submit: Callable[..., Mapping[str, Any]],
                 job_ids: Callable[[], str] | None = None,
                 max_body_bytes: int = _MAX_BODY_BYTES,
                 complete: Callable[..., Mapping[str, Any]] | None = None) -> None:
        if not isinstance(registry, PrincipalRegistry):
            _fail("registry must be a PrincipalRegistry")
        if not callable(submit):
            _fail("submit must be callable")
        if complete is not None and not callable(complete):
            _fail("complete must be callable")
        if job_ids is not None and not callable(job_ids):
            _fail("job_ids must be callable")
        if type(max_body_bytes) is not int or not 0 < max_body_bytes <= _MAX_BODY_BYTES:
            _fail(f"max_body_bytes must be between 1 and {_MAX_BODY_BYTES}")
        self._registry = registry
        self._submit = submit
        self._complete = complete
        self._job_ids = job_ids or (lambda: f"job-{secrets.token_hex(16)}")
        self._max_body_bytes = max_body_bytes

    def handle(self, *, method: Any, path: Any, headers: Any, body: Any) -> Response:
        """One request in, one response out. No exception reaches a client."""
        if type(method) is not str or type(path) is not str:
            return _refusal(400, "MALFORMED_REQUEST")
        if not isinstance(headers, Mapping):
            return _refusal(400, "MALFORMED_REQUEST")
        approving = _APPROVE_PATH.match(path)
        # Without a completion callback the route does not exist, and says so
        # the same way any other unknown path does.
        if approving is not None and self._complete is None:
            return _refusal(404, "NOT_FOUND")
        if path != _PATH and approving is None:
            return _refusal(404, "NOT_FOUND")
        if method != _METHOD:
            return _refusal(405, "METHOD_NOT_ALLOWED")
        if type(body) is not bytes:
            return _refusal(400, "MALFORMED_REQUEST")
        # Size before parsing: a megabyte of nested JSON costs whatever it costs
        # to parse, and the entrance is the one place an unauthenticated caller
        # can make that choice.
        if len(body) > self._max_body_bytes:
            return _refusal(413, "PAYLOAD_TOO_LARGE")

        lowered = {str(name).lower(): value for name, value in headers.items()}
        content_type = lowered.get("content-type")
        if type(content_type) is not str or content_type.split(";")[0].strip() != _CONTENT_TYPE:
            return _refusal(415, "UNSUPPORTED_MEDIA_TYPE")

        # Authenticated before the body is parsed: an unauthenticated caller
        # should not be able to spend this process's time on its JSON.
        principal = self._principal(lowered.get("authorization"))
        if principal is None:
            return _refusal(401, "UNAUTHENTICATED")

        if approving is not None:
            return self._approve(principal, approving.group(1), body,
                                 lowered.get("x-approval-token"))

        payload = self._payload(body)
        if payload is None:
            return _refusal(400, "MALFORMED_REQUEST")

        return self._dispatch(principal, payload)

    def _principal(self, authorization: Any) -> Principal | None:
        if type(authorization) is not str or not authorization.startswith(_AUTH_SCHEME):
            return None
        presented = authorization[len(_AUTH_SCHEME):]
        if not presented or len(presented) > _MAX_KEY_BYTES:
            return None
        try:
            return self._registry.resolve(presented.encode("utf-8"))
        except UnicodeEncodeError:
            return None

    def _payload(self, body: bytes) -> dict[str, str] | None:
        """Exactly one field, refused rather than trimmed.

        Dropping an unexpected `tier` silently would let a caller believe it was
        honoured. Refusing says what happened without saying anything about what
        a privileged request would have looked like.
        """
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return None
        if not isinstance(value, dict) or set(value) != {"text"}:
            return None
        text = value["text"]
        if type(text) is not str or not text:
            return None
        return {"text": text}

    def _dispatch(self, principal: Principal, payload: dict[str, str]) -> Response:
        job_id = self._job_ids()
        if type(job_id) is not str or not job_id:
            return _refusal(500, "REJECTED")
        return self._call(self._submit, job_id, subject=principal.subject,
                          job_id=job_id, payload=payload)

    def _approve(self, principal: Principal, job_id: str, body: bytes,
                 token: Any) -> Response:
        try:
            empty = json.loads(body.decode("utf-8")) == {}
        except (UnicodeDecodeError, ValueError, RecursionError):
            empty = False
        if not empty:
            return _refusal(400, "MALFORMED_REQUEST")
        if type(token) is not str or not _APPROVAL_TOKEN.match(token):
            return _refusal(400, "MALFORMED_REQUEST")
        return self._call(self._complete, job_id, subject=principal.subject,
                          job_id=job_id, approval_token=bytes.fromhex(token))

    def _call(self, target: Callable[..., Any], minted: str, **arguments: Any) -> Response:
        try:
            outcome = target(**arguments)
        except ContractError:
            # The refusal's own sentence stays inside. A client learns that its
            # job was refused, not which check refused it: those messages name
            # policy fields, and naming them to a stranger is a map.
            return _refusal(409, "REJECTED")
        if not isinstance(outcome, Mapping):
            return _refusal(500, "REJECTED")
        body = {"job_id": minted, **{key: value for key, value in outcome.items()}}
        return Response(status=202, reason="ACCEPTED", body=body)


# --- the socket in front of it ---------------------------------------------
#
# Deliberately the thin half. Everything that decides anything is above; this
# turns a socket into those arguments and back, so the security of the entrance
# does not depend on running a server to test it.


def make_handler(entry: HttpEntry):
    """A `BaseHTTPRequestHandler` class bound to one entrance."""
    from http.server import BaseHTTPRequestHandler

    if not isinstance(entry, HttpEntry):
        _fail("entry must be an HttpEntry")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        # Without this a client that declares a body and then sends nothing
        # holds a thread for as long as it likes, and ThreadingHTTPServer gives
        # it a fresh one per connection.
        timeout = 10

        def version_string(self) -> str:
            # Not "BaseHTTP/0.6 Python/3.11.2". A stranger learns the version of
            # the interpreter to target from a header nobody needed.
            return "geniusnew"

        def log_message(self, format: str, *args: Any) -> None:
            # The default logs the request line, which is attacker-chosen text
            # going somewhere an operator reads. Logging belongs to the audit
            # chain, where what may appear is a closed contract.
            return

        def _respond(self, response: Response) -> None:
            payload = response.to_bytes()
            self.send_response(response.status)
            self.send_header("Content-Type", _CONTENT_TYPE)
            self.send_header("Content-Length", str(len(payload)))
            if self.close_connection:
                # Said out loud rather than just dropping the socket: a client
                # that does not know the connection is finished will send its
                # next request into a closing one and call the result a network
                # error, which is the wrong thing to debug.
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def _read_body(self) -> bytes | None:
            # Chunked bodies are not read here, and a body left unread on a
            # keep-alive connection is where the next request gets parsed from
            # attacker-controlled bytes. Refused, and the connection closed.
            if self.headers.get("Transfer-Encoding") is not None:
                return None
            raw = self.headers.get("Content-Length")
            if raw is None:
                return b""
            try:
                length = int(raw)
            except (TypeError, ValueError):
                return None
            if length < 0 or length > _MAX_BODY_BYTES:
                return None
            return self.rfile.read(length)

        def _serve(self) -> None:
            body = self._read_body()
            if body is None:
                # Covers a Content-Length that is not a number, one larger than
                # anything this entrance will read, and a chunked body. Reading
                # it to find out would be doing the work the limit exists to
                # refuse — and because those bytes stay unread, this connection
                # cannot be reused: whatever is left in it would be parsed as
                # the next request.
                self.close_connection = True
                self._respond(_refusal(413, "PAYLOAD_TOO_LARGE"))
                return
            self._respond(entry.handle(method=self.command, path=self.path,
                                       headers=dict(self.headers.items()), body=body))

        def do_HEAD(self) -> None:
            # Status and headers, never a body. Announcing a Content-Length and
            # then writing the bytes anyway desynchronises a keep-alive client,
            # which is the same class of bug as leaving a body unread.
            response = entry.handle(method=self.command, path=self.path,
                                    headers=dict(self.headers.items()), body=b"")
            payload = response.to_bytes()
            self.send_response(response.status)
            self.send_header("Content-Type", _CONTENT_TYPE)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()

        do_POST = _serve
        do_GET = _serve
        do_PUT = _serve
        do_PATCH = _serve
        do_DELETE = _serve

    return Handler


def serve(entry: HttpEntry, *, host: str = "127.0.0.1", port: int = 0):
    """A threading server bound to `entry`, not started. The caller runs it.

    Binds to loopback by default: an entrance that listens on every interface
    the moment someone imports it is a decision, and it should be taken out
    loud.
    """
    from http.server import ThreadingHTTPServer

    if type(host) is not str or not host:
        _fail("host must be a non-empty string")
    if type(port) is not int or not 0 <= port <= 65535:
        _fail("port must be between 0 and 65535")
    return ThreadingHTTPServer((host, port), make_handler(entry))
