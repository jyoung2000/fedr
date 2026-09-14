from __future__ import annotations

import random
from decimal import Decimal

import pytest

from fedr.config.schema import AppSettings, default_settings
from fedr.core.enums import Chain, OrderSide, VenueKind
from fedr.core.models import ExecutionQuote, now_ms
from fedr.engine.gas_guard import GasSnapshot
from fedr.marketdata.orderbook import OrderBook


@pytest.fixture
def settings() -> AppSettings:
    return default_settings("paper")


def make_book(
    venue: str,
    symbol: str,
    mid: Decimal,
    spread_bps: Decimal = Decimal("4"),
    levels: int = 10,
    size: Decimal = Decimal("5"),
    step_bps: Decimal = Decimal("2"),
) -> OrderBook:
    half = mid * spread_bps / Decimal(20_000)
    bids, asks = [], []
    for i in range(levels):
        off = mid * step_bps * i / Decimal(10_000)
        bids.append([mid - half - off, size * (1 + i)])
        asks.append([mid + half + off, size * (1 + i)])
    return OrderBook.from_raw(venue, symbol, bids, asks, ts_ms=now_ms(), source="test")


def cex_quote(
    venue: str,
    side: OrderSide,
    size: Decimal,
    ref_price: Decimal,
    avg_price: Decimal | None = None,
    fee_pct: Decimal | None = Decimal("0.10"),
    fully: bool = True,
    age_ms: int = 0,
    symbol: str = "SOL/USDC",
) -> ExecutionQuote:
    avg = avg_price if avg_price is not None else ref_price
    slip = (
        (avg - ref_price) / ref_price * 100 if side is OrderSide.BUY else (ref_price - avg) / ref_price * 100
    )
    return ExecutionQuote(
        venue=venue,
        kind=VenueKind.CEX,
        symbol=symbol,
        side=side,
        base_amount=size,
        quote_amount=size * avg,
        avg_price=avg,
        reference_price=ref_price,
        slippage_pct=max(Decimal(0), slip),
        price_impact_pct=Decimal(0),
        fully_fillable=fully,
        market_ts_ms=now_ms() - age_ms,
        quote_ts_ms=now_ms() - age_ms,
        fee_pct=fee_pct,
        fee_source="test",
        route=f"{venue} book",
        levels_consumed=1,
        depth_available_base=size * 10,
        extra={"limit_price": str(avg)},
    )


def dex_quote(
    venue: str,
    side: OrderSide,
    size: Decimal,
    ref_price: Decimal,
    avg_price: Decimal,
    impact_pct: Decimal = Decimal("0.05"),
    fee_pct: Decimal | None = Decimal("0.30"),
    chain: Chain = Chain.SOLANA,
    gas_limit: int = 200_000,
    symbol: str = "SOL/USDC",
    age_ms: int = 0,
) -> ExecutionQuote:
    return ExecutionQuote(
        venue=venue,
        kind=VenueKind.DEX,
        symbol=symbol,
        side=side,
        base_amount=size,
        quote_amount=size * avg_price,
        avg_price=avg_price,
        reference_price=ref_price,
        slippage_pct=Decimal(0),
        price_impact_pct=impact_pct,
        fully_fillable=True,
        market_ts_ms=now_ms() - age_ms,
        quote_ts_ms=now_ms() - age_ms,
        fee_pct=fee_pct,
        fee_source="embedded_in_quote",
        gas_limit=gas_limit,
        chain=chain,
        route=f"{venue} router",
        quote_id=f"q-{random.randint(1, 10**9)}",
        min_received=size * avg_price * Decimal("0.995") if side is OrderSide.SELL else None,
    )


def gas_snapshot(
    chain: Chain = Chain.SOLANA,
    gas_price: Decimal = Decimal("0.05"),
    native_usd: Decimal = Decimal("150"),
    priority: Decimal | None = None,
) -> GasSnapshot:
    return GasSnapshot(
        chain=chain,
        gas_price_native=gas_price,
        priority_fee_native=priority if priority is not None else gas_price,
        native_usd=native_usd,
        source="test",
    )
