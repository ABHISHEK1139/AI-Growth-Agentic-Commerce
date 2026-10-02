"""Password hashing and verification for human console logins.

This module exists because the project previously had no human authentication at
all. ``POST /api/v1/auth/login`` accepted a role in the request body and issued a
session for it, so any anonymous caller could become ``PLATFORM_ADMIN``. The
argument for a plain SHA-256 digest written in :mod:`packages.security.apikeys`
does not transfer: an API key is 32 bytes of :mod:`secrets` output with no
dictionary to attack, while a console password is human-chosen and therefore
exactly the case a memory-hard KDF exists for.

Why Argon2id rather than bcrypt or PBKDF2:

* **Memory-hard.** A GPU or ASIC cracking rig gains nothing from parallelism,
  because each guess costs 64 MiB of RAM rather than a few thousand ALU cycles.
  bcrypt is deliberately *not* memory-hard and PBKDF2's iteration count buys
  little against dedicated hardware.
* **id is the hybrid.** Argon2i resists side-channel and timing attacks,
  Argon2d resists GPU cracking, and Argon2id is both: data-independent
  addressing for the first half of each pass, data-dependent for the second.

The parameters below are the OWASP Password Storage Cheat Sheet minimum for
Argon2id (m=19456 KiB, t=2, p=1) rounded to the library's KiB/mib distinction.
They are the floor, not a ceiling: :func:`needs_rehash` exists so a deployment
can raise them later and re-derive hashes transparently on the next login.

The encoded form is PHC (``$argon2id$v=19$m=...,t=...,p=...$salt$hash``) and
carries its own parameters, so raising the defaults above does not invalidate
hashes already in the database.
"""

from __future__ import annotations

import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

__all__ = [
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "hash_password",
    "needs_rehash",
    "normalize_password",
    "password_problem",
    "verify_password",
]

#: 20 MiB. The library's ``memory_cost`` is in KiB; OWASP's 19 MiB is the
#: floor and 20 gives a round number above it.
_MEMORY_COST_KIB = 20 * 1024

#: Two passes. Doubling to 3 costs ~20ms per login and is the first knob to turn
#: if the login endpoint ever shows up in a latency budget.
_TIME_COST = 2

#: One lane. More lanes only help on machines with idle cores to spend, and this
#: service is IO-bound on the request that follows the hash.
_PARALLELISM = 1

#: 16 bytes of salt. The encoded PHC string carries it, so the salt is not
#: secret and does not need to be long enough to brute force against.
_SALT_BYTES = 16

#: Argon2 hashes at most 2**32 bytes, but a multi-megabyte "password" is a
#: denial-of-service vector: it costs 64 MiB and a full pass to reject.
MAX_PASSWORD_LENGTH = 1024

#: Not a security control, only a floor that rejects the empty string and the
#: one-character submissions that make brute force look cheap in a log.
MIN_PASSWORD_LENGTH = 12

_HASHER = PasswordHasher(
    time_cost=_TIME_COST,
    memory_cost=_MEMORY_COST_KIB,
    parallelism=_PARALLELISM,
    hash_len=32,
    salt_len=_SALT_BYTES,
)


def normalize_password(password: str) -> str:
    """The bytes to hash.

    Passwords are used verbatim. Normalising case, stripping whitespace, or
    applying Unicode NFKC before hashing would mean a password the user cannot
    reproduce by typing it still works -- which is a support burden, and hides
    the fact that the credential they believe they chose is not the one stored.
    """
    if not isinstance(password, str):
        raise ValueError("password must be a string")
    return password


def password_problem(password: str) -> str | None:
    """A human-readable reason this password is unacceptable, or ``None``.

    Kept separate from :func:`hash_password` so a registration or reset form can
    show the reason before doing 64 MiB of work, and so the policy lives in one
    place rather than in each caller.
    """
    if not isinstance(password, str):
        return "password must be text"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"password must be at most {MAX_PASSWORD_LENGTH} characters"
    return None


def hash_password(password: str) -> str:
    """Hash ``password`` with a fresh random salt.

    Raises :class:`ValueError` for a password the policy rejects, rather than
    hashing it and failing later: an unacceptable password is a caller bug, and
    a stored hash of a one-character password is worse than no account at all.
    """
    problem = password_problem(password)
    if problem is not None:
        raise ValueError(problem)
    return _HASHER.hash(normalize_password(password))


def verify_password(presented: str, stored_hash: str) -> bool:
    """Whether ``presented`` produced ``stored_hash``.

    Returns ``False`` rather than raising for every failure, because the caller
    is answering "does this credential match" and a malformed or truncated
    stored hash simply does not match anything. A ``False`` here must not be
    distinguishable to the caller from a wrong password.
    """
    if not isinstance(presented, str) or not isinstance(stored_hash, str):
        return False
    if not stored_hash or not presented:
        return False
    if len(presented) > MAX_PASSWORD_LENGTH:
        return False
    try:
        _HASHER.verify(stored_hash, presented)
    except (VerifyMismatchError, VerificationError, InvalidHashError, HashingError):
        return False
    return True


def needs_rehash(stored_hash: str) -> bool:
    """Whether ``stored_hash`` was made with weaker parameters than the current ones.

    A deployment that raises ``_MEMORY_COST_KIB`` should call this after a
    successful :func:`verify_password` and re-hash, which upgrades the stored
    value the next time that user logs in without a password reset.
    """
    if not isinstance(stored_hash, str) or not stored_hash:
        return False
    try:
        return _HASHER.check_needs_rehash(stored_hash)
    except (InvalidHashError, HashingError):
        # An unparseable hash is not something to re-hash on the way in; it is a
        # corrupted row, and the caller should fail the login and alert.
        return False


def dummy_verify() -> None:
    """Spend the same CPU as a real verification, then discard the result.

    Called when a login names an account that does not exist. Without it, "no
    such user" returns in microseconds and "wrong password" takes ~50ms, which
    turns the login endpoint into a free account-enumeration oracle: an attacker
    measures the response time and learns which usernames are real. The dummy
    hash is computed once at import so no attacker can pre-warm it away.
    """
    import contextlib

    with contextlib.suppress(
        VerifyMismatchError, VerificationError, InvalidHashError, HashingError
    ):
        _HASHER.verify(_DUMMY_HASH, "not-a-real-password")


#: A real Argon2id hash of a random secret nobody knows, generated at import.
#: Only its cost matters -- it must be indistinguishable from a stored hash to a
#: timing measurement, and verifying against it is what makes a missing account
#: cost the same as a wrong password.
_DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(32))


def constant_time_equals(a: str, b: str) -> bool:
    """Length-independent string equality.

    Only used for comparing values that are not secrets and are not already
    constant-time (an audit ``input_hash`` echoed back in a response, say). Kept
    here so no caller reaches for ``==`` on a digest.
    """
    return hmac.compare_digest(a, b)
