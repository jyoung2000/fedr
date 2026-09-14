"""User-editable application settings (persisted in the database).

The main Settings page only shows a handful of high-level controls; the full
document below is what the engines consume. Risk profiles CONSERVATIVE and
BALANCED overwrite the numeric limits; CUSTOM leaves them as edited.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from fedr.core.enums import RiskProfile, TradingMode

Dec = Decimal


class GeneralSettings(BaseModel):
    mode: TradingMode = TradingMode.PAPER
    shadow_mode: bool = False
    risk_profile: RiskProfile = RiskProfile.CONSERVATIVE
    advanced_mode: bool = False
    pairs: list[str] = Field(
        default_factory=lambda: ["BTC/USDC", "ETH/USDC", "SOL/USDC", "BTC/USDT", "ETH/USDT", "SOL/USDT"]
    )
    quote_assets: list[str] = Field(default_factory=lambda: ["USDC", "USDT", "USD"])
    scan_interval_ms: int = 1500
    bot_enabled: bool = True  # master on/off for the scanner + auto-execution

    @field_validator("pairs")
    @classmethod
    def _norm_pairs(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for p in v:
            p = p.strip().upper()
            if "/" in p and p not in out:
                out.append(p)
        return out


class TradingSettings(BaseModel):
    auto_execute: bool = True  # when false, opportunities are shown but require the Execute button
    max_trade_size_usd: Dec = Dec("250")
    min_trade_size_usd: Dec = Dec("20")
    min_profit_usd: Dec = Dec("2.00")
    min_worst_case_profit_usd: Dec = Dec("0.50")
    min_roi_pct: Dec = Dec("0.15")
    max_quote_age_ms: int = 3000
    max_risk_score: int = 60
    safety_buffer_pct: Dec = Dec("0.05")  # % of notional always reserved
    latency_allowance_pct: Dec = Dec("0.03")  # adverse move allowance between quote and fill
    partial_fill_allowance_pct: Dec = Dec("0.02")
    failure_reserve_pct: Dec = Dec("0.02")  # expected cost of a failed leg (gas / unwind)
    rebalance_allowance_pct: Dec = Dec("0.03")  # amortised cost of restoring inventory
    stablecoin_equivalence: bool = False  # treat USDC/USDT/USD as the same quote (with haircut)
    stablecoin_haircut_pct: Dec = Dec("0.05")
    worst_case_slippage_multiplier: Dec = Dec("2.0")
    worst_case_fee_multiplier: Dec = Dec("1.25")
    execution_timeout_ms: int = 8000
    leg_price_tolerance_pct: Dec = Dec("0.10")  # limit price slack for IOC legs
    unknown_fee_fallback_pct: Dec | None = None  # None => BLOCK when a fee is unknown


class RiskSettings(BaseModel):
    max_capital_usage_pct: Dec = Dec("50")
    max_exchange_exposure_pct: Dec = Dec("40")
    max_chain_exposure_pct: Dec = Dec("40")
    max_asset_exposure_pct: Dec = Dec("40")
    max_daily_loss_usd: Dec = Dec("50")  # HARD limit
    max_concurrent_trades: int = 1
    max_unhedged_seconds: int = 30
    max_slippage_pct: Dec = Dec("0.30")
    max_gas_usd: Dec = Dec("2.00")
    max_quote_age_ms: int = 3000
    max_failed_trades_per_hour: int = 3
    max_flash_loan_usd: Dec = Dec("25000")
    max_flash_loan_gas_usd: Dec = Dec("5")
    allow_degraded_venues: bool = False
    max_prediction_error_pct: Dec = Dec("0.50")  # of notional; larger trips a breaker
    rapid_move_pct: Dec = Dec("2.0")  # % move within a scan window that trips a breaker
    emergency_reserve_usd: Dec = Dec("200")
    inventory_reserve_pct: Dec = Dec("10")
    leverage_allowed: bool = False
    auto_bridging_allowed: bool = False
    asset_allowlist: list[str] = Field(default_factory=lambda: ["BTC", "ETH", "SOL", "USDC", "USDT", "USD"])


class StrategySettings(BaseModel):
    cex_cex: bool = True
    cex_dex: bool = True
    dex_dex: bool = False
    spot_perp: bool = False
    funding: bool = False
    basis: bool = False
    flash_loan: bool = False

    def enabled(self) -> list[str]:
        return [k for k, v in self.model_dump().items() if v]


class GasSettings(BaseModel):
    max_gas_per_trade_usd: Dec = Dec("2.00")
    max_gas_pct_of_gross: Dec = Dec("10")
    min_profit_after_gas_usd: Dec = Dec("1.00")
    stress_multiplier: Dec = Dec("1.5")
    max_priority_fee_gwei: Dec = Dec("3")
    max_gas_price_gwei: Dec = Dec("60")
    max_priority_fee_lamports: int = 200_000
    # Regime thresholds as multiples of the rolling baseline gas price
    elevated_multiple: Dec = Dec("1.5")
    high_multiple: Dec = Dec("2.5")
    extreme_multiple: Dec = Dec("4.0")
    elevated_profit_multiplier: Dec = Dec("1.5")  # required profit x this in ELEVATED regime
    # Native token reserves per chain (in native units) that the strategy engine may never consume
    gas_reserve: dict[str, Dec] = Field(
        default_factory=lambda: {
            "solana": Dec("0.05"),
            "ethereum": Dec("0.01"),
            "base": Dec("0.003"),
            "arbitrum": Dec("0.003"),
            "optimism": Dec("0.003"),
            "polygon": Dec("5"),
            "bsc": Dec("0.02"),
            "avalanche": Dec("0.2"),
        }
    )
    unknown_gas_policy: str = "block"  # block | conservative_fallback
    conservative_gas_fallback_usd: Dec = Dec("5.00")


class SlippageSettings(BaseModel):
    default_max_pct: Dec = Dec("0.30")
    per_asset_max_pct: dict[str, Dec] = Field(default_factory=lambda: {"BTC": Dec("0.15"), "ETH": Dec("0.20")})
    per_venue_max_pct: dict[str, Dec] = Field(default_factory=dict)
    per_route_max_pct: dict[str, Dec] = Field(default_factory=dict)
    # trade-size tiers in USD -> max slippage pct
    size_tiers: list[tuple[Dec, Dec]] = Field(
        default_factory=lambda: [(Dec("100"), Dec("0.50")), (Dec("1000"), Dec("0.30")), (Dec("10000"), Dec("0.15"))]
    )
    dex_slippage_tolerance_pct: Dec = Dec("0.50")  # minAmountOut tolerance submitted with swaps


class FlashLoanSettings(BaseModel):
    enabled: bool = False
    provider: str = "auto"  # auto | aave_v3 | balancer_v2 | morpho_blue
    max_loan_usd: Dec = Dec("25000")
    min_net_profit_usd: Dec = Dec("10")
    max_gas_usd: Dec = Dec("2")
    max_gas_pct_of_gross: Dec = Dec("10")
    max_slippage_bps: int = 30
    min_profit_gas_ratio: Dec = Dec("5")
    require_simulation: bool = True
    require_atomic: bool = True
    mev_aware: bool = True
    mev_reserve_pct: Dec = Dec("0.05")  # reserve for tips / sandwich risk
    chains: list[str] = Field(default_factory=lambda: ["ethereum", "arbitrum", "base", "optimism", "polygon"])


class PaperSettings(BaseModel):
    starting_balances: dict[str, dict[str, Dec]] = Field(
        default_factory=lambda: {
            "kraken": {"USDC": Dec("2000"), "USD": Dec("500"), "SOL": Dec("0"), "ETH": Dec("0"), "BTC": Dec("0")},
            "coinbase": {"USDC": Dec("2000"), "USD": Dec("500"), "SOL": Dec("0"), "ETH": Dec("0"), "BTC": Dec("0")},
            "solana": {"USDC": Dec("2400"), "SOL": Dec("0.5")},
            "evm": {"USDC": Dec("2400"), "ETH": Dec("0.03")},
        }
    )
    stress_multiplier: Dec = Dec("2.0")  # pessimistic multiplier applied to slippage & gas
    latency_ms: int = 250
    latency_jitter_ms: int = 150
    partial_fill_probability: Dec = Dec("0.05")
    failure_probability: Dec = Dec("0.02")
    quote_drift_pct: Dec = Dec("0.02")  # adverse price drift applied during simulated latency
    fill_ratio_on_partial: Dec = Dec("0.6")


class RebalanceSettings(BaseModel):
    recommendations_enabled: bool = True
    min_net_benefit_usd: Dec = Dec("5")
    imbalance_threshold_pct: Dec = Dec("30")
    lookback_hours: int = 24


class MevSettings(BaseModel):
    enabled: bool = True
    evm_private_submission: bool = True  # only takes effect when FEDR_EVM_PRIVATE_RPC is configured
    solana_jito: bool = False
    max_tip_usd: Dec = Dec("0.50")


class SecuritySettings(BaseModel):
    withdrawal_allowlist: list[dict[str, str]] = Field(default_factory=list)  # [{chain,address,label}]
    require_allowlist_for_withdrawals: bool = True
    wallet_backup_confirmed: bool = False
    session_timeout_minutes: int = 240


class AdvancedSettings(BaseModel):
    websocket_market_data: bool = True
    rest_poll_interval_ms: int = 2000
    orderbook_depth: int = 50
    opportunity_ttl_ms: int = 5000
    max_opportunities_kept: int = 200
    reconciliation_interval_s: int = 30
    health_check_interval_s: int = 15
    experience_learning_enabled: bool = True
    experience_max_extra_buffer_pct: Dec = Dec("0.25")
    log_level: str = "INFO"
    connector_timeout_s: float = 10.0


class LiveActivationState(BaseModel):
    activated: bool = False
    activated_at: str | None = None
    activated_by: str | None = None
    confirmation_phrase_hash: str | None = None
    paper_completed: bool = False
    shadow_reviewed: bool = False


class AppSettings(BaseModel):
    version: int = 1
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    trading: TradingSettings = Field(default_factory=TradingSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    strategies: StrategySettings = Field(default_factory=StrategySettings)
    gas: GasSettings = Field(default_factory=GasSettings)
    slippage: SlippageSettings = Field(default_factory=SlippageSettings)
    flash_loan: FlashLoanSettings = Field(default_factory=FlashLoanSettings)
    paper: PaperSettings = Field(default_factory=PaperSettings)
    rebalance: RebalanceSettings = Field(default_factory=RebalanceSettings)
    mev: MevSettings = Field(default_factory=MevSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    advanced: AdvancedSettings = Field(default_factory=AdvancedSettings)
    live: LiveActivationState = Field(default_factory=LiveActivationState)

    def apply_risk_profile(self, profile: RiskProfile) -> None:
        self.general.risk_profile = profile
        if profile is RiskProfile.CUSTOM:
            return
        preset = RISK_PROFILES[profile]
        for section, values in preset.items():
            model = getattr(self, section)
            for k, v in values.items():
                setattr(model, k, v)

    def to_public_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        d["live"].pop("confirmation_phrase_hash", None)
        return d


RISK_PROFILES: dict[RiskProfile, dict[str, dict[str, Any]]] = {
    RiskProfile.CONSERVATIVE: {
        "trading": {
            "max_trade_size_usd": Dec("250"),
            "min_profit_usd": Dec("2.00"),
            "min_worst_case_profit_usd": Dec("0.50"),
            "min_roi_pct": Dec("0.15"),
            "max_quote_age_ms": 3000,
            "max_risk_score": 60,
            "safety_buffer_pct": Dec("0.05"),
            "worst_case_slippage_multiplier": Dec("2.0"),
        },
        "risk": {
            "max_capital_usage_pct": Dec("50"),
            "max_exchange_exposure_pct": Dec("40"),
            "max_chain_exposure_pct": Dec("40"),
            "max_asset_exposure_pct": Dec("40"),
            "max_daily_loss_usd": Dec("50"),
            "max_concurrent_trades": 1,
            "max_slippage_pct": Dec("0.30"),
            "max_gas_usd": Dec("2.00"),
            "max_failed_trades_per_hour": 3,
            "allow_degraded_venues": False,
        },
        "gas": {"max_gas_per_trade_usd": Dec("2.00"), "max_gas_pct_of_gross": Dec("10"), "stress_multiplier": Dec("1.5")},
    },
    RiskProfile.BALANCED: {
        "trading": {
            "max_trade_size_usd": Dec("1000"),
            "min_profit_usd": Dec("3.00"),
            "min_worst_case_profit_usd": Dec("1.00"),
            "min_roi_pct": Dec("0.10"),
            "max_quote_age_ms": 4000,
            "max_risk_score": 70,
            "safety_buffer_pct": Dec("0.04"),
            "worst_case_slippage_multiplier": Dec("1.75"),
        },
        "risk": {
            "max_capital_usage_pct": Dec("70"),
            "max_exchange_exposure_pct": Dec("50"),
            "max_chain_exposure_pct": Dec("50"),
            "max_asset_exposure_pct": Dec("50"),
            "max_daily_loss_usd": Dec("150"),
            "max_concurrent_trades": 2,
            "max_slippage_pct": Dec("0.40"),
            "max_gas_usd": Dec("4.00"),
            "max_failed_trades_per_hour": 5,
            "allow_degraded_venues": False,
        },
        "gas": {"max_gas_per_trade_usd": Dec("4.00"), "max_gas_pct_of_gross": Dec("15"), "stress_multiplier": Dec("1.5")},
    },
}


def default_settings(default_mode: str = "paper") -> AppSettings:
    s = AppSettings()
    mode = TradingMode(default_mode) if default_mode in TradingMode._value2member_map_ else TradingMode.PAPER
    if mode is TradingMode.LIVE:  # live can never be a boot default
        mode = TradingMode.PAPER
    s.general.mode = mode
    s.apply_risk_profile(RiskProfile.CONSERVATIVE)
    return s
