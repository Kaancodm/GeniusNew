"""Explicit local runner injection for durable HTTP tests without systemd.

The production CLI never calls this file. A green fixture run is not evidence
of a separately credentialed worker service.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geniusnew.__main__ import _serve
from geniusnew.config import load_config
from geniusnew.contracts import ContractError
from geniusnew.isolation import IsolatedWorkerRunner
from geniusnew.keys import derive_keys
from geniusnew.results import WorkerAuthority


def main() -> int:
    """Serve the test configuration with an explicitly injected local isolated runner."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        keys = derive_keys(config.root_secret)
        authority = WorkerAuthority(result_key=keys.result_key,
                                    integrity_key=keys.integrity_key)
        return _serve(args.config, runner_factory=lambda worker: IsolatedWorkerRunner(
            worker, authority=authority))
    except ContractError as exc:
        print(f"geniusnew: refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
