"""Domain enumerations shared across the platform.

Every enum here is a `str` enum so it serialises cleanly into JSON, SQLite and
the audit log without extra glue.
"""
from __future__ import annotations

from enum import Enum


class TradingMode(str, Enum):
    """Execution mode. P&L is NEVER mixed between modes."""

    SIMULATION = "simulation"  # replayed / synthetic data, no external execution
    PAPER = "paper"  # real market data, simulated balances and execution
    TESTNET = "testnet"  # real testnet APIs and test assets
    LIVE = "live"  # real money - explicit UI activation required


class Strategy(str, Enum):
    CEX_CEX = "cex_cex"
    CEX_DEX = "cex_dex"
    DEX_DEX = "dex_dex"
    SPOT_PERP = "spot_perp"
    FUNDING = "funding"
    BASIS = "basis"
    FLASH_LOAN = "flash_loan"


class VenueKind(str, Enum):
    CEX = "cex"
    DEX = "dex"
    PERP = "perp"  # a perpetual-futures market on a CEX


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

    def opposite(self) -> OrderSide:
        return OrderSide.SELL if self is OrderSide.BUY else OrderSide.BUY


class VenueHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ConnectorStatus(str, Enum):
    """Progressive connector lifecycle. Each level implies the previous ones."""

    SUPPORTED = "supported"  # a connector implementation exists
    CONNECTED = "connected"  # credentials / endpoint validated
    HEALTHY = "healthy"  # market data + balances fresh, latency OK
    TRADEABLE = "tradeable"  # order placement verified or explicitly enabled
    ARBITRAGE_ELIGIBLE = "arbitrage_eligible"  # passes all engine requirements


class VerificationLevel(str, Enum):
    NOT_VERIFIED_LIVE = "supported_not_verified_live"
    TESTNET_VERIFIED = "testnet_verified"
    LIVE_VERIFIED = "live_verified"


class Decision(str, Enum):
    SAFE_TO_EXECUTE = "safe_to_execute"
    BLOCKED = "blocked"


class GasRegime(str, Enum):
    NORMAL = "normal"
    ELEVATED = "elevated"
    HIGH = "high"
    EXTREME = "extreme"


class TradeStatus(str, Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    FILLED = "filled"
    PARTIAL = "partial"
    FAILED = "failed"
    ABORTED = "aborted"  # aborted before any leg was submitted
    RECOVERING = "recovering"  # one-leg fill / imbalance -> emergency handling
    HEDGED = "hedged"
    CANCELLED = "cancelled"


class OrderStatus(str, Enum):
    NEW = "new"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


class RiskProfile(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    CUSTOM = "custom"


class CircuitBreakerReason(str, Enum):
    UNEXPECTED_FEES = "unexpected_fees"
    ABNORMAL_SLIPPAGE = "abnormal_slippage"
    REPEATED_ORDER_FAILURE = "repeated_order_failure"
    DEX_TX_FAILURE = "dex_tx_failure"
    RPC_FAILURE = "rpc_failure"
    WEBSOCKET_FAILURE = "websocket_failure"
    BALANCE_DISCREPANCY = "balance_discrepancy"
    POSITION_DISCREPANCY = "position_discrepancy"
    PREDICTION_ERROR = "prediction_error"
    RAPID_MARKET_MOVE = "rapid_market_move"
    GAS_SPIKE = "gas_spike"
    FUNDING_ANOMALY = "funding_anomaly"
    EXCHANGE_MAINTENANCE = "exchange_maintenance"
    RATE_LIMIT = "rate_limit"
    ONE_LEG_FILL = "one_leg_fill"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    MANUAL = "manual"


class Chain(str, Enum):
    SOLANA = "solana"
    ETHEREUM = "ethereum"
    BASE = "base"
    ARBITRUM = "arbitrum"
    OPTIMISM = "optimism"
    POLYGON = "polygon"
    BSC = "bsc"
    AVALANCHE = "avalanche"

    @property
    def is_evm(self) -> bool:
        return self is not Chain.SOLANA

    @property
    def native_token(self) -> str:
        return {
            Chain.SOLANA: "SOL",
            Chain.ETHEREUM: "ETH",
            Chain.BASE: "ETH",
            Chain.ARBITRUM: "ETH",
            Chain.OPTIMISM: "ETH",
            Chain.POLYGON: "POL",
            Chain.BSC: "BNB",
            Chain.AVALANCHE: "AVAX",
        }[self]


class AuditEventType(str, Enum):
    DECISION = "decision"
    EXECUTION = "execution"
    SETTINGS_CHANGE = "settings_change"
    MODE_CHANGE = "mode_change"
    LIVE_ACTIVATION = "live_activation"
    EMERGENCY_STOP = "emergency_stop"
    CIRCUIT_BREAKER = "circuit_breaker"
    WALLET = "wallet"
    EXCHANGE = "exchange"
    RECONCILIATION = "reconciliation"
    SECURITY = "security"
    SYSTEM = "system"
