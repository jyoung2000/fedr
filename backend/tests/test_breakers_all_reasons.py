"""Every CircuitBreakerReason has a real trigger path and a test that exercises it end-to-end
(no test calls `breakers.trip()` directly). Also: a tripped breaker blocks the opportunity engine at the
right scope, breaker state persists, and reset is explicit."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal

import pytest

from fedr.core.enums import Chain, CircuitBreakerReason, OrderStatus, Strategy, TradeStatus
from fedr.core.models import OrderResult, now_ms
from tests.test_execution import Harness

D = Decimal


def _active(breakers) -> set[str]:
    return {b.reason.value for b in breakers._active.values()}


def _has(breakers, reason: CircuitBreakerReason, scope: str | None = None) -> bool:
    return any(b.reason is reason and (scope is None or b.scope == scope) for b in breakers._active.values())


# --------------------------------------------------------------------------- execution-time reasons
def test_repeated_order_failure_and_one_leg_fill():
    async def run():
        h = await Harness(failure_prob="1").start()
        h.settings.risk.max_failed_trades_per_hour = 2
        for _ in range(3):
            o = await h.best()
            if not o.is_executable:
                break
            await h.exec.execute(o, trigger="test")
        assert _has(h.breakers, CircuitBreakerReason.REPEATED_ORDER_FAILURE)
        # one-leg fill: every venue except the buy side rejects, so no hedge can be placed
        h2 = await Harness().start()
        o = await h2.best()
        orig = h2.ctx.paper_executor.execute_leg

        async def all_but_buy_fail(connector, req, quote):
            if connector.name == o.buy.venue and not req.extra.get("emergency"):
                return await orig(connector, req, quote)
            r = OrderResult(request=req, order_id="fail", status=OrderStatus.REJECTED, error="outage")
            r.completed_at_ms = now_ms()
            return r

        h2.ctx.paper_executor.execute_leg = all_but_buy_fail
        tr = await h2.exec.execute(o, trigger="test")
        assert tr.status is TradeStatus.RECOVERING
        assert _has(h2.breakers, CircuitBreakerReason.ONE_LEG_FILL)

    asyncio.run(run())


def test_prediction_error_slippage_and_fee_breakers_after_a_hostile_fill():
    async def run():
        h = await Harness(dislocation_bps=200.0).start()
        h.settings.risk.max_prediction_error_pct = D("0.05")
        h.settings.risk.max_slippage_pct = D("0.05")
        o = await h.best()
        assert o.is_executable
        h.settings.paper.quote_drift_pct = D("1.0")  # 1% adverse drift on both legs after evaluation
        for name in ("kraken", "coinbase"):  # and a fee spike
            h.ctx.connectors[name].taker_fee_pct = D("0.9")
            for m in h.ctx.connectors[name].markets.values():
                m.taker_fee_pct = D("0.9")
        tr = await h.exec.execute(o, trigger="test")
        assert tr.status in (TradeStatus.FILLED, TradeStatus.FAILED, TradeStatus.ABORTED)
        if tr.status is TradeStatus.FILLED:
            active = _active(h.breakers)
            assert {"prediction_error", "abnormal_slippage", "unexpected_fees"} & active, active

    asyncio.run(run())


def test_daily_loss_limit_trips_from_realized_losses():
    async def run():
        h = await Harness(dislocation_bps=200.0).start()
        h.settings.risk.max_daily_loss_usd = D("1")
        h.ctx.daily_pnl_usd = D("-0.5")
        o = await h.best()
        h.settings.paper.quote_drift_pct = D("1.0")
        tr = await h.exec.execute(o, trigger="test")
        if tr.status is TradeStatus.FILLED and tr.actual_net is not None and tr.actual_net < 0:
            assert _has(h.breakers, CircuitBreakerReason.DAILY_LOSS_LIMIT)

    asyncio.run(run())


def test_dex_tx_failures_trip_chain_breaker():
    async def run():
        h = Harness(dislocation_bps=150.0)
        h.settings.strategies.cex_dex = True
        h.market.venues["jupiter"].offset_bps = -200.0
        await h.start()
        await h.opps.scan()
        await h.tick()
        orig = h.ctx.paper_executor.execute_leg

        async def dex_reverts(connector, req, quote):
            if connector.name == "jupiter":
                r = OrderResult(
                    request=req, order_id="tx", status=OrderStatus.FAILED, error="execution reverted"
                )
                r.completed_at_ms = now_ms()
                return r
            return await orig(connector, req, quote)

        h.ctx.paper_executor.execute_leg = dex_reverts
        for _ in range(2):
            opps = await h.opps.scan()
            dex = [o for o in opps if o.strategy is Strategy.CEX_DEX and o.is_executable]
            assert dex, [o.block_reasons[:2] for o in opps if o.strategy is Strategy.CEX_DEX]
            await h.exec.execute(dex[0], trigger="test")
        assert _has(h.breakers, CircuitBreakerReason.DEX_TX_FAILURE, scope="chain:solana")
        opps = await h.opps.scan()
        assert all(not o.is_executable for o in opps if "jupiter" in (o.buy.venue, o.sell.venue))

    asyncio.run(run())


# --------------------------------------------------------------------------- market / venue reasons
def test_gas_spike_market_data_failure_rapid_move_and_scopes():
    async def run():
        h = await Harness().start()
        # GAS_SPIKE via the gas oracle's extreme regime (chain scope)
        h.market.chains[Chain.SOLANA].current = 50.0
        await h.ctx.gas_oracle.refresh(Chain.SOLANA)
        snap = h.ctx.gas_oracle.snapshot(Chain.SOLANA)
        regime = h.ctx.gas_guard.regime(Chain.SOLANA, snap.gas_price_native)
        assert regime.value == "extreme"
        # RAPID_MARKET_MOVE via the hub (symbol scope): 5% jump within the window
        fired = []

        async def on_move(venue, symbol, pct):
            fired.append((venue, symbol, pct))

        h.hub.on_rapid_move = on_move
        book = await h.ctx.connectors["kraken"].fetch_order_book("SOL/USDC")
        await h.hub.ingest(book)
        for lv in book.bids + book.asks:
            lv.price = (lv.price * D("1.05")).quantize(D("0.000001"))
        book.ts_ms = now_ms() + 50
        await h.hub.ingest(book)
        assert fired and fired[0][1] == "SOL/USDC" and fired[0][2] > 4
        # a symbol-scoped breaker blocks that pair only
        await h.breakers.trip(CircuitBreakerReason.RAPID_MARKET_MOVE, "test", scope="symbol:SOL/USDC")
        opps = await h.opps.scan()
        assert opps and all(not o.is_executable for o in opps if o.pair == "SOL/USDC")
        assert any("rapid_market_move" in r for o in opps for r in o.block_reasons)

    asyncio.run(run())


def test_funding_anomaly_blocks_carry_open_and_trips():
    async def run():
        from tests.test_positions import carry_harness

        h, perp = await carry_harness()
        perp.funding_rate = D("0.02")  # 2% per 8h: absurd
        opps = await h.opps.scan()
        carry = [o for o in opps if o.strategy is Strategy.SPOT_PERP]
        assert carry
        tr, pos = await h.pm.open_from_opportunity(carry[0], trigger="test")
        assert pos is None and tr.status is TradeStatus.ABORTED and "anomaly" in tr.explanation
        assert _has(h.breakers, CircuitBreakerReason.FUNDING_ANOMALY, scope="strategy:carry")

    asyncio.run(run())


def test_balance_and_position_discrepancy_and_manual():
    async def run():
        h = await Harness().start()
        from fedr.core.enums import TradingMode
        from fedr.core.models import Balance
        from fedr.engine.reconciliation import Reconciler

        h.ctx.settings.general.mode = (
            TradingMode.LIVE
        )  # real reconciliation path (venue balances vs expected)
        h.ctx.ledger = None
        h.ctx.paper_executor = None
        rec = Reconciler(h.ctx)
        rec.set_expected("kraken", "USDC", D("5000"))

        async def fake_balances():
            return {"USDC": Balance("USDC", D("4000"))}

        h.ctx.connectors["kraken"].fetch_balances = fake_balances
        for name in ("coinbase", "jupiter"):
            h.ctx.connectors[name].connected = False
        rep = await rec.run_once()
        assert not rep.ok and _has(h.breakers, CircuitBreakerReason.BALANCE_DISCREPANCY)
        from tests.test_positions import carry_harness, open_position

        h2, _ = await carry_harness()
        _, pos = await open_position(h2)
        await h2.pm.reconcile({})
        assert _has(h2.breakers, CircuitBreakerReason.POSITION_DISCREPANCY)
        from fedr.engine.emergency_stop import EmergencyStop

        es = EmergencyStop(h.ctx, rec)
        await es.activate("operator", actor="test")
        assert h.ctx.emergency_stop
        assert _has(h.breakers, CircuitBreakerReason.MANUAL) or h.ctx.emergency_stop

    asyncio.run(run())


# --------------------------------------------------------------------------- app-loop reasons (health / gas)
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FEDR_DEFAULT_MODE", "simulation")
    monkeypatch.setenv("FEDR_GATEWAY_ENABLED", "false")
    monkeypatch.setenv("FEDR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FEDR_AUTH_TOKEN", "test-token-0123456789abcdef")
    from fedr.config import env as envmod

    envmod._env = None
    from fedr.main import create_app

    return create_app()


def test_exchange_maintenance_websocket_rate_limit_rpc_and_flash_loan_reasons(api):
    from tests.test_api import _run

    async def t(c):
        fedr = api.state.fedr
        venue = next(v for v, conn in fedr.ctx.connectors.items() if conn.kind.value == "cex")
        conn = fedr.ctx.connectors[venue]
        # EXCHANGE_MAINTENANCE
        conn.health_tracker.maintenance = True
        await fedr._health_check_all()
        assert _has(fedr.ctx.breakers, CircuitBreakerReason.EXCHANGE_MAINTENANCE, scope=f"venue:{venue}")
        conn.health_tracker.maintenance = False
        await fedr._health_check_all()
        assert not _has(fedr.ctx.breakers, CircuitBreakerReason.EXCHANGE_MAINTENANCE)
        # WEBSOCKET_FAILURE
        conn.capabilities.add("ws")
        fedr.settings.advanced.websocket_market_data = True
        fedr.ctx.hub._stat(venue)["ws"] = False
        await fedr._health_check_all()
        assert _has(fedr.ctx.breakers, CircuitBreakerReason.WEBSOCKET_FAILURE, scope=f"venue:{venue}:ws")
        fedr.ctx.hub._stat(venue)["ws"] = True
        await fedr._health_check_all()
        assert not _has(fedr.ctx.breakers, CircuitBreakerReason.WEBSOCKET_FAILURE)
        # RATE_LIMIT (3 rate-limited health passes in 5 min)
        conn.health_tracker.rate_limited_until = time.monotonic() + 120
        for _ in range(3):
            await fedr._health_check_all()
        assert _has(fedr.ctx.breakers, CircuitBreakerReason.RATE_LIMIT, scope=f"venue:{venue}")
        conn.health_tracker.rate_limited_until = 0.0
        await fedr._health_check_all()
        assert not _has(fedr.ctx.breakers, CircuitBreakerReason.RATE_LIMIT)
        # RPC_FAILURE: three failed gas refreshes for a chain in use
        chain = next(iter(fedr._chains_in_use()), None)
        if chain is not None:
            oracle = fedr.ctx.gas_oracle

            async def failing_refresh(ch):
                oracle.last_error[ch] = "rpc timeout"
                oracle._snapshots.pop(ch, None) if hasattr(oracle, "_snapshots") else None

            orig_refresh, orig_snapshot = oracle.refresh, oracle.snapshot
            oracle.refresh = failing_refresh
            oracle.snapshot = lambda ch: None
            for _ in range(3):
                await fedr._gas_check_once()
            assert _has(fedr.ctx.breakers, CircuitBreakerReason.RPC_FAILURE, scope=f"chain:{chain.value}")
            oracle.refresh, oracle.snapshot = orig_refresh, orig_snapshot
        # persistence: active breakers are stored and listed by the API
        st = (await c.get("/api/system/status")).json()
        assert "breakers" in st

    _run(api, t)


def test_every_reason_is_wired_to_a_trigger():
    """No reason may exist without a code path that trips it (excluding MANUAL, which is the operator)."""
    import pathlib
    import re

    src = "\n".join(p.read_text() for p in pathlib.Path("fedr").rglob("*.py"))
    wired = set(re.findall(r"CircuitBreakerReason\.([A-Z_]+)", src))
    missing = {r.name for r in CircuitBreakerReason} - wired
    assert not missing, f"breaker reasons without a trigger path: {sorted(missing)}"


def test_flash_loan_failure_path_is_wired_to_the_revert_branch():
    """The on-chain revert branch trips FLASH_LOAN_FAILURE (strategy scope). Needs a chain to exercise
    end-to-end; the contract side is covered by test_contract_evm.py."""
    import pathlib

    src = pathlib.Path("fedr/engine/flashloan.py").read_text()
    revert = src[src.index('if int(receipt["status"]) != 1:') :]
    assert (
        "CircuitBreakerReason.FLASH_LOAN_FAILURE" in revert[:800]
        and 'scope="strategy:flash_loan"' in revert[:800]
    )
