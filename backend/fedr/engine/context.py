"""Shared runtime context handed to the engines (composition root lives in fedr.app)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

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
        rep = c.health_tracker.classify(name, market_data_max_age_ms=60_000 if c.kind is VenueKind.DEX else 15_000, expect_ws="ws" in c.capabilities)
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
