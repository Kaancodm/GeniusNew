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

**A principal without a grant or an explicit approver role, or a tool without
a worker.** These would authenticate callers without any permitted route, or
advertise a tool the service cannot run. Roles come only from the optional
server-side approvers table; no role is inferred from a grant.

**No anchor state.** Without it the anchor forgets every committed head when the
service restarts, and a shortened chain verifies again. The demo may do that; a
server may not. A server names either `anchor_state` (the anchor as the service's
child) or `anchor_socket` with `anchor_reply_public_key` (gate C2: the anchor as
its own service, whose replies the service believes only when signed by that key).
Both, or neither, is refused.

Nothing here reads the environment. A secret in an environment variable is
visible in `/proc/<pid>/environ` to the same user and inherited by every child,
including the worker; a file with mode 0600 is neither.
"""

from __future__ import annotations

import ipaddress
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from typing import Any, Mapping

from .approvals import ApproverPolicy
from .contracts import ContractError, Grant, Policy
from .http_entry import HttpLimits
from .workers import DeterministicSummarizer, Worker

# Published in scripts/demo.py. Refused here so it cannot become a real key.
DEMO_ROOT_SECRET = b"demo-root-secret-not-for-real-use!!!"

_MIN_SECRET_BYTES = 32
_MAX_SECRET_BYTES = 4096
_MAX_CONFIG_BYTES = 1024 * 1024

_TOP_KEYS = frozenset({"service", "policy", "principals"})
_SERVICE_KEYS = frozenset({"listen_host", "listen_port", "root_secret_file",
                           "database_dsn_file", "limits"})
# One anchor mode is required, and exactly one (see `_anchor`): a child of the
# service persisted at `anchor_state`, or a separate service at `anchor_socket`.
_ANCHOR_KEYS = frozenset({"anchor_state", "anchor_socket", "anchor_reply_public_key"})
_LIMIT_KEYS = frozenset({"rate_per_minute", "burst", "max_in_flight", "max_connections"})
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
    anchor_state: str | None
    anchor_socket: str | None
    anchor_reply_public_key: bytes | None
    database_dsn: str
    limits: HttpLimits
    policy: Policy
    principals: Mapping[str, str]
    approvers: Mapping[str, str]
    workers: tuple[Worker, ...]

    def __repr__(self) -> str:
        # The dataclass default would print the root secret.
        return (f"ServiceConfig(listen_host={self.listen_host!r}, "
                f"listen_port={self.listen_port!r}, anchor_state={self.anchor_state!r}, "
                f"anchor_socket={self.anchor_socket!r}, "
                f"policy={self.policy.version!r}, principals={len(self.principals)})")


def _table(value: Any, name: str, keys: frozenset[str],
           optional: frozenset[str] = frozenset()) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{name} must be a table")
    unknown = set(value) - keys - optional
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


def _read_private_file(path: str, name: str) -> bytes:
    """Read bounded bytes from an owner-only file without following links."""
    try:
        # O_NONBLOCK so that a FIFO at this path is refused below instead of
        # blocking the start until something writes to it.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                             | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise ContractError(f"{name} file cannot be opened without following links") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            _fail(f"{name} file must be a regular file")
        if info.st_uid != os.geteuid():
            _fail(f"{name} file must be owned by the service user")
        if info.st_mode & 0o077:
            _fail(f"{name} file must not be readable or writable by group or others")
        # One byte past the limit is enough to know it was exceeded.
        secret = os.read(descriptor, _MAX_SECRET_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(secret) > _MAX_SECRET_BYTES:
        _fail(f"{name} file must be at most {_MAX_SECRET_BYTES} bytes")
    return secret


def read_database_dsn(path: str) -> str:
    """Keep database credentials out of TOML, argv and inherited environment."""
    raw = _read_private_file(_absolute(path, "service.database_dsn_file"), "database DSN")
    try:
        dsn = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ContractError("database DSN file must be UTF-8") from None
    if not dsn or "\x00" in dsn:
        _fail("database DSN must be non-empty and contain no NUL")
    return dsn


def read_root_secret(path: str) -> bytes:
    """The root secret from a private regular file, byte for byte."""
    secret = _read_private_file(path, "root secret")
    if len(secret) < _MIN_SECRET_BYTES:
        _fail(f"root secret must be at least {_MIN_SECRET_BYTES} bytes")
    if secret == DEMO_ROOT_SECRET:
        _fail("the published demo root secret must not be used by a server")
    return secret


def _anchor_state(value: Any) -> str:
    path = _absolute(value, "service.anchor_state")
    if not os.path.isdir(os.path.dirname(path)):
        _fail("service.anchor_state must be in an existing directory")
    return path


def _anchor(service: Mapping[str, Any]) -> tuple[str | None, str | None, bytes | None]:
    """Exactly one anchor mode: a persisted child, or a served anchor and its key.

    Both at once would leave it unclear which anchor the audit head is committed
    to, and neither would start a service that forgets its heads on restart.
    """
    served = {"anchor_socket", "anchor_reply_public_key"} & set(service)
    if "anchor_state" in service:
        if served:
            _fail("service.anchor_state and a served anchor are mutually exclusive")
        return _anchor_state(service["anchor_state"]), None, None
    if served != {"anchor_socket", "anchor_reply_public_key"}:
        _fail("service needs anchor_state, or anchor_socket with anchor_reply_public_key")
    socket_path = _absolute(service["anchor_socket"], "service.anchor_socket")
    key = service["anchor_reply_public_key"]
    if type(key) is not str or not re.fullmatch(r"[0-9a-f]{64}", key):
        _fail("service.anchor_reply_public_key must be 64 lowercase hex characters")
    return None, socket_path, bytes.fromhex(key)


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


def _principals(value: Any, policy: Policy,
                approvers: Mapping[str, str]) -> tuple[Mapping[str, str], ApproverPolicy]:
    if not isinstance(value, dict) or not value:
        _fail("principals must be a non-empty table of key digest to subject")
    for subject in value.values():
        # A TOML array here would be unhashable in the role lookup below.
        if type(subject) is not str:
            _fail("principals values must be subjects as strings")
    roles = ApproverPolicy(approvers, policy=policy, principal_subjects=value.values())
    granted = {grant.subject for grant in policy.grants}
    for subject in value.values():
        if subject not in granted and subject not in roles.identities:
            # The key is not echoed: a plaintext API key pasted here by mistake
            # would otherwise end up in the service log.
            _fail(f"principal for subject {subject!r} has no grant in the policy")
    # Digest format is checked where it is used, by PrincipalRegistry.
    return dict(value), roles


def _workers(policy: Policy) -> tuple[Worker, ...]:
    missing = [tool for tool in policy.allowed_tools if tool not in BUILTIN_WORKERS]
    if missing:
        _fail(f"no built-in worker for tools: {', '.join(missing)}")
    return tuple(BUILTIN_WORKERS[tool]() for tool in policy.allowed_tools)


def _limits(value: Any) -> HttpLimits:
    # Required rather than defaulted: the proxy template has to match these
    # numbers, so they are written down where the operator can see both.
    return HttpLimits(**_table(value, "service.limits", _LIMIT_KEYS))


def parse_config(data: Mapping[str, Any]) -> ServiceConfig:
    """Check a parsed configuration and read the secret it names."""
    top = _table(data, "configuration", _TOP_KEYS, frozenset({"approvers"}))
    service = _table(top["service"], "service", _SERVICE_KEYS, _ANCHOR_KEYS)
    policy = _policy(top["policy"])
    principals, approver_policy = _principals(
        top["principals"], policy, top.get("approvers", {}))
    anchor_state, anchor_socket, anchor_reply_public_key = _anchor(service)
    return ServiceConfig(
        listen_host=_loopback_host(service["listen_host"]),
        listen_port=_port(service["listen_port"]),
        root_secret=read_root_secret(
            _absolute(service["root_secret_file"], "service.root_secret_file")),
        anchor_state=anchor_state,
        anchor_socket=anchor_socket,
        anchor_reply_public_key=anchor_reply_public_key,
        database_dsn=read_database_dsn(service["database_dsn_file"]),
        limits=_limits(service["limits"]),
        policy=policy,
        principals=principals,
        approvers=approver_policy.identities,
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
