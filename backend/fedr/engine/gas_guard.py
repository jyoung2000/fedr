"""GAS GUARD - dedicated gas economics + regime detection for on-chain legs."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from fedr.config.schema import GasSettings
from fedr.core.enums import Chain, GasRegime
from fedr.core.models import GasAssessment, now_ms
from fedr.core.money import ZERO, D, fmt_money, fmt_pct, pct

GWEI = Decimal("1e9")
LAMPORTS = Decimal("1e9")


@dataclass(slots=True)
class GasSnapshot:
    """Current on-chain fee conditions for one chain."""

    chain: Chain
    gas_price_native: Decimal  # EVM: gwei (base fee + priority); Solana: lamports per compute unit (priority)
    priority_fee_native: Decimal  # EVM: gwei tip; Solana: micro-lamports/CU expressed as lamports/CU
    native_usd: Decimal  # USD price of the native token
    l1_data_fee_usd: Decimal = ZERO  # OP-stack L1 data fee component (per tx)
    base_fee_lamports: int = 5000  # Solana per-signature base fee
    ts_ms: int = field(default_factory=now_ms)
    source: str = "unknown"

    @property
    def age_ms(self) -> int:
        return now_ms() - self.ts_ms


class GasBaseline:
    """Exponentially-weighted baseline of gas price per chain (regime detection)."""

    def __init__(self, alpha: Decimal = Decimal("0.05")):
        self.alpha = alpha
        self._baseline: dict[Chain, Decimal] = {}
        self._samples: dict[Chain, int] = {}

    def update(self, chain: Chain, gas_price: Decimal) -> Decimal:
        prev = self._baseline.get(chain)
        if prev is None:
            self._baseline[chain] = gas_price
            self._samples[chain] = 1
        else:
            self._baseline[chain] = prev * (1 - self.alpha) + gas_price * self.alpha
            self._samples[chain] += 1
        return self._baseline[chain]

    def get(self, chain: Chain) -> Decimal | None:
        return self._baseline.get(chain)

    def seed(self, chain: Chain, value: Decimal) -> None:
        self._baseline[chain] = value
        self._samples[chain] = max(self._samples.get(chain, 0), 1)


class GasGuard:
    def __init__(self, settings: GasSettings, baseline: GasBaseline | None = None):
        self.settings = settings
        self.baseline = baseline or GasBaseline()

    # ---- estimation --------------------------------------------------------------------
    def estimate_cost_usd(
        self, snap: GasSnapshot, gas_limit: int, compute_units: int | None = None
    ) -> Decimal:
        """Expected USD cost of a transaction at the current conditions."""
        if snap.chain is Chain.SOLANA:
            cu = compute_units or gas_limit or 200_000
            priority_lamports = snap.priority_fee_native * cu  # lamports/CU * CU
            total_lamports = D(snap.base_fee_lamports) + priority_lamports
            return total_lamports / LAMPORTS * snap.native_usd
        gas_cost_eth = snap.gas_price_native * D(gas_limit) / GWEI
        return gas_cost_eth * snap.native_usd + snap.l1_data_fee_usd

    def regime(self, chain: Chain, gas_price: Decimal) -> GasRegime:
        base = self.baseline.get(chain)
        if base is None or base <= 0:
            return GasRegime.NORMAL
        ratio = gas_price / base
        if ratio >= self.settings.extreme_multiple:
            return GasRegime.EXTREME
        if ratio >= self.settings.high_multiple:
            return GasRegime.HIGH
        if ratio >= self.settings.elevated_multiple:
            return GasRegime.ELEVATED
        return GasRegime.NORMAL

    # ---- assessment --------------------------------------------------------------------
    def assess(
        self,
        snap: GasSnapshot | None,
        gas_limit: int,
        gross_profit_usd: Decimal,
        *,
        compute_units: int | None = None,
        max_gas_override_usd: Decimal | None = None,
        legs: int = 1,
    ) -> GasAssessment:
        s = self.settings
        reasons: list[str] = []
        if snap is None:
            return GasAssessment(
                chain=None,
                regime=GasRegime.NORMAL,
                current_gas_cost_usd=ZERO,
                expected_gas_cost_usd=ZERO,
                stress_gas_cost_usd=ZERO,
                gas_pct_of_gross=ZERO,
                passed=False,
                reasons=["gas conditions unknown"],
            )
        if snap.age_ms > 60_000:
            reasons.append(f"gas snapshot stale ({snap.age_ms // 1000}s)")
        current = self.estimate_cost_usd(snap, gas_limit, compute_units) * legs
        expected = current
        stress = current * s.stress_multiplier
        regime = self.regime(snap.chain, snap.gas_price_native)
        max_gas = max_gas_override_usd if max_gas_override_usd is not None else s.max_gas_per_trade_usd
        gas_pct = pct(expected, gross_profit_usd) if gross_profit_usd > 0 else Decimal("100")
        priority_usd = ZERO
        if snap.chain is Chain.SOLANA:
            cu = compute_units or gas_limit or 200_000
            priority_usd = snap.priority_fee_native * cu / LAMPORTS * snap.native_usd * legs
            if snap.priority_fee_native * cu > s.max_priority_fee_lamports:
                reasons.append(
                    f"priority fee {int(snap.priority_fee_native * cu)} lamports exceeds max {s.max_priority_fee_lamports}"
                )
        else:
            priority_usd = snap.priority_fee_native * D(gas_limit) / GWEI * snap.native_usd * legs
            if snap.priority_fee_native > s.max_priority_fee_gwei:
                reasons.append(
                    f"priority fee {snap.priority_fee_native} gwei exceeds max {s.max_priority_fee_gwei} gwei"
                )
            if snap.gas_price_native > s.max_gas_price_gwei:
                reasons.append(
                    f"gas price {snap.gas_price_native} gwei exceeds max {s.max_gas_price_gwei} gwei"
                )
        if expected > max_gas:
            reasons.append(f"expected gas {fmt_money(expected)} exceeds max per trade {fmt_money(max_gas)}")
        if stress > max_gas * s.stress_multiplier:
            reasons.append(f"stress gas {fmt_money(stress)} exceeds tolerance")
        if gross_profit_usd > 0 and gas_pct > s.max_gas_pct_of_gross:
            reasons.append(f"gas is {fmt_pct(gas_pct)} of gross (max {fmt_pct(s.max_gas_pct_of_gross)})")
        if regime is GasRegime.EXTREME:
            reasons.append("gas regime EXTREME - on-chain trading paused")
        return GasAssessment(
            chain=snap.chain,
            regime=regime,
            current_gas_cost_usd=current,
            expected_gas_cost_usd=expected,
            stress_gas_cost_usd=stress,
            gas_pct_of_gross=gas_pct,
            passed=not reasons,
            reasons=reasons,
            gas_price_native=snap.gas_price_native,
            priority_fee_usd=priority_usd,
        )
