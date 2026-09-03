"""Signed bearer tokens and session values (Requirement 24.1, 24.7).

One codec, two uses. Both are compact JWS objects — ``header.payload.signature``,
base64url, HMAC-SHA256 — because that shape is what every HTTP client already
knows how to carry, and because the standard library has everything needed to
produce and check it. No dependency was added for this: ``hmac`` supplies the MAC
and ``hmac.compare_digest`` supplies the constant-time comparison, so there is no
hand-rolled crypto here, only a hand-rolled *encoding* around a stdlib primitive.

Verification order is the security-relevant part, and it is:

1. shape and bounded length, before anything is parsed
2. the signature, recomputed over the received bytes and compared in constant time
3. the header, and only then, so a caller cannot select the algorithm used to check
   their own token (``alg: none`` and HS/RS confusion both die here)
4. the claims, including a strict expiry check

Nothing from the payload influences steps 1 to 3. An expired token is refused with
no leeway (Requirement 24.7); a token dated in the future is tolerated only within
a small clock skew, because a staging box with a drifting clock should not lock
every agent out.

The encoded form begins with ``eyJ``, which the logging redactor already treats as
a credential shape, so a token that reaches a log line by accident is masked.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from packages.errors.exceptions import UnauthenticatedError
from packages.observability.context import new_id
from packages.security.principals import (
    AuthMethod,
    Principal,
    Role,
    Scope,
    grant_scopes,
)

ALGORITHM: Final[str] = "HS256"
TOKEN_TYPE_ACCESS: Final[str] = "access"  # noqa: S105 - signed claim type label
TOKEN_TYPE_SESSION: Final[str] = "session"  # noqa: S105 - signed claim type label

#: A credential larger than this is not a credential we issued. Bounded before
#: parsing so a megabyte of base64 cannot become a megabyte of JSON.
MAX_TOKEN_LENGTH: Final[int] = 4096

#: Tolerance for a token stamped slightly in the future. Applies to ``iat`` only;
#: ``exp`` is checked without leeway.
CLOCK_SKEW_SECONDS: Final[int] = 60

#: Refuse to issue a credential valid for longer than this, whatever configuration
#: says. A session is a day by default; a month-long bearer token is an incident
#: waiting for a laptop to be stolen.
MAX_TTL_SECONDS: Final[int] = 30 * 86_400

_HEADER: Final[Mapping[str, str]] = {"alg": ALGORITHM, "typ": "JWT"}


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


class TokenError(UnauthenticatedError):
    """Base for every reason a credential did not verify. Answers 401."""


class TokenMalformedError(TokenError):
    """Not shaped like a token we issue: wrong segment count, bad base64, not JSON."""


class TokenSignatureError(TokenError):
    """The signature did not verify, or the header selected another algorithm."""


class TokenExpiredError(TokenError):
    """The credential is past its expiry (Requirement 24.7)."""


class TokenClaimsError(TokenError):
    """Well-signed, but the claims are not usable: unknown role, unknown scope."""


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (binascii.Error, ValueError) as exc:
        raise TokenMalformedError(
            "The credential could not be read.", details={"reason": "malformed"}
        ) from exc


def _json_segment(payload: Mapping[str, Any]) -> str:
    # Sorted and separator-tight so the same claims always produce the same bytes;
    # a signature over a non-deterministic encoding is a signature over nothing.
    encoded = json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _b64url_encode(encoded)


def _sign(signing_input: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()


def encode_signed_token(claims: Mapping[str, Any], *, secret: str) -> str:
    """Serialize and sign ``claims``.

    An empty secret raises rather than producing a token anyone can forge. The
    startup guard in :meth:`apps.api.config.Settings.validate_for_env` already
    refuses a placeholder secret outside local development; this refuses the
    degenerate case everywhere.
    """
    if not secret:
        raise ValueError("refusing to sign a token with an empty secret")

    header_segment = _json_segment(_HEADER)
    payload_segment = _json_segment(claims)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature_segment = _b64url_encode(_sign(signing_input, secret))
    return f"{header_segment}.{payload_segment}.{signature_segment}"


def decode_signed_token(
    token: str,
    *,
    secret: str,
    expected_type: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Verify ``token`` and return its claims.

    Raises a :class:`TokenError` subclass for every failure. The caller does not
    need to distinguish them to answer the request — they all mean 401 — but the
    distinction is useful in a log line and lets a client tell "refresh me" from
    "your credential is wrong".
    """
    if not secret:
        raise ValueError("refusing to verify a token with an empty secret")
    if not isinstance(token, str) or not token:
        raise TokenMalformedError("A credential is required.", details={"reason": "missing"})
    if len(token) > MAX_TOKEN_LENGTH:
        raise TokenMalformedError(
            "The credential could not be read.", details={"reason": "too_large"}
        )

    segments = token.split(".")
    if len(segments) != 3:
        raise TokenMalformedError(
            "The credential could not be read.", details={"reason": "malformed"}
        )
    header_segment, payload_segment, signature_segment = segments

    # Step 2: the signature, over exactly the bytes that arrived.
    expected_signature = _sign(f"{header_segment}.{payload_segment}".encode("ascii"), secret)
    presented_signature = _b64url_decode(signature_segment)
    if not hmac.compare_digest(expected_signature, presented_signature):
        raise TokenSignatureError(
            "The credential could not be verified.", details={"reason": "signature"}
        )

    # Step 3: the header, now that it is known to be ours. Checking `alg` before
    # the signature would let the token choose how it is validated.
    header = _decode_json_segment(header_segment)
    if header.get("alg") != ALGORITHM:
        raise TokenSignatureError(
            "The credential could not be verified.", details={"reason": "algorithm"}
        )

    # Step 4: the claims.
    claims = _decode_json_segment(payload_segment)
    if claims.get("typ") != expected_type:
        # A session value presented as a bearer token, or the reverse. Both are
        # signed by us, so only this check keeps the two surfaces apart.
        raise TokenClaimsError(
            "The credential is not valid for this surface.",
            details={"reason": "wrong_type"},
        )

    reference = time.time() if now is None else now
    expires_at = _int_claim(claims, "exp")
    if reference >= expires_at:
        raise TokenExpiredError("The credential has expired.", details={"reason": "expired"})
    issued_at = _int_claim(claims, "iat")
    if issued_at - CLOCK_SKEW_SECONDS > reference:
        raise TokenClaimsError(
            "The credential is not valid yet.", details={"reason": "issued_in_future"}
        )

    return claims


def _decode_json_segment(segment: str) -> dict[str, Any]:
    try:
        decoded = json.loads(_b64url_decode(segment))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TokenMalformedError(
            "The credential could not be read.", details={"reason": "malformed"}
        ) from exc
    if not isinstance(decoded, dict):
        raise TokenMalformedError(
            "The credential could not be read.", details={"reason": "malformed"}
        )
    return decoded


def _int_claim(claims: Mapping[str, Any], name: str) -> int:
    value = claims.get(name)
    # `bool` is an `int` in Python; a boolean expiry is nonsense, not a timestamp.
    if isinstance(value, bool) or not isinstance(value, int):
        raise TokenClaimsError("The credential is not usable.", details={"reason": f"bad_{name}"})
    return value


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A freshly minted credential and what it grants.

    ``__repr__`` deliberately omits the token. This object is passed around and
    ends up in tracebacks and debugger output; the encoded value is the credential
    itself, and there is no reason for it to be there.
    """

    token: str
    expires_at: int
    principal: Principal

    @property
    def expires_in(self) -> int:
        """Seconds of remaining life, for a ``token_type``/``expires_in`` response."""
        return max(0, self.expires_at - int(time.time()))

    def __repr__(self) -> str:
        return (
            f"IssuedToken(subject={self.principal.subject!r}, "
            f"expires_at={self.expires_at}, token=<redacted>)"
        )


def _issue(
    *,
    token_type: str,
    method: AuthMethod,
    secret: str,
    subject: str,
    role: Role,
    merchant_id: str,
    buyer_id: str | None,
    scopes: Iterable[Scope] | None,
    ttl_seconds: int,
    now: float | None = None,
    client_id: str | None = None,
) -> IssuedToken:
    if ttl_seconds < 1:
        raise ValueError("token ttl must be at least one second")
    if ttl_seconds > MAX_TTL_SECONDS:
        raise ValueError(f"token ttl must not exceed {MAX_TTL_SECONDS} seconds")

    issued_at = int(time.time() if now is None else now)
    expires_at = issued_at + ttl_seconds
    granted = grant_scopes(role, scopes)

    # Built before signing, so an invalid combination (a buyer with no buyer_id, a
    # merchant role holding payment:write) fails at issue time rather than at the
    # first request that presents the token.
    principal = Principal(
        subject=subject,
        role=role,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        scopes=granted,
        method=method,
        expires_at=expires_at,
        client_id=client_id,
    )

    claims: dict[str, Any] = {
        "typ": token_type,
        "sub": subject,
        "role": role.value,
        "tenant": merchant_id,
        "scopes": sorted(scope.value for scope in granted),
        "iat": issued_at,
        "exp": expires_at,
        # A unique identifier per credential, so a single token can be traced
        # through the audit trail and revoked by identity later.
        "jti": new_id("cred"),
    }
    if buyer_id is not None:
        claims["buyer"] = buyer_id
    if client_id is not None:
        claims["cid"] = client_id

    return IssuedToken(
        token=encode_signed_token(claims, secret=secret),
        expires_at=expires_at,
        principal=principal,
    )


def issue_access_token(
    *,
    secret: str,
    subject: str,
    role: Role,
    merchant_id: str,
    ttl_seconds: int,
    buyer_id: str | None = None,
    scopes: Iterable[Scope] | None = None,
    now: float | None = None,
    client_id: str | None = None,
) -> IssuedToken:
    """A short-lived scoped bearer token for the public agent surface."""
    return _issue(
        token_type=TOKEN_TYPE_ACCESS,
        method=AuthMethod.TOKEN,
        secret=secret,
        subject=subject,
        role=role,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        scopes=scopes,
        ttl_seconds=ttl_seconds,
        now=now,
        client_id=client_id,
    )


def issue_session_token(
    *,
    secret: str,
    subject: str,
    role: Role,
    merchant_id: str,
    ttl_seconds: int,
    buyer_id: str | None = None,
    now: float | None = None,
) -> IssuedToken:
    """A session value for the web surface.

    Signed with a different secret than access tokens (``SESSION_SECRET`` rather
    than ``JWT_SECRET``) and stamped with a different ``typ``, so neither can be
    presented where the other belongs even if one secret leaks. A session always
    carries the full scope set its role allows: narrowing is a property of an
    agent credential, not of a signed-in human.
    """
    return _issue(
        token_type=TOKEN_TYPE_SESSION,
        method=AuthMethod.SESSION,
        secret=secret,
        subject=subject,
        role=role,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        scopes=None,
        ttl_seconds=ttl_seconds,
        now=now,
    )


# ---------------------------------------------------------------------------
# Verifying
# ---------------------------------------------------------------------------


def _principal_from_claims(claims: Mapping[str, Any], *, method: AuthMethod) -> Principal:
    subject = claims.get("sub")
    tenant = claims.get("tenant")
    if not isinstance(subject, str) or not subject:
        raise TokenClaimsError("The credential is not usable.", details={"reason": "bad_subject"})
    if not isinstance(tenant, str) or not tenant:
        raise TokenClaimsError("The credential is not usable.", details={"reason": "bad_tenant"})

    raw_role = claims.get("role")
    if not isinstance(raw_role, str):
        raise TokenClaimsError("The credential is not usable.", details={"reason": "unknown_role"})
    try:
        role = Role(raw_role)
    except ValueError as exc:
        raise TokenClaimsError(
            "The credential is not usable.", details={"reason": "unknown_role"}
        ) from exc

    raw_scopes = claims.get("scopes", [])
    if not isinstance(raw_scopes, list):
        raise TokenClaimsError("The credential is not usable.", details={"reason": "bad_scopes"})
    try:
        # Strict rather than lenient: a credential carrying a scope this build does
        # not understand is a credential we cannot reason about, and honouring the
        # part we recognise is how a downgrade attack gets a foothold.
        scopes = frozenset(Scope(value) for value in raw_scopes)
    except ValueError as exc:
        raise TokenClaimsError(
            "The credential is not usable.", details={"reason": "unknown_scope"}
        ) from exc

    buyer_id = claims.get("buyer")
    if buyer_id is not None and not isinstance(buyer_id, str):
        raise TokenClaimsError("The credential is not usable.", details={"reason": "bad_buyer"})
    client_id = claims.get("cid")
    if client_id is not None and not isinstance(client_id, str):
        raise TokenClaimsError("The credential is not usable.", details={"reason": "bad_client"})

    try:
        return Principal(
            subject=subject,
            role=role,
            merchant_id=tenant,
            buyer_id=buyer_id,
            scopes=scopes,
            method=method,
            expires_at=_int_claim(claims, "exp"),
            client_id=client_id,
        )
    except ValueError as exc:
        # A signed token whose claims violate a principal invariant — a buyer with
        # no buyer id, a merchant role holding payment:write. Either we issued it
        # before a rule changed, or the secret is compromised. Refuse it.
        raise TokenClaimsError(
            "The credential is not usable.", details={"reason": "invalid_claims"}
        ) from exc


def principal_from_access_token(token: str, *, secret: str, now: float | None = None) -> Principal:
    """Verify a bearer token and rebuild the principal it names."""
    claims = decode_signed_token(token, secret=secret, expected_type=TOKEN_TYPE_ACCESS, now=now)
    return _principal_from_claims(claims, method=AuthMethod.TOKEN)


def principal_from_session_token(token: str, *, secret: str, now: float | None = None) -> Principal:
    """Verify a session value and rebuild the principal it names."""
    claims = decode_signed_token(token, secret=secret, expected_type=TOKEN_TYPE_SESSION, now=now)
    return _principal_from_claims(claims, method=AuthMethod.SESSION)
