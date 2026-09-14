"""Profit Guard unit tests + the adversarial ACCEPTANCE tests from the specification."""

from decimal import Decimal

from fedr.core.enums import Chain, Decision, GasRegime, OrderSide
from fedr.core.models import GasAssessment
from fedr.engine.gas_guard import GasGuard
from fedr.engine.profit_guard import FlashLoanTerms, ProfitGuard, ProfitGuardInputs
from tests.conftest import cex_quote, dex_quote, gas_snapshot

D = Decimal


def _passing_gas(expected=D("0.20"), stress=D("0.30"), regime=GasRegime.NORMAL) -> GasAssessment:
    return GasAssessment(
        chain=Chain.SOLANA,
        regime=regime,
        current_gas_cost_usd=expected,
        expected_gas_cost_usd=expected,
        stress_gas_cost_usd=stress,
        gas_pct_of_gross=D("1"),
        passed=True,
    )


def test_identity_gross_minus_costs_equals_executable_net(settings):
    """gross - attributed execution costs == executable proceeds - executable cost (no double counting)."""
    buy = cex_quote("kraken", OrderSide.BUY, D("10"), D("100"), avg_price=D("100.05"), fee_pct=D("0.26"))
    sell = cex_quote("coinbase", OrderSide.SELL, D("10"), D("101"), avg_price=D("100.95"), fee_pct=D("0.60"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings))
    c = a.expected_costs
    executable_net = sell.quote_amount * (1 - D("0.60") / 100) - buy.quote_amount * (1 + D("0.26") / 100)
    attributed = a.gross_profit - c.slippage - c.buy_trading_fee - c.sell_trading_fee
    assert abs(attributed - executable_net) < D("0.0001")
    assert a.gross_profit == D("10")  # (101-100)*10 at reference prices


def test_zero_size_blocked(settings):
    buy = cex_quote("a", OrderSide.BUY, D("0"), D("100"))
    sell = cex_quote("b", OrderSide.SELL, D("0"), D("101"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings))
    assert a.decision is Decision.BLOCKED


def test_unknown_fee_blocks_not_assumed_zero(settings):
    buy = cex_quote("a", OrderSide.BUY, D("10"), D("100"), fee_pct=None)
    sell = cex_quote("b", OrderSide.SELL, D("10"), D("103"), fee_pct=D("0.1"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings))
    assert a.decision is Decision.BLOCKED
    assert "buy_trading_fee" in a.expected_costs.unknown
    assert any("unknown cost" in r for r in a.reasons)


def test_unknown_fee_fallback_applies_when_configured(settings):
    settings.trading.unknown_fee_fallback_pct = D("0.5")
    buy = cex_quote("a", OrderSide.BUY, D("10"), D("100"), fee_pct=None)
    sell = cex_quote("b", OrderSide.SELL, D("10"), D("103"), fee_pct=D("0.1"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings))
    assert not a.expected_costs.unknown
    assert a.expected_costs.buy_trading_fee == D("1000") * D("0.5") / 100


def test_stale_quote_blocks(settings):
    buy = cex_quote("a", OrderSide.BUY, D("10"), D("100"), age_ms=10_000)
    sell = cex_quote("b", OrderSide.SELL, D("10"), D("103"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings))
    assert a.decision is Decision.BLOCKED and any("stale" in r for r in a.reasons)


def test_insufficient_depth_blocks(settings):
    buy = cex_quote("a", OrderSide.BUY, D("10"), D("100"), fully=False)
    sell = cex_quote("b", OrderSide.SELL, D("10"), D("103"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings))
    assert a.decision is Decision.BLOCKED and any("insufficient depth" in r for r in a.reasons)


def test_dex_leg_requires_gas_or_blocks(settings):
    buy = cex_quote("kraken", OrderSide.BUY, D("10"), D("100"), fee_pct=D("0.26"))
    sell = dex_quote("jupiter", OrderSide.SELL, D("10"), D("103"), D("102.7"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=None))
    assert a.decision is Decision.BLOCKED and "gas" in a.expected_costs.unknown


def test_dex_gas_conservative_fallback(settings):
    settings.gas.unknown_gas_policy = "conservative_fallback"
    buy = cex_quote("kraken", OrderSide.BUY, D("10"), D("100"), fee_pct=D("0.26"))
    sell = dex_quote("jupiter", OrderSide.SELL, D("10"), D("103"), D("102.7"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=None))
    assert a.expected_costs.gas == settings.gas.conservative_gas_fallback_usd
    assert "gas" not in a.expected_costs.unknown


def test_dex_attribution_does_not_double_count_router_fee(settings):
    """Router output already includes the pool fee; the deviation is split into fee + impact, not added twice."""
    buy = cex_quote("kraken", OrderSide.BUY, D("10"), D("100"), fee_pct=D("0.26"))
    sell = dex_quote(
        "jupiter", OrderSide.SELL, D("10"), D("103"), D("102.5"), impact_pct=D("0.1"), fee_pct=D("0.30")
    )
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=_passing_gas()))
    c = a.expected_costs
    deviation = (D("103") - D("102.5")) * 10
    assert abs((c.dex_swap_fee + c.price_impact + c.slippage) - deviation) < D("0.0001")
    executable = sell.quote_amount - buy.quote_amount * (1 + D("0.26") / 100)
    attributed = (
        a.gross_profit - c.slippage - c.price_impact - c.dex_swap_fee - c.buy_trading_fee - c.sell_trading_fee
    )
    assert abs(attributed - executable) < D("0.0001")


def test_worst_case_is_never_better_than_expected(settings):
    buy = cex_quote("a", OrderSide.BUY, D("10"), D("100"), avg_price=D("100.1"))
    sell = dex_quote("j", OrderSide.SELL, D("10"), D("103"), D("102.6"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=_passing_gas()))
    assert a.worst_case_profit < a.expected_net_profit < a.gross_profit


def test_roi_threshold(settings):
    settings.trading.min_roi_pct = D("5")  # absurd requirement
    buy = cex_quote("a", OrderSide.BUY, D("10"), D("100"))
    sell = cex_quote("b", OrderSide.SELL, D("10"), D("103"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings))
    assert a.decision is Decision.BLOCKED and any("ROI" in r for r in a.reasons)


def test_elevated_gas_regime_raises_required_profit(settings):
    buy = cex_quote("a", OrderSide.BUY, D("10"), D("100"), fee_pct=D("0.1"))
    sell = dex_quote("j", OrderSide.SELL, D("10"), D("103"), D("102.8"))
    normal = ProfitGuard().assess(
        ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=_passing_gas())
    )
    elevated = ProfitGuard().assess(
        ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=_passing_gas(regime=GasRegime.ELEVATED))
    )
    assert (
        elevated.required_min_profit == normal.required_min_profit * settings.gas.elevated_profit_multiplier
    )


def test_extreme_gas_regime_blocks_on_chain(settings):
    buy = cex_quote("a", OrderSide.BUY, D("10"), D("100"), fee_pct=D("0.1"))
    sell = dex_quote("j", OrderSide.SELL, D("10"), D("110"), D("109.8"))
    a = ProfitGuard().assess(
        ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=_passing_gas(regime=GasRegime.EXTREME))
    )
    assert a.decision is Decision.BLOCKED and any("EXTREME" in r for r in a.reasons)


def test_experience_extra_buffer_reduces_expected(settings):
    buy = cex_quote("a", OrderSide.BUY, D("10"), D("100"))
    sell = cex_quote("b", OrderSide.SELL, D("10"), D("103"))
    base = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings))
    learned = ProfitGuard().assess(
        ProfitGuardInputs(buy=buy, sell=sell, settings=settings, experience_extra_pct=D("0.10"))
    )
    assert learned.expected_net_profit < base.expected_net_profit
    assert learned.expected_costs.safety_buffer > base.expected_costs.safety_buffer


def test_explanation_is_one_sentence(settings):
    buy = cex_quote("kraken", OrderSide.BUY, D("10"), D("100"), fee_pct=D("0.1"))
    sell = cex_quote("coinbase", OrderSide.SELL, D("10"), D("103"), fee_pct=D("0.1"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings))
    assert a.decision is Decision.SAFE_TO_EXECUTE
    assert a.explanation.startswith("Eligible because") and a.explanation.endswith(".")


# ---------------------------------------------------------------------------- ACCEPTANCE TESTS


def test_acceptance_1_headline_profit_but_true_profit_below_minimum(settings):
    """Gross +$20 looks great, but fees/gas/slippage/impact/rebalance/safety leave ~$1 (< $5 minimum) -> BLOCK."""
    settings.trading.min_profit_usd = D("5")
    settings.trading.min_worst_case_profit_usd = D("0")
    settings.trading.rebalance_allowance_pct = D("0.10")
    settings.trading.safety_buffer_pct = D("0.20")
    settings.trading.latency_allowance_pct = D("0")
    settings.trading.partial_fill_allowance_pct = D("0")
    settings.trading.failure_reserve_pct = D("0")
    size = D("10")
    # gross = (102 - 100) * 10 = $20 at reference prices
    buy = cex_quote(
        "kraken", OrderSide.BUY, size, D("100"), avg_price=D("100.40"), fee_pct=D("0.25")
    )  # slippage $4, fee ~$2.5
    sell = dex_quote(
        "jupiter", OrderSide.SELL, size, D("102"), D("101.60"), impact_pct=D("0.20"), fee_pct=D("0.20")
    )  # dex fee ~$2, impact $2
    gas = _passing_gas(expected=D("3.00"), stress=D("4.50"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=gas))
    assert a.gross_profit == D("20")
    assert a.expected_net_profit < D("5")
    assert a.expected_net_profit > D("0")  # still "positive" - and still BLOCKED
    assert a.decision is Decision.BLOCKED
    assert any("below the required minimum" in r for r in a.reasons)


def test_acceptance_2_expected_positive_but_worst_case_negative(settings):
    """Gross +$50, expected +$18, worst case -$3, minimum worst case +$5 -> BLOCK."""
    settings.trading.min_profit_usd = D("5")
    settings.trading.min_worst_case_profit_usd = D("5")
    settings.trading.worst_case_slippage_multiplier = D("3")
    settings.trading.worst_case_fee_multiplier = D("1.25")
    size = D("10")
    buy = cex_quote(
        "kraken", OrderSide.BUY, size, D("100"), avg_price=D("100.90"), fee_pct=D("0.30")
    )  # slippage $9
    sell = cex_quote(
        "coinbase", OrderSide.SELL, size, D("105"), avg_price=D("104.10"), fee_pct=D("0.30")
    )  # slippage $9
    gas = _passing_gas(expected=D("2"), stress=D("6"))
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=gas))
    assert a.gross_profit == D("50")
    assert a.expected_net_profit > D("5")
    assert a.worst_case_profit < D("5")
    assert a.decision is Decision.BLOCKED
    assert any("worst-case" in r for r in a.reasons)


def test_acceptance_3_flash_loan_eligible_when_everything_passes(settings):
    """Flash-loan: gross +$30 on a $2,000 loan; fee, DEX deviation, gas, MEV reserve and the on-chain
    slippage tolerance still leave worst case >= required +$5 and simulation passed -> EXECUTE-ELIGIBLE."""
    settings.flash_loan.enabled = True
    settings.flash_loan.min_net_profit_usd = D("5")
    settings.trading.min_worst_case_profit_usd = D("5")
    settings.trading.min_profit_usd = D("5")
    settings.flash_loan.max_slippage_bps = 30
    settings.flash_loan.min_profit_gas_ratio = D("5")
    size = D("1")
    buy = dex_quote(
        "uniswap-base",
        OrderSide.BUY,
        size,
        D("2000"),
        D("2001"),
        impact_pct=D("0.03"),
        fee_pct=D("0.05"),
        chain=Chain.BASE,
        symbol="ETH/USDC",
    )
    sell = dex_quote(
        "pancakeswap-base",
        OrderSide.SELL,
        size,
        D("2030"),
        D("2028.7"),
        impact_pct=D("0.03"),
        fee_pct=D("0.05"),
        chain=Chain.BASE,
        symbol="ETH/USDC",
    )
    gas = _passing_gas(expected=D("1.00"), stress=D("1.50"))
    fl = FlashLoanTerms(
        provider="aave_v3",
        loan_amount_usd=buy.quote_amount,
        fee_pct=D("0.05"),
        simulation_passed=True,
        atomic=True,
    )
    a = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=gas, flash_loan=fl))
    c, w = a.expected_costs, a.worst_case_costs
    assert a.gross_profit == D("30")
    assert c.flash_loan_fee > 0 and c.mev_reserve > 0 and c.gas == D("1")
    assert c.rebalance_allowance == 0 and c.partial_fill_allowance == 0 and c.latency_allowance == 0
    assert w.slippage >= D("2000") * 30 / 10_000  # on-chain tolerance fully consumed in the worst case
    assert w.failure_reserve == D("1.50")  # a full revert still burns stress gas
    assert a.capital_required < D("10")  # borrowed principal is repaid atomically
    assert a.expected_net_profit > a.worst_case_profit >= D("5"), a.reasons
    assert a.decision is Decision.SAFE_TO_EXECUTE, a.reasons


def test_acceptance_3b_flash_loan_blocked_without_simulation(settings):
    settings.flash_loan.enabled = True
    size = D("10")
    buy = dex_quote("a", OrderSide.BUY, size, D("2000"), D("2001"), chain=Chain.BASE, symbol="ETH/USDC")
    sell = dex_quote("b", OrderSide.SELL, size, D("2010"), D("2008"), chain=Chain.BASE, symbol="ETH/USDC")
    fl = FlashLoanTerms(
        provider="aave_v3",
        loan_amount_usd=buy.quote_amount,
        fee_pct=D("0.05"),
        simulation_passed=None,
        atomic=True,
    )
    a = ProfitGuard().assess(
        ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=_passing_gas(), flash_loan=fl)
    )
    assert a.decision is Decision.BLOCKED and any("simulation" in r for r in a.reasons)


def test_gas_guard_blocks_when_gas_dominates_gross(settings):
    """Gross $10, gas $1.50, allowed 10% of gross -> BLOCK; gas $0.20 -> PASS."""
    guard = GasGuard(settings.gas)
    snap = gas_snapshot(
        Chain.BASE, gas_price=D("30"), native_usd=D("3000")
    )  # 30 gwei * 200k gas = 0.006 ETH = $18
    bad = guard.assess(snap, 200_000, D("10"))
    assert not bad.passed and any("of gross" in r or "exceeds max" in r for r in bad.reasons)
    snap_cheap = gas_snapshot(Chain.BASE, gas_price=D("0.02"), native_usd=D("3000"))  # $0.012
    good = guard.assess(snap_cheap, 200_000, D("10"))
    assert good.passed and good.gas_pct_of_gross < D("10")


def test_gas_becomes_expensive_before_execution_blocks(settings):
    """Same route passes with cheap gas, is blocked once gas spikes (pre-execution re-validation)."""
    buy = cex_quote("kraken", OrderSide.BUY, D("2"), D("3000"), fee_pct=D("0.1"), symbol="ETH/USDC")
    sell = dex_quote(
        "uniswap-base", OrderSide.SELL, D("2"), D("3030"), D("3028"), chain=Chain.BASE, symbol="ETH/USDC"
    )
    guard = GasGuard(settings.gas)
    guard.baseline.seed(Chain.BASE, D("0.02"))
    cheap = guard.assess(gas_snapshot(Chain.BASE, D("0.02"), D("3000")), 300_000, D("60"))
    ok = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=cheap))
    assert ok.decision is Decision.SAFE_TO_EXECUTE, ok.reasons
    spike = guard.assess(gas_snapshot(Chain.BASE, D("40"), D("3000")), 300_000, D("60"))
    blocked = ProfitGuard().assess(ProfitGuardInputs(buy=buy, sell=sell, settings=settings, gas=spike))
    assert blocked.decision is Decision.BLOCKED
    assert any("gas" in r.lower() for r in blocked.reasons)


def test_carry_round_trips_double_fees_and_halve_worst_case_funding_income(settings):
    buy = cex_quote("binance", OrderSide.BUY, D("1"), D("60000"), fee_pct=D("0.1"), symbol="BTC/USDT")
    sell = cex_quote(
        "binance-perp", OrderSide.SELL, D("1"), D("60030"), fee_pct=D("0.05"), symbol="BTC/USDT:USDT"
    )
    one = ProfitGuard().assess(
        ProfitGuardInputs(buy=buy, sell=sell, settings=settings, funding_cost_usd=D("-40"))
    )
    assert one.expected_costs.funding == D("-40") and one.worst_case_costs.funding == D("-20")
    two = ProfitGuard().assess(
        ProfitGuardInputs(buy=buy, sell=sell, settings=settings, funding_cost_usd=D("-40"), round_trips=2)
    )
    assert two.expected_costs.buy_trading_fee == one.expected_costs.buy_trading_fee * 2
