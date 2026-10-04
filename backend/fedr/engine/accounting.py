"""Accounting: actual vs estimated profit, P&L attribution, capital summary.

Settlement rule (audit F08 / invariant C): a realized result is booked only from AUTHORITATIVE values -
fills or venue-reported cost, fees with a known asset and conversion, gas with a known USD price. Missing
data is never treated as zero. When anything is missing the outcome is ``complete=False``: the trade is
marked UNRECONCILED (or stays UNKNOWN) and no realized P&L is booked until it is resolved.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from fedr.core.enums import OrderSide, TradeStatus
from fedr.core.models import OrderResult, TradeRecord
from fedr.core.money import ZERO, D

PriceFn = Callable[[str], Decimal | None]


@dataclass(slots=True)
class TradeOutcome:
    actual_gross: Decimal
    trading_fees: Decimal
    gas: Decimal
    funding: Decimal
    actual_net: Decimal
    prediction_error: Decimal  # estimated_net - actual_net (positive = we over-estimated)
    slippage_variance: Decimal  # realized execution price deviation vs quoted (USD)
    fee_variance: Decimal
    gas_variance: Decimal
    matched_base: Decimal
    unmatched_base: Decimal
    complete: bool = True
    missing: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LegValue:
    filled: Decimal
    quote_value: Decimal | None
    fee_usd: Decimal | None
    gas_usd: Decimal | None
    missing: list[str]


def _split(symbol: str) -> tuple[str, str]:
    base, quote = symbol.split(":")[0].split("/")
    return base, quote


def leg_value(
    leg: OrderResult | None, quote_usd_rate: Decimal, price_usd: PriceFn | None = None, name: str = "leg"
) -> LegValue:
    """Authoritative value of one leg. ``missing`` lists every value that is not known."""
    if leg is None:
        return LegValue(ZERO, ZERO, ZERO, ZERO, [])
    missing: list[str] = []
    if leg.outcome_unknown:
        missing.append(f"{name}: outcome unknown ({leg.status.value})")
    qv = leg.quote_value
    if qv is None:
        missing.append(f"{name}: filled {leg.filled} but no authoritative quote value")
    base, quote = _split(leg.request.symbol)
    fee_usd: Decimal | None = ZERO
    if not leg.fee_known:
        fee_usd = None
        missing.append(f"{name}: fee not reported by the venue")
    elif leg.filled > 0:
        if leg.fills_cover_filled and all(f.fee_known for f in leg.fills) and not leg.fee_items:
            items = [(f.fee_amount, f.fee_asset, f.price) for f in leg.fills]
        elif leg.fee_items:
            items = [(a, c, leg.avg_price) for a, c in leg.fee_items]
        elif leg.fees_complete:
            items = [(leg.fee_quote, quote, None)] if leg.fee_quote else []
        else:
            items = [(f.fee_amount, f.fee_asset, f.price) for f in leg.fills]
        total = ZERO
        for amount, asset, px in items:
            if amount == 0:
                continue
            if asset in (quote, ""):
                # "" only occurs for producers that report fees in the quote unit implicitly (legacy paper)
                total += amount * quote_usd_rate
            elif asset == base:
                p = px or leg.avg_price
                if not p:
                    fee_usd = None
                    missing.append(f"{name}: fee in {asset} without a fill price")
                    break
                total += amount * p * quote_usd_rate
            else:
                p = price_usd(asset) if price_usd else None
                if p is None:
                    fee_usd = None
                    missing.append(f"{name}: fee {amount} {asset} has no USD price")
                    break
                total += amount * p
        if fee_usd is not None:
            fee_usd = total
    gas_usd: Decimal | None = leg.gas_cost_usd
    if not leg.gas_known:
        gas_usd = None
        missing.append(f"{name}: network fee not priced")
    return LegValue(leg.filled, qv, fee_usd, gas_usd, missing)


def compute_outcome(
    tr: TradeRecord,
    quote_usd_rate: Decimal = Decimal("1"),
    estimated: dict | None = None,
    price_usd: PriceFn | None = None,
) -> TradeOutcome:
    b = leg_value(tr.buy, quote_usd_rate, price_usd, "buy")
    s = leg_value(tr.sell, quote_usd_rate, price_usd, "sell")
    h = leg_value(tr.hedge, quote_usd_rate, price_usd, "hedge")
    missing = b.missing + s.missing + h.missing
    bq, sq, hq = (x.quote_value or ZERO for x in (b, s, h))
    # The hedge neutralises leftover exposure: treat it as the opposite leg for the unmatched size.
    gross = (sq - bq) * quote_usd_rate
    if tr.hedge is not None:
        gross += (hq if tr.hedge.request.side is OrderSide.SELL else -hq) * quote_usd_rate
    fees = sum((x.fee_usd or ZERO for x in (b, s, h)), ZERO)
    gas = sum((x.gas_usd or ZERO for x in (b, s, h)), ZERO)
    funding = ZERO
    net = gross - fees - gas - funding
    est = estimated or {}
    est_fees = (
        D(est.get("buy_trading_fee", 0)) + D(est.get("sell_trading_fee", 0)) + D(est.get("dex_swap_fee", 0))
    )
    est_gas = D(est.get("gas", 0)) + D(est.get("priority_fee", 0))
    # realized slippage vs quoted execution prices
    slip_var = ZERO
    if tr.buy and tr.buy.avg_price and tr.buy.request.extra.get("quoted_price"):
        slip_var += (tr.buy.avg_price - D(tr.buy.request.extra["quoted_price"])) * b.filled
    if tr.sell and tr.sell.avg_price and tr.sell.request.extra.get("quoted_price"):
        slip_var += (D(tr.sell.request.extra["quoted_price"]) - tr.sell.avg_price) * s.filled
    hedge_sell = tr.hedge is not None and tr.hedge.request.side is OrderSide.SELL
    matched = min(b.filled, s.filled + (h.filled if hedge_sell else ZERO))
    unmatched = abs(b.filled - s.filled - (h.filled if hedge_sell else -h.filled if tr.hedge else ZERO))
    return TradeOutcome(
        actual_gross=gross,
        trading_fees=fees,
        gas=gas,
        funding=funding,
        actual_net=net,
        prediction_error=tr.estimated_net - net,
        slippage_variance=slip_var * quote_usd_rate,
        fee_variance=fees - est_fees,
        gas_variance=gas - est_gas,
        matched_base=matched,
        unmatched_base=unmatched,
        complete=not missing,
        missing=missing,
    )


def apply_outcome(tr: TradeRecord, out: TradeOutcome) -> None:
    if not out.complete:
        # never present partial numbers as a realized result
        tr.actual_gross = tr.actual_fees = tr.actual_gas = tr.actual_net = tr.prediction_error = None
        tr.log("settlement_incomplete", missing="; ".join(out.missing)[:500])
        if tr.status not in (TradeStatus.FAILED, TradeStatus.ABORTED, TradeStatus.UNKNOWN):
            tr.status = TradeStatus.UNRECONCILED
        return
    tr.actual_gross = out.actual_gross
    tr.actual_fees = out.trading_fees
    tr.actual_gas = out.gas
    tr.actual_net = out.actual_net
    tr.prediction_error = out.prediction_error
    if tr.status not in (
        TradeStatus.FAILED,
        TradeStatus.ABORTED,
        TradeStatus.RECOVERING,
        TradeStatus.HEDGED,
        TradeStatus.UNKNOWN,
    ):
        tr.status = TradeStatus.FILLED if out.unmatched_base == 0 else TradeStatus.PARTIAL
