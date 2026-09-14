"""Execution-engine failure tests: one-leg fills, partial fills, DEX/RPC failures, quote expiry,
emergency stop, reconciliation and restart recovery. All run against an in-memory context with
synthetic venues and the paper executor (zero latency)."""
from __future__ import annotations

import asyncio
import random
from decimal import Decimal

import pytest

from fedr.config.env import EnvSettings
from fedr.config.schema import default_settings
from fedr.connectors.base import QuoteExpired, VenueUnavailable
from fedr.connectors.paper.ledger import PaperLedger
from fedr.core.enums import Chain, CircuitBreakerReason, Decision, OrderSide, OrderStatus, TradeStatus, TradingMode, VenueKind
from fedr.core.models import OrderResult, now_ms
from fedr.db.engine import Database
from fedr.db.repo import Repo
from fedr.engine.circuit_breakers import CircuitBreakerManager
from fedr.engine.context import EngineContext
from fedr.engine.emergency_stop import EmergencyStop
from fedr.engine.execution.executor import ExecutionEngine
from fedr.engine.execution.paper_executor import PaperExecutor
from fedr.engine.experience import ExperienceEngine
from fedr.engine.inventory import InventoryManager
from fedr.engine.opportunity import OpportunityEngine
from fedr.engine.rebalancer import Rebalancer
from fedr.engine.reconciliation import Reconciler
from fedr.marketdata.gas import GasOracle
from fedr.marketdata.hub import MarketDataHub
from fedr.sim.synthetic import SyntheticMarket, VenueProfile, ChainProfile
from fedr.sim.venues import SyntheticCexVenue, SyntheticDexVenue

D = Decimal
PAIRS = ["SOL/USDC"]


async def _no_sleep(_: float) -> None:
    return None


class Harness:
    """A tiny in-memory FEDR: two CEX venues with a forced dislocation, one DEX, paper ledger."""

    def __init__(self, *, dislocation_bps: float = 150.0, with_db: bool = False, seed: int = 1, failure_prob: str = "0", partial_prob: str = "0"):
        self.settings = default_settings("paper")
        self.settings.general.mode = TradingMode.SIMULATION
        self.settings.paper.latency_ms = 0
        self.settings.paper.latency_jitter_ms = 0
        self.settings.paper.failure_probability = D(failure_prob)
        self.settings.paper.partial_fill_probability = D(partial_prob)
        self.settings.paper.quote_drift_pct = D("0")
        self.settings.paper.stress_multiplier = D("1")
        self.settings.strategies.cex_dex = True
        self.with_db = with_db
        self.seed = seed
        venues = [
            VenueProfile("kraken", "cex", offset_bps=-dislocation_bps / 2, spread_bps=2.0, depth_scale=2.0, vol_bps=0.0, dislocation_prob=0.0),
            VenueProfile("coinbase", "cex", offset_bps=dislocation_bps / 2, spread_bps=2.0, depth_scale=2.0, vol_bps=0.0, dislocation_prob=0.0),
            VenueProfile("jupiter", "dex", offset_bps=0.0, vol_bps=0.0, dislocation_prob=0.0, chain=Chain.SOLANA, pool_fee_pct=D("0.25"), pool_liquidity_usd=5_000_000),
        ]
        self.market = SyntheticMarket(PAIRS, venues, [ChainProfile(Chain.SOLANA, base_gas=0.05, priority=0.05, native_usd=D("150"), spike_prob=0.0)], seed=seed)
        self.hub = MarketDataHub()
        self.hub.prices.external = self.market.price_usd
        self.inventory = InventoryManager(self.hub.prices.get)
        self.breakers = CircuitBreakerManager()
        self.db = None
        self.repo = None

    async def start(self, tmp_path=None):
        if self.with_db:
            self.db = Database(f"sqlite+aiosqlite:///{tmp_path}/t.db")
            await self.db.init()
            self.repo = Repo(self.db)
        self.ctx = EngineContext(env=EnvSettings(), settings=self.settings, repo=self.repo, hub=self.hub, gas_oracle=GasOracle(self.hub.prices.get, synthetic=self.market), breakers=self.breakers, inventory=self.inventory, experience=ExperienceEngine(self.repo), rebalancer=Rebalancer(self.settings.rebalance, self.inventory))
        self.ctx.connectors["kraken"] = SyntheticCexVenue(self.market, "kraken", PAIRS, D("0.16"))
        self.ctx.connectors["coinbase"] = SyntheticCexVenue(self.market, "coinbase", PAIRS, D("0.16"))
        self.ctx.connectors["jupiter"] = SyntheticDexVenue(self.market, "jupiter", PAIRS, Chain.SOLANA)
        for c in self.ctx.connectors.values():
            await c.connect()
        self.ledger = PaperLedger()
        self.ledger.seed({"kraken": {"USDC": D("5000"), "SOL": D("10")}, "coinbase": {"USDC": D("5000"), "SOL": D("10")}, "solana": {"USDC": D("3000"), "SOL": D("6")}})
        self.ctx.ledger = self.ledger
        self.ctx.paper_executor = PaperExecutor(self.ledger, lambda: self.settings.paper, self.ctx.gas_oracle.snapshot, self.ctx.gas_guard, rng=random.Random(self.seed), sleep=_no_sleep)
        self.opps = OpportunityEngine(self.ctx)
        self.exec = ExecutionEngine(self.ctx, self.opps)
        await self.ctx.gas_oracle.refresh(Chain.SOLANA)
        await self.tick()
        return self

    async def tick(self):
        for c in self.ctx.connectors.values():
            if c.kind is VenueKind.CEX:
                for sym in c.markets:
                    await self.hub.ingest(await c.fetch_order_book(sym))
        for v in self.ledger.venues():
            self.inventory.update_balances(v, self.ledger.balances(v), kind="chain" if v == "solana" else "cex")

    async def best(self):
        opps = await self.opps.scan()
        return opps[0]

    def total_value(self) -> Decimal:
        return self.ledger.total_usd(self.hub.prices.get)


@pytest.fixture
def harness():
    return Harness


def test_dislocation_produces_executable_and_paper_trade_reconciles(harness):
    async def run():
        h = await harness().start()
        o = await h.best()
        assert o.decision is Decision.SAFE_TO_EXECUTE, o.block_reasons
        assert o.buy.venue == "kraken" and o.sell.venue == "coinbase"
        before = h.total_value()
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.FILLED, tr.explanation
        assert tr.actual_net is not None and tr.actual_net > 0
        after = h.total_value()
        assert abs((after - before) - tr.actual_net) < D("0.05")  # ledger moved by the realized net
        assert "Executed" in tr.explanation and "realized net" in tr.explanation
        # reservations released
        for v in ("kraken", "coinbase"):
            for a in ("USDC", "SOL"):
                assert h.ledger.get(v, a).used == 0

    asyncio.run(run())


def test_execution_revalidates_and_aborts_when_spread_disappears(harness):
    async def run():
        h = await harness().start()
        o = await h.best()
        assert o.is_executable
        # market moves before execution: remove the dislocation
        h.market.venues["kraken"].offset_bps = 0.0
        h.market.venues["coinbase"].offset_bps = 0.0
        await h.tick()
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED
        assert "re-validation failed" in tr.explanation
        assert tr.buy is None and tr.sell is None  # nothing submitted

    asyncio.run(run())


def test_one_leg_fill_enters_recovery_and_hedges(harness):
    async def run():
        h = await harness().start()
        o = await h.best()
        assert o.is_executable
        sell_venue = h.ctx.connectors[o.sell.venue]
        orig = h.ctx.paper_executor.execute_leg

        async def failing_leg(connector, req, quote):
            if connector.name == sell_venue.name and not req.extra.get("emergency"):
                res = OrderResult(request=req, order_id="fail", status=OrderStatus.REJECTED, error="simulated venue outage")
                res.completed_at_ms = now_ms()
                return res
            return await orig(connector, req, quote)

        h.ctx.paper_executor.execute_leg = failing_leg
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status in (TradeStatus.HEDGED, TradeStatus.RECOVERING)
        assert tr.buy.filled > 0 and tr.sell.filled == 0
        assert tr.hedge is not None and tr.hedge.filled > 0
        assert tr.status is TradeStatus.HEDGED
        assert any(e["event"] == "one_leg_imbalance" for e in tr.events)
        assert any(e["event"] == "hedge_filled" for e in tr.events)
        assert "neutralised" in tr.explanation
        # base exposure is flat again: SOL total unchanged (within hedge slippage tolerance)
        assert h.ctx.unhedged_since_ms is None

    asyncio.run(run())


def test_one_leg_fill_without_hedge_venue_trips_breaker(harness):
    async def run():
        h = await harness().start()
        o = await h.best()
        assert o.is_executable

        async def all_but_buy_fail(connector, req, quote):
            if connector.name == o.buy.venue and not req.extra.get("emergency"):
                return await orig(connector, req, quote)
            res = OrderResult(request=req, order_id="fail", status=OrderStatus.REJECTED, error="outage")
            res.completed_at_ms = now_ms()
            return res

        orig = h.ctx.paper_executor.execute_leg
        h.ctx.paper_executor.execute_leg = all_but_buy_fail
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.RECOVERING
        assert any(b.reason is CircuitBreakerReason.ONE_LEG_FILL for b in h.breakers.active)
        # a subsequent evaluation is blocked by the breaker
        o2 = await h.best()
        assert o2.decision is Decision.BLOCKED and any("circuit breaker" in r for r in o2.block_reasons)

    asyncio.run(run())


def test_partial_fill_is_hedged_and_recorded(harness):
    async def run():
        h = await harness(partial_prob="1").start()  # every leg partially fills
        h.settings.paper.fill_ratio_on_partial = D("0.5")
        o = await h.best()
        assert o.is_executable
        # make only the sell leg partial
        orig = h.ctx.paper_executor.execute_leg
        state = {"n": 0}

        async def leg(connector, req, quote):
            state["n"] += 1
            if connector.name == o.buy.venue:
                h.settings.paper.partial_fill_probability = D("0")
            else:
                h.settings.paper.partial_fill_probability = D("1") if not req.extra.get("emergency") else D("0")
            return await orig(connector, req, quote)

        h.ctx.paper_executor.execute_leg = leg
        tr = await h.exec.execute(o, trigger="test")
        assert tr.buy.filled > tr.sell.filled > 0
        assert tr.hedge is not None
        assert tr.status in (TradeStatus.HEDGED, TradeStatus.RECOVERING)

    asyncio.run(run())


def test_both_legs_failing_counts_toward_breaker(harness):
    async def run():
        h = await harness(failure_prob="1").start()
        h.settings.risk.max_failed_trades_per_hour = 2
        o = await h.best()
        assert o.is_executable
        tr1 = await h.exec.execute(o, trigger="test")
        assert tr1.status is TradeStatus.FAILED and "Both legs failed" in tr1.explanation
        o = await h.best()
        assert o.is_executable, o.block_reasons
        tr2 = await h.exec.execute(o, trigger="test")
        assert tr2.status is TradeStatus.FAILED
        assert any(b.reason is CircuitBreakerReason.REPEATED_ORDER_FAILURE for b in h.breakers.active)

    asyncio.run(run())


def test_dex_quote_expiry_requotes_or_blocks(harness):
    """A stale DEX quoteId must trigger a requote; if the requote is worse than the limit the leg is rejected."""
    async def run():
        h = await harness().start()
        o = await h.best()
        jup = h.ctx.connectors["jupiter"]
        req_calls = {"n": 0}
        h.ctx.paper_executor = None  # force the live path through a fake connector
        h.ctx.settings.general.mode = TradingMode.TESTNET

        class FakeDex:
            name = "jupiter"
            kind = VenueKind.DEX
            chain = Chain.SOLANA
            connected = True
            trading_enabled = True

            async def get_quote(self, symbol, side, amount, order_book=None):
                q = await jup.get_quote(symbol, side, amount)
                q.avg_price = q.avg_price * D("0.90") if side is OrderSide.SELL else q.avg_price * D("1.10")  # much worse
                return q

            async def place_order(self, req):
                req_calls["n"] += 1
                if req_calls["n"] == 1:
                    raise QuoteExpired("quote stale")
                res = OrderResult(request=req, order_id="x", status=OrderStatus.FILLED, filled=req.amount, avg_price=req.limit_price)
                return res

        from fedr.core.models import OrderRequest

        req = OrderRequest(venue="jupiter", symbol="SOL/USDC", side=OrderSide.SELL, amount=D("1"), limit_price=D("150"), quote_id="old")
        res = await h.exec._submit(FakeDex(), req, o.sell)
        assert res.status is OrderStatus.REJECTED and "requote worse" in (res.error or "")
        assert req_calls["n"] == 1  # never re-submitted a worse quote

    asyncio.run(run())


def test_dex_venue_unavailable_is_a_failed_leg_not_a_crash(harness):
    async def run():
        h = await harness().start()
        o = await h.best()

        async def boom(connector, req, quote):
            raise VenueUnavailable("RPC down")

        h.ctx.paper_executor.execute_leg = boom
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.FAILED and "RPC down" in tr.explanation

    asyncio.run(run())


def test_gas_spike_blocks_on_chain_route_but_not_cex_route(harness):
    async def run():
        h = await harness().start()
        h.market.chains[Chain.SOLANA].current = 50.0  # 1000x baseline
        await h.ctx.gas_oracle.refresh(Chain.SOLANA)
        opps = await h.opps.scan()
        onchain = [x for x in opps if x.sell.venue == "jupiter" or x.buy.venue == "jupiter"]
        cex = [x for x in opps if "jupiter" not in (x.buy.venue, x.sell.venue)]
        assert onchain and all(not x.is_executable for x in onchain)
        assert any("gas" in r.lower() for x in onchain for r in x.block_reasons)
        assert any(x.is_executable for x in cex)

    asyncio.run(run())


def test_emergency_stop_blocks_execution_and_persists(harness, tmp_path):
    async def run():
        h = await harness(with_db=True).start(tmp_path)
        estop = EmergencyStop(h.ctx, Reconciler(h.ctx))
        rep = await estop.activate("test")
        assert h.ctx.emergency_stop and rep.reconciliation is not None
        o = await h.best()
        assert not o.is_executable and any("EMERGENCY STOP" in r for r in o.block_reasons)
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED
        # restart: state must survive
        h2 = await harness(with_db=True).start(tmp_path)
        estop2 = EmergencyStop(h2.ctx, Reconciler(h2.ctx))
        await estop2.restore()
        assert h2.ctx.emergency_stop
        await estop2.release()
        assert not h2.ctx.emergency_stop
        await h2.db.close()
        await h.db.close()

    asyncio.run(run())


def test_reconciliation_pauses_on_discrepancy(harness):
    async def run():
        h = await harness().start()
        h.ctx.settings.general.mode = TradingMode.LIVE  # real reconciliation path
        h.ctx.ledger = None
        h.ctx.paper_executor = None
        rec = Reconciler(h.ctx)
        rec.set_expected("kraken", "USDC", D("5000"))

        async def fake_balances():
            from fedr.core.models import Balance

            return {"USDC": Balance("USDC", D("4000"))}

        h.ctx.connectors["kraken"].fetch_balances = fake_balances
        for name in ("coinbase", "jupiter"):
            h.ctx.connectors[name].connected = False
        rep = await rec.run_once()
        assert not rep.ok and any("expected 5000" in d for d in rep.discrepancies)
        assert any(b.reason is CircuitBreakerReason.BALANCE_DISCREPANCY for b in h.breakers.active)

    asyncio.run(run())


def test_restart_recovery_persists_trades_pnl_and_paper_balances(harness, tmp_path):
    async def run():
        h = await harness(with_db=True).start(tmp_path)
        o = await h.best()
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.FILLED
        await h.repo.save_paper_balances(TradingMode.SIMULATION, h.ledger.dump())
        summary = await h.repo.pnl_summary(TradingMode.SIMULATION)
        assert summary["trades"] == 1 and D(summary["total"]["net"]) == tr.actual_net
        await h.db.close()
        # "restart"
        db2 = Database(f"sqlite+aiosqlite:///{tmp_path}/t.db")
        repo2 = Repo(db2)
        trades = await repo2.list_trades(TradingMode.SIMULATION)
        assert trades and trades[0].id == tr.id and trades[0].status == "filled"
        bals = await repo2.load_paper_balances(TradingMode.SIMULATION)
        assert bals["kraken"]["USDC"][0] == h.ledger.get("kraken", "USDC").free
        detail = await repo2.get_trade(tr.id)
        assert detail.payload["buy"]["fills"] and detail.payload["events"]
        audit = await repo2.list_audit(event_type="execution")
        assert audit and "Executed" in audit[0].summary
        await db2.close()

    asyncio.run(run())


def test_shadow_mode_records_but_never_submits(harness):
    async def run():
        h = await harness().start()
        h.settings.general.shadow_mode = True
        o = await h.best()
        assert o.is_executable
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED and "shadow" in tr.explanation
        assert h.ledger.get("kraken", "USDC").used == 0

    asyncio.run(run())


def test_max_concurrent_trades_respected(harness):
    async def run():
        h = await harness().start()
        h.ctx.open_trade_ids.add("other")
        o = await h.best()
        assert not o.is_executable and any("concurrent" in r for r in o.block_reasons)

    asyncio.run(run())


def test_flash_loan_paper_path_aborts_when_unprofitable_after_refresh(harness):
    async def run():
        from fedr.engine.flashloan import FlashLoanEngine
        from fedr.engine.opportunity import RouteCandidate
        from fedr.core.enums import Strategy

        h = await harness().start()
        h.settings.flash_loan.enabled = True
        h.settings.strategies.flash_loan = True
        h.settings.strategies.dex_dex = True
        fl = FlashLoanEngine(h.ctx, None, EnvSettings())
        h.ctx.flash_loan_simulator = fl.simulate
        # two DEXes on Base (EVM: Aave V3 flash loans) with a large dislocation between them
        h.market.pairs.append("ETH/USDC")
        h.market.fair["ETH/USDC"] = 3200.0
        h.market.chains[Chain.BASE] = ChainProfile(Chain.BASE, base_gas=0.01, priority=0.001, native_usd=D("3200"), spike_prob=0.0)
        h.market.chains[Chain.BASE].current = 0.01
        for name, off in (("uniswap-base", 0.0), ("pancakeswap-base", 250.0)):
            h.market.venues[name] = VenueProfile(name, "dex", offset_bps=off, vol_bps=0.0, dislocation_prob=0.0, chain=Chain.BASE, pool_fee_pct=D("0.30"), pool_liquidity_usd=8_000_000)
            h.ctx.connectors[name] = SyntheticDexVenue(h.market, name, ["ETH/USDC"], Chain.BASE)
            await h.ctx.connectors[name].connect()
        await h.ledger.adjust("base", "ETH", D("0.05"))
        await h.ledger.adjust("base", "USDC", D("100"))
        await h.tick()
        await h.ctx.gas_oracle.refresh(Chain.BASE)
        cand = RouteCandidate(Strategy.FLASH_LOAN, "ETH/USDC", h.ctx.connectors["uniswap-base"], h.ctx.connectors["pancakeswap-base"], flash_loan=True)
        o = await h.opps.evaluate(cand)
        assert o is not None and o.is_executable, o.block_reasons if o else "no evaluation"
        assert o.profit.capital_required < o.buy.quote_amount  # borrowed principal is not our capital
        # route becomes unprofitable after quote refresh -> ABORT BEFORE BROADCAST
        h.market.venues["pancakeswap-base"].offset_bps = 0.0
        tr = await fl.execute(o, h.opps, trigger="test")
        assert tr.status is TradeStatus.ABORTED and "no longer profitable" in tr.explanation
        # restore and execute atomically in paper
        h.market.venues["pancakeswap-base"].offset_bps = 250.0
        o = await h.opps.evaluate(cand)
        assert o is not None and o.is_executable, o.block_reasons
        before = h.ledger.get("base", "USDC").free
        tr = await fl.execute(o, h.opps, trigger="test")
        assert tr.status is TradeStatus.FILLED and tr.actual_net is not None and tr.actual_net > 0, tr.explanation
        assert h.ledger.get("base", "USDC").free > before

    asyncio.run(run())


def test_carry_strategy_is_evaluation_only(harness):
    async def run():
        from fedr.core.enums import Strategy
        from fedr.sim.venues import SyntheticPerpVenue

        h = await harness().start()
        h.settings.strategies.spot_perp = True
        h.ctx.connectors["binance-perp"] = SyntheticPerpVenue(h.market, "binance-perp", PAIRS, D("0.05"))
        await h.ctx.connectors["binance-perp"].connect()
        h.ledger.seed({**{v: {a: b.free for a, b in h.ledger.balances(v).items()} for v in h.ledger.venues()}, "binance-perp": {"USDC": D("5000")}})
        await h.tick()
        opps = await h.opps.scan()
        carry = [o for o in opps if o.strategy is Strategy.SPOT_PERP]
        assert carry, "carry route should be evaluated"
        o = carry[0]
        assert o.extra["carry"] is not None and o.profit.expected_costs.funding <= 0  # funding income modelled
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED and "evaluation-only" in tr.explanation
        assert h.ledger.get("binance-perp", "USDC").used == 0

    asyncio.run(run())
