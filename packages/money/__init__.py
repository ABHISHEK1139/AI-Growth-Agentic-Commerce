"""Exact money helpers for integer minor-unit amounts.

Money enters and leaves this package as integers or decimal strings. Binary
floating-point values are rejected at the boundary rather than rounded after
precision has already been lost.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_CURRENCY_EXPONENTS = {
    "INR": 2,
    "USD": 2,
}
_CURRENCY_SYMBOLS = {
    "INR": "₹",
    "USD": "$",
}
_DECIMAL_RE = re.compile(r"^(?P<whole>0|[1-9]\d*)(?:\.(?P<fraction>\d+))?$")


class MoneyValueError(ValueError, TypeError):
    """Raised when a value cannot represent a valid minor-unit amount."""


def _integer(value: int, *, name: str, non_negative: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyValueError(f"{name} must be an integer minor-unit value")
    if non_negative and value < 0:
        raise MoneyValueError(f"{name} must be non-negative")
    return value


def currency_exponent(currency: str) -> int:
    """Return the configured minor-unit exponent for an ISO currency code."""
    normalized = currency.strip().upper()
    try:
        return _CURRENCY_EXPONENTS[normalized]
    except KeyError as exc:
        raise MoneyValueError(f"unsupported currency: {currency}") from exc


def add_minor_units(*amounts: int) -> int:
    """Add non-negative integer amounts exactly."""
    return sum(_integer(amount, name="amount") for amount in amounts)


def sum_minor_units(amounts: Iterable[int]) -> int:
    """Add an iterable of non-negative integer amounts exactly."""
    return sum(_integer(amount, name="amount") for amount in amounts)


def subtract_minor_units(amount_minor: int, deduction_minor: int) -> int:
    """Subtract without permitting a negative monetary result."""
    amount = _integer(amount_minor, name="amount_minor")
    deduction = _integer(deduction_minor, name="deduction_minor")
    result = amount - deduction
    if result < 0:
        raise MoneyValueError("deduction_minor cannot exceed amount_minor")
    return result


def multiply_minor_units(unit_amount_minor: int, quantity: int) -> int:
    """Multiply an integer unit price by a positive integer quantity."""
    amount = _integer(unit_amount_minor, name="unit_amount_minor")
    count = _integer(quantity, name="quantity")
    if count < 1:
        raise MoneyValueError("quantity must be at least one")
    return amount * count


def calculate_total_minor(
    unit_price_minor: int,
    quantity: int,
    *,
    shipping_minor: int = 0,
    tax_minor: int = 0,
    discount_minor: int = 0,
) -> int:
    """Compute an exact checkout total using integer arithmetic only."""
    subtotal = multiply_minor_units(unit_price_minor, quantity)
    gross = add_minor_units(subtotal, shipping_minor, tax_minor)
    return subtract_minor_units(gross, discount_minor)


def format_minor_units(
    amount_minor: int,
    *,
    currency: str | None = None,
    grouping: bool = False,
) -> str:
    """Format minor units as an exact major-unit decimal string.

    If ``currency`` is supplied, the ISO code prefixes the number. The output is
    accepted by :func:`parse_major_units`, including grouped output.
    """
    amount = _integer(amount_minor, name="amount_minor")
    exponent = currency_exponent(currency) if currency is not None else 2
    factor = 10**exponent
    whole, fraction = divmod(amount, factor)
    whole_text = f"{whole:,}" if grouping else str(whole)
    number = whole_text if exponent == 0 else f"{whole_text}.{fraction:0{exponent}d}"
    return f"{currency.strip().upper()} {number}" if currency is not None else number


def format_currency(amount_minor: int, currency: str = "INR") -> str:
    """Format an amount for display with an ISO code and digit grouping."""
    return format_minor_units(amount_minor, currency=currency, grouping=True)


def parse_major_units(value: str | int, *, currency: str | None = None) -> int:
    """Parse an exact major-unit value into integer minor units.

    Strings may contain grouping separators and either the expected ISO code or
    currency symbol. More precision than the currency supports is rejected; it
    is never rounded. Integers are interpreted as whole major units. Floats,
    booleans, exponent notation, and negative values are deliberately refused.
    """
    exponent = currency_exponent(currency) if currency is not None else 2
    factor = 10**exponent

    from decimal import ROUND_HALF_EVEN, Decimal

    if isinstance(value, bool | float):
        raise MoneyValueError("money must be provided as an integer or decimal string, never float")
    if isinstance(value, int):
        if value < 0:
            raise MoneyValueError("money must be a non-negative integer")
        return _integer(value, name="value", non_negative=True) * factor
    if not isinstance(value, str):
        raise MoneyValueError("money must be provided as an integer or decimal string")

    text = value.strip()
    if currency is not None:
        code = currency.strip().upper()
        symbol = _CURRENCY_SYMBOLS.get(code)
        if text.upper().startswith(code):
            text = text[len(code) :].strip()
        elif symbol is not None and text.startswith(symbol):
            text = text[len(symbol) :].strip()
    text = text.replace(",", "")
    match = _DECIMAL_RE.fullmatch(text)
    if match is None:
        raise MoneyValueError("money must be a non-negative base-10 decimal")

    fraction = match.group("fraction") or ""
    if len(fraction) > exponent:
        raise MoneyValueError(f"money has more than {exponent} fractional digits")
    padded_fraction = fraction.ljust(exponent, "0")
    return int(match.group("whole")) * factor + (int(padded_fraction) if padded_fraction else 0)


__all__ = [
    "MoneyValueError",
    "add_minor_units",
    "calculate_total_minor",
    "currency_exponent",
    "format_currency",
    "format_minor_units",
    "multiply_minor_units",
    "parse_major_units",
    "subtract_minor_units",
    "sum_minor_units",
]
