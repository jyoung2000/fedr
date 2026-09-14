"""RISK ENGINE - deterministic limits and a 0..100 risk score. AI never overrides it."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from fedr.config.schema import AppSettings
from fedr.core.enums import Chain, Strategy, VenueHealth, VenueKind
from fedr.core.models import ExecutionQuote, RiskAssessment
from fedr.core.money import ZERO, D, fmt_money, pct


@dataclass(slots=True)
class PortfolioState:
    """Snapshot of what the risk engine needs to know about current exposure."""

    total_capital_usd: Decimal = ZERO
    usable_capital_usd: Decimal = ZERO
    exposure_by_venue_usd: dict[str, Decimal] = field(default_factory=dict)
    exposure_by_chain_usd: dict[str, Decimal] = field(default_factory=dict)
    exposure_by_asset_usd: dict[str, Decimal] = field(default_factory=dict)
    open_trades: int = 0
    daily_realized_pnl_usd: Decimal = ZERO
    failed_trades_last_hour: int = 0
    venue_health: dict[str, VenueHealth] = field(default_factory=dict)
    available_balances: dict[tuple[str, str], Decimal] = field(default_factory=dict)  # (venue, asset) -> free
    unhedged_seconds: int = 0
    active_breakers: list[str] = field(default_factory=list)
    gas_reserve_ok: dict[str, bool] = field(default_factory=dict)


class RiskEngine:
    def __init__(self, settings: AppSettings):
        self.settings = settings

    def assess(
        self,
        *,
        strategy: Strategy,
        buy: ExecutionQuote,
        sell: ExecutionQuote,
        base: str,
        quote: str,
        notional_usd: Decimal,
        capital_required_usd: Decimal,
        portfolio: PortfolioState,
        gas_usd: Decimal = ZERO,
        flash_loan_usd: Decimal | None = None,
    ) -> RiskAssessment:
        r = self.settings.risk
        t = self.settings.trading
        reasons: list[str] = []
        factors: dict = {}
        score = 0

        # --- hard limits ----------------------------------------------------------------
        if flash_loan_usd is None and notional_usd > t.max_trade_size_usd:
            reasons.append(
                f"trade size {fmt_money(notional_usd)} exceeds max {fmt_money(t.max_trade_size_usd)}"
            )
        if notional_usd < t.min_trade_size_usd:
            reasons.append(
                f"trade size {fmt_money(notional_usd)} below minimum {fmt_money(t.min_trade_size_usd)}"
            )
        if portfolio.active_breakers:
            reasons.extend(portfolio.active_breakers)
        if portfolio.open_trades >= r.max_concurrent_trades:
            reasons.append(
                f"max concurrent trades reached ({portfolio.open_trades}/{r.max_concurrent_trades})"
            )
        if portfolio.daily_realized_pnl_usd <= -r.max_daily_loss_usd:
            reasons.append(
                f"daily loss limit hit ({fmt_money(portfolio.daily_realized_pnl_usd)} vs -{fmt_money(r.max_daily_loss_usd)})"
            )
        if portfolio.failed_trades_last_hour >= r.max_failed_trades_per_hour:
            reasons.append(f"too many failed trades in the last hour ({portfolio.failed_trades_last_hour})")
        if portfolio.unhedged_seconds > r.max_unhedged_seconds:
            reasons.append(f"unhedged exposure open for {portfolio.unhedged_seconds}s")
        if gas_usd > r.max_gas_usd:
            reasons.append(f"gas {fmt_money(gas_usd)} exceeds risk max {fmt_money(r.max_gas_usd)}")
        if base not in r.asset_allowlist or quote not in r.asset_allowlist:
            reasons.append(f"asset not on allowlist ({base}/{quote})")
        if flash_loan_usd is not None:
            if flash_loan_usd > r.max_flash_loan_usd:
                reasons.append(
                    f"flash loan {fmt_money(flash_loan_usd)} exceeds max {fmt_money(r.max_flash_loan_usd)}"
                )
            if gas_usd > r.max_flash_loan_gas_usd:
                reasons.append(
                    f"flash loan gas {fmt_money(gas_usd)} exceeds max {fmt_money(r.max_flash_loan_gas_usd)}"
                )

        # --- capital usage / exposures ---------------------------------------------------
        total = portfolio.total_capital_usd
        if total > 0:
            usage = pct(capital_required_usd, total)
            factors["capital_usage_pct"] = str(usage)
            if usage > r.max_capital_usage_pct:
                reasons.append(f"capital usage {usage:.1f}% exceeds max {r.max_capital_usage_pct}%")
            score += int(min(30, usage / r.max_capital_usage_pct * 30)) if r.max_capital_usage_pct > 0 else 0
            for venue in {buy.venue, sell.venue}:
                exp = portfolio.exposure_by_venue_usd.get(venue, ZERO) + notional_usd
                if pct(exp, total) > r.max_exchange_exposure_pct:
                    reasons.append(
                        f"exposure on {venue} would reach {pct(exp, total):.1f}% (max {r.max_exchange_exposure_pct}%)"
                    )
            for leg in (buy, sell):
                if leg.chain is not None:
                    exp = portfolio.exposure_by_chain_usd.get(leg.chain.value, ZERO) + notional_usd
                    if pct(exp, total) > r.max_chain_exposure_pct:
                        reasons.append(
                            f"exposure on chain {leg.chain.value} would exceed {r.max_chain_exposure_pct}%"
                        )
            # A spot arbitrage leaves net asset exposure unchanged (bought on one venue, sold on the other);
            # carry strategies open a position, so only they add the notional.
            carry = strategy in (Strategy.SPOT_PERP, Strategy.FUNDING, Strategy.BASIS)
            exp = portfolio.exposure_by_asset_usd.get(base, ZERO) + (notional_usd if carry else ZERO)
            if pct(exp, total) > r.max_asset_exposure_pct:
                reasons.append(
                    f"exposure to {base} {'would exceed' if carry else 'already exceeds'} {r.max_asset_exposure_pct}% of capital"
                )
        elif capital_required_usd > 0 and flash_loan_usd is None:
            reasons.append("no capital available")

        # --- balances (prefunded inventory) ----------------------------------------------
        if flash_loan_usd is None:
            need_quote = buy.quote_amount * (1 + D(buy.fee_pct or 0) / 100)
            have_quote = portfolio.available_balances.get((buy.venue, quote))
            if have_quote is None:
                reasons.append(f"balance of {quote} on {buy.venue} unknown")
            elif have_quote < need_quote:
                reasons.append(
                    f"insufficient {quote} on {buy.venue}: need {need_quote:.2f}, have {have_quote:.2f}"
                )
            if sell.kind is VenueKind.PERP:
                # a perpetual short is collateralised in quote (1x - leverage is not allowed by default)
                margin = sell.quote_amount if not r.leverage_allowed else sell.quote_amount / 2
                have_margin = portfolio.available_balances.get((sell.venue, quote))
                if have_margin is None:
                    reasons.append(f"collateral balance of {quote} on {sell.venue} unknown")
                elif have_margin < margin:
                    reasons.append(
                        f"insufficient {quote} collateral on {sell.venue}: need {margin:.2f}, have {have_margin:.2f}"
                    )
            else:
                have_base = portfolio.available_balances.get((sell.venue, base))
                if have_base is None:
                    reasons.append(f"balance of {base} on {sell.venue} unknown")
                elif have_base < sell.base_amount:
                    reasons.append(
                        f"insufficient {base} on {sell.venue}: need {sell.base_amount}, have {have_base}"
                    )
        for leg in (buy, sell):
            if leg.kind is VenueKind.DEX and leg.chain is not None:
                ok = portfolio.gas_reserve_ok.get(leg.chain.value)
                if ok is False:
                    reasons.append(f"gas reserve on {leg.chain.value} is below the configured minimum")

        # --- venue health -----------------------------------------------------------------
        for venue in {buy.venue, sell.venue}:
            h = portfolio.venue_health.get(venue, VenueHealth.UNKNOWN)
            factors[f"health:{venue}"] = h.value
            if h is VenueHealth.HEALTHY:
                continue
            if h is VenueHealth.DEGRADED and r.allow_degraded_venues:
                score += 15
                continue
            reasons.append(f"venue {venue} is {h.value}")

        # --- soft score components --------------------------------------------------------
        slip = buy.slippage_pct + sell.slippage_pct + buy.price_impact_pct + sell.price_impact_pct
        score += int(min(25, slip / max(r.max_slippage_pct, Decimal("0.01")) * 25))
        if slip > r.max_slippage_pct:
            reasons.append(f"combined slippage {slip:.3f}% exceeds max {r.max_slippage_pct}%")
        age = max(buy.age_ms, sell.age_ms)
        score += int(min(15, Decimal(age) / max(r.max_quote_age_ms, 1) * 15))
        if strategy in (Strategy.DEX_DEX, Strategy.FLASH_LOAN):
            score += 15
        elif strategy in (Strategy.CEX_DEX, Strategy.SPOT_PERP, Strategy.FUNDING, Strategy.BASIS):
            score += 8
        if not buy.fully_fillable or not sell.fully_fillable:
            score += 15
        score = max(0, min(100, score))
        factors["slippage_total_pct"] = str(slip)
        factors["quote_age_ms"] = age
        if score > t.max_risk_score:
            reasons.append(f"risk score {score} exceeds maximum {t.max_risk_score}")
        return RiskAssessment(score=score, passed=not reasons, reasons=reasons, factors=factors)

    def capital_breakdown(
        self, total_usd: Decimal, reserved_gas_usd: Decimal, at_risk_usd: Decimal
    ) -> dict[str, Decimal]:
        r = self.settings.risk
        emergency = min(total_usd, r.emergency_reserve_usd)
        inventory = (total_usd - emergency) * r.inventory_reserve_pct / 100
        usable = max(ZERO, total_usd - emergency - inventory - reserved_gas_usd)
        return {
            "total": total_usd,
            "usable": usable,
            "reserved": emergency + inventory + reserved_gas_usd,
            "emergency_reserve": emergency,
            "inventory_reserve": inventory,
            "gas_reserve": reserved_gas_usd,
            "at_risk": at_risk_usd,
        }


__all__ = ["Chain", "PortfolioState", "RiskEngine"]
