"""PROFIT GUARD - no trade (paper, testnet or live) may bypass this module.

Design
------
* Prices come from executable quotes only (order-book walks / router quotes).
* ``gross_profit`` is measured at *reference* prices (top of book / pool spot)
  so the UI can show "gross spread"; the executable deviation of each leg is
  then attributed to slippage / price impact / DEX swap fee. This keeps the
  identity  gross - attributed execution costs == executable net  exact and
  avoids double counting fees a router already baked into its output amount.
* Every other cost is subtracted explicitly. If a *required* cost is unknown
  the guard does not assume zero - it blocks (or applies a documented
  conservative fallback when the user configured one).
* Two results are produced: ``expected_net_profit`` and ``worst_case_profit``
  (stressed slippage, fees, gas and doubled allowances). Both must clear their
  thresholds. All decisions are deterministic and explainable in one sentence.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from fedr.config.schema import AppSettings
from fedr.core.enums import Decision, GasRegime, VenueKind
from fedr.core.models import CostBreakdown, ExecutionQuote, GasAssessment, ProfitAssessment
from fedr.core.money import HUNDRED, ONE, ZERO, D, fmt_money, fmt_pct, pct


@dataclass(slots=True)
class FlashLoanTerms:
    provider: str
    loan_amount_usd: Decimal
    fee_pct: Decimal  # e.g. 0.05 for Aave V3
    simulation_passed: bool | None  # None = not run yet
    atomic: bool = True


@dataclass(slots=True)
class ProfitGuardInputs:
    buy: ExecutionQuote
    sell: ExecutionQuote
    settings: AppSettings
    gas: GasAssessment | None = None  # required when either leg is on-chain
    quote_usd_rate: Decimal = ONE  # USD value of one unit of the quote asset
    experience_extra_pct: Decimal = ZERO  # learned extra buffer (% of notional) for this route
    flash_loan: FlashLoanTerms | None = None
    funding_cost_usd: Decimal = ZERO  # for spot/perp, funding & basis strategies (positive = cost)
    bridge_fee_usd: Decimal | None = ZERO  # None = unknown
    withdrawal_fee_usd: Decimal | None = ZERO
    deposit_fee_usd: Decimal | None = ZERO
    stablecoin_haircut_pct: Decimal = ZERO  # when buy/sell quote assets differ (USDC vs USDT)
    now_ms: int | None = None
    round_trips: int = 1  # carry strategies must also unwind: fees are paid twice


class ProfitGuard:
    """Deterministic all-in profit calculator + hard profit rule."""

    def assess(self, inp: ProfitGuardInputs) -> ProfitAssessment:
        s = inp.settings
        t = s.trading
        buy, sell = inp.buy, inp.sell
        reasons: list[str] = []
        unknown: list[str] = []
        rate = D(inp.quote_usd_rate)

        size = min(buy.base_amount, sell.base_amount)
        if size <= 0:
            return self._blocked(inp, ["trade size is zero"], size)
        if buy.base_amount != sell.base_amount:
            reasons.append(
                f"leg sizes differ (buy {buy.base_amount} vs sell {sell.base_amount}); evaluated at {size}"
            )
        if not buy.fully_fillable:
            reasons.append(f"insufficient depth on {buy.venue} to buy {size} (only {buy.depth_available_base} available)")
        if not sell.fully_fillable:
            reasons.append(
                f"insufficient depth on {sell.venue} to sell {size} (only {sell.depth_available_base} available)"
            )

        # ---- freshness ---------------------------------------------------------
        max_age = min(t.max_quote_age_ms, s.risk.max_quote_age_ms)
        age = max(buy.age_ms, sell.age_ms)
        if age > max_age:
            reasons.append(f"quote is stale ({age} ms > {max_age} ms)")

        # ---- reference economics (gross) --------------------------------------------
        # scale quote amounts proportionally if sizes differ (conservative: average price kept)
        buy_notional_ref = size * buy.reference_price * rate
        sell_notional_ref = size * sell.reference_price * rate
        gross = sell_notional_ref - buy_notional_ref
        gross_spread_pct = pct(sell.reference_price - buy.reference_price, buy.reference_price)

        # ---- executable economics ------------------------------------------------------
        buy_exec = size * buy.avg_price * rate
        sell_exec = size * sell.avg_price * rate
        exp = CostBreakdown()
        wc = CostBreakdown()

        # attribute buy-leg deviation
        buy_dev = max(ZERO, buy_exec - buy_notional_ref)
        sell_dev = max(ZERO, sell_notional_ref - sell_exec)
        self._attribute_leg(buy, buy_notional_ref, buy_dev, exp, wc, t, unknown, is_buy=True)
        self._attribute_leg(sell, sell_notional_ref, sell_dev, exp, wc, t, unknown, is_buy=False)

        # ---- gas -----------------------------------------------------------------------
        on_chain = buy.kind is VenueKind.DEX or sell.kind is VenueKind.DEX or inp.flash_loan is not None
        if on_chain:
            if inp.gas is None:
                if s.gas.unknown_gas_policy == "conservative_fallback":
                    exp.gas = s.gas.conservative_gas_fallback_usd
                    wc.gas = s.gas.conservative_gas_fallback_usd * s.gas.stress_multiplier
                    reasons.append(f"gas unknown - conservative fallback {fmt_money(exp.gas)} applied")
                else:
                    unknown.append("gas")
            else:
                exp.gas = inp.gas.expected_gas_cost_usd
                wc.gas = inp.gas.stress_gas_cost_usd
                exp.priority_fee = inp.gas.priority_fee_usd
                wc.priority_fee = inp.gas.priority_fee_usd * s.gas.stress_multiplier
                if not inp.gas.passed:
                    reasons.extend(f"gas guard: {r}" for r in inp.gas.reasons)

        # ---- transfers / funding ---------------------------------------------------------
        for name, val in (
            ("bridge_fee", inp.bridge_fee_usd),
            ("withdrawal_fee", inp.withdrawal_fee_usd),
            ("deposit_fee", inp.deposit_fee_usd),
        ):
            if val is None:
                unknown.append(name)
            else:
                setattr(exp, name, D(val))
                setattr(wc, name, D(val))
        exp.funding = inp.funding_cost_usd
        if inp.funding_cost_usd > 0:
            wc.funding = inp.funding_cost_usd * t.worst_case_fee_multiplier
        else:  # funding *income* (negative cost): worst case assumes only half of it materialises
            wc.funding = inp.funding_cost_usd / 2
        if inp.round_trips > 1:
            for cb in (exp, wc):
                for f in ("buy_trading_fee", "sell_trading_fee", "dex_swap_fee", "slippage", "price_impact"):
                    setattr(cb, f, getattr(cb, f) * inp.round_trips)

        # ---- allowances (percent of notional) -----------------------------------------
        notional = buy_notional_ref
        atomic = inp.flash_loan is not None
        if atomic:
            # Atomic execution: no inventory to rebalance, no partial fills, no inter-leg latency. The real
            # risks are (a) a revert that still burns gas and (b) the on-chain slippage tolerance being consumed.
            exp.rebalance_allowance = wc.rebalance_allowance = ZERO
            exp.latency_allowance = wc.latency_allowance = ZERO
            exp.partial_fill_allowance = wc.partial_fill_allowance = ZERO
            exp.failure_reserve = wc.gas * Decimal("0.5")  # expected cost of reverts (documented assumption: 50% weight)
            wc.failure_reserve = wc.gas  # worst case: a full revert on top of a successful retry
            tol = notional * Decimal(s.flash_loan.max_slippage_bps) / Decimal(10_000)
            wc.slippage += tol  # min-out tolerance fully consumed
        else:
            exp.rebalance_allowance = notional * t.rebalance_allowance_pct / HUNDRED
            wc.rebalance_allowance = exp.rebalance_allowance
            exp.latency_allowance = notional * t.latency_allowance_pct / HUNDRED
            wc.latency_allowance = exp.latency_allowance * 2
            exp.partial_fill_allowance = notional * t.partial_fill_allowance_pct / HUNDRED
            wc.partial_fill_allowance = exp.partial_fill_allowance * 2
            exp.failure_reserve = notional * t.failure_reserve_pct / HUNDRED
            wc.failure_reserve = exp.failure_reserve * 2 + (wc.gas if on_chain else ZERO)  # a failed tx still burns gas
        buffer_pct = t.safety_buffer_pct + max(ZERO, D(inp.experience_extra_pct)) + D(inp.stablecoin_haircut_pct)
        exp.safety_buffer = notional * buffer_pct / HUNDRED
        wc.safety_buffer = exp.safety_buffer

        # ---- flash loan ----------------------------------------------------------------
        capital_required = buy_exec + exp.buy_trading_fee + exp.gas + exp.priority_fee
        if inp.flash_loan is not None:
            fl = inp.flash_loan
            exp.flash_loan_fee = fl.loan_amount_usd * fl.fee_pct / HUNDRED
            wc.flash_loan_fee = exp.flash_loan_fee
            mev_pct = s.flash_loan.mev_reserve_pct if s.flash_loan.mev_aware else ZERO
            exp.mev_reserve = notional * mev_pct / HUNDRED
            wc.mev_reserve = exp.mev_reserve * 2
            capital_required = exp.gas + exp.priority_fee + exp.flash_loan_fee  # borrowed principal is repaid atomically
            if s.flash_loan.require_simulation and fl.simulation_passed is not True:
                reasons.append("flash loan: transaction simulation has not passed")
            if s.flash_loan.require_atomic and not fl.atomic:
                reasons.append("flash loan: route is not atomically executable")
            if fl.loan_amount_usd > s.flash_loan.max_loan_usd:
                reasons.append(f"flash loan {fmt_money(fl.loan_amount_usd)} exceeds maximum {fmt_money(s.flash_loan.max_loan_usd)}")
            if fl.loan_amount_usd > s.risk.max_flash_loan_usd:
                reasons.append("flash loan exceeds risk-engine maximum")

        exp.unknown = list(dict.fromkeys(unknown))
        wc.unknown = list(exp.unknown)

        expected_net = gross - exp.total()
        worst_case = gross - wc.total()

        # ---- thresholds ------------------------------------------------------------------
        min_profit = t.min_profit_usd
        min_worst = t.min_worst_case_profit_usd
        min_roi = t.min_roi_pct
        if inp.flash_loan is not None:
            min_profit = max(min_profit, s.flash_loan.min_net_profit_usd)
        if inp.gas is not None and inp.gas.regime is GasRegime.ELEVATED:
            min_profit = min_profit * s.gas.elevated_profit_multiplier
            min_worst = min_worst * s.gas.elevated_profit_multiplier
        if inp.gas is not None and inp.gas.regime is GasRegime.HIGH and on_chain:
            # HIGH: block marginal on-chain opportunities - require double the thresholds
            min_profit = min_profit * 2
            min_worst = min_worst * 2
        if inp.gas is not None and inp.gas.regime is GasRegime.EXTREME and on_chain:
            reasons.append("gas regime EXTREME - on-chain trading paused")

        roi = pct(expected_net, capital_required) if capital_required > 0 else ZERO

        if exp.unknown:
            reasons.append("unknown cost(s) could not be estimated: " + ", ".join(exp.unknown))
        if expected_net < min_profit:
            reasons.append(
                f"expected net {fmt_money(expected_net)} is below the required minimum {fmt_money(min_profit)}"
            )
        if worst_case < min_worst:
            reasons.append(
                f"worst-case net {fmt_money(worst_case)} is below the required minimum {fmt_money(min_worst)}"
            )
        if roi < min_roi:
            reasons.append(f"expected ROI {fmt_pct(roi)} is below the required minimum {fmt_pct(min_roi)}")
        if on_chain and inp.gas is not None and gross > 0:
            gas_pct = pct(exp.gas + exp.priority_fee, gross)
            if gas_pct > s.gas.max_gas_pct_of_gross:
                reasons.append(f"gas is {fmt_pct(gas_pct)} of gross profit (max {fmt_pct(s.gas.max_gas_pct_of_gross)})")
            if expected_net < s.gas.min_profit_after_gas_usd:
                reasons.append(
                    f"profit after gas {fmt_money(expected_net)} below minimum {fmt_money(s.gas.min_profit_after_gas_usd)}"
                )
        if inp.flash_loan is not None and (exp.gas + exp.priority_fee) > 0:
            ratio = expected_net / (exp.gas + exp.priority_fee)
            if ratio < s.flash_loan.min_profit_gas_ratio:
                reasons.append(
                    f"flash loan profit/gas ratio {ratio:.1f} below minimum {s.flash_loan.min_profit_gas_ratio}"
                )

        decision = Decision.SAFE_TO_EXECUTE if not reasons else Decision.BLOCKED
        expected_pct = pct(expected_net, notional)
        worst_pct = pct(worst_case, notional)
        explanation = self._explain(decision, reasons, expected_net, worst_case, exp, buy, sell, min_worst)
        return ProfitAssessment(
            gross_profit=gross,
            expected_net_profit=expected_net,
            worst_case_profit=worst_case,
            capital_required=capital_required,
            gross_spread_pct=gross_spread_pct,
            expected_net_pct=expected_pct,
            worst_case_pct=worst_pct,
            net_roi_pct=roi,
            required_min_profit=min_profit,
            required_min_worst_case=min_worst,
            required_min_roi_pct=min_roi,
            expected_costs=exp,
            worst_case_costs=wc,
            decision=decision,
            reasons=reasons,
            explanation=explanation,
            buy_execution_price=buy.avg_price,
            sell_execution_price=sell.avg_price,
        )

    # ------------------------------------------------------------------------- helpers
    @staticmethod
    def _attribute_leg(
        leg: ExecutionQuote,
        notional_ref: Decimal,
        deviation: Decimal,
        exp: CostBreakdown,
        wc: CostBreakdown,
        t,
        unknown: list[str],
        *,
        is_buy: bool,
    ) -> None:
        fee_mult = t.worst_case_fee_multiplier
        slip_mult = t.worst_case_slippage_multiplier
        if leg.kind is VenueKind.DEX:
            # Router output already includes the pool fee + price impact -> the deviation *is* the cost.
            fee_part = ZERO
            if leg.fee_pct is not None:
                fee_part = min(deviation, notional_ref * leg.fee_pct / HUNDRED)
            impact_part = ZERO
            if leg.price_impact_pct > 0:
                impact_part = min(deviation - fee_part, notional_ref * leg.price_impact_pct / HUNDRED)
            residual = max(ZERO, deviation - fee_part - impact_part)
            exp.dex_swap_fee += fee_part
            wc.dex_swap_fee += fee_part * fee_mult
            exp.price_impact += impact_part
            wc.price_impact += impact_part * slip_mult
            exp.slippage += residual
            wc.slippage += residual * slip_mult
            if leg.fee_pct is None and deviation == 0 and leg.fee_in_quote is None:
                # A DEX quote with no deviation and no fee information is not trustworthy.
                unknown.append("dex_swap_fee")
        else:
            exp.slippage += deviation
            wc.slippage += deviation * slip_mult
            fee = None
            if leg.fee_in_quote is not None:
                fee = D(leg.fee_in_quote)
            elif leg.fee_pct is not None:
                fee = (notional_ref + (deviation if is_buy else -deviation)) * leg.fee_pct / HUNDRED
            elif t.unknown_fee_fallback_pct is not None:
                fee = notional_ref * t.unknown_fee_fallback_pct / HUNDRED
            if fee is None:
                unknown.append("buy_trading_fee" if is_buy else "sell_trading_fee")
                fee = ZERO
            if is_buy:
                exp.buy_trading_fee += fee
                wc.buy_trading_fee += fee * fee_mult
            else:
                exp.sell_trading_fee += fee
                wc.sell_trading_fee += fee * fee_mult
            if leg.is_maker and leg.fee_pct is not None:
                # If a maker leg does not rest and converts to taker, the difference is a cost.
                taker = D(leg.extra.get("taker_fee_pct", leg.fee_pct))
                diff = max(ZERO, (taker - leg.fee_pct)) * notional_ref / HUNDRED
                exp.maker_taker_adjustment += diff
                wc.maker_taker_adjustment += diff

    @staticmethod
    def _explain(decision, reasons, expected_net, worst_case, exp, buy, sell, min_worst) -> str:
        if decision is Decision.SAFE_TO_EXECUTE:
            return (
                f"Eligible because expected net profit is {fmt_money(expected_net)}, worst-case profit is "
                f"{fmt_money(worst_case)} (required {fmt_money(min_worst)}), gas is {fmt_money(exp.gas + exp.priority_fee)}, "
                f"slippage is {fmt_pct(buy.slippage_pct + sell.slippage_pct)} and all-in costs total {fmt_money(exp.total())}."
            )
        return "Blocked because " + "; ".join(reasons[:3]) + ("." if not reasons[0].endswith(".") else "")

    def _blocked(self, inp: ProfitGuardInputs, reasons: list[str], size: Decimal) -> ProfitAssessment:
        t = inp.settings.trading
        return ProfitAssessment(
            gross_profit=ZERO,
            expected_net_profit=ZERO,
            worst_case_profit=ZERO,
            capital_required=ZERO,
            gross_spread_pct=ZERO,
            expected_net_pct=ZERO,
            worst_case_pct=ZERO,
            net_roi_pct=ZERO,
            required_min_profit=t.min_profit_usd,
            required_min_worst_case=t.min_worst_case_profit_usd,
            required_min_roi_pct=t.min_roi_pct,
            expected_costs=CostBreakdown(),
            worst_case_costs=CostBreakdown(),
            decision=Decision.BLOCKED,
            reasons=reasons,
            explanation="Blocked because " + "; ".join(reasons) + ".",
        )
