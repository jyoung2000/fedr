"""Shared runtime context handed to the engines (composition root lives in fedr.app)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from fedr.config.env import EnvSettings
from fedr.config.schema import AppSettings
from fedr.connectors.base import VenueConnector
from fedr.connectors.paper.ledger import PaperLedger
from fedr.core.enums import Chain, TradingMode, VenueHealth, VenueKind
from fedr.core.models import HealthReport
from fedr.db.repo import Repo
from fedr.engine.circuit_breakers import CircuitBreakerManager
from fedr.engine.execution.paper_executor import PaperExecutor
from fedr.engine.experience import ExperienceEngine
from fedr.engine.gas_guard import GasGuard
from fedr.engine.inventory import InventoryManager
from fedr.engine.latency_guard import LatencyGuard
from fedr.engine.profit_guard import ProfitGuard
from fedr.engine.rebalancer import Rebalancer
from fedr.engine.risk_engine import RiskEngine
from fedr.engine.slippage_guard import SlippageGuard
from fedr.marketdata.gas import GasOracle
from fedr.marketdata.hub import MarketDataHub


class AdmissionGate:
    """The ONE final admission boundary for every risk-increasing submission (invariant B).

    * ``lock`` serialises the final check + reservation + durable journal write of every executor, so
      capacity and reservations cannot be over-committed by concurrent requests.
    * ``generation`` is bumped whenever the connector set / mode is rebuilt; an admission taken under an
      older generation is refused (structural changes never swap a connector under active work).
    * ``recovery_complete`` is the startup barrier: nothing is admitted until the journal of unfinished
      work has been resolved or quarantined.

    Linearization point of an emergency stop: the synchronous re-check performed after the journal write and
    immediately before the transport tasks are created (no await in between). A stop that happens before it
    prevents the submission; a pair admitted before it is submitted as a pair and then monitored/reconciled.
    """

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.generation = 0
        # Open by default for engine-level use (tests, tools); the application composition root closes it
        # before connectors are built and opens it only after journal recovery (FedrApp.start).
        self.recovery_complete = True
        self.recovery_note = ""

    def bump(self) -> int:
        self.generation += 1
        return self.generation


@dataclass
class EngineContext:
    env: EnvSettings
    settings: AppSettings
    repo: Repo | None
    hub: MarketDataHub
    gas_oracle: GasOracle
    breakers: CircuitBreakerManager
    inventory: InventoryManager
    experience: ExperienceEngine
    rebalancer: Rebalancer
    connectors: dict[str, VenueConnector] = field(default_factory=dict)
    health: dict[str, HealthReport] = field(default_factory=dict)
    ledger: PaperLedger | None = None
    paper_executor: PaperExecutor | None = None
    emergency_stop: bool = False
    open_trade_ids: set[str] = field(default_factory=set)
    daily_pnl_usd: Decimal = Decimal("0")
    failed_trades_last_hour: int = 0
    unhedged_since_ms: int | None = None
    flash_loan_simulator: Callable[..., Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    admission: AdmissionGate = field(default_factory=AdmissionGate)

    async def book_realized(self, net_usd: Decimal, source: str) -> None:
        """Single path that updates the in-memory daily realized P&L and enforces the daily-loss limit
        immediately (spot trades, flash loans and carry closes all go through here)."""
        from fedr.core.enums import CircuitBreakerReason
        from fedr.core.money import fmt_money

        self.daily_pnl_usd += net_usd
        if self.daily_pnl_usd <= -self.settings.risk.max_daily_loss_usd:
            await self.breakers.trip(
                CircuitBreakerReason.DAILY_LOSS_LIMIT,
                f"daily realized loss {fmt_money(self.daily_pnl_usd)} reached the hard limit (last: {source})",
                auto=True,
            )

    def admission_block(
        self,
        *,
        generation: int,
        mode: TradingMode,
        risk_increasing: bool = True,
        venues: tuple[str, ...] = (),
        chains: tuple[str, ...] = (),
        strategy: str | None = None,
        symbol: str | None = None,
        count_capacity: bool = True,
    ) -> str | None:
        """Synchronous final admission check (call only while holding ``admission.lock``)."""
        if not self.admission.recovery_complete:
            return f"startup recovery has not completed ({self.admission.recovery_note})"
        if self.mode is not mode or self.admission.generation != generation:
            return "mode or venue configuration changed while the trade was being prepared"
        if not risk_increasing:
            return None
        if self.emergency_stop:
            return "EMERGENCY STOP is active"
        if self.settings.general.shadow_mode:
            return "shadow mode: evaluation only, nothing submitted"
        if mode is TradingMode.LIVE and not self.settings.live.activated:
            return "live trading is not activated"
        if count_capacity and len(self.open_trade_ids) >= self.settings.risk.max_concurrent_trades:
            return "max concurrent trades reached"
        reasons: list[str] = []
        for v in venues or (None,):
            reasons += self.breakers.blocks(venue=v, strategy=strategy, symbol=symbol)
        for c in chains:
            reasons += self.breakers.blocks(chain=c)
        if reasons:
            return reasons[0]
        return None

    # ---- derived helpers ---------------------------------------------------------------
    @property
    def mode(self) -> TradingMode:
        return self.settings.general.mode

    @property
    def is_simulated_execution(self) -> bool:
        return self.mode in (TradingMode.SIMULATION, TradingMode.PAPER)

    @property
    def profit_guard(self) -> ProfitGuard:
        return ProfitGuard()

    @property
    def gas_guard(self) -> GasGuard:
        return GasGuard(self.settings.gas, self.gas_oracle.baseline)

    @property
    def risk_engine(self) -> RiskEngine:
        return RiskEngine(self.settings)

    @property
    def slippage_guard(self) -> SlippageGuard:
        return SlippageGuard(self.settings.slippage)

    @property
    def latency_guard(self) -> LatencyGuard:
        return LatencyGuard(min(self.settings.trading.max_quote_age_ms, self.settings.risk.max_quote_age_ms))

    def venue_health(self, name: str) -> VenueHealth:
        """Live classification from the connector's rolling statistics (no I/O).

        The periodic health loop performs the active checks (API pings, maintenance flags) that feed
        the tracker; this method reflects the latest market-data/latency/error state at decision time.
        """
        c = self.connectors.get(name)
        if c is None or not c.connected:
            return VenueHealth.UNKNOWN
        rep = c.health_tracker.classify(
            name,
            market_data_max_age_ms=60_000 if c.kind is VenueKind.DEX else 15_000,
            expect_ws="ws" in c.capabilities,
        )
        prev = self.health.get(name)
        if prev is not None and prev.maintenance:
            rep.maintenance = True
            rep.health = VenueHealth.BLOCKED
            rep.reasons.append("exchange maintenance")
        self.health[name] = rep
        return rep.health

    def price_usd(self, asset: str) -> Decimal | None:
        return self.hub.prices.get(asset)

    def connectors_of(self, kind: VenueKind) -> list[VenueConnector]:
        return [c for c in self.connectors.values() if c.kind is kind and c.connected]

    def ledger_venue_for(self, c: VenueConnector) -> str:
        if c.kind is VenueKind.DEX and c.chain is not None:
            return c.chain.value
        return c.name

    def gas_reserve_ok(self, chain: Chain) -> bool | None:
        reserve = self.settings.gas.gas_reserve.get(chain.value)
        if reserve is None:
            return None
        have = self.inventory.available(chain.value, chain.native_token)
        return have >= reserve
