"""Compact JSON shapes for the UI - server-side aggregation, no raw market data streams."""

from __future__ import annotations

from decimal import Decimal

from fedr.core.models import Opportunity, TradeRecord, to_jsonable
from fedr.core.money import fmt_money, fmt_pct, q


def money(v: Decimal | None) -> str | None:
    return None if v is None else str(q(v, 4))


def opportunity_summary(o: Opportunity) -> dict:
    p = o.profit
    return {
        "id": o.id,
        "mode": o.mode.value,
        "strategy": o.strategy.value,
        "pair": o.pair,
        "route": o.route,
        "buy_venue": o.buy.venue,
        "sell_venue": o.sell.venue,
        "size_base": str(o.size_base),
        "notional_usd": o.extra.get("notional_usd"),
        "gross_pct": money(p.gross_spread_pct),
        "gross_usd": money(p.gross_profit),
        "expected_pct": money(p.expected_net_pct),
        "expected_usd": money(p.expected_net_profit),
        "worst_pct": money(p.worst_case_pct),
        "worst_usd": money(p.worst_case_profit),
        "required_usd": money(p.required_min_profit),
        "required_worst_usd": money(p.required_min_worst_case),
        "roi_pct": money(p.net_roi_pct),
        "risk_score": o.risk.score,
        "decision": o.decision.value,
        "status": "SAFE TO EXECUTE" if o.is_executable else "BLOCKED",
        "block_reasons": o.block_reasons,
        "explanation": o.explanation,
        "gas_regime": o.gas.regime.value if o.gas else None,
        "age_ms": max(o.buy.age_ms, o.sell.age_ms),
        "created_at_ms": o.created_at_ms,
        "expires_at_ms": o.expires_at_ms,
        "flash_loan": bool(o.extra.get("flash_loan")),
        "labels": {
            "gross": fmt_pct(p.gross_spread_pct),
            "expected": fmt_pct(p.expected_net_pct),
            "worst": fmt_pct(p.worst_case_pct),
            "expected_usd": fmt_money(p.expected_net_profit),
            "worst_usd": fmt_money(p.worst_case_profit),
        },
    }


def opportunity_detail(o: Opportunity) -> dict:
    p = o.profit
    d = opportunity_summary(o)
    d.update(
        {
            "buy": {
                "venue": o.buy.venue,
                "kind": o.buy.kind.value,
                "symbol": o.buy.symbol,
                "execution_price": money(o.buy.avg_price),
                "reference_price": money(o.buy.reference_price),
                "slippage_pct": money(o.buy.slippage_pct),
                "price_impact_pct": money(o.buy.price_impact_pct),
                "fee_pct": money(o.buy.fee_pct) if o.buy.fee_pct is not None else None,
                "fee_source": o.buy.fee_source,
                "route": o.buy.route,
                "fully_fillable": o.buy.fully_fillable,
                "levels": o.buy.levels_consumed,
                "age_ms": o.buy.age_ms,
                "quote_amount": money(o.buy.quote_amount),
                "chain": o.buy.chain.value if o.buy.chain else None,
            },
            "sell": {
                "venue": o.sell.venue,
                "kind": o.sell.kind.value,
                "symbol": o.sell.symbol,
                "execution_price": money(o.sell.avg_price),
                "reference_price": money(o.sell.reference_price),
                "slippage_pct": money(o.sell.slippage_pct),
                "price_impact_pct": money(o.sell.price_impact_pct),
                "fee_pct": money(o.sell.fee_pct) if o.sell.fee_pct is not None else None,
                "fee_source": o.sell.fee_source,
                "route": o.sell.route,
                "fully_fillable": o.sell.fully_fillable,
                "levels": o.sell.levels_consumed,
                "age_ms": o.sell.age_ms,
                "quote_amount": money(o.sell.quote_amount),
                "min_received": money(o.sell.min_received) if o.sell.min_received is not None else None,
                "chain": o.sell.chain.value if o.sell.chain else None,
            },
            "costs": {k: money(Decimal(v)) for k, v in p.expected_costs.as_dict().items()},
            "worst_case_costs": {k: money(Decimal(v)) for k, v in p.worst_case_costs.as_dict().items()},
            "unknown_costs": p.expected_costs.unknown,
            "capital_required": money(p.capital_required),
            "required_roi_pct": money(p.required_min_roi_pct),
            "gas": to_jsonable(o.gas) if o.gas else None,
            "risk": {
                "score": o.risk.score,
                "passed": o.risk.passed,
                "reasons": o.risk.reasons,
                "factors": to_jsonable(o.risk.factors),
            },
            "alternatives": o.alternatives,
            "carry": o.extra.get("carry"),
            "experience_extra_pct": o.extra.get("experience_extra_pct"),
        }
    )
    return d


def trade_summary(t: TradeRecord) -> dict:
    fees = t.actual_fees
    return {
        "id": t.id,
        "mode": t.mode.value,
        "strategy": t.strategy.value,
        "pair": t.pair,
        "route": t.route,
        "status": t.status.value,
        "size_base": str(t.size_base),
        "estimated_gross": money(t.estimated_gross),
        "estimated_net": money(t.estimated_net),
        "estimated_worst_case": money(t.estimated_worst_case),
        "actual_gross": money(t.actual_gross),
        "actual_fees": money(fees),
        "actual_gas": money(t.actual_gas),
        "actual_net": money(t.actual_net),
        "prediction_error": money(t.prediction_error),
        "explanation": t.explanation,
        "started_at_ms": t.started_at_ms,
        "completed_at_ms": t.completed_at_ms,
    }


def trade_detail(t: TradeRecord) -> dict:
    d = trade_summary(t)
    d["legs"] = {
        k: to_jsonable(v) for k, v in (("buy", t.buy), ("sell", t.sell), ("hedge", t.hedge)) if v is not None
    }
    d["estimated_costs"] = t.estimated_costs
    d["events"] = t.events
    return d


def trade_row_from_db(row) -> dict:
    return {
        "id": row.id,
        "mode": row.mode,
        "strategy": row.strategy,
        "pair": row.pair,
        "route": row.route,
        "status": row.status,
        "size_base": row.size_base,
        "estimated_gross": row.estimated_gross,
        "estimated_net": row.estimated_net,
        "estimated_worst_case": row.estimated_worst_case,
        "actual_gross": row.actual_gross,
        "actual_fees": row.actual_fees,
        "actual_gas": row.actual_gas,
        "actual_net": row.actual_net,
        "prediction_error": row.prediction_error,
        "explanation": row.explanation,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }
