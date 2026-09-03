"""The error code registry â€” one source of truth for every failure this system
can report (design: "Error code registry").

Clients switch on these codes. The frontend drives recovery UI from them and the
external buyer agent branches on them, so a code is part of the public contract:
stable spelling, stable HTTP status, stable retryable flag. That is why the table
lives here as data rather than being restated at each raise site, and why a test
asserts the table against the design document row by row.

Three codes are *in band*: they are not HTTP errors at all. An amount above the
auto-approval limit, an unknown payment outcome, and a discount request beyond
the floor are answers, not failures, so they travel inside a success envelope
with HTTP 200 and let the caller decide what to do next. Collapsing them into
4xx would erase the distinction between "we could not tell you" and "we are
telling you something you need to act on".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class ErrorCode(StrEnum):
    """Every code a client may observe.

    The domain block mirrors the design's registry table. The transport block
    covers the generic conditions the middleware has to name â€” a malformed body,
    an unmatched route, an unexpected exception â€” so that no response can ever
    escape without a code.
    """

    # --- Domain: catalog and offers --------------------------------------
    OFFER_NOT_FOUND = "OFFER_NOT_FOUND"
    OFFER_EXPIRED = "OFFER_EXPIRED"
    INVENTORY_UNAVAILABLE = "INVENTORY_UNAVAILABLE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    PRICE_CHANGED = "PRICE_CHANGED"
    CHECKOUT_EXPIRED = "CHECKOUT_EXPIRED"

    # --- Domain: policy ---------------------------------------------------
    POLICY_BLOCKED = "POLICY_BLOCKED"
    AMOUNT_ABOVE_MAX_LIMIT = "AMOUNT_ABOVE_MAX_LIMIT"
    AMOUNT_ABOVE_AUTO_LIMIT = "AMOUNT_ABOVE_AUTO_LIMIT"
    CATEGORY_NOT_ALLOWED = "CATEGORY_NOT_ALLOWED"
    MERCHANT_NOT_ALLOWED = "MERCHANT_NOT_ALLOWED"
    POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"

    # --- Domain: authorization -------------------------------------------
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    AUTHORIZATION_CHECKOUT_MISMATCH = "AUTHORIZATION_CHECKOUT_MISMATCH"
    AUTHORIZATION_ALREADY_CONSUMED = "AUTHORIZATION_ALREADY_CONSUMED"

    # --- Domain: idempotency and payment ---------------------------------
    IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST = "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"
    REQUEST_IN_PROGRESS = "REQUEST_IN_PROGRESS"
    PAYMENT_TIMEOUT = "PAYMENT_TIMEOUT"
    PAYMENT_UNKNOWN = "PAYMENT_UNKNOWN"
    WEBHOOK_SIGNATURE_INVALID = "WEBHOOK_SIGNATURE_INVALID"

    # --- Domain: negotiation ---------------------------------------------
    MAX_DISCOUNT_EXCEEDED = "MAX_DISCOUNT_EXCEEDED"
    NEGOTIATION_ROUNDS_EXCEEDED = "NEGOTIATION_ROUNDS_EXCEEDED"

    # --- Domain: agent safety --------------------------------------------
    TOOL_BLOCKED = "TOOL_BLOCKED"
    PROMPT_INJECTION_SUSPECTED = "PROMPT_INJECTION_SUSPECTED"

    # --- Domain: state machine -------------------------------------------
    ILLEGAL_TRANSITION = "ILLEGAL_TRANSITION"
    ALREADY_FINALIZED = "ALREADY_FINALIZED"

    # --- Domain: access ---------------------------------------------------
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"

    # --- Transport --------------------------------------------------------
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    GATEWAY_TIMEOUT = "GATEWAY_TIMEOUT"


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    """What a code means on the wire.

    ``retryable`` is ``None`` only for in-band codes, where retrying is not the
    question being asked.
    """

    code: ErrorCode
    http_status: int
    retryable: bool | None
    message: str
    #: True when the code is delivered inside a *success* envelope rather than an
    #: error envelope: an answer that needs a decision, not a failure.
    in_band: bool = False

    @property
    def is_retryable(self) -> bool:
        """Retryable as a plain boolean, for the error envelope.

        An in-band code never reaches an error envelope, so collapsing ``None``
        to ``False`` here cannot mislabel anything a client will see.
        """
        return bool(self.retryable)


def _spec(
    code: ErrorCode,
    status: int,
    retryable: bool | None,
    message: str,
    *,
    in_band: bool = False,
) -> ErrorSpec:
    return ErrorSpec(
        code=code, http_status=status, retryable=retryable, message=message, in_band=in_band
    )


# Messages are written for the caller, not the operator: they say what happened
# and imply what to do, and they never contain an identifier, an amount, a host,
# or a driver string. Anything specific belongs in ``details`` or a log line.
_SPECS: tuple[ErrorSpec, ...] = (
    # --- Catalog and offers ----------------------------------------------
    _spec(ErrorCode.OFFER_NOT_FOUND, 404, False, "The requested offer does not exist."),
    _spec(ErrorCode.OFFER_EXPIRED, 409, False, "The selected offer is no longer valid."),
    _spec(
        ErrorCode.INVENTORY_UNAVAILABLE,
        409,
        False,
        "The requested quantity is not available.",
    ),
    _spec(
        ErrorCode.VERSION_CONFLICT,
        409,
        True,
        "The record changed while this request was in flight.",
    ),
    _spec(
        ErrorCode.PRICE_CHANGED,
        409,
        False,
        "The price changed after approval, so no charge was made.",
    ),
    _spec(ErrorCode.CHECKOUT_EXPIRED, 409, False, "This checkout has expired."),
    # --- Policy -----------------------------------------------------------
    _spec(ErrorCode.POLICY_BLOCKED, 403, False, "This action is not permitted by policy."),
    _spec(
        ErrorCode.AMOUNT_ABOVE_MAX_LIMIT,
        403,
        False,
        "The amount is above the maximum permitted for this buyer.",
    ),
    _spec(
        ErrorCode.AMOUNT_ABOVE_AUTO_LIMIT,
        200,
        None,
        "The amount is above the automatic approval limit and needs explicit approval.",
        in_band=True,
    ),
    _spec(
        ErrorCode.CATEGORY_NOT_ALLOWED,
        403,
        False,
        "This product category is not permitted for this buyer.",
    ),
    _spec(
        ErrorCode.MERCHANT_NOT_ALLOWED,
        403,
        False,
        "This merchant is not permitted for this buyer.",
    ),
    _spec(
        ErrorCode.POLICY_VERSION_MISMATCH,
        409,
        False,
        "Policy changed since this approval was granted.",
    ),
    # --- Authorization ----------------------------------------------------
    _spec(ErrorCode.AUTHORIZATION_EXPIRED, 409, False, "The approval has expired."),
    _spec(
        ErrorCode.AUTHORIZATION_CHECKOUT_MISMATCH,
        409,
        False,
        "The approval does not belong to this checkout.",
    ),
    _spec(
        ErrorCode.AUTHORIZATION_ALREADY_CONSUMED,
        409,
        False,
        "The approval has already been used.",
    ),
    # --- Idempotency and payment ------------------------------------------
    _spec(
        ErrorCode.IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST,
        422,
        False,
        "This idempotency key was already used with a different request body.",
    ),
    _spec(
        ErrorCode.REQUEST_IN_PROGRESS,
        409,
        True,
        "An identical request is still being processed.",
    ),
    _spec(
        ErrorCode.PAYMENT_TIMEOUT,
        504,
        True,
        "The payment provider did not answer in time.",
    ),
    _spec(
        ErrorCode.PAYMENT_UNKNOWN,
        200,
        True,
        "The payment outcome is not confirmed yet. No second payment was created.",
        in_band=True,
    ),
    _spec(
        ErrorCode.WEBHOOK_SIGNATURE_INVALID,
        400,
        False,
        "The webhook signature did not verify.",
    ),
    # --- Negotiation ------------------------------------------------------
    _spec(
        ErrorCode.MAX_DISCOUNT_EXCEEDED,
        200,
        None,
        "The requested discount is beyond what can be offered. A counter-offer is included.",
        in_band=True,
    ),
    _spec(
        ErrorCode.NEGOTIATION_ROUNDS_EXCEEDED,
        409,
        False,
        "The negotiation round limit for this offer has been reached.",
    ),
    # --- Agent safety -----------------------------------------------------
    _spec(ErrorCode.TOOL_BLOCKED, 403, False, "That tool is not permitted in this context."),
    _spec(
        ErrorCode.PROMPT_INJECTION_SUSPECTED,
        400,
        False,
        "The request contained instructions that cannot be followed.",
    ),
    # --- State machine ----------------------------------------------------
    _spec(
        ErrorCode.ILLEGAL_TRANSITION,
        409,
        False,
        "That change is not allowed from the current state.",
    ),
    _spec(ErrorCode.ALREADY_FINALIZED, 409, False, "This record is already final."),
    # --- Access -----------------------------------------------------------
    _spec(ErrorCode.FORBIDDEN, 403, False, "You do not have access to this resource."),
    _spec(
        ErrorCode.RATE_LIMITED,
        429,
        True,
        "Too many requests. Retry after the interval in the Retry-After header.",
    ),
    # --- Transport --------------------------------------------------------
    _spec(ErrorCode.BAD_REQUEST, 400, False, "The request could not be processed as sent."),
    _spec(ErrorCode.UNAUTHENTICATED, 401, False, "Authentication is required."),
    _spec(ErrorCode.NOT_FOUND, 404, False, "The requested resource does not exist."),
    _spec(
        ErrorCode.METHOD_NOT_ALLOWED,
        405,
        False,
        "That method is not supported for this resource.",
    ),
    _spec(ErrorCode.CONFLICT, 409, False, "The request conflicts with the current state."),
    _spec(ErrorCode.PAYLOAD_TOO_LARGE, 413, False, "The request payload is too large."),
    _spec(ErrorCode.VALIDATION_ERROR, 422, False, "The request failed validation."),
    # Deliberately generic: the cause is logged with a traceback, never returned.
    # Not marked retryable, because for a payment endpoint a blind client retry is
    # the more expensive mistake than a manual one.
    _spec(ErrorCode.INTERNAL_ERROR, 500, False, "An internal error occurred."),
    _spec(
        ErrorCode.SERVICE_UNAVAILABLE,
        503,
        True,
        "The service is temporarily unavailable.",
    ),
    _spec(ErrorCode.GATEWAY_TIMEOUT, 504, True, "An upstream dependency timed out."),
)

#: The registry. Read-only at runtime so no import can quietly repoint a code.
ERROR_REGISTRY: Mapping[ErrorCode, ErrorSpec] = MappingProxyType(
    {spec.code: spec for spec in _SPECS}
)

#: Codes answered inside a success envelope rather than an error envelope.
IN_BAND_CODES: frozenset[ErrorCode] = frozenset(spec.code for spec in _SPECS if spec.in_band)

#: Maps a bare HTTP status onto a code, for exceptions raised without one.
_STATUS_TO_CODE: Mapping[int, ErrorCode] = MappingProxyType(
    {
        400: ErrorCode.BAD_REQUEST,
        401: ErrorCode.UNAUTHENTICATED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        405: ErrorCode.METHOD_NOT_ALLOWED,
        409: ErrorCode.CONFLICT,
        413: ErrorCode.PAYLOAD_TOO_LARGE,
        422: ErrorCode.VALIDATION_ERROR,
        429: ErrorCode.RATE_LIMITED,
        500: ErrorCode.INTERNAL_ERROR,
        503: ErrorCode.SERVICE_UNAVAILABLE,
        504: ErrorCode.GATEWAY_TIMEOUT,
    }
)


def spec_for(code: ErrorCode) -> ErrorSpec:
    """Look up a code. A missing spec is a programming error, so it raises.

    Every member of :class:`ErrorCode` is covered by a test, which is what makes
    this lookup total in practice.
    """
    try:
        return ERROR_REGISTRY[code]
    except KeyError:  # pragma: no cover - guarded by test_error_registry
        raise KeyError(f"No registry entry for error code {code!r}") from None


def http_status_for(code: ErrorCode) -> int:
    return spec_for(code).http_status


def code_for_status(status: int) -> ErrorCode:
    """Best-effort code for an exception that carried only a status."""
    mapped = _STATUS_TO_CODE.get(status)
    if mapped is not None:
        return mapped
    return ErrorCode.INTERNAL_ERROR if status >= 500 else ErrorCode.BAD_REQUEST
