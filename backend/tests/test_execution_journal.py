"""Executor-level regression tests for audit F02/F03/F04 and invariants A/B/D.

* an unknown leg keeps reservations, quarantines the venue, never triggers a hedge on the assumption
  that it did not fill, and is settled exactly once after resolution;
* a stop / shadow switch during re-quoting or during the journal write results in ZERO transport calls;
* concurrent requests cannot exceed ``max_concurrent_trades``;
* nothing is transmitted when the durable intent cannot be written;
* a restart rebuilds unfinished trades from the journal, restores reservations, resolves them by lookup
  and books them once - without re-submitting.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from fedr.core.enums import CircuitBreakerReason, OrderStatus, TradeStatus, TradingMode
from fedr.core.models import OrderResult, now_ms
from fedr.engine.execution.executor import ExecutionEngine
from tests.test_execution import Harness

D = Decimal


def _has(h, reason, scope=None):
    return any(b.reason is reason and (scope is None or b.scope == scope) for b in h.breakers.active)


class Counter:
    def __init__(self, h):
        self.calls: list[str] = []
        self.orig = h.ctx.paper_executor.execute_leg
        h.ctx.paper_executor.execute_leg = self

    async def __call__(self, connector, req, quote):
        self.calls.append(connector.name)
        return await self.orig(connector, req, quote)


def _unknown(req, why="response lost"):
    r = OrderResult(request=req, order_id="", status=OrderStatus.UNKNOWN, error=why)
    r.raw["unknown_reason"] = why
    return r


def test_unknown_leg_keeps_reservation_quarantines_venue_and_never_hedges():
    async def run():
        h = await Harness().start()
        o = await h.best()
        assert o.is_executable
        sell = o.sell.venue
        orig = h.ctx.paper_executor.execute_leg
        hedge_calls = []

        async def leg(connector, req, quote):
            if req.extra.get("emergency"):
                hedge_calls.append(req)
            if connector.name == sell and not req.extra.get("emergency"):
                return _unknown(req)
            return await orig(connector, req, quote)

        h.ctx.paper_executor.execute_leg = leg
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.UNKNOWN
        assert tr.hedge is None and hedge_calls == []  # no hedge on the assumption of "no fill"
        assert h.inventory.reservation(tr.id)  # capital stays reserved
        assert tr.id in h.ctx.open_trade_ids  # and occupies capacity
        assert _has(h, CircuitBreakerReason.UNRESOLVED_ORDER, f"venue:{sell}")
        assert tr.actual_net is None
        # a new request on the same route is not admitted
        o2 = await h.best()
        tr2 = await h.exec.execute(o2, trigger="test")
        assert tr2.status is TradeStatus.ABORTED
        # the venue later reports the real outcome: the sell filled
        sell_leg = tr.sell

        async def resolve(leg):
            leg.status = OrderStatus.FILLED
            leg.filled = leg.request.amount
            leg.cost = leg.request.amount * D(leg.request.extra["quoted_price"])
            leg.avg_price = D(leg.request.extra["quoted_price"])
            leg.fee_items = [(D("0.01"), "USDC")]
            leg.fees_complete = True
            return leg

        h.ctx.connectors[sell].resolve_order = resolve
        assert await h.exec.resolve_unresolved() == 1
        assert sell_leg.status is OrderStatus.FILLED
        assert tr.status is TradeStatus.FILLED and tr.actual_net is not None
        assert not h.inventory.reservation(tr.id) and tr.id not in h.ctx.open_trade_ids
        assert not _has(h, CircuitBreakerReason.UNRESOLVED_ORDER)
        assert hedge_calls == []  # balanced once resolved: still no hedge

    asyncio.run(run())


def test_leg_timeout_is_unknown_not_failed():
    async def run():
        h = await Harness().start()
        o = await h.best()
        sell = h.ctx.connectors[o.sell.venue]
        sell.max_order_duration_s = lambda req: 0.01
        orig = h.ctx.paper_executor.execute_leg

        async def leg(connector, req, quote):
            if connector is sell:
                await asyncio.sleep(3600)
            return await orig(connector, req, quote)

        h.ctx.paper_executor.execute_leg = leg
        h.settings.paper.latency_ms = 0
        tr = await asyncio.wait_for(h.exec.execute(o, trigger="test"), 30)
        assert tr.sell.status is OrderStatus.UNKNOWN and "timed out" in (tr.sell.error or "")
        assert tr.status is TradeStatus.UNKNOWN  # was FAILED with filled 0

    asyncio.run(run())


def test_emergency_stop_during_requote_prevents_any_transport():
    """Audit F04 reproduction: stop activated after fresh-quote evaluation but before submission."""

    async def run():
        h = await Harness().start()
        o = await h.best()
        calls = Counter(h)
        orig_eval = h.opps.evaluate

        async def evaluate(cand, size=None):
            r = await orig_eval(cand, size=size)
            h.ctx.emergency_stop = True  # the user presses STOP while we re-quote
            return r

        h.opps.evaluate = evaluate
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED and "EMERGENCY STOP" in tr.explanation
        assert calls.calls == []
        assert not h.inventory.reservation(tr.id)

    asyncio.run(run())


def test_shadow_mode_during_requote_prevents_any_transport():
    async def run():
        h = await Harness().start()
        o = await h.best()
        calls = Counter(h)
        orig_eval = h.opps.evaluate

        async def evaluate(cand, size=None):
            r = await orig_eval(cand, size=size)
            h.settings.general.shadow_mode = True
            return r

        h.opps.evaluate = evaluate
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED and calls.calls == []

    asyncio.run(run())


def test_emergency_stop_during_journal_write_prevents_transport_and_releases_capital(tmp_path):
    async def run():
        h = await Harness(with_db=True).start(tmp_path)
        o = await h.best()
        calls = Counter(h)
        before = h.ledger.dump()
        orig_save = h.repo.save_trade

        async def save(tr):
            await orig_save(tr)
            if tr.status is TradeStatus.EXECUTING:
                h.ctx.emergency_stop = True  # stop lands while the intent is being written

        h.repo.save_trade = save
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED and calls.calls == []
        assert all(leg.status is OrderStatus.REJECTED for _r, leg in tr.legs())
        assert h.ledger.dump() == before  # paper reservations returned
        assert not h.inventory.reservation(tr.id) and not h.ctx.open_trade_ids
        rows = await h.repo.orders_for_trade(tr.id)
        assert rows and all(r.status == "rejected" for r in rows)  # journal shows "not submitted"

    asyncio.run(run())


def test_concurrent_requests_cannot_exceed_capacity():
    async def run():
        h = await Harness().start()
        h.settings.risk.max_concurrent_trades = 1
        o = await h.best()
        orig = h.ctx.paper_executor.execute_leg
        admitted = set()

        async def slow(connector, req, quote):
            admitted.add(req.client_order_id[:2] + str(id(req)))
            await asyncio.sleep(0.2)  # both trades are in flight at the same time
            return await orig(connector, req, quote)

        h.ctx.paper_executor.execute_leg = slow
        t1, t2 = await asyncio.gather(h.exec.execute(o, trigger="a"), h.exec.execute(o, trigger="b"))
        statuses = sorted([t1.status.value, t2.status.value])
        assert len(admitted) == 2  # exactly one pair was transmitted
        assert TradeStatus.ABORTED.value in statuses
        aborted = t1 if t1.status is TradeStatus.ABORTED else t2
        assert "max concurrent" in aborted.explanation

    asyncio.run(run())


def test_nothing_is_transmitted_when_the_intent_cannot_be_journaled(tmp_path):
    async def run():
        h = await Harness(with_db=True).start(tmp_path)
        o = await h.best()
        calls = Counter(h)
        before = h.ledger.dump()
        orig_save = h.repo.save_trade

        async def save(tr):
            if tr.status is TradeStatus.EXECUTING:
                raise OSError("disk full")
            await orig_save(tr)

        h.repo.save_trade = save
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED and "journal" in tr.explanation
        assert calls.calls == [] and h.ledger.dump() == before

    asyncio.run(run())


def test_startup_barrier_blocks_admission_until_recovery_completes():
    async def run():
        h = await Harness().start()
        o = await h.best()
        calls = Counter(h)
        h.ctx.admission.recovery_complete = False
        h.ctx.admission.recovery_note = "journal recovery running"
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED and "startup recovery" in tr.explanation
        assert calls.calls == []

    asyncio.run(run())


def test_configuration_generation_change_refuses_admission():
    async def run():
        h = await Harness().start()
        o = await h.best()
        calls = Counter(h)
        orig_eval = h.opps.evaluate

        async def evaluate(cand, size=None):
            r = await orig_eval(cand, size=size)
            h.ctx.admission.bump()  # connectors were rebuilt while we were re-quoting
            return r

        h.opps.evaluate = evaluate
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.ABORTED and "configuration changed" in tr.explanation
        assert calls.calls == []

    asyncio.run(run())


def _go_real(h, mode=TradingMode.TESTNET):
    """Switch a harness to a real-venue code path (no paper executor) with scripted venues."""
    h.settings.general.mode = mode
    h.ctx.paper_executor = None
    for c in h.ctx.connectors.values():
        c.trading_enabled = True


def test_restart_recovers_journaled_trade_resolves_by_lookup_and_books_once(tmp_path):
    async def run():
        h = await Harness(with_db=True).start(tmp_path)
        o = await h.best()
        _go_real(h)
        placed: list[str] = []
        venue_state: dict[str, dict] = {}

        def make_place(name):
            async def place(req):
                placed.append(req.client_order_id)
                px = D(req.extra["quoted_price"])
                venue_state[req.client_order_id] = {"px": px, "amount": req.amount}
                if name == o.sell.venue:
                    raise ConnectionResetError("response lost after the venue accepted the order")
                r = OrderResult(request=req, order_id="v-" + req.client_order_id, status=OrderStatus.FILLED)
                r.filled, r.cost, r.avg_price = req.amount, req.amount * px, px
                r.fee_items, r.fees_complete, r.completed_at_ms = [(D("0.05"), "USDC")], True, now_ms()
                return r

            return place

        for name in (o.buy.venue, o.sell.venue):
            h.ctx.connectors[name].place_order = make_place(name)
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.UNKNOWN and len(placed) == 2
        rows = await h.repo.unresolved_trades(TradingMode.TESTNET)
        assert [r.id for r in rows] == [tr.id]

        # ---- process restart: fresh engine, fresh inventory reservations, same database ----
        h.inventory._reservations.clear()
        h.ctx.open_trade_ids.clear()
        await h.breakers.reset()
        e2 = ExecutionEngine(h.ctx, h.opps)
        sell_venue = h.ctx.connectors[o.sell.venue]
        lookups = []

        async def resolve_unavailable(leg):
            lookups.append(leg.request.client_order_id)
            leg.raw["lookup"] = "unavailable"
            return leg

        sell_venue.resolve_order = resolve_unavailable
        rep = await e2.recover_on_startup()
        assert rep["loaded"] == 1 and rep["still_unknown"] == 1
        assert h.inventory.reservation(tr.id)  # reservation restored from the journal
        assert _has(h, CircuitBreakerReason.UNRESOLVED_ORDER, f"venue:{o.sell.venue}")
        assert len(placed) == 2  # nothing re-submitted

        async def resolve_found(leg):
            st = venue_state[leg.request.client_order_id]
            leg.status, leg.filled = OrderStatus.FILLED, st["amount"]
            leg.cost, leg.avg_price = st["amount"] * st["px"], st["px"]
            leg.order_id, leg.fee_items, leg.fees_complete = "v-x", [(D("0.05"), "USDC")], True
            return leg

        sell_venue.resolve_order = resolve_found
        assert await e2.resolve_unresolved() == 1
        assert len(placed) == 2
        assert not await h.repo.unresolved_trades(TradingMode.TESTNET)
        summary = await h.repo.pnl_summary(TradingMode.TESTNET)
        assert summary["trades"] == 1  # booked exactly once
        assert not h.inventory.reservation(tr.id)
        # replaying the resolution pass is a no-op
        assert await e2.resolve_unresolved() == 0
        assert (await h.repo.pnl_summary(TradingMode.TESTNET))["trades"] == 1

    asyncio.run(run())


def test_restart_with_simulated_legs_in_flight_treats_them_as_not_executed(tmp_path):
    async def run():
        h = await Harness(with_db=True).start(tmp_path)
        o = await h.best()
        orig_save = h.repo.save_trade
        crashed = {}

        async def save(tr):
            await orig_save(tr)
            if tr.status is TradeStatus.EXECUTING and not crashed:
                crashed["id"] = tr.id
                raise asyncio.CancelledError()  # process dies right after the intent is durable

        h.repo.save_trade = save
        try:
            await h.exec.execute(o, trigger="test")
        except asyncio.CancelledError:
            pass
        h.repo.save_trade = orig_save
        tid = crashed["id"]
        assert [r.id for r in await h.repo.unresolved_trades()] == [tid]
        e2 = ExecutionEngine(h.ctx, h.opps)
        h.ctx.open_trade_ids.clear()
        calls = Counter(h)
        await e2.recover_on_startup()
        row = await h.repo.get_trade(tid)
        assert row.status == "failed" and calls.calls == []
        assert not await h.repo.unresolved_trades()

    asyncio.run(run())


def test_operator_can_resolve_an_unknown_leg_with_evidence(tmp_path):
    async def run():
        h = await Harness(with_db=True).start(tmp_path)
        o = await h.best()
        orig = h.ctx.paper_executor.execute_leg

        async def leg(connector, req, quote):
            if connector.name == o.sell.venue:
                return _unknown(req)
            return await orig(connector, req, quote)

        h.ctx.paper_executor.execute_leg = leg
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.UNKNOWN
        cid = tr.sell.request.client_order_id
        try:
            await h.exec.operator_resolve_leg(
                tr.id,
                cid,
                filled=tr.sell.request.amount,
                cost=None,
                fee_amount=None,
                fee_asset=None,
                note="x",
            )
        except ValueError as exc:
            assert "cost" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("a filled leg without cost must be refused")
        await h.exec.operator_resolve_leg(
            tr.id,
            cid,
            filled=D("0"),
            cost=None,
            fee_amount=None,
            fee_asset=None,
            note="venue order history shows the order expired unfilled",
            actor="ops",
        )
        assert tr.sell.status is OrderStatus.CANCELLED
        assert tr.status in (TradeStatus.HEDGED, TradeStatus.RECOVERING)  # real imbalance handled now
        assert tr.sell.raw["operator_resolution"]["actor"] == "ops"
        audit = await h.repo.list_audit(10, "reconciliation")
        assert any("Operator resolved" in a.summary for a in audit)

    asyncio.run(run())
