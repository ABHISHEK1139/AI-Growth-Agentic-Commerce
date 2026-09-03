"""Signed credentials: issue, verify, and refuse (Requirement 24.1, 24.2, 24.7).

The clock is injected everywhere, so "expired" is a deterministic assertion rather
than a sleep, and the suite has no timing flake to inherit.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import pytest

from packages.errors.registry import ErrorCode
from packages.observability.logging import REDACTED, redact
from packages.security.principals import AuthMethod, Principal, Role, Scope, grant_scopes
from packages.security.tokens import (
    CLOCK_SKEW_SECONDS,
    MAX_TOKEN_LENGTH,
    IssuedToken,
    TokenClaimsError,
    TokenExpiredError,
    TokenMalformedError,
    TokenSignatureError,
    decode_signed_token,
    encode_signed_token,
    issue_access_token,
    issue_session_token,
    principal_from_access_token,
    principal_from_session_token,
)

ACCESS_SECRET = "unit-test-access-secret"
SESSION_SECRET = "unit-test-session-secret"
NOW = 1_700_000_000.0
MERCHANT = "merchant_demo"
BUYER = "buyer_ada"


def a_buyer_token(**overrides: Any) -> IssuedToken:
    kwargs: dict[str, Any] = {
        "secret": ACCESS_SECRET,
        "subject": BUYER,
        "role": Role.BUYER,
        "merchant_id": MERCHANT,
        "buyer_id": BUYER,
        "ttl_seconds": 3600,
        "now": NOW,
    }
    kwargs.update(overrides)
    return issue_access_token(**kwargs)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _tamper_payload(token: str, **claim_overrides: Any) -> str:
    """Rewrite claims while keeping the original signature. Forgery, essentially."""
    header, payload, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded.update(claim_overrides)
    return f"{header}.{_b64url(json.dumps(decoded, separators=(',', ':')).encode())}.{signature}"


class TestRoundTrip:
    def test_an_access_token_rebuilds_the_principal_it_was_issued_for(self) -> None:
        issued = issue_access_token(
            secret=ACCESS_SECRET,
            subject=BUYER,
            role=Role.BUYER,
            merchant_id=MERCHANT,
            buyer_id=BUYER,
            scopes={Scope.CATALOG_READ, Scope.CHECKOUT_WRITE},
            ttl_seconds=3600,
            now=NOW,
            client_id="apc_1",
        )

        principal = principal_from_access_token(issued.token, secret=ACCESS_SECRET, now=NOW)

        assert principal.subject == BUYER
        assert principal.role is Role.BUYER
        assert principal.merchant_id == MERCHANT
        assert principal.buyer_id == BUYER
        assert principal.scopes == {Scope.CATALOG_READ, Scope.CHECKOUT_WRITE}
        assert principal.method is AuthMethod.TOKEN
        assert principal.client_id == "apc_1"
        assert principal.expires_at == int(NOW) + 3600

    def test_a_session_carries_every_scope_the_role_allows(self) -> None:
        issued = issue_session_token(
            secret=SESSION_SECRET,
            subject=BUYER,
            role=Role.BUYER,
            merchant_id=MERCHANT,
            buyer_id=BUYER,
            ttl_seconds=86_400,
            now=NOW,
        )

        principal = principal_from_session_token(issued.token, secret=SESSION_SECRET, now=NOW)

        assert principal.method is AuthMethod.SESSION
        assert principal.scopes == grant_scopes(Role.BUYER)

    def test_requested_scopes_are_capped_by_the_role(self) -> None:
        """A merchant operator asking to pay is rejected with ForbiddenError."""
        from packages.errors.exceptions import ForbiddenError

        with pytest.raises(ForbiddenError):
            issue_access_token(
                secret=ACCESS_SECRET,
                subject="user_ops",
                role=Role.MERCHANT_OPERATOR,
                merchant_id=MERCHANT,
                scopes={Scope.CATALOG_READ, Scope.PAYMENT_WRITE},
                ttl_seconds=3600,
                now=NOW,
            )

    def test_the_encoded_form_is_recognised_by_the_log_redactor(self) -> None:
        """A token that leaks into a log line must be masked. The formatter matches
        the `eyJ` prefix, so the encoding has to keep producing it."""
        issued = a_buyer_token()

        assert issued.token.startswith("eyJ")
        masked = redact({"note": f"token {issued.token}"})
        assert issued.token not in masked["note"]
        assert REDACTED in masked["note"]


class TestExpiry:
    def test_a_token_is_valid_up_to_the_last_second_before_expiry(self) -> None:
        issued = a_buyer_token(ttl_seconds=60)

        principal = principal_from_access_token(issued.token, secret=ACCESS_SECRET, now=NOW + 59)

        assert principal.subject == BUYER

    def test_an_expired_token_is_denied(self) -> None:
        """Requirement 24.7. No leeway on `exp`: the boundary itself is expired."""
        issued = a_buyer_token(ttl_seconds=60)

        with pytest.raises(TokenExpiredError) as caught:
            principal_from_access_token(issued.token, secret=ACCESS_SECRET, now=NOW + 60)

        assert caught.value.code is ErrorCode.UNAUTHENTICATED
        assert caught.value.http_status == 401
        assert caught.value.details["reason"] == "expired"

    def test_a_long_expired_token_is_denied(self) -> None:
        issued = a_buyer_token(ttl_seconds=3600)

        with pytest.raises(TokenExpiredError):
            principal_from_access_token(issued.token, secret=ACCESS_SECRET, now=NOW + 86_400)

    def test_extending_the_expiry_by_hand_does_not_work(self) -> None:
        """The obvious attack on an expired token: move `exp`. The signature covers
        it, so this must fail as a signature error, not as an expiry error."""
        issued = a_buyer_token(ttl_seconds=60)
        forged = _tamper_payload(issued.token, exp=int(NOW) + 999_999)

        with pytest.raises(TokenSignatureError):
            principal_from_access_token(forged, secret=ACCESS_SECRET, now=NOW + 600)

    def test_a_token_stamped_in_the_future_is_refused_beyond_the_skew(self) -> None:
        issued = a_buyer_token(now=NOW + CLOCK_SKEW_SECONDS + 10)

        with pytest.raises(TokenClaimsError):
            principal_from_access_token(issued.token, secret=ACCESS_SECRET, now=NOW)

    def test_a_small_clock_drift_is_tolerated(self) -> None:
        """A staging box with a drifting clock must not lock every agent out."""
        issued = a_buyer_token(now=NOW + 5)

        assert principal_from_access_token(issued.token, secret=ACCESS_SECRET, now=NOW)

    def test_expires_in_counts_down_from_the_wall_clock(self) -> None:
        issued = issue_access_token(
            secret=ACCESS_SECRET,
            subject=BUYER,
            role=Role.BUYER,
            merchant_id=MERCHANT,
            buyer_id=BUYER,
            ttl_seconds=120,
        )

        assert 110 <= issued.expires_in <= 120
        assert issued.expires_at == pytest.approx(int(time.time()) + 120, abs=2)


class TestForgeryAndTampering:
    def test_a_tampered_claim_is_denied(self) -> None:
        """The privilege-escalation attempt: same signature, wider role."""
        issued = a_buyer_token()
        forged = _tamper_payload(issued.token, role=Role.PLATFORM_ADMIN.value)

        with pytest.raises(TokenSignatureError):
            principal_from_access_token(forged, secret=ACCESS_SECRET, now=NOW)

    def test_a_tenant_swap_is_denied(self) -> None:
        issued = a_buyer_token()
        forged = _tamper_payload(issued.token, tenant="merchant_rival")

        with pytest.raises(TokenSignatureError):
            principal_from_access_token(forged, secret=ACCESS_SECRET, now=NOW)

    def test_a_token_signed_with_another_secret_is_denied(self) -> None:
        issued = a_buyer_token(secret="some-other-secret")

        with pytest.raises(TokenSignatureError):
            principal_from_access_token(issued.token, secret=ACCESS_SECRET, now=NOW)

    def test_a_session_value_is_not_a_bearer_token(self) -> None:
        """Same codec, different secret and different `typ`. Either check alone
        would stop this; both are asserted because the two surfaces must not be
        interchangeable even if one secret is reused by mistake."""
        session = issue_session_token(
            secret=ACCESS_SECRET,
            subject=BUYER,
            role=Role.BUYER,
            merchant_id=MERCHANT,
            buyer_id=BUYER,
            ttl_seconds=3600,
            now=NOW,
        )

        with pytest.raises(TokenClaimsError):
            principal_from_access_token(session.token, secret=ACCESS_SECRET, now=NOW)

    def test_a_bearer_token_is_not_a_session_value(self) -> None:
        issued = a_buyer_token(secret=SESSION_SECRET)

        with pytest.raises(TokenClaimsError):
            principal_from_session_token(issued.token, secret=SESSION_SECRET, now=NOW)

    def test_the_algorithm_cannot_be_downgraded_to_none(self) -> None:
        """`alg: none` is the classic JWT bypass. It fails on the signature check,
        before the header is even parsed, which is the ordering that matters."""
        header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        payload = _b64url(
            json.dumps(
                {
                    "typ": "access",
                    "sub": BUYER,
                    "role": Role.PLATFORM_ADMIN.value,
                    "tenant": MERCHANT,
                    "scopes": [],
                    "iat": int(NOW),
                    "exp": int(NOW) + 3600,
                }
            ).encode()
        )

        with pytest.raises(TokenSignatureError):
            principal_from_access_token(f"{header}.{payload}.", secret=ACCESS_SECRET, now=NOW)

    @pytest.mark.parametrize(
        "candidate",
        ["", "not-a-token", "a.b", "a.b.c.d", "...", "eyJ.eyJ.!!!not-base64!!!"],
        ids=["empty", "no-dots", "two-parts", "four-parts", "empty-parts", "bad-base64"],
    )
    def test_a_malformed_credential_is_denied(self, candidate: str) -> None:
        with pytest.raises((TokenMalformedError, TokenSignatureError)):
            principal_from_access_token(candidate, secret=ACCESS_SECRET, now=NOW)

    def test_an_oversized_credential_is_refused_before_parsing(self) -> None:
        with pytest.raises(TokenMalformedError) as caught:
            principal_from_access_token("x" * (MAX_TOKEN_LENGTH + 1), secret=ACCESS_SECRET)

        assert caught.value.details["reason"] == "too_large"

    def test_signing_with_an_empty_secret_is_refused(self) -> None:
        """A token signed with "" verifies for anyone who guesses that."""
        with pytest.raises(ValueError, match="empty secret"):
            encode_signed_token({"typ": "access"}, secret="")

        with pytest.raises(ValueError, match="empty secret"):
            decode_signed_token("a.b.c", secret="", expected_type="access")


class TestClaimValidation:
    def _signed(self, **claims: Any) -> str:
        base: dict[str, Any] = {
            "typ": "access",
            "sub": BUYER,
            "role": Role.BUYER.value,
            "tenant": MERCHANT,
            "buyer": BUYER,
            "scopes": [Scope.CATALOG_READ.value],
            "iat": int(NOW),
            "exp": int(NOW) + 3600,
        }
        base.update(claims)
        return encode_signed_token(base, secret=ACCESS_SECRET)

    def test_an_unknown_role_is_refused(self) -> None:
        with pytest.raises(TokenClaimsError) as caught:
            principal_from_access_token(
                self._signed(role="superuser"), secret=ACCESS_SECRET, now=NOW
            )

        assert caught.value.details["reason"] == "unknown_role"

    def test_an_unknown_scope_is_refused_rather_than_ignored(self) -> None:
        """Honouring the recognised half of a credential we cannot fully interpret
        is how a downgrade attack gets a foothold."""
        with pytest.raises(TokenClaimsError) as caught:
            principal_from_access_token(
                self._signed(scopes=["catalog:read", "ledger:write"]),
                secret=ACCESS_SECRET,
                now=NOW,
            )

        assert caught.value.details["reason"] == "unknown_scope"

    def test_a_buyer_without_a_buyer_id_is_refused(self) -> None:
        """A validly signed token that would make every ownership check vacuous."""
        with pytest.raises(TokenClaimsError) as caught:
            principal_from_access_token(self._signed(buyer=None), secret=ACCESS_SECRET, now=NOW)

        assert caught.value.details["reason"] == "invalid_claims"

    def test_a_merchant_role_holding_payment_scope_is_refused(self) -> None:
        with pytest.raises(TokenClaimsError):
            principal_from_access_token(
                self._signed(
                    role=Role.MERCHANT_ADMIN.value,
                    buyer=None,
                    scopes=[Scope.PAYMENT_WRITE.value],
                ),
                secret=ACCESS_SECRET,
                now=NOW,
            )

    @pytest.mark.parametrize(
        "bad_exp", ["soon", None, True, 1.5], ids=["str", "null", "bool", "float"]
    )
    def test_a_non_integer_expiry_is_refused(self, bad_exp: Any) -> None:
        with pytest.raises(TokenClaimsError):
            principal_from_access_token(self._signed(exp=bad_exp), secret=ACCESS_SECRET, now=NOW)

    def test_missing_subject_is_refused(self) -> None:
        with pytest.raises(TokenClaimsError):
            principal_from_access_token(self._signed(sub=""), secret=ACCESS_SECRET, now=NOW)

    def test_missing_tenant_is_refused(self) -> None:
        """A principal with no tenant could not produce a scoped query at all."""
        with pytest.raises(TokenClaimsError):
            principal_from_access_token(self._signed(tenant=None), secret=ACCESS_SECRET, now=NOW)

    def test_a_non_object_payload_is_refused(self) -> None:
        signed = encode_signed_token({"typ": "access"}, secret=ACCESS_SECRET)
        header, _, signature = signed.split(".")

        with pytest.raises((TokenMalformedError, TokenSignatureError)):
            principal_from_access_token(
                f"{header}.{_b64url(b'[1,2,3]')}.{signature}", secret=ACCESS_SECRET, now=NOW
            )


class TestIssuedTokenDoesNotLeak:
    def test_repr_omits_the_credential(self) -> None:
        """This object lands in tracebacks and debugger output, and the encoded value
        is the credential itself."""
        issued = a_buyer_token()

        assert issued.token not in repr(issued)
        assert "<redacted>" in repr(issued)
        assert BUYER in repr(issued)

    @pytest.mark.parametrize("ttl", [0, -1, 30 * 86_400 + 1])
    def test_an_implausible_ttl_is_refused(self, ttl: int) -> None:
        with pytest.raises(ValueError, match="ttl"):
            a_buyer_token(ttl_seconds=ttl)


class TestPrincipalInvariants:
    def test_scopes_beyond_the_role_are_rejected_at_construction(self) -> None:
        with pytest.raises(ValueError, match="may not hold"):
            Principal(
                subject="user_ops",
                role=Role.MERCHANT_OPERATOR,
                merchant_id=MERCHANT,
                scopes=frozenset({Scope.PAYMENT_WRITE}),
            )

    def test_a_principal_must_carry_a_tenant(self) -> None:
        with pytest.raises(ValueError, match="merchant tenant"):
            Principal(subject=BUYER, role=Role.BUYER, merchant_id="", buyer_id=BUYER)

    def test_a_buyer_must_carry_a_buyer_id(self) -> None:
        with pytest.raises(ValueError, match="buyer_id"):
            Principal(subject=BUYER, role=Role.BUYER, merchant_id=MERCHANT)

    def test_log_fields_carry_identity_and_no_credential(self) -> None:
        fields = a_buyer_token().principal.as_log_fields()

        assert fields["actor_id"] == BUYER
        assert fields["actor_role"] == Role.BUYER.value
        assert fields["merchant_id"] == MERCHANT
        assert not any("token" in key or "secret" in key for key in fields)
