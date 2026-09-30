from __future__ import annotations

import argparse
import ipaddress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .actions import allowed_actions, run_action
from .checks import DEFAULT_REPO, snapshot, agent_hub
from .mail_center import snapshot as mail_snapshot

STATIC = Path(__file__).with_name("static")


class Handler(BaseHTTPRequestHandler):
    server_version = "GeniusControlDeck/0.1"

    def _trusted_host(self) -> bool:
        # A page on any domain can resolve its own name to 127.0.0.1 (DNS
        # rebinding) and then read /api/mail as same-origin. The Host header is
        # the one thing such a page cannot choose, so it is checked first.
        if self.headers.get("Host") not in self.server.allowed_hosts():
            self.send_error(421)
            return False
        return True

    def do_GET(self) -> None:
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
            self._json(agent_hub())
            return
        if path == "/api/mail":
            self._json(mail_snapshot())
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self._trusted_host():
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {
                f"{scheme}://{host}" for host in self.server.allowed_hosts()
                for scheme in ("http", "https")}:
            self.send_error(403)
            return
        if urlparse(self.path).path != "/api/action":
            self.send_error(404)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.send_error(415)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if size <= 0 or size > 1024:
            self.send_error(413)
            return
        try:
            payload = json.loads(self.rfile.read(size))
            action = payload["action"]
            if not isinstance(action, str) or action not in allowed_actions():
                raise ValueError
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            self.send_error(400)
            return
        self._json(run_action(action, self.server.repo))

    def _json(self, payload: object) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
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
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class Server(ThreadingHTTPServer):
    repo: Path
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
    parser = argparse.ArgumentParser(description="GeniusNew read-only control deck")
    parser.add_argument("--host", type=bind_host, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--allow-host", type=host_name, action="append", default=[],
                        help="extra Host header a local proxy presents, e.g. a tailnet name")
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("port must be between 1 and 65535")
    server = Server((args.host, args.port), Handler)
    server.repo = args.repo.resolve()
    server.extra_hosts = frozenset(args.allow_host)
    print(f"Control Deck: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()