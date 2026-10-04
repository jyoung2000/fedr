"""Audit F06 (carry P&L booked at entry AND at exit) and F07 (derivative semantics) regressions.

The audit reproduction: entry booked +7.28 (the entry spread, like a spot arbitrage); the completed
position and the paper ledger agreed on +4.72; the P&L table reported +12.00. These tests require the P&L
table, the position, and the paper ledger's equity change to agree, for full and partial lifecycles, and
the daily-loss limit to apply immediately after a close.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from fedr.core.enums import CircuitBreakerReason, OrderStatus, Strategy, TradeStatus, TradingMode
from fedr.core.models import OrderResult, now_ms
from fedr.engine.positions import CarryPolicy, PositionManager
from tests.test_positions import PERP, PSYM, carry_harness, open_position, refresh_perp_book

D = Decimal
TOL = D("0.0000001")


def _equity(h) -> Decimal:
    # paper equity including the "<quote>:collateral" bucket
    return h.ledger.total_usd(h.hub.prices.get)


async def _booked_total(h) -> Decimal:
    return D((await h.repo.pnl_summary(h.ctx.mode))["total"]["net"])


def test_lifecycle_is_booked_once_and_matches_ledger_equity(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True, policy=CarryPolicy(exit_basis_pct=D("0.1")))
        before = _equity(h)
        tr, pos = await open_position(h)
        entry_booked = await _booked_total(h)
        # only the cash cost of opening (fees) is realized at entry - never the entry spread
        assert entry_booked < 0 and abs(entry_booked + D(pos.fees_usd)) < TOL
        assert D(pos.payload["entry_booked_usd"]) == entry_booked
        await h.pm.accrue_funding(pos, D("0.0001"))  # simulated settlement credited to the paper ledger
        perp.premium = D("1.0")
        await refresh_perp_book(h, perp)
        await h.pm.monitor()
        assert pos.status == "closed"
        booked = await _booked_total(h)
        realized = D(pos.realized_net_usd)
        assert abs(booked - realized) < TOL, (booked, realized)  # booked exactly once in total
        assert abs((_equity(h) - before) - realized) < D("0.01")  # == ledger equity change
        assert abs(h.ctx.daily_pnl_usd - booked) < TOL  # risk totals updated with settlement

    asyncio.run(run())


def test_daily_loss_limit_applies_immediately_after_a_losing_close(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True)
        _, pos = await open_position(h)
        h.settings.risk.max_daily_loss_usd = D("0.01")
        perp.premium = D("1.05")  # perp rallies: closing the short realizes a loss
        await refresh_perp_book(h, perp)
        tr = await h.pm.close(pos, "manual", trigger="user")
        assert tr.status is TradeStatus.FILLED and D(pos.realized_net_usd) < 0
        assert any(b.reason is CircuitBreakerReason.DAILY_LOSS_LIMIT for b in h.breakers.active)

    asyncio.run(run())


def test_partial_close_is_cumulative_and_finishes_with_one_booking(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True)
        before = _equity(h)
        _, pos = await open_position(h)
        orig = h.ctx.paper_executor.execute_leg
        fail = {"perp": True}

        async def leg(connector, req, quote):
            if connector.name == PERP and req.reduce_only and fail["perp"]:
                r = OrderResult(request=req, order_id="x", status=OrderStatus.REJECTED, error="venue busy")
                r.completed_at_ms = now_ms()
                return r
            return await orig(connector, req, quote)

        h.ctx.paper_executor.execute_leg = leg
        tr1 = await h.pm.close(pos, "manual", trigger="user")
        assert tr1.status is TradeStatus.RECOVERING and pos.status == "closing"
        spot_rem, perp_rem = h.pm.remaining(pos)
        assert spot_rem == 0 and perp_rem == D(pos.size_base)
        booked_mid = await _booked_total(h)
        fail["perp"] = False
        tr2 = await h.pm.close(pos, "manual retry", trigger="user")  # only the perp leg is traded now
        assert tr2.sell is None and tr2.buy is not None and tr2.buy.request.amount == D(pos.size_base)
        assert pos.status == "closed" and tr2.status is TradeStatus.FILLED
        booked = await _booked_total(h)
        assert booked != booked_mid
        assert abs(booked - D(pos.realized_net_usd)) < TOL
        assert abs((_equity(h) - before) - D(pos.realized_net_usd)) < D("0.01")
        # restart replay of the close settlement is a no-op
        await h.pm._settle_close_trade(tr2)
        assert await _booked_total(h) == booked

    asyncio.run(run())


def test_two_positions_on_one_perp_venue_release_only_their_own_collateral(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True)
        _, p1 = await open_position(h)
        await h.tick()
        await refresh_perp_book(h, perp)
        _, p2 = await open_position(h)
        coll_before = h.ledger.get(PERP, "USDC:collateral").free
        await h.pm.close(p1, "manual", trigger="user")
        coll_after = h.ledger.get(PERP, "USDC:collateral").free
        p2_coll = D(p2.entry_perp_price) * D(p2.size_base)
        assert abs(coll_after - p2_coll) < D("0.01") and coll_after < coll_before

    asyncio.run(run())


def test_entry_with_unknown_leg_creates_the_position_when_resolved(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True)
        orig = h.ctx.paper_executor.execute_leg
        state = {}

        async def leg(connector, req, quote):
            r = await orig(connector, req, quote)
            if connector.name == PERP and not req.reduce_only:
                state["real"] = r  # the venue filled it, but we lost the answer
                u = OrderResult(request=req, order_id="", status=OrderStatus.UNKNOWN, error="timeout")
                u.raw["unknown_reason"] = "test"
                return u
            return r

        h.ctx.paper_executor.execute_leg = leg
        opps = await h.opps.scan()
        carry = [o for o in opps if o.strategy is Strategy.SPOT_PERP and o.is_executable]
        tr, pos = await h.pm.open_from_opportunity(carry[0], trigger="test")
        assert tr.status is TradeStatus.UNKNOWN and pos is None and not h.pm.open
        assert tr.hedge is None  # no hedge on the assumption that the perp leg did not fill

        async def resolve(leg_):
            real = state["real"]
            for f in ("status", "filled", "avg_price", "fills", "cost", "fees_complete", "fee_quote"):
                setattr(leg_, f, getattr(real, f))
            return leg_

        perp.resolve_order = resolve
        assert await h.exec.resolve_unresolved() == 1
        assert len(h.pm.open) == 1
        pos = next(iter(h.pm.open.values()))
        assert pos.payload["trade_id"] == tr.id and D(pos.size_base) == tr.sell.filled

    asyncio.run(run())


def test_live_carry_requires_verified_derivative_settings(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True)
        opps = await h.opps.scan()
        carry = [o for o in opps if o.strategy is Strategy.SPOT_PERP and o.is_executable][0]
        h.settings.general.mode = TradingMode.TESTNET
        h.ctx.paper_executor = None
        for c in h.ctx.connectors.values():
            c.trading_enabled = True
        placed = []

        async def place(req):  # must never be reached
            placed.append(req)
            raise AssertionError("submitted without verified derivative settings")

        for c in h.ctx.connectors.values():
            c.place_order = place
        tr, pos = await h.pm.open_from_opportunity(carry, trigger="test")
        assert tr.status is TradeStatus.ABORTED and "not verified" in tr.explanation and not placed

        async def verified(symbol):
            return ["leverage 5x configured; FEDR's risk model is 1x"]

        perp.verify_derivative_settings = verified
        tr, pos = await h.pm.open_from_opportunity(carry, trigger="test")
        assert tr.status is TradeStatus.ABORTED and "5x" in tr.explanation and not placed

    asyncio.run(run())


def test_reconcile_is_keyed_by_venue_symbol_and_side(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True)
        _, pos = await open_position(h)
        size = D(pos.size_base)
        ok = await h.pm.reconcile([{"venue": PERP, "symbol": PSYM, "side": "short", "size_base": size}])
        assert ok == []
        # same total size reported on ANOTHER venue: the old symbol-aggregated check passed this
        await h.breakers.reset()
        bad = await h.pm.reconcile(
            [{"venue": "other-perp", "symbol": PSYM, "side": "short", "size_base": size}]
        )
        assert any("expected short" in p for p in bad) and any("untracked" in p for p in bad)
        # a LONG on the perp venue is never mistaken for our short
        await h.breakers.reset()
        bad = await h.pm.reconcile(
            [
                {"venue": PERP, "symbol": PSYM, "side": "short", "size_base": size},
                {"venue": PERP, "symbol": PSYM, "side": "long", "size_base": size},
            ]
        )
        assert any("untracked long" in p for p in bad)

    asyncio.run(run())


def test_live_funding_estimate_is_not_booked_as_realized(tmp_path):
    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True, policy=CarryPolicy(exit_basis_pct=D("0.1")))
        _, pos = await open_position(h)
        h.settings.general.mode = TradingMode.TESTNET  # accrual on a real venue without funding history
        await h.pm.accrue_funding(pos, D("0.0001"))
        assert (
            pos.payload["funding_estimated"]
            and pos.payload["funding_periods"][-1]["source"] == "rate_estimate"
        )
        h.settings.general.mode = TradingMode.SIMULATION
        before = await _booked_total(h)
        perp.premium = D("1.0")
        await refresh_perp_book(h, perp)
        tr = await h.pm.close(pos, "manual", trigger="user")
        assert tr.status is TradeStatus.UNRECONCILED and pos.realized_net_usd is None
        assert await _booked_total(h) == before  # nothing booked on an estimate

    asyncio.run(run())


def test_pre_upgrade_position_books_only_the_remainder(tmp_path):
    """Migration v3 stores what the old code had already booked at entry; the close books the rest."""

    async def run():
        h, perp = await carry_harness(tmp_path, with_db=True, policy=CarryPolicy(exit_basis_pct=D("0.1")))
        _, pos = await open_position(h)
        old_entry_booking = D("7.2821234379517")  # the audit's entry-spread booking
        pos.payload = {**pos.payload, "legacy": True, "entry_booked_usd": str(old_entry_booking)}
        before = await _booked_total(h)
        perp.premium = D("1.0")
        await refresh_perp_book(h, perp)
        await h.pm.monitor()
        booked_now = await _booked_total(h) - before
        assert abs(booked_now - (D(pos.realized_net_usd) - old_entry_booking)) < TOL

    asyncio.run(run())


def test_position_manager_rebinds_hooks():
    async def run():
        h, perp = await carry_harness()
        pm2 = PositionManager(h.ctx, h.exec)
        assert h.exec.carry_opener == pm2._position_from_trade

    asyncio.run(run())
