"""Venue connector abstraction. Strategy code never imports ccxt or Gateway directly."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import deque
from decimal import Decimal

from fedr.core.enums import Chain, TradingMode, VenueHealth, VenueKind, VerificationLevel
from fedr.core.models import (
    Balance,
    ExecutionQuote,
    FeeSchedule,
    HealthReport,
    MarketInfo,
    OrderRequest,
    OrderResult,
    now_ms,
)
from fedr.marketdata.orderbook import OrderBook


class ConnectorError(Exception):
    retryable = False


class NotSupportedError(ConnectorError):
    pass


class VenueUnavailable(ConnectorError):
    retryable = True


class InsufficientBalance(ConnectorError):
    pass


class OrderRejected(ConnectorError):
    pass


class QuoteExpired(ConnectorError):
    pass


class HealthTracker:
    """Rolling latency / failure statistics used to classify venue health."""

    def __init__(self, window: int = 50):
        self.latencies: deque[float] = deque(maxlen=window)
        self.errors: deque[float] = deque(maxlen=200)
        self.order_failures: deque[float] = deque(maxlen=200)
        self.rate_limited_until: float = 0.0
        self.maintenance: bool = False
        self.ws_connected: bool | None = None
        self.last_market_data_ms: int = 0
        self.last_balance_ms: int = 0
        self.orders_ok: int = 0
        self.orders_failed: int = 0

    def record_latency(self, ms: float) -> None:
        self.latencies.append(ms)

    def record_error(self) -> None:
        self.errors.append(time.monotonic())

    def record_order(self, ok: bool) -> None:
        if ok:
            self.orders_ok += 1
        else:
            self.orders_failed += 1
            self.order_failures.append(time.monotonic())

    def errors_last(self, seconds: float) -> int:
        cutoff = time.monotonic() - seconds
        return sum(1 for t in self.errors if t >= cutoff)

    def order_failures_last(self, seconds: float) -> int:
        cutoff = time.monotonic() - seconds
        return sum(1 for t in self.order_failures if t >= cutoff)

    @property
    def avg_latency_ms(self) -> float | None:
        return sum(self.latencies) / len(self.latencies) if self.latencies else None

    @property
    def rate_limited(self) -> bool:
        return time.monotonic() < self.rate_limited_until

    def classify(self, venue: str, *, market_data_max_age_ms: int = 15_000, expect_ws: bool = False) -> HealthReport:
        reasons: list[str] = []
        health = VenueHealth.HEALTHY
        md_age = (now_ms() - self.last_market_data_ms) if self.last_market_data_ms else None
        bal_age = (now_ms() - self.last_balance_ms) if self.last_balance_ms else None
        if self.maintenance:
            health = VenueHealth.BLOCKED
            reasons.append("exchange maintenance")
        if self.rate_limited:
            health = max_health(health, VenueHealth.DEGRADED)
            reasons.append("rate limited")
        if md_age is None:
            health = max_health(health, VenueHealth.UNHEALTHY)
            reasons.append("no market data yet")
        elif md_age > market_data_max_age_ms:
            health = max_health(health, VenueHealth.UNHEALTHY if md_age > market_data_max_age_ms * 4 else VenueHealth.DEGRADED)
            reasons.append(f"market data {md_age // 1000}s old")
        lat = self.avg_latency_ms
        if lat is not None and lat > 2500:
            health = max_health(health, VenueHealth.DEGRADED)
            reasons.append(f"high API latency ({lat:.0f} ms)")
        err = self.errors_last(300)
        if err >= 10:
            health = max_health(health, VenueHealth.UNHEALTHY)
            reasons.append(f"{err} API errors in 5 min")
        elif err >= 3:
            health = max_health(health, VenueHealth.DEGRADED)
            reasons.append(f"{err} API errors in 5 min")
        of = self.order_failures_last(3600)
        if of >= 3:
            health = max_health(health, VenueHealth.DEGRADED)
            reasons.append(f"{of} order failures in 1h")
        if expect_ws and self.ws_connected is False:
            health = max_health(health, VenueHealth.DEGRADED)
            reasons.append("websocket disconnected (REST fallback)")
        total = self.orders_ok + self.orders_failed
        reliability = (self.orders_ok / total * 100) if total else None
        return HealthReport(
            venue=venue,
            health=health,
            api_latency_ms=lat,
            ws_connected=self.ws_connected,
            market_data_age_ms=md_age,
            balance_age_ms=bal_age,
            order_failures_1h=of,
            rate_limited=self.rate_limited,
            maintenance=self.maintenance,
            execution_reliability_pct=reliability,
            reasons=reasons,
        )


_ORDER = [VenueHealth.HEALTHY, VenueHealth.DEGRADED, VenueHealth.UNHEALTHY, VenueHealth.BLOCKED]


def max_health(a: VenueHealth, b: VenueHealth) -> VenueHealth:
    """Return the *worse* of two health states."""
    if a is VenueHealth.UNKNOWN:
        return b
    if b is VenueHealth.UNKNOWN:
        return a
    return _ORDER[max(_ORDER.index(a), _ORDER.index(b))]


class VenueConnector(ABC):
    name: str
    display_name: str
    kind: VenueKind
    chain: Chain | None = None
    mode: TradingMode
    verification: VerificationLevel = VerificationLevel.NOT_VERIFIED_LIVE
    capabilities: set[str]

    def __init__(self, name: str, kind: VenueKind, mode: TradingMode, display_name: str | None = None):
        self.name = name
        self.display_name = display_name or name.capitalize()
        self.kind = kind
        self.mode = mode
        self.capabilities = set()
        self.health_tracker = HealthTracker()
        self.markets: dict[str, MarketInfo] = {}
        self.connected = False
        self.trading_enabled = False
        self.last_error: str | None = None

    # --- lifecycle ---------------------------------------------------------------
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def load_markets(self) -> dict[str, MarketInfo]: ...

    def market(self, symbol: str) -> MarketInfo | None:
        return self.markets.get(symbol)

    def supports_symbol(self, symbol: str) -> bool:
        return symbol in self.markets

    # --- market data / quotes --------------------------------------------------------
    async def fetch_order_book(self, symbol: str, depth: int = 50) -> OrderBook:
        raise NotSupportedError(f"{self.name} has no order book")

    @abstractmethod
    async def get_quote(
        self, symbol: str, side, base_amount: Decimal, *, order_book: OrderBook | None = None
    ) -> ExecutionQuote: ...

    @abstractmethod
    async def fetch_fees(self, symbol: str) -> FeeSchedule: ...

    # --- account -----------------------------------------------------------------
    @abstractmethod
    async def fetch_balances(self) -> dict[str, Balance]: ...

    @abstractmethod
    async def place_order(self, req: OrderRequest) -> OrderResult: ...

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        raise NotSupportedError("cancel not supported")

    async def fetch_order(self, order_id: str, symbol: str) -> OrderResult | None:
        return None

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict]:
        return []

    # --- health ------------------------------------------------------------------
    async def health_check(self) -> HealthReport:
        return self.health_tracker.classify(self.name, expect_ws="ws" in self.capabilities)

    def status_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "kind": self.kind.value,
            "chain": self.chain.value if self.chain else None,
            "mode": self.mode.value,
            "connected": self.connected,
            "trading_enabled": self.trading_enabled,
            "markets": len(self.markets),
            "capabilities": sorted(self.capabilities),
            "verification": self.verification.value,
            "last_error": self.last_error,
        }
