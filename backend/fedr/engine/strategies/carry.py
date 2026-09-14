"""Carry strategies: spot <-> perpetual, funding-rate and basis.

These open a delta-neutral position (long spot, short perp - or the reverse)
and hold it. Expected profit = basis captured + expected funding income over
the configured horizon - fees for opening AND closing. Nothing here is a free
lunch: funding can flip and basis can widen, which is why they are OFF by
default and use the same Profit Guard with doubled fees and halved
worst-case funding income.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from fedr.connectors.base import VenueConnector
from fedr.core.enums import OrderSide, Strategy, VenueKind
from fedr.core.models import ExecutionQuote
from fedr.core.money import HUNDRED, ZERO, D

HOLD_HOURS_DEFAULT = 24


@dataclass(slots=True)
class CarryQuote:
    perp_symbol: str
    funding_rate: Decimal | None  # per interval, fraction (e.g. 0.0001 = 0.01%)
    interval_hours: Decimal
    hold_hours: Decimal
    expected_funding_income_usd: Decimal  # positive = we receive
    funding_cost_usd: Decimal  # what Profit Guard subtracts (negative = income)
    basis_pct: Decimal
    direction: str  # long_spot_short_perp | short_spot_long_perp (only the first is supported without margin)


def perp_symbol(pair: str) -> str:
    base, quote = pair.split("/")
    return f"{base}/{quote}:{quote}"


async def evaluate_carry(
    ctx, strategy: Strategy, spot: VenueConnector, perp: VenueConnector, pair: str, size: Decimal
) -> tuple[ExecutionQuote | None, ExecutionQuote | None, CarryQuote | None]:
    """Return (buy_leg, sell_leg, carry) for a long-spot / short-perp position."""
    psym = perp_symbol(pair)
    book = ctx.hub.get_book(spot.name, pair)
    if book is None:
        book = await spot.fetch_order_book(pair, ctx.settings.advanced.orderbook_depth)
        await ctx.hub.ingest(book)
    pbook = ctx.hub.get_book(perp.name, psym)
    if pbook is None:
        pbook = await perp.fetch_order_book(psym, ctx.settings.advanced.orderbook_depth)
        await ctx.hub.ingest(pbook)
    buy_q = await spot.get_quote(pair, OrderSide.BUY, size, order_book=book)
    sell_q = await perp.get_quote(psym, OrderSide.SELL, size, order_book=pbook)
    sell_q.kind = VenueKind.PERP
    fr = None
    interval = Decimal("8")
    fetch = getattr(perp, "fetch_funding_rate", None)
    if fetch is not None:
        try:
            raw = await fetch(psym)
            if raw and raw.get("fundingRate") is not None:
                fr = D(raw["fundingRate"])
                iv = str(raw.get("interval") or "8h").rstrip("h")
                interval = D(iv) if iv.replace(".", "").isdigit() else Decimal("8")
        except Exception:
            fr = None
    notional = size * sell_q.avg_price
    hold = Decimal(HOLD_HOURS_DEFAULT)
    if strategy is Strategy.FUNDING and fr is None:
        return None, None, None  # unknown funding -> cannot evaluate a funding strategy
    periods = hold / interval if interval > 0 else ZERO
    # short perp receives funding when the rate is positive
    income = (fr or ZERO) * periods * notional
    if strategy is Strategy.BASIS:
        income = income * Decimal("0.5")  # basis strategy does not rely on funding; count only half
    basis_pct = (
        (sell_q.avg_price - buy_q.avg_price) / buy_q.avg_price * HUNDRED if buy_q.avg_price > 0 else ZERO
    )
    carry = CarryQuote(
        perp_symbol=psym,
        funding_rate=fr,
        interval_hours=interval,
        hold_hours=hold,
        expected_funding_income_usd=income,
        funding_cost_usd=-income,
        basis_pct=basis_pct,
        direction="long_spot_short_perp",
    )
    return buy_q, sell_q, carry
