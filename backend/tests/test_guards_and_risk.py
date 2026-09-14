import asyncio
from decimal import Decimal

import pytest

from fedr.config.schema import RiskProfile, default_settings
from fedr.core.enums import Chain, CircuitBreakerReason, OrderSide, Strategy, VenueHealth
from fedr.engine.circuit_breakers import CircuitBreakerManager
from fedr.engine.gas_guard import GasBaseline, GasGuard
from fedr.engine.latency_guard import LatencyGuard
from fedr.engine.risk_engine import PortfolioState, RiskEngine
from fedr.engine.slippage_guard import SlippageGuard
from tests.conftest import cex_quote, dex_quote, gas_snapshot

D = Decimal


def test_slippage_limits_asset_venue_route_size(settings):
    g = SlippageGuard(settings.slippage)
    assert g.limit_for("BTC", "kraken", "kraken->coinbase", D("50")) == D("0.15")  # asset-specific beats default
    settings.slippage.per_venue_max_pct["kraken"] = D("0.05")
    assert g.limit_for("SOL", "kraken", "x", D("50")) == D("0.05")
    settings.slippage.per_route_max_pct["a->b"] = D("0.02")
    assert g.limit_for("SOL", "z", "a->b", D("50")) == D("0.02")
    # size tiers: >10k notional falls to the tightest tier
    assert g.limit_for("SOL", "z", "q", D("50000")) == D("0.15")


def test_slippage_guard_blocks_excess(settings):
    g = SlippageGuard(settings.slippage)
    leg = cex_quote("kraken", OrderSide.BUY, D("1"), D("100"), avg_price=D("101"))  # 1% slippage
    assert g.check(leg, "kraken->x", D("100"), "SOL")


def test_latency_guard_rejects_stale():
    g = LatencyGuard(1000)
    fresh = cex_quote("a", OrderSide.BUY, D("1"), D("1"))
    stale = cex_quote("b", OrderSide.SELL, D("1"), D("1"), age_ms=5000)
    assert not g.check(fresh)
    assert g.check(fresh, stale)
    tl = g.timeline_for(fresh, stale)
    tl.mark_decision()
    assert tl.as_dict()["data_to_decision_ms"] >= 5000


def test_gas_regimes_and_estimates(settings):
    guard = GasGuard(settings.gas, GasBaseline())
    guard.baseline.seed(Chain.ETHEREUM, D("10"))
    assert guard.regime(Chain.ETHEREUM, D("12")) .value == "normal"
    assert guard.regime(Chain.ETHEREUM, D("16")).value == "elevated"
    assert guard.regime(Chain.ETHEREUM, D("30")).value == "high"
    assert guard.regime(Chain.ETHEREUM, D("50")).value == "extreme"
    snap = gas_snapshot(Chain.ETHEREUM, D("20"), D("3000"), priority=D("1"))
    assert guard.estimate_cost_usd(snap, 100_000) == D("20") * 100_000 / D("1e9") * 3000
    sol = gas_snapshot(Chain.SOLANA, D("0.1"), D("150"))
    assert guard.estimate_cost_usd(sol, 200_000) == (D(5000) + D("0.1") * 200_000) / D("1e9") * 150


def test_gas_guard_max_priority_and_price(settings):
    guard = GasGuard(settings.gas)
    snap = gas_snapshot(Chain.ETHEREUM, D("100"), D("3000"), priority=D("10"))
    a = guard.assess(snap, 100_000, D("1000"))
    assert not a.passed and any("priority" in r for r in a.reasons) and any("gas price" in r for r in a.reasons)
    assert not guard.assess(None, 100_000, D("100")).passed


def _portfolio(**kw) -> PortfolioState:
    base = dict(total_capital_usd=D("10000"), usable_capital_usd=D("8000"), venue_health={"a": VenueHealth.HEALTHY, "b": VenueHealth.HEALTHY}, available_balances={("a", "USDC"): D("5000"), ("b", "SOL"): D("100")})
    base.update(kw)
    return PortfolioState(**base)


def _legs(size=D("2"), px=D("100")):
    return cex_quote("a", OrderSide.BUY, size, px, fee_pct=D("0.1")), cex_quote("b", OrderSide.SELL, size, px * D("1.01"), fee_pct=D("0.1"))


def test_risk_engine_passes_healthy_small_trade(settings):
    buy, sell = _legs()
    r = RiskEngine(settings).assess(strategy=Strategy.CEX_CEX, buy=buy, sell=sell, base="SOL", quote="USDC", notional_usd=D("200"), capital_required_usd=D("200"), portfolio=_portfolio())
    assert r.passed and r.score <= settings.trading.max_risk_score


@pytest.mark.parametrize(
    "kw,needle",
    [
        ({"open_trades": 1}, "concurrent"),
        ({"daily_realized_pnl_usd": D("-60")}, "daily loss"),
        ({"failed_trades_last_hour": 5}, "failed trades"),
        ({"unhedged_seconds": 90}, "unhedged"),
        ({"venue_health": {"a": VenueHealth.DEGRADED, "b": VenueHealth.HEALTHY}}, "degraded"),
        ({"venue_health": {"a": VenueHealth.BLOCKED, "b": VenueHealth.HEALTHY}}, "blocked"),
        ({"available_balances": {("a", "USDC"): D("10"), ("b", "SOL"): D("100")}}, "insufficient USDC"),
        ({"available_balances": {("a", "USDC"): D("5000")}}, "unknown"),
        ({"active_breakers": ["circuit breaker active: gas_spike"]}, "circuit breaker"),
        ({"gas_reserve_ok": {"solana": False}}, "gas reserve"),
    ],
)
def test_risk_engine_hard_limits(settings, kw, needle):
    buy, sell = _legs()
    if "gas_reserve_ok" in kw:
        sell = dex_quote("b", OrderSide.SELL, D("2"), D("101"), D("100.9"), chain=Chain.SOLANA)
    r = RiskEngine(settings).assess(strategy=Strategy.CEX_CEX, buy=buy, sell=sell, base="SOL", quote="USDC", notional_usd=D("200"), capital_required_usd=D("200"), portfolio=_portfolio(**kw))
    assert not r.passed and any(needle in x for x in r.reasons), r.reasons


def test_risk_engine_exposure_and_size_limits(settings):
    buy, sell = _legs(size=D("50"))
    r = RiskEngine(settings).assess(strategy=Strategy.CEX_CEX, buy=buy, sell=sell, base="SOL", quote="USDC", notional_usd=D("5000"), capital_required_usd=D("5000"), portfolio=_portfolio())
    assert any("exceeds max" in x for x in r.reasons)
    r2 = RiskEngine(settings).assess(strategy=Strategy.CEX_CEX, buy=buy, sell=sell, base="SOL", quote="USDC", notional_usd=D("200"), capital_required_usd=D("200"), portfolio=_portfolio(exposure_by_venue_usd={"a": D("4000")}))
    assert any("exposure on a" in x for x in r2.reasons)
    r3 = RiskEngine(settings).assess(strategy=Strategy.CEX_CEX, buy=buy, sell=sell, base="DOGE", quote="USDC", notional_usd=D("200"), capital_required_usd=D("200"), portfolio=_portfolio())
    assert any("allowlist" in x for x in r3.reasons)


def test_risk_engine_degraded_allowed_when_configured(settings):
    settings.risk.allow_degraded_venues = True
    buy, sell = _legs()
    r = RiskEngine(settings).assess(strategy=Strategy.CEX_CEX, buy=buy, sell=sell, base="SOL", quote="USDC", notional_usd=D("200"), capital_required_usd=D("200"), portfolio=_portfolio(venue_health={"a": VenueHealth.DEGRADED, "b": VenueHealth.HEALTHY}))
    assert r.passed and r.score >= 15


def test_flash_loan_risk_limits(settings):
    buy = dex_quote("a", OrderSide.BUY, D("10"), D("2000"), D("2001"), chain=Chain.BASE)
    sell = dex_quote("b", OrderSide.SELL, D("10"), D("2010"), D("2009"), chain=Chain.BASE)
    r = RiskEngine(settings).assess(strategy=Strategy.FLASH_LOAN, buy=buy, sell=sell, base="ETH", quote="USDC", notional_usd=D("20000"), capital_required_usd=D("5"), portfolio=_portfolio(), gas_usd=D("6"), flash_loan_usd=D("30000"))
    assert any("flash loan" in x and "exceeds max" in x for x in r.reasons)
    assert any("flash loan gas" in x for x in r.reasons)


def test_capital_breakdown(settings):
    cb = RiskEngine(settings).capital_breakdown(D("10000"), D("50"), D("100"))
    assert cb["total"] == D("10000") and cb["usable"] + cb["reserved"] == D("10000")
    assert cb["emergency_reserve"] == settings.risk.emergency_reserve_usd


def test_risk_profiles_apply_and_custom_preserves():
    s = default_settings()
    s.apply_risk_profile(RiskProfile.BALANCED)
    assert s.trading.max_trade_size_usd == D("1000") and s.risk.max_concurrent_trades == 2
    s.trading.max_trade_size_usd = D("777")
    s.apply_risk_profile(RiskProfile.CUSTOM)
    assert s.trading.max_trade_size_usd == D("777")
    s.apply_risk_profile(RiskProfile.CONSERVATIVE)
    assert s.trading.max_trade_size_usd == D("250")


def test_live_can_never_be_boot_default():
    s = default_settings("live")
    assert s.general.mode.value == "paper"


def test_circuit_breakers_trip_reset_and_counters():
    async def run():
        changes = []

        async def on_change(active):
            changes.append(len(active))

        cb = CircuitBreakerManager(on_change=on_change)
        await cb.trip(CircuitBreakerReason.GAS_SPIKE, "gas", scope="chain:base")
        assert cb.blocks(chain="base") and not cb.blocks(venue="kraken")
        assert not cb.is_tripped()  # scoped breaker does not block globally
        await cb.trip(CircuitBreakerReason.BALANCE_DISCREPANCY, "diff")
        assert cb.is_tripped() and len(cb.blocks(venue="x")) == 1
        for _ in range(2):
            assert not await cb.record_and_check("fails", 3, CircuitBreakerReason.REPEATED_ORDER_FAILURE, "fails")
        assert await cb.record_and_check("fails", 3, CircuitBreakerReason.REPEATED_ORDER_FAILURE, "fails")
        n = await cb.reset(CircuitBreakerReason.GAS_SPIKE, scope="chain:base")
        assert n == 1 and len(cb.active) == 2
        await cb.reset()
        assert not cb.active and changes[-1] == 0

    asyncio.run(run())
