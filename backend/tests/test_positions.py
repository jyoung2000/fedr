"""Carry (spot-perp / funding / basis) lifecycle in paper mode: open both legs through the position
manager, mark, accrue funding, apply exit rules, close, book P&L, survive restart, reconcile.
Also: rebalancing method comparison (transfer / bridge / swap) - bridging is never automated.

Everything here runs on synthetic venues + the paper ledger. It verifies the *logic*; it does NOT verify
behaviour against a live derivatives venue (see docs/PRODUCTION_VERIFICATION_MATRIX.md)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest

from fedr.core.enums import CircuitBreakerReason, OrderStatus, Strategy, TradeStatus
from fedr.core.models import OrderResult, now_ms
from fedr.db.models import utcnow
from fedr.engine.inventory import InventoryManager
from fedr.engine.positions import CarryPolicy, PositionManager
from fedr.engine.rebalancer import Rebalancer
from fedr.sim.venues import SyntheticPerpVenue
from tests.test_execution import PAIRS, Harness

D = Decimal
PERP = "binance-perp"
PSYM = "SOL/USDC:USDC"


async def carry_harness(tmp_path=None, *, premium="1.01", with_db=False, policy: CarryPolicy | None = None):
    h = Harness(with_db=with_db)
    await h.start(tmp_path)
    h.settings.strategies.spot_perp = True
    perp = SyntheticPerpVenue(h.market, PERP, PAIRS, D("0.05"))
    perp.premium = D(premium)
    h.ctx.connectors[PERP] = perp
    await perp.connect()
    h.ledger.seed(
        {
            **{v: {a: b.free for a, b in h.ledger.balances(v).items()} for v in h.ledger.venues()},
            PERP: {"USDC": D("5000")},
        }
    )
    h.settings.trading.max_trade_size_usd = D("1000")
    await h.tick()
    await h.hub.ingest(await perp.fetch_order_book(PSYM))  # the app's scan loop ingests PERP books too
    h.pm = PositionManager(h.ctx, h.exec, policy or CarryPolicy(exit_basis_pct=D("0.01")))
    return h, perp


async def refresh_perp_book(h, perp):
    await asyncio.sleep(0.002)
    await h.hub.ingest(await perp.fetch_order_book(PSYM))


async def open_position(h):
    opps = await h.opps.scan()
    carry = [o for o in opps if o.strategy is Strategy.SPOT_PERP and o.is_executable]
    assert carry, [(o.strategy.value, o.block_reasons) for o in opps]
    tr, pos = await h.pm.open_from_opportunity(carry[0], trigger="test")
    assert tr.status is TradeStatus.FILLED, tr.explanation
    assert pos is not None and pos.status == "open"
    return tr, pos


def test_open_reserves_quote_collateral_and_tracks_position():
    async def run():
        h, perp = await carry_harness()
        tr, pos = await open_position(h)
        notional = tr.sell.quote_amount
        usdc = h.ledger.get(PERP, "USDC")
        coll = h.ledger.get(PERP, "USDC:collateral")
        assert usdc.used == 0, "reservation must be fully released or moved to collateral"
        assert abs(coll.free - notional) < D("0.01")
        assert abs(usdc.free - (D("5000") - notional - tr.sell.fee_quote)) < D("0.01")
        assert h.ledger.get(PERP, "SOL").free == 0  # a perp short never creates base inventory
        assert D(pos.entry_basis_pct) > D("0.9")  # ~1% premium captured
        m = await h.pm.mark(pos)
        assert m["liquidation_distance_pct"] > D("80")  # 1x short: liquidation far above entry
        assert h.pm.exit_reason(pos, m) is None

    asyncio.run(run())


def test_funding_accrual_and_negative_streak():
    async def run():
        h, perp = await carry_harness()
        _, pos = await open_position(h)
        await h.pm.accrue_funding(pos, D("0.0001"))
        assert D(pos.funding_collected_usd) > 0
        assert pos.payload["negative_funding_streak"] == 0
        await h.pm.accrue_funding(pos, D("-0.0002"))
        await h.pm.accrue_funding(pos, D("-0.0002"))
        assert pos.payload["negative_funding_streak"] == 2
        assert len(pos.payload["funding_periods"]) == 3
        m = await h.pm.mark(pos)
        assert "funding negative" in (h.pm.exit_reason(pos, m) or "")
        # due-funding scheduler: nothing due right after opening, due once the interval elapsed
        calls = []

        async def provider(venue, symbol):
            calls.append((venue, symbol))
            return D("0.0003")

        assert await h.pm.accrue_due_funding(provider) == 0
        pos.payload = {**pos.payload, "last_funding_ms": now_ms() - 9 * 3_600_000}
        assert await h.pm.accrue_due_funding(provider) == 1
        assert calls == [(PERP, PSYM)]

    asyncio.run(run())


def test_exit_rules_max_hold_liquidation_and_basis_widening():
    async def run():
        h, perp = await carry_harness()
        _, pos = await open_position(h)
        m = await h.pm.mark(pos)
        pos.opened_at = utcnow() - timedelta(hours=73)
        assert "max hold" in h.pm.exit_reason(pos, await h.pm.mark(pos))
        pos.opened_at = utcnow()
        # perp rallies 60% against the short: liquidation distance shrinks below the 25% minimum
        perp.premium = D("1.60")
        await refresh_perp_book(h, perp)
        m = await h.pm.mark(pos)
        assert m["liquidation_distance_pct"] < D("25")
        assert "liquidation distance" in h.pm.exit_reason(pos, m)
        # basis widening stop (perp premium 3% vs 1% at entry)
        perp.premium = D("1.03")
        await refresh_perp_book(h, perp)
        m = await h.pm.mark(pos)
        assert "basis widened" in h.pm.exit_reason(pos, m)

    asyncio.run(run())


def test_close_on_convergence_books_pnl_and_returns_collateral(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True, policy=CarryPolicy(exit_basis_pct=D("0.1")))
        before_total = h.total_value()  # only USDC + the seeded SOL: comparable with the post-close state
        tr, pos = await open_position(h)
        perp.premium = D("1.0")  # basis converges to zero
        await refresh_perp_book(h, perp)
        marks = await h.pm.monitor()
        assert marks and "basis converged" in marks[0]["exit_reason"]
        assert pos.status == "closed" and pos.id not in h.pm.open
        assert D(pos.realized_net_usd) > 0, pos.realized_net_usd  # 1% basis captured minus fees
        assert h.ledger.get(PERP, "USDC:collateral").free == 0
        assert h.ledger.get(PERP, "USDC").used == 0
        # ledger conservation: the whole round trip moved the paper capital by exactly the realized net
        assert abs((h.total_value() - before_total) - D(pos.realized_net_usd)) < D("0.01")
        # persisted: position row + close trade + P&L
        rows = await h.repo.list_positions(h.ctx.mode)
        assert rows and rows[0].status == "closed" and rows[0].exit_reason.startswith("basis converged")
        pnl = await h.repo.pnl_summary(h.ctx.mode)
        assert D(pnl["today_net"]) > 0
        trades = await h.repo.list_trades(h.ctx.mode, limit=5)
        assert any(t.id.startswith("cls_") for t in trades)

    asyncio.run(run())


def test_incomplete_close_trips_breaker_and_keeps_position(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True)
        _, pos = await open_position(h)
        orig = h.ctx.paper_executor.execute_leg

        async def failing_leg(connector, req, quote):
            if connector.name == PERP and req.reduce_only:
                res = OrderResult(
                    request=req, order_id="fail", status=OrderStatus.REJECTED, error="venue down"
                )
                res.completed_at_ms = now_ms()
                return res
            return await orig(connector, req, quote)

        h.ctx.paper_executor.execute_leg = failing_leg
        tr = await h.pm.close(pos, "manual test")
        assert tr.status is TradeStatus.RECOVERING
        assert pos.status == "closing" and pos.id in h.pm.open
        assert h.breakers.is_tripped("strategy:carry")
        assert any(b.reason is CircuitBreakerReason.POSITION_DISCREPANCY for b in h.breakers._active.values())
        assert h.ledger.get(PERP, "USDC:collateral").free > 0  # perp leg still open

    asyncio.run(run())


def test_emergency_stop_suspends_auto_exits_but_manual_close_works():
    async def run():
        h, perp = await carry_harness(policy=CarryPolicy(exit_basis_pct=D("0.1")))
        _, pos = await open_position(h)
        perp.premium = D("1.0")
        await refresh_perp_book(h, perp)
        h.ctx.emergency_stop = True
        marks = await h.pm.monitor()
        assert marks[0]["exit_reason"] is None and "suspended" in marks[0]["suspended"]
        assert pos.status == "open"
        tr = await h.pm.close(pos, "manual close", trigger="user")
        assert tr.status is TradeStatus.FILLED and pos.status == "closed"

    asyncio.run(run())


def test_positions_survive_restart_and_reconcile_mismatch(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True)
        _, pos = await open_position(h)
        pm2 = PositionManager(h.ctx, h.exec)
        await pm2.load()
        assert pos.id in pm2.open and pm2.open[pos.id].size_base == pos.size_base
        # venue reports no short at all -> discrepancy breaker
        problems = await pm2.reconcile({})
        assert problems and h.breakers.is_tripped()
        assert any(b.reason is CircuitBreakerReason.POSITION_DISCREPANCY for b in h.breakers._active.values())
        assert "expected short" in problems[0]

    asyncio.run(run())


def test_rebalancer_compares_methods_and_never_automates_bridging():
    inv = InventoryManager(lambda a: D("1"))
    from fedr.config.schema import default_settings

    st = default_settings("paper").rebalance
    st.recommendations_enabled = True
    st.min_net_benefit_usd = D("1")
    r = Rebalancer(st, inv)
    from fedr.core.models import Balance

    inv.update_balances("kraken", {"USDC": Balance(asset="USDC", free=D("5000"))}, kind="cex")
    inv.update_balances("coinbase", {"USDC": Balance(asset="USDC", free=D("10"))}, kind="cex")
    inv.update_balances("ethereum", {"USDC": Balance(asset="USDC", free=D("100"))}, kind="chain")
    inv.update_balances("solana", {"USDC": Balance(asset="USDC", free=D("2000"))}, kind="chain")
    m = {x["method"]: x for x in r.method_costs("kraken", "coinbase", "USDC", D("1000"))}
    assert set(m) == {"transfer", "bridge", "swap"}
    assert m["transfer"]["available"] and m["bridge"]["available"] is False
    assert all(x["manual"] for x in m.values())  # FEDR never moves capital by itself
    # chain -> chain: a plain transfer is impossible, bridging is estimated but never available
    m2 = {x["method"]: x for x in r.method_costs("solana", "ethereum", "USDC", D("1000"))}
    assert m2["transfer"]["available"] is False and m2["bridge"]["available"] is False
    assert m2["swap"]["available"] is True
    assert r.transfer_cost_usd("solana", "ethereum", "USDC", D("1000")) == m2["swap"]["cost_usd"]
    for _ in range(20):
        r.note_blocked_by_inventory("coinbase", "USDC", D("20"))
    recs = r.recommend()
    assert recs and recs[0].to_venue == "coinbase" and recs[0].method in {"transfer", "swap"}
    assert {a["method"] for a in recs[0].alternatives} == {"transfer", "bridge", "swap"}
    assert recs[0].estimated_cost_usd == min(D(a["cost_usd"]) for a in recs[0].alternatives if a["available"])


@pytest.mark.parametrize("premium", ["1.001"])
def test_carry_not_executable_when_basis_does_not_cover_round_trip_fees(premium):
    async def run():
        h, perp = await carry_harness(premium=premium)
        opps = await h.opps.scan()
        carry = [o for o in opps if o.strategy is Strategy.SPOT_PERP]
        assert carry and not carry[0].is_executable
        assert any("profit" in b.lower() or "net" in b.lower() for b in carry[0].block_reasons), carry[
            0
        ].block_reasons

    asyncio.run(run())
