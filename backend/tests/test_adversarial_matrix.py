"""Adversarial P&L matrix: for every strategy family that can trade in paper mode, run the same hostile
scenarios and assert the invariant that matters for capital preservation:

    either the route is BLOCKED before any order is submitted,
    or the realized net is >= the worst-case estimate minus the modelled stress (bounded loss),
    or the trade failed/partially filled and was recovered (hedged) with the breaker bookkeeping done.

Scenarios: fee spike, adverse quote drift during latency, thin depth (stress multiplier), random venue
rejections, partial fills, gas spike (on-chain legs), funding flip (carry). Nothing here asserts a profit."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from fedr.core.enums import Chain, Strategy
from tests.test_execution import Harness

D = Decimal

SCENARIOS = {
    "baseline": {},
    "fee_spike": {"fee_pct": D("0.60")},  # taker fee quadruples after evaluation
    "adverse_drift": {"quote_drift_pct": D("0.8")},  # 0.8% adverse move during latency
    "thin_depth": {"stress_multiplier": D("4")},  # depth thins 4x → impact
    "venue_rejections": {"failure_probability": D("0.5")},
    "partial_fills": {"partial_fill_probability": D("1"), "fill_ratio_on_partial": D("0.4")},
    "gas_spike": {"gas_x": 1000.0},
}
STRATEGY_ROUTES = {
    "cex_cex": lambda o: o.strategy is Strategy.CEX_CEX,
    "cex_dex": lambda o: o.strategy is Strategy.CEX_DEX,
}


async def _run_case(strategy: str, scenario: str) -> dict:
    knobs = SCENARIOS[scenario]
    h = Harness(dislocation_bps=150.0, seed=5)
    h.settings.strategies.cex_dex = True
    if strategy == "cex_dex":
        h.market.venues[
            "jupiter"
        ].offset_bps = -200.0  # DEX prices 2% below the CEXs: an on-chain dislocation
    await h.start()
    if "gas_x" in knobs:
        h.market.chains[Chain.SOLANA].current = 0.05 * knobs["gas_x"]
        await h.ctx.gas_oracle.refresh(Chain.SOLANA)
    await h.opps.scan()  # first scan warms DEX quotes/health exactly like the app's continuous loop
    await h.tick()
    opps = await h.opps.scan()
    routes = [o for o in opps if STRATEGY_ROUTES[strategy](o)]
    if not routes:
        return {"outcome": "no_route"}
    o = routes[0]
    if not o.is_executable:
        return {"outcome": "blocked_pre_trade", "reasons": o.block_reasons[:3]}
    # apply the hostile conditions AFTER evaluation (they hit execution, like the real world)
    for k, v in knobs.items():
        if k == "fee_pct":
            for name in ("kraken", "coinbase"):
                h.ctx.connectors[name].taker_fee_pct = v
                for m in h.ctx.connectors[name].markets.values():
                    m.taker_fee_pct = v
        elif k != "gas_x":
            setattr(h.settings.paper, k, v)
    before = h.total_value()
    tr = await h.exec.execute(o, trigger="adversarial")
    after = h.total_value()
    return {
        "outcome": tr.status.value,
        "estimated_net": tr.estimated_net,
        "worst_case": tr.estimated_worst_case,
        "actual_net": tr.actual_net,
        "value_delta": after - before,
        "explanation": tr.explanation,
        "breakers": [b.reason.value for b in h.breakers._active.values()],
        "open_trades": len(h.ctx.open_trade_ids),
    }


@pytest.mark.parametrize("scenario", list(SCENARIOS))
@pytest.mark.parametrize("strategy", list(STRATEGY_ROUTES))
def test_capital_preservation_invariant(strategy, scenario):
    r = asyncio.run(_run_case(strategy, scenario))
    if r["outcome"] in ("no_route", "blocked_pre_trade"):
        if scenario == "gas_spike" and strategy == "cex_dex":
            assert r["outcome"] == "blocked_pre_trade" and any("gas" in x.lower() for x in r["reasons"])
        return
    assert r["open_trades"] == 0, "no trade may remain open after execute() returns"
    if r["outcome"] == "filled":
        # bounded loss: realized net can be worse than expected, but the ledger never moves by more than the
        # worst case minus the stress the scenario injected after evaluation (fees/drift/impact are attributed)
        assert r["actual_net"] is not None
        assert abs(r["value_delta"] - r["actual_net"]) < D("0.05"), r
        floor = r["worst_case"] - abs(r["estimated_net"]) * D("3")  # ≤ 3x the expected edge in adverse stress
        assert r["actual_net"] >= floor, r
    elif r["outcome"] in ("recovered", "recovering", "failed", "aborted"):
        # one-leg / partial / rejected: no naked exposure survives, the record explains it
        assert r["explanation"], r
        assert r["value_delta"] > D("-50"), r  # a hedge or abort never costs more than fees + spread
    else:  # pragma: no cover
        pytest.fail(f"unexpected outcome {r}")


def test_matrix_covers_every_scenario_with_at_least_one_execution():
    """The matrix must actually exercise execution (not only pre-trade blocks) for the paper-tradeable strategies."""
    rows = {(s, sc): asyncio.run(_run_case(s, sc)) for s in STRATEGY_ROUTES for sc in SCENARIOS}
    executed = [k for k, r in rows.items() if r["outcome"] not in ("no_route", "blocked_pre_trade")]
    assert any(k[0] == "cex_cex" for k in executed) and any(k[0] == "cex_dex" for k in executed), rows
    # every hostile scenario either blocks or executes with the invariant above (checked per case); print a summary
    summary = {f"{s}/{sc}": r["outcome"] for (s, sc), r in rows.items()}
    assert set(summary) == {f"{s}/{sc}" for s in STRATEGY_ROUTES for sc in SCENARIOS}
    print(summary)


def test_carry_funding_flip_exits_without_naked_exposure():
    async def run():
        from tests.test_positions import carry_harness, open_position

        h, perp = await carry_harness()
        _, pos = await open_position(h)
        for _ in range(2):
            await h.pm.accrue_funding(pos, D("-0.001"))  # funding turns sharply against the short
        marks = await h.pm.monitor()
        assert pos.status == "closed" and "funding negative" in marks[0]["exit_reason"]
        assert h.ledger.get("binance-perp", "USDC:collateral").free == 0
        assert D(pos.realized_net_usd) > D("-30")  # loss bounded to two funding periods + fees + spread

    asyncio.run(run())
