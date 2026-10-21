"""Password boundary for invited Portal accounts; grants no Core authority."""

from __future__ import annotations

import re

from argon2 import PasswordHasher, exceptions
from argon2.low_level import Type

from .contracts import ContractError

_MAX_PASSWORD_BYTES = 1024
_HASH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4,
                       hash_len=32, salt_len=16, type=Type.ID)
_PHC = re.compile(r"\$argon2id\$v=19\$m=65536,t=3,p=4\$"
                  r"[A-Za-z0-9+/]{22}\$[A-Za-z0-9+/]{43}\Z")


def _password_bytes(password: str) -> bytes:
    if type(password) is not str:
        raise ContractError("invalid credentials")
    try:
        encoded = password.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractError("invalid credentials") from exc
    if not 0 < len(encoded) <= _MAX_PASSWORD_BYTES:
        raise ContractError("invalid credentials")
    return encoded


def hash_password(password: str) -> str:
    """Hash exact UTF-8 password bytes with a fresh Argon2id salt."""
    encoded = _password_bytes(password)
    try:
        stored_hash = _HASH.hash(encoded)
    except (exceptions.HashingError, exceptions.UnsupportedParametersError,
            MemoryError) as exc:
        raise ContractError("password hashing unavailable") from exc
    if type(stored_hash) is not str or _PHC.fullmatch(stored_hash) is None:
        raise ContractError("password hashing unavailable")
    return stored_hash


def verify_password(stored_hash: str, password: str) -> None:
    """Accept only this Portal's Argon2id profile and an exact password match."""
    encoded = _password_bytes(password)
    if type(stored_hash) is not str or _PHC.fullmatch(stored_hash) is None:
        raise ContractError("invalid credentials")
    try:
        valid = _HASH.verify(stored_hash, encoded)
    except (exceptions.VerificationError, exceptions.InvalidHashError,
            exceptions.UnsupportedParametersError, MemoryError) as exc:
        raise ContractError("invalid credentials") from exc
    if valid is not True:
        raise ContractError("invalid credentials")
