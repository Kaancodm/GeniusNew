from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .actions import allowed_actions, run_action
from .checks import DEFAULT_REPO, snapshot, agent_hub
from .mail_center import snapshot as mail_snapshot
from .harpa_inbox import HarpaPayloadError, snapshot as harpa_snapshot, store as store_harpa

STATIC = Path(__file__).with_name("static")


def _deny(handler: BaseHTTPRequestHandler, status: int) -> None:
    """HTTP refusal shape enumerated by the existing mutation guard."""
    handler.send_error(status)


def _refusal() -> None:
    """Disable ingress when no trustworthy credential is available."""
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "GeniusControlDeck/0.1"
    timeout = 10

    def _trusted_host(self) -> bool:
        # A page on any domain can resolve its own name to 127.0.0.1 (DNS
        # rebinding) and then read /api/mail as same-origin. The Host header is
        # the one thing such a page cannot choose, so it is checked first.
        if self.headers.get("Host") not in self.server.allowed_hosts():
            _deny(self, 421)
            return False
        return True

    def do_GET(self) -> None:
        """Serve dashboard resources and snapshots only for an allowed Host header."""
        if not self._trusted_host():
            return
        path = urlparse(self.path).path
        if path == "/":
            self._file("index.html", "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(snapshot(self.server.repo))
            return
        if path == "/api/agents":
            self._json(agent_hub(self.server.repo))
            return
        if path == "/api/mail":
            self._json(mail_snapshot())
            return
        if path == "/api/harpa":
            self._json(harpa_snapshot(self.server.harpa_inbox))
            return
        _deny(self, 404)

    def do_POST(self) -> None:
        """Route trusted-host writes through HARPA authentication or action validation."""
        if not self._trusted_host():
            return
        path = urlparse(self.path).path
        if path == "/api/harpa":
            self._harpa()
            return
        if path != "/api/action":
            _deny(self, 404)
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {
                f"{scheme}://{host}" for host in self.server.allowed_hosts()
                for scheme in ("http", "https")}:
            _deny(self, 403)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            _deny(self, 415)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            _deny(self, 400)
            return
        if size <= 0 or size > 1024:
            _deny(self, 413)
            return
        try:
            payload = json.loads(self.rfile.read(size))
            action = payload["action"]
            if not isinstance(action, str) or action not in allowed_actions():
                raise ValueError
        except (json.JSONDecodeError, KeyError, ValueError, TypeError, RecursionError):
            _deny(self, 400)
            return
        self._json(run_action(action, self.server.repo))

    def _harpa(self) -> None:
        """Authenticate a bounded JSON report and acknowledge storage with HTTP 202.

        Return an HTTP error if the inbox is disabled, authentication fails, or the
        request cannot be validated or stored.
        """
        token = self.server.harpa_token()
        if token is None or self.server.harpa_inbox is None:
            _deny(self, 404)
            return
        supplied = self.headers.get("Authorization", "")
        if not supplied.isascii() or not hmac.compare_digest(supplied, f"Bearer {token}"):
            _deny(self, 401)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            _deny(self, 415)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            _deny(self, 400)
            return
        if size <= 0 or size > 16384:
            _deny(self, 413)
            return
        try:
            payload = json.loads(self.rfile.read(size))
            result = store_harpa(payload, self.server.harpa_inbox)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, HarpaPayloadError):
            _deny(self, 400)
            return
        except OSError:
            _deny(self, 503)
            return
        self._json(result, status=202)

    def _json(self, payload: object, *, status: int = 200) -> None:
        """Send a JSON response with the requested status and cache/security headers."""
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name: str, content_type: str) -> None:
        body = (STATIC / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class Server(ThreadingHTTPServer):
    repo: Path
    harpa_inbox: Path | None = None
    harpa_token_file: Path | None = None
    # Names a local proxy presents, such as `tailscale serve` or an SSH tunnel;
    # each one is named explicitly by whoever starts the deck.
    extra_hosts: frozenset[str] = frozenset()

    def allowed_hosts(self) -> frozenset[str]:
        host, port = self.server_address[:2]
        names = {"127.0.0.1", "localhost", host}
        own = {f"{name}:{port}" for name in names}
        if port == 80:
            # Clients leave the default port out of Host.
            own |= names
        return frozenset(own | self.extra_hosts)

    def harpa_token(self) -> str | None:
        """Read the stripped token, returning None if absent, unreadable, or invalid."""
        if self.harpa_token_file is None:
            return _refusal()
        try:
            token = self.harpa_token_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if not (32 <= len(token) <= 256 and token.isascii() and "\n" not in token):
            return _refusal()
        return token


# Tailscale hands out IPv4 addresses from this range only. On a host whose ISP
# also uses CGNAT it can belong to the public-facing interface;
# docs/CONTROL-DECK.md names that limit. IPv6 is refused: the server is an
# AF_INET socket and would fail at bind time instead of here.
_TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def bind_host(value: str) -> str:
    # The deck has no login: whoever reaches the port sees mail metadata and
    # can start the allowlisted actions. It therefore listens on loopback or
    # on the private tailnet, never on a wildcard or a LAN/public address.
    if value == "localhost":
        return value
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        address = None
    if address is not None and (address.is_loopback or address in _TAILSCALE_NETWORK):
        return value
    raise argparse.ArgumentTypeError("host must be an IPv4 loopback or Tailscale address")


def host_name(value: str) -> str:
    if not value or len(value) > 253 or any(c.isspace() or c in "/@\\" for c in value):
        raise argparse.ArgumentTypeError("allow-host must be a bare host[:port]")
    return value


def main() -> None:
    """Parse listener, repository, and optional HARPA settings, then serve forever."""
    parser = argparse.ArgumentParser(description="GeniusNew read-only control deck")
    parser.add_argument("--host", type=bind_host, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--harpa-inbox", type=Path)
    parser.add_argument("--harpa-token-file", type=Path)
    parser.add_argument("--allow-host", type=host_name, action="append", default=[],
                        help="extra Host header a local proxy presents, e.g. a tailnet name")
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("port must be between 1 and 65535")
    server = Server((args.host, args.port), Handler)
    server.repo = args.repo.resolve()
    server.harpa_inbox = args.harpa_inbox
    server.harpa_token_file = args.harpa_token_file
    server.extra_hosts = frozenset(args.allow_host)
    print(f"Control Deck: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
