"""Decimal helpers. All engine arithmetic uses Decimal - never float."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_EVEN, Decimal, InvalidOperation, getcontext
from typing import Any

getcontext().prec = 34

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
BPS = Decimal("10000")


def D(value: Any, default: Decimal | None = None) -> Decimal:
    """Convert anything reasonable into a Decimal without float artefacts."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        if default is not None:
            return default
        raise ValueError("cannot convert None to Decimal")
    if isinstance(value, bool):
        raise TypeError("bool is not a valid money value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        if default is not None:
            return default
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def pct(part: Decimal, whole: Decimal) -> Decimal:
    """part / whole as a percentage (0 when whole is 0)."""
    if whole == 0:
        return ZERO
    return part / whole * HUNDRED


def pct_of(value: Decimal, percent: Decimal) -> Decimal:
    return value * percent / HUNDRED


def bps_of(value: Decimal, bps: Decimal) -> Decimal:
    return value * bps / BPS


def q(value: Decimal, places: int = 8) -> Decimal:
    """Quantise for display / storage (banker's rounding)."""
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round *down* to an exchange step size (never round an order size up)."""
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round *up* to a step size (conservative for sell limit prices)."""
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def clamp(value: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, value))


def fmt_money(value: Decimal | None, symbol: str = "$", places: int = 2) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else "+" if value > 0 else ""
    return f"{sign}{symbol}{abs(q(value, places)):,.{places}f}"


def fmt_pct(value: Decimal | None, places: int = 2) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else "+" if value > 0 else ""
    return f"{sign}{abs(q(value, places)):.{places}f}%"
