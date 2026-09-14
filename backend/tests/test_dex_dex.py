"""DEX↔DEX arbitrage on one chain (two synthetic Solana DEX venues sharing the chain wallet):
evaluation, execution through the paper path with gas on both legs, gas-spike blocking, and ledger
conservation. This is the paper verification for matrix row ST-03; on-chain execution stays blocked."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from fedr.core.enums import Chain, Decision, Strategy, TradeStatus
from tests.test_execution import Harness

D = Decimal


def _harness():
    h = Harness(dislocation_bps=0.0, extra_dex=True)  # CEXs aligned: the only dislocation is raydium -2%
    h.settings.strategies.dex_dex = True
    h.settings.strategies.cex_dex = False
    h.settings.strategies.cex_cex = False
    return h


def test_dex_dex_routes_are_evaluated_same_chain_only_with_gas_on_both_legs():
    async def run():
        h = await _harness().start()
        await h.opps.scan()
        await h.tick()
        opps = [o for o in (await h.opps.scan()) if o.strategy is Strategy.DEX_DEX]
        assert opps, "expected DEX↔DEX candidates between jupiter and raydium"
        assert all({o.buy.venue, o.sell.venue} == {"jupiter", "raydium"} for o in opps)
        best = next((o for o in opps if o.is_executable), None)
        assert best is not None, [o.block_reasons[:3] for o in opps]
        assert best.buy.venue == "raydium" and best.sell.venue == "jupiter"  # buy the cheap venue
        costs = best.profit.expected_costs.as_dict()
        assert D(costs.get("gas", "0")) > 0, "both legs are on-chain: gas must be attributed"
        assert D(costs.get("dex_swap_fee", "0")) > 0  # pool fees on both legs, attributed once each
        assert D(costs.get("buy_trading_fee", "0")) == 0 and D(costs.get("sell_trading_fee", "0")) == 0
        assert best.profit.worst_case_profit <= best.profit.expected_net_profit

    asyncio.run(run())


def test_dex_dex_executes_in_paper_with_conserved_chain_ledger():
    async def run():
        h = await _harness().start()
        await h.opps.scan()
        await h.tick()
        best = next(o for o in await h.opps.scan() if o.strategy is Strategy.DEX_DEX and o.is_executable)
        before = h.total_value()
        tr = await h.exec.execute(best, trigger="test")
        assert tr.status is TradeStatus.FILLED, tr.explanation
        assert tr.actual_net is not None
        after = h.total_value()
        assert abs((after - before) - tr.actual_net) < D("0.05")  # ledger moves exactly by realized net
        assert tr.buy.gas_cost_usd > 0 and tr.sell.gas_cost_usd > 0

    asyncio.run(run())


def test_dex_dex_blocked_by_gas_spike_and_by_disabled_strategy():
    async def run():
        h = await _harness().start()
        await h.opps.scan()
        await h.tick()
        h.market.chains[Chain.SOLANA].current = 50.0  # 1000x gas
        await h.ctx.gas_oracle.refresh(Chain.SOLANA)
        opps = [o for o in (await h.opps.scan()) if o.strategy is Strategy.DEX_DEX]
        assert opps and all(o.decision is Decision.BLOCKED for o in opps)
        assert any("gas" in r.lower() for o in opps for r in o.block_reasons)
        h.settings.strategies.dex_dex = False
        assert not [c for c in h.opps.candidates() if c.strategy is Strategy.DEX_DEX]

    asyncio.run(run())
