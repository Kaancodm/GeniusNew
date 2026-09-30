"""`python -m geniusnew`: run the service, or compute an API key digest for its config.

    python -m geniusnew serve --config /etc/geniusnew/geniusnew.toml
    python -m geniusnew digest-api-key < key-file

`serve` refuses to start on any configuration error (`geniusnew/config.py`) and
exits non-zero; there is no partial start. It stops on SIGTERM or SIGINT, closes
the listener, then ends the anchor process it started.

`digest-api-key` reads the key from standard input rather than the command line,
where it would land in shell history and in every other user's `ps` output. It
prints only the SHA-256 digest, which is what the configuration holds. A client
presents the key as `Authorization: Bearer <key>`.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from typing import Sequence

from .config import ServiceConfig, load_config, read_database_dsn
from .contracts import ContractError
from .http_entry import PrincipalRegistry, serve
from .wiring import Service, build


def _fail(message: str) -> None:
    raise ContractError(message)


def _refuse_discontinuous_start(service: Service) -> None:
    """Refuse to start behind an anchor that remembers more than this chain holds.

    The audit chain lives in process memory until it is persisted (gate B5 in
    `docs/ROADMAP-V02.md`); the anchor's state file survives a restart. After a
    restart the anchor therefore holds a head this empty chain cannot extend, and
    every request would be refused one by one. Starting anyway would advertise a
    service that cannot serve, and resetting the anchor is exactly the rollback
    it exists to prevent. So the start is refused, and says why.
    """
    committed, _ = service.anchor.committed
    held = len(service.chain.records)
    if committed > held:
        _fail(f"the anchor has committed {committed} audit records but this process "
              f"holds {held}; the audit chain is not persisted yet (gate B5), so a "
              "restart cannot continue it")


def _serve(config_path: str) -> int:
    config = load_config(config_path)
    from .database import open_database

    with open_database(config.database_dsn):
        return _run_service(config)


def _run_service(config: ServiceConfig) -> int:
    service = build(root_secret=config.root_secret, policy=config.policy,
                    principals=config.principals, workers=config.workers,
                    anchor_state=config.anchor_state)
    try:
        _refuse_discontinuous_start(service)
        server = serve(service.entry, host=config.listen_host, port=config.listen_port)
    except BaseException:
        service.close()
        raise
    stop = threading.Event()

    def request_stop(signum, frame) -> None:  # noqa: ARG001 - signal handler signature
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    # serve_forever in a thread: shutdown() blocks until the loop has stopped,
    # so calling it from the thread running the loop would never return.
    loop = threading.Thread(target=server.serve_forever, name="geniusnew-http", daemon=True)
    try:
        loop.start()
        host, port = server.server_address[:2]
        print(f"geniusnew: listening on {host}:{port} (policy {config.policy.version})",
              file=sys.stderr, flush=True)
        stop.wait()
        print("geniusnew: stopping", file=sys.stderr, flush=True)
    finally:
        server.shutdown()
        server.server_close()
        service.close()
    return 0


def _digest_api_key() -> int:
    # One trailing line ending is what `echo` or an editor adds. A key travels in
    # an HTTP header, which cannot carry one, so it is never part of the key.
    key = sys.stdin.buffer.read(1024).removesuffix(b"\n").removesuffix(b"\r")
    print(PrincipalRegistry.digest(key))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m geniusnew")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("serve", help="run the HTTP service")
    run.add_argument("--config", required=True, help="path to the TOML configuration")
    migration = commands.add_parser("migrate", help="apply Core migrations as the schema owner")
    migration.add_argument("--dsn-file", required=True, help="private migration DSN file")
    commands.add_parser("digest-api-key", help="SHA-256 digest of the key on stdin")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "serve":
            return _serve(arguments.config)
        if arguments.command == "migrate":
            from .database import migrate

            migrate(read_database_dsn(arguments.dsn_file))
            return 0
        return _digest_api_key()
    except ContractError as exc:
        # The message is the refusal; a traceback would add nothing an operator
        # needs and could carry configuration values.
        print(f"geniusnew: refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
