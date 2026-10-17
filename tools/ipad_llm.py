"""Call an iPad-hosted OpenAI-compatible model over a verified Tailscale peer.

This is an operator tool, not a GeniusNew worker. Model output is untrusted text;
it cannot authorize jobs, call tools, or sign results.
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
import unicodedata
from urllib import error, request

from geniusnew.contracts import ContractError


_TAILNET = ipaddress.ip_network("100.64.0.0/10")
_MAX_WIRE = 64 * 1024
_MAX_PROMPT = 8 * 1024
_MAX_REPLY = 32 * 1024
_MAX_MODEL = 128
_INTERFACE = "tailscale0"


def _fail(message: str) -> None:
    """Refuse with a ContractError; the single choke point for every check below."""
    raise ContractError(message)


def _endpoint(host: str, port: int) -> str:
    """Build the base URL, refusing anything but an exact Tailscale IPv4 host and valid port."""
    if type(host) is not str or type(port) is not int or not 1 <= port <= 65535:
        _fail("iPad endpoint must have an IPv4 address and a valid port")
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        _fail("iPad endpoint must have an IPv4 address and a valid port")
    if address not in _TAILNET or str(address) != host:
        _fail("iPad endpoint must be an exact Tailscale IPv4 address")
    return f"http://{host}:{port}"


def _tailnet_status() -> dict:
    """Return the local `tailscale status --json` output, or refuse if it is unavailable."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True,
            timeout=5, check=False,
        )
        if result.returncode != 0:
            _fail("Tailscale status is unavailable")
        status = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        _fail("Tailscale status is unavailable")
    if type(status) is not dict:
        _fail("Tailscale status is invalid")
    return status


def _require_ipad_peer(host: str, status: dict) -> None:
    """Refuse unless the tailnet is online and `host` is an online iOS/iPadOS peer."""
    if (status.get("BackendState") != "Running"
            or type(status.get("Self")) is not dict
            or status["Self"].get("Online") is not True):
        _fail("Tailscale is not online")
    peers = status.get("Peer")
    if type(peers) is not dict:
        _fail("Tailscale peer data is unavailable")
    if not any(
        type(peer) is dict and peer.get("Online") is True
        and peer.get("OS") in ("iOS", "iPadOS")
        and type(peer.get("TailscaleIPs")) is list
        and host in peer["TailscaleIPs"]
        for peer in peers.values()
    ):
        _fail("endpoint is not an online iPad Tailscale peer")


def _require_tailnet_route(host: str) -> None:
    """A 100.x address is also CGNAT, so verify the actual egress interface."""
    try:
        result = subprocess.run(
            ["ip", "-j", "route", "get", host], capture_output=True, text=True,
            timeout=5, check=False,
        )
        if result.returncode != 0:
            _fail("iPad route is unavailable")
        routes = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        _fail("iPad route is unavailable")
    if (type(routes) is not list or len(routes) != 1
            or type(routes[0]) is not dict or routes[0].get("dev") != "tailscale0"):
        _fail("iPad route does not use the Tailscale interface")


def _api_key(path: Path) -> str:
    """Read the opaque API key from a private, non-symlinked, owned-by-us regular file."""
    if not isinstance(path, Path):
        _fail("API key file path is invalid")
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                _fail("API key file must be owned by this user and private")
            raw = os.read(descriptor, 258)
        finally:
            os.close(descriptor)
    except OSError:
        _fail("API key file is unavailable")
    raw = raw.removesuffix(b"\n")
    if not 32 <= len(raw) <= 256 or any(byte < 33 or byte > 126 for byte in raw):
        _fail("API key file must contain one opaque ASCII key")
    return raw.decode("ascii")


class _NoRedirect(request.HTTPRedirectHandler):
    """Refuse any HTTP redirect so the API key is never replayed to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Refuse the redirect instead of following it."""
        _fail("iPad API redirects are forbidden")


class _DeadlineSocket(socket.socket):
    """Apply one wall-clock deadline to every socket operation, including reads."""

    def __init__(self, deadline: float) -> None:
        """Create a plain TCP socket that enforces the given monotonic deadline."""
        super().__init__(socket.AF_INET, socket.SOCK_STREAM)
        self._deadline = deadline

    def _arm(self) -> None:
        """Set the per-call timeout to the time left before the deadline, or refuse."""
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            _fail("iPad API deadline expired")
        self.settimeout(remaining)

    def connect(self, address) -> None:
        """Connect, after re-arming the timeout against the overall deadline."""
        self._arm()
        return super().connect(address)

    def sendall(self, data, flags=0) -> None:
        """Send, after re-arming the timeout against the overall deadline."""
        self._arm()
        return super().sendall(data, flags)

    def recv(self, size, flags=0) -> bytes:
        """Receive, after re-arming the timeout against the overall deadline."""
        self._arm()
        return super().recv(size, flags)

    def recv_into(self, buffer, nbytes=0, flags=0) -> int:
        """Receive into a buffer, after re-arming the timeout against the overall deadline."""
        self._arm()
        return super().recv_into(buffer, nbytes, flags)


class _BoundHTTPConnection(http.client.HTTPConnection):
    """Prevent a route change from sending the API key outside Tailscale."""

    def __init__(self, host, timeout=120, **kwargs):
        """Record the absolute deadline derived from `timeout` for the eventual connect."""
        super().__init__(host, timeout=timeout, **kwargs)
        self._deadline = time.monotonic() + float(timeout)

    def connect(self) -> None:
        """Open a deadline-bound socket pinned to the Tailscale interface, or refuse."""
        connection = _DeadlineSocket(self._deadline)
        try:
            try:
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE,
                                      _INTERFACE.encode("ascii") + b"\0")
            except OSError:
                _fail("Tailscale socket binding failed; check interface and permissions")
            connection.connect((self.host, self.port))
        except BaseException:
            connection.close()
            raise
        self.sock = connection


class _BoundHTTPHandler(request.HTTPHandler):
    """Route plain HTTP requests through the interface-bound connection class."""

    def http_open(self, req):
        """Open the request using `_BoundHTTPConnection` instead of the default connection."""
        return self.do_open(_BoundHTTPConnection, req)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    """JSON object hook that refuses a response with duplicate keys at any level."""
    result = {}
    for name, value in pairs:
        if name in result:
            _fail("iPad API response contains duplicate JSON fields")
        result[name] = value
    return result


def _reject_constant(_value: str) -> None:
    """JSON parse-constant hook that refuses NaN/Infinity instead of silently accepting them."""
    _fail("iPad API response contains a non-JSON number")


def _request_json(url: str, key: str, *, payload: dict | None,
                  timeout: float) -> object:
    """Send a size-bounded GET or POST with the bearer key and return parsed, bounded JSON."""
    wire = None if payload is None else json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    if wire is not None and len(wire) > _MAX_WIRE:
        _fail("iPad API request is too large")
    method = "GET" if wire is None else "POST"
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if wire is not None:
        headers["Content-Type"] = "application/json"
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect(),
                                  _BoundHTTPHandler())
    try:
        response = opener.open(request.Request(url, data=wire, headers=headers,
                                               method=method), timeout=timeout)
        with response:
            if response.status != 200:
                _fail("iPad API did not return success")
            raw = response.read(_MAX_WIRE + 1)
    except error.HTTPError:
        _fail("iPad API did not return success")
    except (OSError, TimeoutError, error.URLError, http.client.HTTPException):
        _fail("iPad API is unavailable")
    if not raw or len(raw) > _MAX_WIRE:
        _fail("iPad API response is empty or too large")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        _fail("iPad API response is invalid JSON")


def _model(value: str) -> str:
    """Validate a model ID: non-empty, bounded UTF-8 size, no control or separator characters."""
    if type(value) is not str:
        _fail("model ID is invalid")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        _fail("model ID is invalid")
    if (not value or size > _MAX_MODEL
            or any(unicodedata.category(char)[0] in "CZ" for char in value)):
        _fail("model ID is invalid")
    return value


def _timeout(value: float) -> float:
    """Validate that the overall request deadline is a number within 0.1 to 300 seconds."""
    if type(value) not in (int, float) or not 0.1 <= value <= 300:
        _fail("timeout must be between 0.1 and 300 seconds")
    return float(value)


def models(host: str, port: int, key_file: Path, *, timeout: float = 10) -> tuple[str, ...]:
    """List the verified iPad's model IDs, refusing an empty, duplicated, or oversized list."""
    base = _endpoint(host, port)
    _require_ipad_peer(host, _tailnet_status())
    _require_tailnet_route(host)
    key = _api_key(key_file)
    result = _request_json(base + "/v1/models", key, payload=None,
                           timeout=_timeout(timeout))
    if type(result) is not dict or type(result.get("data")) is not list:
        _fail("iPad model list is invalid")
    ids = []
    for item in result["data"]:
        if type(item) is not dict:
            _fail("iPad model list is invalid")
        ids.append(_model(item.get("id")))
    if not ids or len(ids) > 64 or len(set(ids)) != len(ids):
        _fail("iPad model list is empty, duplicated, or too large")
    return tuple(ids)


def chat(host: str, port: int, key_file: Path, model: str, prompt: str,
         *, timeout: float = 120, max_tokens: int = 256) -> str:
    """Send one bounded prompt to the verified iPad and return its plain-text reply.

    The reply is untrusted model output: a requested tool call, a non-stop finish
    reason, a wrong role, a mismatched model, or unprintable control characters are
    all refused rather than returned, since nothing here may authorize an action.
    """
    base = _endpoint(host, port)
    _require_ipad_peer(host, _tailnet_status())
    _require_tailnet_route(host)
    key = _api_key(key_file)
    model = _model(model)
    if type(prompt) is not str:
        _fail("prompt must be non-empty and bounded UTF-8 text")
    try:
        prompt_size = len(prompt.encode("utf-8"))
    except UnicodeEncodeError:
        _fail("prompt must be non-empty and bounded UTF-8 text")
    if not prompt or prompt_size > _MAX_PROMPT:
        _fail("prompt must be non-empty and bounded UTF-8 text")
    if type(max_tokens) is not int or not 1 <= max_tokens <= 1024:
        _fail("max_tokens must be between 1 and 1024")
    result = _request_json(
        base + "/v1/chat/completions", key,
        payload={"model": model, "messages": [{"role": "user", "content": prompt}],
                 "stream": False, "max_tokens": max_tokens},
        timeout=_timeout(timeout),
    )
    if type(result) is not dict or type(result.get("choices")) is not list or len(result["choices"]) != 1:
        _fail("iPad completion is invalid")
    choice = result["choices"][0]
    if type(choice) is not dict or type(choice.get("message")) is not dict:
        _fail("iPad completion is invalid")
    message = choice["message"]
    if (message.get("role") != "assistant" or message.get("tool_calls")
            or message.get("function_call") or choice.get("finish_reason") != "stop"):
        _fail("iPad completion requested a tool or has an invalid role")
    answer = message.get("content")
    if type(answer) is not str:
        _fail("iPad completion text is empty or too large")
    try:
        answer_size = len(answer.encode("utf-8"))
    except UnicodeEncodeError:
        _fail("iPad completion text is empty or too large")
    if (not answer or answer_size > _MAX_REPLY
            or any((ord(char) < 32 and char not in "\t\n\r")
                   or 127 <= ord(char) <= 159 for char in answer)):
        _fail("iPad completion text is empty or too large")
    if result.get("model") != model:
        _fail("iPad completion model does not match the request")
    return answer


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: run `models` or `chat` and print a refusal message on `ContractError`."""
    parser = argparse.ArgumentParser(description="Query an iPad model over Tailscale")
    parser.add_argument("command", choices=("models", "chat"))
    parser.add_argument("--host", required=True, help="iPad Tailscale IPv4 address")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--model", help="exact model ID for chat")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--max-tokens", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "models":
            if args.model is not None or args.max_tokens is not None:
                _fail("models does not accept chat options")
            names = models(args.host, args.port, args.key_file,
                           timeout=10 if args.timeout is None else args.timeout)
            for name in names:
                print(name)
        else:
            if args.model is None:
                _fail("chat requires an exact model ID")
            prompt = sys.stdin.read(_MAX_PROMPT + 1)
            answer = chat(args.host, args.port, args.key_file, args.model, prompt,
                          timeout=120 if args.timeout is None else args.timeout,
                          max_tokens=256 if args.max_tokens is None else args.max_tokens)
            print(answer)
    except ContractError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
