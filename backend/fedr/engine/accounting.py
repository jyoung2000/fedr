"""Accounting: actual vs estimated profit, P&L attribution, capital summary."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from fedr.core.enums import OrderSide, TradeStatus
from fedr.core.models import OrderResult, TradeRecord
from fedr.core.money import ZERO, D


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


def _leg_value(leg: OrderResult | None) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """(filled_base, quote_amount, fee_quote, gas_usd) for a leg."""
    if leg is None:
        return ZERO, ZERO, ZERO, ZERO
    return leg.filled, leg.quote_amount, leg.fee_quote, leg.gas_cost_usd


def compute_outcome(tr: TradeRecord, quote_usd_rate: Decimal = Decimal("1"), estimated: dict | None = None) -> TradeOutcome:
    bf, bq, bfee, bgas = _leg_value(tr.buy)
    sf, sq, sfee, sgas = _leg_value(tr.sell)
    hf, hq, hfee, hgas = _leg_value(tr.hedge)
    # The hedge neutralises leftover exposure: treat it as the opposite leg for the unmatched size.
    gross = (sq - bq) * quote_usd_rate
    if tr.hedge is not None:
        gross += (hq if tr.hedge.request.side is OrderSide.SELL else -hq) * quote_usd_rate
    fees = (bfee + sfee + hfee) * quote_usd_rate
    gas = bgas + sgas + hgas
    funding = D(tr.events and 0)
    net = gross - fees - gas - funding
    est = estimated or {}
    est_slip = D(est.get("slippage", 0)) + D(est.get("price_impact", 0))
    est_fees = D(est.get("buy_trading_fee", 0)) + D(est.get("sell_trading_fee", 0)) + D(est.get("dex_swap_fee", 0))
    est_gas = D(est.get("gas", 0)) + D(est.get("priority_fee", 0))
    # realized slippage vs quoted execution prices
    slip_var = ZERO
    if tr.buy and tr.buy.avg_price and tr.buy.request.extra.get("quoted_price"):
        slip_var += (tr.buy.avg_price - D(tr.buy.request.extra["quoted_price"])) * bf
    if tr.sell and tr.sell.avg_price and tr.sell.request.extra.get("quoted_price"):
        slip_var += (D(tr.sell.request.extra["quoted_price"]) - tr.sell.avg_price) * sf
    matched = min(bf, sf + (hf if tr.hedge and tr.hedge.request.side is OrderSide.SELL else ZERO))
    unmatched = abs(bf - sf - (hf if tr.hedge and tr.hedge.request.side is OrderSide.SELL else -hf if tr.hedge else ZERO))
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
    )


def apply_outcome(tr: TradeRecord, out: TradeOutcome) -> None:
    tr.actual_gross = out.actual_gross
    tr.actual_fees = out.trading_fees
    tr.actual_gas = out.gas
    tr.actual_net = out.actual_net
    tr.prediction_error = out.prediction_error
    if tr.status not in (TradeStatus.FAILED, TradeStatus.ABORTED, TradeStatus.RECOVERING, TradeStatus.HEDGED):
        tr.status = TradeStatus.FILLED if out.unmatched_base == 0 else TradeStatus.PARTIAL
