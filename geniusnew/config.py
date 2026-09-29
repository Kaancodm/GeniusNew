"""Server configuration: the only way a running service learns its secrets and policy.

`docs/ROADMAP-V02.md` gate C1. Until now the only thing that started the whole
path was `scripts/demo.py`, which derives its keys from a published demo secret
and invents its API key. A server cannot do either. This module reads one TOML
file and turns it into the arguments `wiring.build` needs, and refuses anything
it cannot vouch for.

## What it refuses, and why each is a refusal rather than a default

**Unknown keys.** A misspelt `requires_aproval` that is silently ignored becomes
`requires_approval = false`. Every table has a closed key set.

**A listener that is not loopback.** The HTTP entrance has no TLS, no rate limit
and no sessions (`SECURITY.md`). A reverse proxy on the same host provides them;
binding anything else would put the bare entrance on a network, and that is a
decision to take in a reviewed change to this file, not in a config value.

**A root secret that anyone else could read, or that is not a file.** The root
secret derives every signing key. It is read from a regular file owned by the
service user with no group or other permission bits, opened without following
symlinks so the checks and the read concern the same file. Its bytes are used
exactly as stored: stripping a trailing newline would make the same file mean
two different keys depending on which tool wrote it.

**The demo secret.** It is published in this repository. A service that starts
with it signs with keys anyone can derive.

**A principal without a grant, or a tool without a worker.** Both would start a
service that authenticates callers it can never serve, or advertises a tool it
cannot run. They are configuration errors, and the place to find them is at
start, not per request.

**No anchor state.** Without it the anchor forgets every committed head when the
service restarts, and a shortened chain verifies again. The demo may do that; a
server may not.

Nothing here reads the environment. A secret in an environment variable is
visible in `/proc/<pid>/environ` to the same user and inherited by every child,
including the worker; a file with mode 0600 is neither.
"""

from __future__ import annotations

import ipaddress
import os
import stat
import tomllib
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import ContractError, Grant, Policy
from .workers import DeterministicSummarizer, Worker

# Published in scripts/demo.py. Refused here so it cannot become a real key.
DEMO_ROOT_SECRET = b"demo-root-secret-not-for-real-use!!!"

_MIN_SECRET_BYTES = 32
_MAX_SECRET_BYTES = 4096
_MAX_CONFIG_BYTES = 1024 * 1024

_TOP_KEYS = frozenset({"service", "policy", "principals"})
_SERVICE_KEYS = frozenset({"listen_host", "listen_port", "root_secret_file", "anchor_state"})
_POLICY_KEYS = frozenset({"version", "orchestrator_id", "handoff_ttl_seconds",
                          "allowed_tools", "allowed_sandbox_profiles", "grants"})
_GRANT_KEYS = frozenset({"subject", "user_id", "worker_agent_id", "tier", "tools",
                         "sandbox_profile", "requires_approval"})

# The workers a server can run. A tool in the policy with no entry here would be
# advertised and then fail per request, so it is refused at start instead.
BUILTIN_WORKERS: Mapping[str, type[Worker]] = {
    DeterministicSummarizer.tool: DeterministicSummarizer,
}


def _fail(message: str) -> None:
    raise ContractError(message)


@dataclass(frozen=True)
class ServiceConfig:
    """Everything `wiring.build` and the listener need, already checked."""

    listen_host: str
    listen_port: int
    root_secret: bytes
    anchor_state: str
    policy: Policy
    principals: Mapping[str, str]
    workers: tuple[Worker, ...]

    def __repr__(self) -> str:
        # The dataclass default would print the root secret.
        return (f"ServiceConfig(listen_host={self.listen_host!r}, "
                f"listen_port={self.listen_port!r}, anchor_state={self.anchor_state!r}, "
                f"policy={self.policy.version!r}, principals={len(self.principals)})")


def _table(value: Any, name: str, keys: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{name} must be a table")
    unknown = set(value) - keys
    if unknown:
        _fail(f"{name} has unknown keys: {', '.join(sorted(unknown))}")
    missing = keys - set(value)
    if missing:
        _fail(f"{name} is missing keys: {', '.join(sorted(missing))}")
    return value


def _loopback_host(value: Any) -> str:
    if type(value) is not str or not value:
        _fail("service.listen_host must be a non-empty string")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ContractError("service.listen_host must be a literal IP address") from exc
    if not address.is_loopback:
        _fail("service.listen_host must be a loopback address; "
              "put a TLS reverse proxy in front instead")
    return value


def _port(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        _fail("service.listen_port must be an integer between 1 and 65535")
    return value


def _absolute(value: Any, name: str) -> str:
    if type(value) is not str or not value:
        _fail(f"{name} must be a non-empty string")
    if not os.path.isabs(value):
        _fail(f"{name} must be an absolute path")
    return value


def read_root_secret(path: str) -> bytes:
    """The root secret from a private regular file, byte for byte."""
    try:
        # O_NONBLOCK so that a FIFO at this path is refused below instead of
        # blocking the start until something writes to it.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                             | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise ContractError("root secret file cannot be opened without following links") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            _fail("root secret file must be a regular file")
        if info.st_uid != os.geteuid():
            _fail("root secret file must be owned by the service user")
        if info.st_mode & 0o077:
            _fail("root secret file must not be readable or writable by group or others")
        # One byte past the limit is enough to know it was exceeded.
        secret = os.read(descriptor, _MAX_SECRET_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(secret) < _MIN_SECRET_BYTES:
        _fail(f"root secret must be at least {_MIN_SECRET_BYTES} bytes")
    if len(secret) > _MAX_SECRET_BYTES:
        _fail(f"root secret file must be at most {_MAX_SECRET_BYTES} bytes")
    if secret == DEMO_ROOT_SECRET:
        _fail("the published demo root secret must not be used by a server")
    return secret


def _anchor_state(value: Any) -> str:
    path = _absolute(value, "service.anchor_state")
    if not os.path.isdir(os.path.dirname(path)):
        _fail("service.anchor_state must be in an existing directory")
    return path


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(type(item) is str for item in value):
        _fail(f"{name} must be a list of strings")
    return tuple(value)


def _policy(value: Any) -> Policy:
    table = _table(value, "policy", _POLICY_KEYS)
    raw_grants = table["grants"]
    # Emptiness is Policy's to refuse; this only keeps iteration well-defined.
    if not isinstance(raw_grants, list):
        _fail("policy.grants must be an array of tables")
    grants = []
    for raw in raw_grants:
        grant = _table(raw, "policy.grants entry", _GRANT_KEYS)
        grants.append(Grant(
            subject=grant["subject"], user_id=grant["user_id"],
            worker_agent_id=grant["worker_agent_id"], tier=grant["tier"],
            tools=_strings(grant["tools"], "policy.grants.tools"),
            sandbox_profile=grant["sandbox_profile"],
            requires_approval=grant["requires_approval"],
        ))
    return Policy(
        version=table["version"], orchestrator_id=table["orchestrator_id"],
        handoff_ttl_seconds=table["handoff_ttl_seconds"],
        allowed_tools=_strings(table["allowed_tools"], "policy.allowed_tools"),
        allowed_sandbox_profiles=_strings(table["allowed_sandbox_profiles"],
                                          "policy.allowed_sandbox_profiles"),
        grants=tuple(grants),
    )


def _principals(value: Any, policy: Policy) -> Mapping[str, str]:
    if not isinstance(value, dict) or not value:
        _fail("principals must be a non-empty table of key digest to subject")
    granted = {grant.subject for grant in policy.grants}
    for digest, subject in value.items():
        # A TOML array here would be unhashable in the membership test below.
        if type(subject) is not str:
            _fail("principals values must be subjects as strings")
        if subject not in granted:
            # The key is not echoed: a plaintext API key pasted here by mistake
            # would otherwise end up in the service log.
            _fail(f"principal for subject {subject!r} has no grant in the policy")
    # Digest format is checked where it is used, by PrincipalRegistry.
    return dict(value)


def _workers(policy: Policy) -> tuple[Worker, ...]:
    missing = [tool for tool in policy.allowed_tools if tool not in BUILTIN_WORKERS]
    if missing:
        _fail(f"no built-in worker for tools: {', '.join(missing)}")
    return tuple(BUILTIN_WORKERS[tool]() for tool in policy.allowed_tools)


def parse_config(data: Mapping[str, Any]) -> ServiceConfig:
    """Check a parsed configuration and read the secret it names."""
    top = _table(data, "configuration", _TOP_KEYS)
    service = _table(top["service"], "service", _SERVICE_KEYS)
    policy = _policy(top["policy"])
    return ServiceConfig(
        listen_host=_loopback_host(service["listen_host"]),
        listen_port=_port(service["listen_port"]),
        root_secret=read_root_secret(
            _absolute(service["root_secret_file"], "service.root_secret_file")),
        anchor_state=_anchor_state(service["anchor_state"]),
        policy=policy,
        principals=_principals(top["principals"], policy),
        workers=_workers(policy),
    )


def load_config(path: str) -> ServiceConfig:
    """Read and check one TOML configuration file."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_MAX_CONFIG_BYTES + 1)
    except OSError as exc:
        raise ContractError("configuration file cannot be read") from exc
    if len(raw) > _MAX_CONFIG_BYTES:
        _fail(f"configuration file must be at most {_MAX_CONFIG_BYTES} bytes")
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ContractError("configuration file is not valid UTF-8 TOML") from exc
    return parse_config(data)
