"""Core domain dataclasses.

These are the in-memory objects the engines exchange. They are deliberately
plain dataclasses (not ORM models) so the engine stays testable without a
database. Persistence mapping lives in ``fedr.db``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from fedr.core.enums import (
    Chain,
    Decision,
    GasRegime,
    OrderSide,
    OrderStatus,
    Strategy,
    TradeStatus,
    TradingMode,
    VenueHealth,
    VenueKind,
)
from fedr.core.money import ZERO, D


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


# --------------------------------------------------------------------------- markets


@dataclass(slots=True)
class MarketInfo:
    venue: str
    symbol: str  # unified "BASE/QUOTE" (perps: "BASE/QUOTE:SETTLE")
    base: str
    quote: str
    kind: VenueKind
    active: bool = True
    amount_step: Decimal = Decimal("0.00000001")
    price_step: Decimal = Decimal("0.00000001")
    min_amount: Decimal = ZERO
    max_amount: Decimal | None = None
    min_cost: Decimal = ZERO
    taker_fee_pct: Decimal | None = None  # None => unknown (Profit Guard will block/estimate)
    maker_fee_pct: Decimal | None = None
    contract_size: Decimal = Decimal("1")
    settle: str | None = None
    chain: Chain | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FeeSchedule:
    taker_pct: Decimal | None
    maker_pct: Decimal | None
    source: str  # "exchange_api" | "market_metadata" | "documented_fallback" | "unknown"
    fetched_at_ms: int = field(default_factory=now_ms)

    @property
    def known(self) -> bool:
        return self.taker_pct is not None


@dataclass(slots=True)
class Balance:
    asset: str
    free: Decimal = ZERO
    used: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.free + self.used


# --------------------------------------------------------------------------- quotes


@dataclass(slots=True)
class ExecutionQuote:
    """An *executable* quote for one leg at an exact size.

    For CEX legs it is the result of walking the live order book; for DEX legs it
    is a router quote for the exact amount. It is never a ticker/mid/last price.
    """

    venue: str
    kind: VenueKind
    symbol: str
    side: OrderSide
    base_amount: Decimal
    quote_amount: Decimal  # gross cost (buy) / gross proceeds (sell), before fees
    avg_price: Decimal
    reference_price: Decimal  # top-of-book / spot price used only for slippage math
    slippage_pct: Decimal  # avg vs reference (depth slippage)
    price_impact_pct: Decimal  # DEX-reported price impact, else 0
    fully_fillable: bool  # False when the book/route can't absorb the size
    market_ts_ms: int  # timestamp of the market data used
    quote_ts_ms: int = field(default_factory=now_ms)
    fee_pct: Decimal | None = None  # taker/swap fee percentage (None = unknown)
    fee_source: str = "unknown"
    fee_in_quote: Decimal | None = None  # explicit fee amount in quote asset if known
    gas_cost_usd: Decimal | None = None  # DEX only, None = unknown
    gas_limit: int | None = None
    gas_price_native: Decimal | None = None
    priority_fee_usd: Decimal = ZERO
    min_received: Decimal | None = None  # DEX minimum output at configured slippage
    route: str = ""  # human readable route description
    quote_id: str | None = None
    chain: Chain | None = None
    levels_consumed: int = 0
    depth_available_base: Decimal = ZERO
    is_maker: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def age_ms(self) -> int:
        return now_ms() - min(self.market_ts_ms, self.quote_ts_ms)

    def net_quote_amount(self) -> Decimal | None:
        """Quote amount after trading fee (None if fee unknown)."""
        fee = self.fee_in_quote
        if fee is None:
            if self.fee_pct is None:
                return None
            fee = self.quote_amount * self.fee_pct / Decimal(100)
        return self.quote_amount + fee if self.side is OrderSide.BUY else self.quote_amount - fee


# --------------------------------------------------------------------------- profit guard


@dataclass(slots=True)
class CostBreakdown:
    """Every cost the Profit Guard subtracts, in the quote/USD unit of the trade."""

    buy_trading_fee: Decimal = ZERO
    sell_trading_fee: Decimal = ZERO
    maker_taker_adjustment: Decimal = ZERO
    dex_swap_fee: Decimal = ZERO
    lp_fee: Decimal = ZERO
    gas: Decimal = ZERO
    priority_fee: Decimal = ZERO
    slippage: Decimal = ZERO
    price_impact: Decimal = ZERO
    bridge_fee: Decimal = ZERO
    withdrawal_fee: Decimal = ZERO
    deposit_fee: Decimal = ZERO
    funding: Decimal = ZERO
    rebalance_allowance: Decimal = ZERO
    latency_allowance: Decimal = ZERO
    partial_fill_allowance: Decimal = ZERO
    failure_reserve: Decimal = ZERO
    safety_buffer: Decimal = ZERO
    flash_loan_fee: Decimal = ZERO
    mev_reserve: Decimal = ZERO
    unknown: list[str] = field(default_factory=list)  # costs that could not be estimated

    FIELDS = (
        "buy_trading_fee",
        "sell_trading_fee",
        "maker_taker_adjustment",
        "dex_swap_fee",
        "lp_fee",
        "gas",
        "priority_fee",
        "slippage",
        "price_impact",
        "bridge_fee",
        "withdrawal_fee",
        "deposit_fee",
        "funding",
        "rebalance_allowance",
        "latency_allowance",
        "partial_fill_allowance",
        "failure_reserve",
        "safety_buffer",
        "flash_loan_fee",
        "mev_reserve",
    )

    def total(self) -> Decimal:
        return sum((getattr(self, f) for f in self.FIELDS), ZERO)

    def as_dict(self) -> dict[str, str]:
        d = {f: str(getattr(self, f)) for f in self.FIELDS}
        d["total"] = str(self.total())
        return d


@dataclass(slots=True)
class ProfitAssessment:
    gross_profit: Decimal
    expected_net_profit: Decimal
    worst_case_profit: Decimal
    capital_required: Decimal
    gross_spread_pct: Decimal
    expected_net_pct: Decimal
    worst_case_pct: Decimal
    net_roi_pct: Decimal
    required_min_profit: Decimal
    required_min_worst_case: Decimal
    required_min_roi_pct: Decimal
    expected_costs: CostBreakdown
    worst_case_costs: CostBreakdown
    decision: Decision
    reasons: list[str] = field(default_factory=list)  # block reasons (empty when passing)
    explanation: str = ""  # one-sentence, human readable
    buy_execution_price: Decimal = ZERO
    sell_execution_price: Decimal = ZERO

    @property
    def passed(self) -> bool:
        return self.decision is Decision.SAFE_TO_EXECUTE


@dataclass(slots=True)
class GasAssessment:
    chain: Chain | None
    regime: GasRegime
    current_gas_cost_usd: Decimal
    expected_gas_cost_usd: Decimal
    stress_gas_cost_usd: Decimal
    gas_pct_of_gross: Decimal
    passed: bool
    reasons: list[str] = field(default_factory=list)
    gas_price_native: Decimal | None = None
    priority_fee_usd: Decimal = ZERO


@dataclass(slots=True)
class RiskAssessment:
    score: int  # 0 (safest) .. 100
    passed: bool
    reasons: list[str] = field(default_factory=list)
    factors: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- opportunities


@dataclass(slots=True)
class Opportunity:
    id: str
    mode: TradingMode
    strategy: Strategy
    base: str
    quote: str
    buy: ExecutionQuote
    sell: ExecutionQuote
    size_base: Decimal
    profit: ProfitAssessment
    gas: GasAssessment | None
    risk: RiskAssessment
    decision: Decision
    block_reasons: list[str]
    explanation: str
    created_at_ms: int = field(default_factory=now_ms)
    expires_at_ms: int = 0
    alternatives: list[dict[str, Any]] = field(default_factory=list)  # e.g. flash-loan vs prefunded
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def pair(self) -> str:
        return f"{self.base}/{self.quote}"

    @property
    def route(self) -> str:
        return f"{self.buy.venue} → {self.sell.venue}"

    @property
    def is_executable(self) -> bool:
        return self.decision is Decision.SAFE_TO_EXECUTE


# --------------------------------------------------------------------------- execution


@dataclass(slots=True)
class OrderRequest:
    venue: str
    symbol: str
    side: OrderSide
    amount: Decimal
    limit_price: Decimal | None  # None => market (only for DEX swaps with min_received)
    time_in_force: str = "IOC"
    client_order_id: str = field(default_factory=lambda: new_id("ord"))
    min_received: Decimal | None = None
    max_gas_usd: Decimal | None = None
    quote_id: str | None = None
    deadline_ms: int | None = None
    reduce_only: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Fill:
    order_id: str
    venue: str
    symbol: str
    side: OrderSide
    amount: Decimal
    price: Decimal
    fee_amount: Decimal
    fee_asset: str
    ts_ms: int = field(default_factory=now_ms)
    trade_id: str | None = None
    tx_hash: str | None = None
    gas_used: int | None = None
    gas_cost_usd: Decimal | None = None


@dataclass(slots=True)
class OrderResult:
    request: OrderRequest
    order_id: str
    status: OrderStatus
    filled: Decimal = ZERO
    avg_price: Decimal | None = None
    fills: list[Fill] = field(default_factory=list)
    fee_quote: Decimal = ZERO  # total fee expressed in the quote asset
    gas_cost_usd: Decimal = ZERO
    submitted_at_ms: int = field(default_factory=now_ms)
    completed_at_ms: int | None = None
    tx_hash: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def remaining(self) -> Decimal:
        return max(ZERO, self.request.amount - self.filled)

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.FAILED,
        )

    @property
    def quote_amount(self) -> Decimal:
        return sum((f.amount * f.price for f in self.fills), ZERO)


@dataclass(slots=True)
class TradeRecord:
    id: str
    mode: TradingMode
    strategy: Strategy
    opportunity_id: str
    pair: str
    route: str
    size_base: Decimal
    status: TradeStatus
    estimated_gross: Decimal
    estimated_net: Decimal
    estimated_worst_case: Decimal
    estimated_costs: dict[str, str]
    buy: OrderResult | None = None
    sell: OrderResult | None = None
    hedge: OrderResult | None = None
    actual_gross: Decimal | None = None
    actual_fees: Decimal | None = None
    actual_gas: Decimal | None = None
    actual_net: Decimal | None = None
    prediction_error: Decimal | None = None
    explanation: str = ""
    started_at_ms: int = field(default_factory=now_ms)
    completed_at_ms: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def log(self, event: str, **data: Any) -> None:
        self.events.append({"ts": now_ms(), "event": event, **{k: str(v) for k, v in data.items()}})


# --------------------------------------------------------------------------- venue health


@dataclass(slots=True)
class HealthReport:
    venue: str
    health: VenueHealth
    api_latency_ms: float | None = None
    ws_connected: bool | None = None
    market_data_age_ms: int | None = None
    balance_age_ms: int | None = None
    order_failures_1h: int = 0
    rate_limited: bool = False
    maintenance: bool = False
    execution_reliability_pct: float | None = None
    reasons: list[str] = field(default_factory=list)
    checked_at_ms: int = field(default_factory=now_ms)


def to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses / Decimals / enums into JSON-safe values."""
    from dataclasses import fields, is_dataclass
    from enum import Enum

    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    return obj


__all__ = [
    "Balance",
    "CostBreakdown",
    "D",
    "ExecutionQuote",
    "FeeSchedule",
    "Fill",
    "GasAssessment",
    "HealthReport",
    "MarketInfo",
    "Opportunity",
    "OrderRequest",
    "OrderResult",
    "ProfitAssessment",
    "RiskAssessment",
    "TradeRecord",
    "new_id",
    "now_ms",
    "to_jsonable",
]
