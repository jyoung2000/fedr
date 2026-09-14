"""Learning / experience engine.

Deterministic, auditable statistics per route. It can only *increase* safety
buffers (bounded by settings) - it never authorises a trade.
"""

from __future__ import annotations

from decimal import Decimal

from fedr.core.enums import Strategy, TradingMode
from fedr.core.money import ZERO, D, clamp
from fedr.db.models import ExperienceStat, utcnow
from fedr.db.repo import Repo

ALPHA = Decimal("0.2")


def size_bucket(notional_usd: Decimal) -> str:
    for edge, name in (
        (Decimal("100"), "xs"),
        (Decimal("500"), "s"),
        (Decimal("2000"), "m"),
        (Decimal("10000"), "l"),
    ):
        if notional_usd <= edge:
            return name
    return "xl"


def route_key(
    mode: TradingMode, strategy: Strategy, buy_venue: str, sell_venue: str, pair: str, notional_usd: Decimal
) -> str:
    return f"{mode.value}|{strategy.value}|{buy_venue}|{sell_venue}|{pair}|{size_bucket(notional_usd)}"


class ExperienceEngine:
    def __init__(
        self, repo: Repo | None, max_extra_buffer_pct: Decimal = Decimal("0.25"), enabled: bool = True
    ):
        self.repo = repo
        self.max_extra = max_extra_buffer_pct
        self.enabled = enabled
        self._cache: dict[str, ExperienceStat] = {}

    async def load(self) -> None:
        if self.repo is None:
            return
        for s in await self.repo.list_experience(limit=1000):
            self._cache[s.key] = s

    def get(self, key: str) -> ExperienceStat | None:
        return self._cache.get(key)

    def extra_buffer_pct(self, key: str) -> Decimal:
        if not self.enabled:
            return ZERO
        s = self._cache.get(key)
        if s is None or s.samples < 3:
            return ZERO
        return clamp(D(s.extra_buffer_pct), ZERO, self.max_extra)

    async def record(
        self,
        key: str,
        *,
        notional_usd: Decimal,
        prediction_error_usd: Decimal,
        slippage_variance_usd: Decimal,
        fee_variance_usd: Decimal,
        gas_variance_usd: Decimal,
        filled: bool,
        latency_ms: int,
    ) -> ExperienceStat:
        if notional_usd <= 0:
            notional_usd = Decimal("1")
        s = self._cache.get(key) or ExperienceStat(
            key=key,
            samples=0,
            error_pct_ewma="0",
            slippage_bias_pct="0",
            fee_variance_pct="0",
            gas_variance_pct="0",
            fill_reliability="1",
            latency_ms_ewma="0",
            extra_buffer_pct="0",
        )
        err_pct = prediction_error_usd / notional_usd * 100
        slip_pct = slippage_variance_usd / notional_usd * 100
        fee_pct = fee_variance_usd / notional_usd * 100
        gas_pct = gas_variance_usd / notional_usd * 100
        if s.samples == 0:
            s.error_pct_ewma, s.slippage_bias_pct, s.fee_variance_pct, s.gas_variance_pct = map(
                str, (err_pct, slip_pct, fee_pct, gas_pct)
            )
            s.fill_reliability = "1" if filled else "0"
            s.latency_ms_ewma = str(latency_ms)
        else:
            s.error_pct_ewma = str(D(s.error_pct_ewma) * (1 - ALPHA) + err_pct * ALPHA)
            s.slippage_bias_pct = str(D(s.slippage_bias_pct) * (1 - ALPHA) + slip_pct * ALPHA)
            s.fee_variance_pct = str(D(s.fee_variance_pct) * (1 - ALPHA) + fee_pct * ALPHA)
            s.gas_variance_pct = str(D(s.gas_variance_pct) * (1 - ALPHA) + gas_pct * ALPHA)
            s.fill_reliability = str(
                D(s.fill_reliability) * (1 - ALPHA) + (Decimal(1) if filled else ZERO) * ALPHA
            )
            s.latency_ms_ewma = str(D(s.latency_ms_ewma) * (1 - ALPHA) + Decimal(latency_ms) * ALPHA)
        s.samples += 1
        # Extra buffer = positive persistent under-estimation of costs, plus an unreliability penalty
        unreliability = (Decimal(1) - D(s.fill_reliability)) * Decimal("0.10")
        s.extra_buffer_pct = str(clamp(max(ZERO, D(s.error_pct_ewma)) + unreliability, ZERO, self.max_extra))
        s.updated_at = utcnow()
        self._cache[key] = s
        if self.repo is not None:
            await self.repo.save_experience(s)
        return s

    def summary(self) -> list[dict]:
        return [
            {
                "key": s.key,
                "samples": s.samples,
                "error_pct": s.error_pct_ewma,
                "slippage_bias_pct": s.slippage_bias_pct,
                "fee_variance_pct": s.fee_variance_pct,
                "gas_variance_pct": s.gas_variance_pct,
                "fill_reliability": s.fill_reliability,
                "latency_ms": s.latency_ms_ewma,
                "extra_buffer_pct": s.extra_buffer_pct,
            }
            for s in sorted(self._cache.values(), key=lambda x: -x.samples)
        ]
