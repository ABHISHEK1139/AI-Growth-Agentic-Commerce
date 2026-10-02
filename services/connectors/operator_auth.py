"""Operator account lookup, credential verification, and lockout policy.

Lives in ``services`` rather than ``apps/api/routers/auth.py`` because the login
path has rules that must hold regardless of which delivery layer calls it, and
because they are testable without a request. Three of them were absent before
this module and their absence is the whole reason the project had no login:

1. **A password is verified, never trusted.** The previous endpoint read ``role``
   from the request body and minted a session for it, so any anonymous caller
   could become ``PLATFORM_ADMIN``. The role now comes from the stored row.
2. **A missing account and a wrong password are indistinguishable.** Both return
   ``None`` from :func:`authenticate`, and both spend the same Argon2 time via
   :func:`packages.security.passwords.dummy_verify`, so the endpoint cannot be
   used to enumerate which emails have accounts.
3. **Repeated failure locks the account.** Without a lockout, the Argon2 cost
   that makes offline cracking expensive does nothing about online guessing, and
   ``MERCHANT_ADMIN`` is one password away.

The lockout counters live on the row rather than in Redis on purpose: an account
lockout that resets when Redis restarts is not a lockout, and the same argument
applies to a process restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.observability.context import new_id
from packages.security import passwords
from packages.security.principals import Role
from services.connectors.models import OperatorAccount

#: Consecutive failures before the account locks. Five is chosen so a mistyped
#: password twice is survivable but a scripted attempt is not: at this threshold
#: an attacker gets ~5 guesses per lockout period rather than one per request.
MAX_FAILED_LOGINS = 5

#: How long a lockout lasts. Long enough to make online guessing
#: 5 guesses per 15 minutes, short enough that a real operator locked out by a
#: typo can self-serve rather than paging someone with database access.
LOCKOUT_DURATION = timedelta(minutes=15)

#: A failure older than this no longer counts, so an operator who mistypes a
#: password today and forgets it tomorrow is not one typo from a lockout.
FAILURE_MEMORY = timedelta(hours=24)


class LoginError(Exception):
    """A login that did not succeed.

    Carries no distinction between "no such account" and "wrong password": the
    caller renders one message, and a distinguishable error is an account
    enumeration oracle.
    """


@dataclass(frozen=True, slots=True)
class AuthenticatedOperator:
    """The result of a successful credential check."""

    operator_id: str
    email: str
    display_name: str
    role: Role
    merchant_id: str
    must_change_password: bool
    #: True when the stored hash used weaker parameters than the current policy.
    #: The caller re-hashes after a successful login so raising the cost later
    #: upgrades accounts on their next sign-in rather than requiring a reset.
    needs_rehash: bool


def _coerce_role(value: str) -> Role | None:
    """The stored role, or ``None`` if it is not one this build recognises.

    ``None`` rather than a fallback: a role string that does not parse must
    refuse the login, not silently downgrade the account to the lowest role or
    crash the endpoint. Both make an operator's access depend on data this code
    does not control.
    """
    try:
        return Role(value)
    except ValueError:
        return None


def find_by_email(session: Session, email: str) -> OperatorAccount | None:
    """The account for a normalised email address, or ``None``."""
    if not isinstance(email, str) or not email.strip():
        return None
    return session.execute(
        select(OperatorAccount).where(OperatorAccount.email == normalize_email(email))
    ).scalar_one_or_none()


def normalize_email(email: str) -> str:
    """The stored form of an address.

    Lowercased and stripped so ``Admin@Example.com`` and ``admin@example.com``
    are one account rather than two, which is what an operator expects and what
    stops a lockout from being side-stepped by re-casing the address.
    """
    return email.strip().lower()


def is_locked(account: OperatorAccount, now: datetime | None = None) -> bool:
    """Whether this account is inside its lockout window."""
    if account.locked_until is None:
        return False
    current = now or datetime.now(UTC)
    locked_until = account.locked_until
    # SQLite round-trips a naive datetime even for a timezone-aware column, so
    # compare in UTC after normalising. Comparing naive to aware raises, which
    # would turn "check the lockout" into a 500 on the local dev datastore.
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=UTC)
    return current < locked_until


def authenticate(
    session: Session,
    *,
    email: str,
    password: str,
    now: datetime | None = None,
) -> AuthenticatedOperator:
    """Verify a credential pair and return the account it belongs to.

    Raises :class:`LoginError` for every failure mode. A caller cannot
    distinguish "no such email" from "wrong password" from "account disabled",
    and cannot distinguish a lockout from a bad password, because the response
    to all of them is the same 401 with the same body.
    """
    current = now or datetime.now(UTC)
    account = find_by_email(session, email)

    if account is None:
        # Spend the same CPU a real verification would, so response time does not
        # reveal whether the address exists.
        passwords.dummy_verify()
        raise LoginError("invalid credentials")

    if is_locked(account, current):
        # Still verify. A locked account that returns instantly is itself a
        # signal: it confirms the address exists, which is the one thing the
        # constant-time path above exists to hide.
        passwords.dummy_verify()
        raise LoginError("invalid credentials")

    if not passwords.verify_password(password, account.password_hash):
        _record_failure(session, account, current)
        raise LoginError("invalid credentials")

    if account.status != "active":
        # A correct password on a disabled account. Verified first so a disabled
        # account is not distinguishable by timing from a wrong password.
        raise LoginError("invalid credentials")

    role = _coerce_role(account.role)
    if role is None:
        raise LoginError("account role is not recognised by this build")

    account.failed_login_count = 0
    account.locked_until = None
    account.last_failed_login_at = None
    account.last_login_at = current

    # Transparent upgrade: the password just proved correct, so re-deriving the
    # hash costs nothing the user notices and permanently raises the cost of
    # attacking this row offline. Checked before the rewrite so it reports
    # whether the *stored* hash was stale, which is what the caller acts on.
    stale = passwords.needs_rehash(account.password_hash)
    if stale:
        account.password_hash = passwords.hash_password(password)

    session.flush()
    return AuthenticatedOperator(
        operator_id=account.operator_id,
        email=account.email,
        display_name=account.display_name,
        role=role,
        merchant_id=account.merchant_id,
        must_change_password=account.must_change_password,
        needs_rehash=stale,
    )


def _record_failure(session: Session, account: OperatorAccount, now: datetime) -> None:
    """Count a failed attempt and lock the account once the threshold is hit.

    The counter decays: a last attempt older than :data:`FAILURE_MEMORY` starts
    the run again. Without that, an operator who mistypes their password once a
    week is one careless Friday away from being locked out of the console by
    five isolated typos spread over a month.
    """
    previous = account.failed_login_count or 0
    last_failure = account.last_failed_login_at
    if last_failure is not None and now - _aware(last_failure) > FAILURE_MEMORY:
        previous = 0

    previous += 1
    account.last_failed_login_at = now
    if previous >= MAX_FAILED_LOGINS:
        account.locked_until = now + LOCKOUT_DURATION
        # Reset rather than keep counting: leaving the counter above the
        # threshold would re-lock the account on the very next failure with no
        # window in between, turning one lockout into a permanent one.
        account.failed_login_count = 0
    else:
        account.failed_login_count = previous
    session.flush()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def create_operator(
    session: Session,
    *,
    merchant_id: str,
    email: str,
    display_name: str,
    password: str,
    role: Role,
    must_change_password: bool = False,
) -> OperatorAccount:
    """Create an operator account.

    Refuses rather than silently repairing a bad password: an account created
    with a one-character password is an account that will be locked out by its
    own policy, and a caller that cannot set a real password has a different
    problem than the one this function can solve.
    """
    problem = passwords.password_problem(password)
    if problem is not None:
        raise ValueError(problem)

    account = OperatorAccount(
        operator_id=new_id("opr"),
        merchant_id=merchant_id,
        email=normalize_email(email),
        display_name=display_name.strip() or email,
        password_hash=passwords.hash_password(password),
        role=role.value,
        status="active",
        must_change_password=must_change_password,
    )
    session.add(account)
    try:
        session.flush()
    except IntegrityError as exc:
        # The unique index on email is the only realistic conflict, and it is
        # the one that must not be swallowed: two accounts for one address means
        # a login that resolves to whichever row the database returned first.
        session.rollback()
        raise ValueError(f"an account already exists for {normalize_email(email)}") from exc
    return account


def set_password(
    session: Session,
    account: OperatorAccount,
    password: str,
    *,
    now: datetime | None = None,
) -> None:
    """Replace an account's password and clear its lockout.

    Used by both the first-login change and an operator-initiated reset. Clearing
    ``failed_login_count`` here is deliberate: a reset that left the counter
    high would mean the next typo locked the account again immediately, and the
    person who just proved they own the mailbox cannot sign in.
    """
    problem = passwords.password_problem(password)
    if problem is not None:
        raise ValueError(problem)
    account.password_hash = passwords.hash_password(password)
    account.must_change_password = False
    account.failed_login_count = 0
    account.locked_until = None
    account.last_failed_login_at = None
    if now is not None:
        account.last_login_at = now
    session.flush()
