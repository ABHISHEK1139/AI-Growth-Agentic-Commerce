"""Argon2id password hashing, and the properties the login path depends on."""

from __future__ import annotations

import pytest

from packages.security import passwords


def test_hash_and_verify_round_trip() -> None:
    stored = passwords.hash_password("correct-horse-battery-staple")

    assert stored.startswith("$argon2id$")
    assert passwords.verify_password("correct-horse-battery-staple", stored)


def test_hash_never_contains_the_plaintext() -> None:
    secret = "correct-horse-battery-staple"

    assert secret not in passwords.hash_password(secret)


def test_same_password_hashes_differently_each_time() -> None:
    """Distinct salts, so identical passwords are not visible as identical rows.

    Without this, an operator comparing two hashes learns whether two accounts
    share a password, and a table of digests becomes a dictionary of passwords.
    """
    first = passwords.hash_password("correct-horse-battery-staple")
    second = passwords.hash_password("correct-horse-battery-staple")

    assert first != second
    # Both still verify, so the difference is the salt and nothing else.
    assert passwords.verify_password("correct-horse-battery-staple", first)
    assert passwords.verify_password("correct-horse-battery-staple", second)


def test_wrong_password_is_rejected() -> None:
    stored = passwords.hash_password("correct-horse-battery-staple")

    assert not passwords.verify_password("correct-horse-battery-stapl", stored)


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not-a-hash",
        "$argon2id$v=19$malformed",
        "$2b$12$an-argon2i-hash-for-the-wrong-algorithm",
    ],
)
def test_malformed_stored_hash_is_false_not_an_exception(stored: str) -> None:
    """The caller is answering "does this match", so a bad row is a no-match.

    Raising here would turn one corrupted `password_hash` column into a 500 on
    every login attempt, which is a self-inflicted denial of service against the
    one account that most needs to be able to sign in to fix it.
    """
    assert not passwords.verify_password("correct-horse-battery-staple", stored)


@pytest.mark.parametrize("presented", ["", "x" * 2000])
def test_absurd_presented_password_is_false(presented: str) -> None:
    stored = passwords.hash_password("correct-horse-battery-staple")

    assert not passwords.verify_password(presented, stored)


def test_hash_password_refuses_a_short_password() -> None:
    with pytest.raises(ValueError, match="at least"):
        passwords.hash_password("short")


def test_hash_password_refuses_an_enormous_password() -> None:
    """A megabyte password costs 20 MiB and a full pass to reject.

    Hashing it anyway would let one request allocate as much memory as it likes.
    """
    with pytest.raises(ValueError, match="at most"):
        passwords.hash_password("x" * (passwords.MAX_PASSWORD_LENGTH + 1))


def test_password_problem_reports_why_and_is_none_when_acceptable() -> None:
    assert passwords.password_problem("short") is not None
    assert passwords.password_problem("correct-horse-battery-staple") is None


def test_needs_rehash_is_false_for_a_fresh_hash() -> None:
    """Otherwise every login would rewrite the row for no reason."""

    assert not passwords.needs_rehash(passwords.hash_password("correct-horse-battery-staple"))


def test_needs_rehash_is_false_for_an_unparseable_hash() -> None:
    assert not passwords.needs_rehash("not-a-hash")


def test_dummy_verify_costs_the_same_as_a_real_verification() -> None:
    """The anti-enumeration property, asserted rather than asserted-in-a-comment.

    "No such user" must not be faster than "wrong password", or the login
    endpoint measures out which addresses have accounts.
    """
    import time

    stored = passwords.hash_password("correct-horse-battery-staple")

    # Warm both paths first; the first call pays one-time library setup.
    passwords.verify_password("x", stored)
    passwords.dummy_verify()

    samples_real = []
    samples_dummy = []
    for _ in range(3):
        start = time.perf_counter()
        passwords.verify_password("wrong-password-value", stored)
        samples_real.append(time.perf_counter() - start)

        start = time.perf_counter()
        passwords.dummy_verify()
        samples_dummy.append(time.perf_counter() - start)

    real = min(samples_real)
    dummy = min(samples_dummy)
    # A generous 3x band: enough to catch "returns immediately" and not so tight
    # that a loaded CI box fails it.
    assert dummy > real / 3, f"dummy_verify {dummy:.4f}s vs real {real:.4f}s"
