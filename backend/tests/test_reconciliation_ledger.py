"""Audit F05 / invariant D: reconciliation against a durable ledger.

Reproductions of the audited counterexamples, then the surrounding cases:
1. expected USDC 1,000; the venue reports 900 -> the old code adopted 900 and reported OK;
2. an expected asset disappears from the balance map -> the old code never looked at it;
plus failed observations, failed open-order queries, normal fills, deposits, in-flight work, operator
adjustments, untracked orders and restarts.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from fedr.connectors.base import VenueUnavailable
from fedr.core.enums import CircuitBreakerReason, OrderSide, OrderStatus, Strategy, TradeStatus, TradingMode
from fedr.core.models import Balance, Fill, OrderRequest, OrderResult, TradeRecord
from fedr.engine.reconciliation import Reconciler
from tests.test_execution import Harness

D = Decimal
LIVE = TradingMode.LIVE


async def _live(tmp_path, balances: dict[str, str]):
    h = await Harness(with_db=True).start(tmp_path)
    h.ctx.settings.general.mode = LIVE
    h.ctx.ledger = None
    h.ctx.paper_executor = None
    k = h.ctx.connectors["kraken"]
    k.credentials = {"apiKey": "k"}
    state = {"bal": {a: D(x) for a, x in balances.items()}, "fail": False, "orders": [], "orders_fail": False}

    async def fetch_balances():
        if state["fail"]:
            raise VenueUnavailable("balance endpoint timed out")
        return {a: Balance(a, x) for a, x in state["bal"].items()}

    async def fetch_open_orders(symbol=None):
        if state["orders_fail"]:
            raise VenueUnavailable("open orders endpoint down")
        return list(state["orders"])

    k.fetch_balances = fetch_balances
    k.fetch_open_orders = fetch_open_orders
    for name in ("coinbase", "jupiter"):
        h.ctx.connectors[name].connected = False
    rec = Reconciler(h.ctx, h.exec)
    return h, rec, state


def _breaker(h, scope=None):
    return any(
        b.reason is CircuitBreakerReason.BALANCE_DISCREPANCY and (scope is None or b.scope == scope)
        for b in h.breakers.active
    )


def test_lost_funds_are_never_blessed_by_a_fresh_snapshot(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "1000"})
        assert await rec.ensure_checkpoint("kraken", {"USDC": D("1000")})
        state["bal"]["USDC"] = D("900")  # 100 USDC vanish
        rep1 = await rec.run_once()
        assert not rep1.ok and any("USDC expected 1000" in d for d in rep1.discrepancies)
        rep2 = await rec.run_once()  # the old code had adopted 900 here and reported OK
        assert not rep2.ok and any("USDC expected 1000" in d for d in rep2.discrepancies)
        assert _breaker(h, "global")

    asyncio.run(run())


def test_an_expected_asset_missing_from_the_venue_report_is_a_discrepancy(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "1000", "SOL": "2"})
        await rec.ensure_checkpoint("kraken", {"USDC": D("1000"), "SOL": D("2")})
        del state["bal"]["USDC"]
        rep = await rec.run_once()
        assert not rep.ok and any("no longer reports it" in d for d in rep.discrepancies)

    asyncio.run(run())


def test_failed_observation_is_incomplete_not_ok(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "1000"})
        await rec.ensure_checkpoint("kraken", {"USDC": D("1000")})
        state["fail"] = True
        rep = await rec.run_once()
        assert not rep.ok and rep.status == "incomplete" and rep.incomplete
        assert _breaker(h, "venue:kraken")
        state["fail"] = False
        rep = await rec.run_once()
        assert rep.ok and not _breaker(h, "venue:kraken")  # observable again: venue pause lifted

    asyncio.run(run())


def test_failed_open_order_query_is_incomplete_not_empty(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "1000"})
        await rec.ensure_checkpoint("kraken", {"USDC": D("1000")})
        state["orders_fail"] = True
        rep = await rec.run_once()
        assert not rep.ok and any("open orders could not be observed" in i for i in rep.incomplete)

    asyncio.run(run())


def _filled_trade(price="100", amount="1", fee="0.1") -> TradeRecord:
    req = OrderRequest(
        venue="kraken", symbol="SOL/USDC", side=OrderSide.BUY, amount=D(amount), limit_price=D(price)
    )
    leg = OrderResult(request=req, order_id="v1", status=OrderStatus.FILLED, filled=D(amount))
    leg.fills = [
        Fill(
            order_id="v1",
            venue="kraken",
            symbol="SOL/USDC",
            side=OrderSide.BUY,
            amount=D(amount),
            price=D(price),
            fee_amount=D(fee),
            fee_asset="USDC",
            trade_id="t1",
        )
    ]
    return TradeRecord(
        id="trd_x",
        mode=LIVE,
        strategy=Strategy.CEX_CEX,
        opportunity_id="o",
        pair="SOL/USDC",
        route="r",
        size_base=D(amount),
        status=TradeStatus.FILLED,
        estimated_gross=D(0),
        estimated_net=D(0),
        estimated_worst_case=D(0),
        estimated_costs={},
        buy=leg,
    )


def test_normal_fills_reconcile_and_replay_does_not_double_count(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "1000"})
        await rec.ensure_checkpoint("kraken", {"USDC": D("1000")})
        tr = _filled_trade()
        await rec.record_trade(tr)
        await rec.record_trade(tr)  # replay (restart / repeated hook) is a no-op
        state["bal"] = {"USDC": D("899.9"), "SOL": D("1")}
        rep = await rec.run_once()
        assert rep.ok, rep.discrepancies
        bal = await h.repo.ledger_balances(LIVE)
        assert bal[("kraken", "USDC")] == D("899.9") and bal[("kraken", "SOL")] == D("1")

    asyncio.run(run())


def test_unreconciled_fill_defers_the_account_instead_of_guessing(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "1000"})
        await rec.ensure_checkpoint("kraken", {"USDC": D("1000")})
        tr = _filled_trade()
        tr.buy.fills = []
        tr.buy.cost = D("100")
        tr.buy.fees_complete = False  # the venue never reported the fee
        tr.status = TradeStatus.UNRECONCILED
        await rec.record_trade(tr)
        state["bal"] = {"USDC": D("899.9"), "SOL": D("1")}
        rep = await rec.run_once()
        assert any(d.startswith("kraken") for d in rep.deferred) and not rep.discrepancies
        # restart: the deferral is rebuilt from the persisted trade
        await h.repo.save_trade(tr)
        rec2 = Reconciler(h.ctx, h.exec)
        await rec2.load()
        rep2 = await rec2.run_once()
        assert any(d.startswith("kraken") for d in rep2.deferred)

    asyncio.run(run())


def test_increase_on_idle_account_is_booked_as_an_audited_deposit(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "1000"})
        await rec.ensure_checkpoint("kraken", {"USDC": D("1000")})
        seen = []

        async def on_dep(dep):
            seen.append(dep)

        rec.on_deposit = on_dep
        state["bal"]["USDC"] = D("1500")
        rep = await rec.run_once()
        assert rep.ok and rep.deposits and seen and seen[0]["amount"] == D("500")
        rep = await rec.run_once()
        assert rep.ok and not rep.deposits  # booked once
        rows = await h.repo.list_ledger_entries(LIVE)
        assert any(r.kind == "transfer" and D(r.delta) == D("500") for r in rows)

    asyncio.run(run())


def test_in_flight_work_defers_comparison(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "1000"})
        await rec.ensure_checkpoint("kraken", {"USDC": D("1000")})
        await h.inventory.restore("trd_busy", [("kraken", "USDC", D("100"))])
        h.ctx.open_trade_ids.add("trd_busy")
        state["bal"]["USDC"] = D("900")  # mid-trade movement
        rep = await rec.run_once()
        assert not rep.discrepancies and any("in flight" in d for d in rep.deferred)

    asyncio.run(run())


def test_operator_adjustment_is_explicit_and_audited(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "1000"})
        await rec.ensure_checkpoint("kraken", {"USDC": D("1000")})
        state["bal"]["USDC"] = D("990")
        assert not (await rec.run_once()).ok
        try:
            await rec.adjust("kraken", "USDC", observed=D("990"), note="  ")
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("an adjustment without a note must be refused")
        delta = await rec.adjust(
            "kraken", "USDC", observed=D("990"), note="withdrawal fee charged by the venue", actor="ops"
        )
        assert delta == D("-10")
        assert (await rec.run_once()).ok
        rows = await h.repo.list_ledger_entries(LIVE)
        assert [r.kind for r in rows].count("opening") == 1 and any(r.kind == "adjustment" for r in rows)
        audit = await h.repo.list_audit(10, "reconciliation")
        assert any("adjustment" in a.summary for a in audit)

    asyncio.run(run())


def test_untracked_open_orders_are_discrepancies_journaled_ones_are_not(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "1000"})
        await rec.ensure_checkpoint("kraken", {"USDC": D("1000")})
        state["orders"] = [{"id": "999", "clientOrderId": "someone-else", "symbol": "SOL/USDC"}]
        rep = await rec.run_once()
        assert any("untracked open order" in d for d in rep.discrepancies)
        tr = _filled_trade()
        tr.buy.request.client_order_id = "fb_mine"
        await h.repo.save_trade(tr)
        state["orders"] = [{"id": "1000", "clientOrderId": "fb_mine", "symbol": "SOL/USDC"}]
        rep = await rec.run_once()
        assert not any("untracked" in d for d in rep.discrepancies)

    asyncio.run(run())


def test_activity_without_opening_checkpoint_is_never_silently_baselined(tmp_path):
    async def run():
        h, rec, state = await _live(tmp_path, {"USDC": "900", "SOL": "1"})
        await rec.record_trade(_filled_trade())  # a fill booked before any baseline existed
        assert not await rec.ensure_checkpoint("kraken", {"USDC": D("900")})
        rep = await rec.run_once()
        assert not rep.ok and any("without an opening checkpoint" in i for i in rep.incomplete)

    asyncio.run(run())
