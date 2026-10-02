"""Console operator accounts: verification, lockout, and the roles they grant.

These run against a real SQLite database rather than a mock. The properties under
test are the ones a mock cannot check: that a failed login actually persists a
counter, that a lockout actually refuses, and that a disabled account is
distinguishable only in timing and not in outcome.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from packages.security.principals import Role
from services.connectors.models import OperatorAccount
from services.connectors.operator_auth import (
    MAX_FAILED_LOGINS,
    LoginError,
    authenticate,
    create_operator,
    find_by_email,
    is_locked,
    set_password,
)

PASSWORD = "correct-horse-battery-staple"
OTHER_PASSWORD = "a-different-good-password"


@pytest.fixture
def session():
    """A session with the merchant and operator tables, and nothing else.

    The merchant row is required because ``operator_account`` carries a foreign
    key to it, and SQLite enforces that even with foreign keys nominally off
    depending on how the connection was made -- so it is inserted rather than
    assumed.
    """
    from sqlalchemy import create_engine

    import services.connectors.models  # noqa: F401 - register the tables
    import tests.sqlite_types  # noqa: F401 - registers the JSONB/ARRAY compilers
    from packages.db.base import Base
    from services.catalog.models import Merchant

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Merchant(merchant_id="merchant_demo", name="Demo", status="active"))
    db.flush()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _account(session, **overrides):
    kwargs = {
        "merchant_id": "merchant_demo",
        "email": "admin@merchant.local",
        "display_name": "Admin",
        "password": PASSWORD,
        "role": Role.MERCHANT_ADMIN,
    }
    kwargs.update(overrides)
    return create_operator(session, **kwargs)


# --- Verification ---------------------------------------------------------


def test_correct_password_authenticates(session) -> None:
    _account(session)

    operator = authenticate(session, email="admin@merchant.local", password=PASSWORD)

    assert operator.role is Role.MERCHANT_ADMIN
    assert operator.merchant_id == "merchant_demo"
    assert operator.email == "admin@merchant.local"


def test_role_comes_from_the_stored_row_not_the_request(session) -> None:
    """The vulnerability this module exists to close.

    The endpoint it replaced took ``role`` in the request body and issued a
    session for it, so any anonymous caller could ask for ``platform_admin`` and
    receive it. Nothing in the login path reads a role from the caller now.
    """
    _account(session, role=Role.MERCHANT_OPERATOR)

    operator = authenticate(session, email="admin@merchant.local", password=PASSWORD)

    assert operator.role is Role.MERCHANT_OPERATOR


def test_wrong_password_is_refused(session) -> None:
    _account(session)

    with pytest.raises(LoginError):
        authenticate(session, email="admin@merchant.local", password="not-the-password")


def test_unknown_account_is_refused(session) -> None:
    with pytest.raises(LoginError):
        authenticate(session, email="nobody@merchant.local", password=PASSWORD)


def test_unknown_account_and_wrong_password_are_indistinguishable(session) -> None:
    """The anti-enumeration property, asserted on the raised error.

    Both must raise the same exception with the same message. A distinct message
    for "no such email" is a way to enumerate who has access to a merchant.
    """
    _account(session)

    errors: list[str] = []
    for email, password in [
        ("nobody@merchant.local", PASSWORD),
        ("admin@merchant.local", "not-the-password"),
    ]:
        with pytest.raises(LoginError) as excinfo:
            authenticate(session, email=email, password=password)
        errors.append(str(excinfo.value))

    assert errors[0] == errors[1]


def test_disabled_account_cannot_sign_in(session) -> None:
    account = _account(session)
    account.status = "disabled"
    session.flush()

    with pytest.raises(LoginError):
        authenticate(session, email="admin@merchant.local", password=PASSWORD)


def test_unrecognised_stored_role_refuses_rather_than_downgrading(session) -> None:
    """A role string this build does not parse must not fall back to a lower role.

    Silently downgrading would make an operator's access depend on data this code
    does not control, and silently upgrading would be worse.
    """
    account = _account(session)
    account.role = "superuser"
    session.flush()

    with pytest.raises(LoginError):
        authenticate(session, email="admin@merchant.local", password=PASSWORD)


def test_email_is_matched_case_insensitively(session) -> None:
    _account(session)

    operator = authenticate(session, email="Admin@Merchant.LOCAL", password=PASSWORD)

    assert operator.email == "admin@merchant.local"


# --- Lockout --------------------------------------------------------------


def test_repeated_failure_locks_the_account(session) -> None:
    account = _account(session)
    now = datetime.now(UTC)

    for _ in range(MAX_FAILED_LOGINS):
        with pytest.raises(LoginError):
            authenticate(session, email="admin@merchant.local", password="wrong", now=now)

    session.refresh(account)
    assert is_locked(account, now) is True
    with pytest.raises(LoginError):
        authenticate(session, email="admin@merchant.local", password=PASSWORD, now=now)


def test_lockout_expires(session) -> None:
    """A lockout with no expiry locks an operator out until someone runs a script."""
    _account(session)
    now = datetime.now(UTC)
    for _ in range(MAX_FAILED_LOGINS):
        with pytest.raises(LoginError):
            authenticate(session, email="admin@merchant.local", password="wrong", now=now)

    later = now + timedelta(minutes=20)
    operator = authenticate(session, email="admin@merchant.local", password=PASSWORD, now=later)
    assert operator.email == "admin@merchant.local"


def test_a_successful_login_clears_the_failure_counter(session) -> None:
    account = _account(session)
    now = datetime.now(UTC)
    with pytest.raises(LoginError):
        authenticate(session, email="admin@merchant.local", password="wrong", now=now)

    authenticate(session, email="admin@merchant.local", password=PASSWORD, now=now)
    session.refresh(account)

    assert account.failed_login_count == 0
    assert account.locked_until is None


def test_failure_counter_decays_after_the_memory_window(session) -> None:
    """Five typos spread over five months must not lock anyone out.

    Without decay the counter only ever resets on a success, so an occasional
    mistyped password accumulates silently until the next one locks the account.
    """
    account = _account(session)
    start = datetime.now(UTC)
    with pytest.raises(LoginError):
        authenticate(session, email="admin@merchant.local", password="wrong", now=start)

    # Two days later -- outside the 24-hour memory window -- three more failures
    # should not be the fourth and fifth of a five-attempt run.
    later = start + timedelta(days=2)
    for _ in range(MAX_FAILED_LOGINS - 2):
        with pytest.raises(LoginError):
            authenticate(session, email="admin@merchant.local", password="wrong", now=later)

    session.refresh(account)
    assert account.locked_until is None or account.locked_until > later + timedelta(hours=1)


# --- Creation and password changes ----------------------------------------


def test_password_is_never_stored_in_plaintext(session) -> None:
    account = _account(session)

    assert PASSWORD not in account.password_hash
    assert account.password_hash.startswith("$argon2id$")


def test_creation_refuses_a_weak_password(session) -> None:
    with pytest.raises(ValueError, match="at least"):
        _account(session, password="short")


def test_creation_refuses_a_duplicate_email(session) -> None:
    _account(session)

    with pytest.raises(ValueError, match="already exists"):
        _account(session, email="Admin@Merchant.Local")


def test_set_password_replaces_the_hash_and_clears_the_flag(session) -> None:
    account = _account(session, must_change_password=True)

    set_password(session, account, OTHER_PASSWORD)

    assert PASSWORD not in account.password_hash
    assert account.must_change_password is False
    assert authenticate(session, email="admin@merchant.local", password=OTHER_PASSWORD)


def test_set_password_clears_a_lockout(session) -> None:
    """A reset that left the counter high would re-lock on the next typo.

    The person who just proved they own the account would be unable to sign in
    with the password they were just given.
    """
    account = _account(session)
    now = datetime.now(UTC)
    for _ in range(MAX_FAILED_LOGINS):
        with pytest.raises(LoginError):
            authenticate(session, email="admin@merchant.local", password="wrong", now=now)
    session.refresh(account)
    assert is_locked(account, now)

    set_password(session, account, OTHER_PASSWORD)

    authenticate(session, email="admin@merchant.local", password=OTHER_PASSWORD, now=now)


def test_set_password_refuses_a_weak_replacement(session) -> None:
    account = _account(session)

    with pytest.raises(ValueError, match="at least"):
        set_password(session, account, "short")


def test_find_by_email_normalizes(session) -> None:
    _account(session)

    found = find_by_email(session, "  ADMIN@merchant.local  ")

    assert found is not None
    assert found.email == "admin@merchant.local"


def test_find_by_email_returns_none_for_a_blank_query(session) -> None:
    assert find_by_email(session, "   ") is None


def test_is_locked_tolerates_a_naive_timestamp(session) -> None:
    """SQLite hands back a naive datetime even for a timezone-aware column.

    Comparing naive to aware raises TypeError, which would turn "check the
    lockout" into a 500 on the local development datastore.
    """
    account = OperatorAccount(
        operator_id="opr_x",
        merchant_id="merchant_demo",
        email="a@b.co",
        display_name="A",
        password_hash="x",
        role=Role.MERCHANT_ADMIN.value,
        status="active",
        must_change_password=False,
        failed_login_count=0,
        last_failed_login_at=None,
        locked_until=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15),
        last_login_at=None,
    )

    assert is_locked(account, datetime.now(UTC)) is True


def test_successful_login_records_the_timestamp(session) -> None:
    _account(session)
    now = datetime.now(UTC)

    authenticate(session, email="admin@merchant.local", password=PASSWORD, now=now)

    account = session.execute(
        select(OperatorAccount).where(OperatorAccount.email == "admin@merchant.local")
    ).scalar_one()
    assert account.last_login_at is not None
