"""SQLAlchemy ORM models. Money values are stored as TEXT decimals (exact)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SettingsRow(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ExchangeAccount(Base):
    __tablename__ = "exchange_accounts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    exchange_id: Mapped[str] = mapped_column(String(40), index=True)  # ccxt id
    label: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(16), index=True)  # paper | testnet | live (credential scope)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sandbox: Mapped[bool] = mapped_column(Boolean, default=False)
    credentials_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # AES-GCM blob
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)  # {read, trade, withdraw}
    status: Mapped[str] = mapped_column(String(32), default="supported")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Wallet(Base):
    __tablename__ = "wallets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    chain: Mapped[str] = mapped_column(String(24), index=True)  # evm | solana (bot signer family)
    address: Mapped[str] = mapped_column(String(128), index=True)
    label: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))  # bot | external
    provider: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # metamask | phantom | ledger | other
    key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # bot wallets only
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)
    backup_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="live")  # testnet | live key scope
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OpportunityRow(Base):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    strategy: Mapped[str] = mapped_column(String(24), index=True)
    pair: Mapped[str] = mapped_column(String(32), index=True)
    route: Mapped[str] = mapped_column(String(120))
    decision: Mapped[str] = mapped_column(String(24), index=True)
    gross_pct: Mapped[str] = mapped_column(String(32))
    expected_net_usd: Mapped[str] = mapped_column(String(32))
    worst_case_usd: Mapped[str] = mapped_column(String(32))
    risk_score: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    strategy: Mapped[str] = mapped_column(String(24), index=True)
    opportunity_id: Mapped[str] = mapped_column(String(40))
    pair: Mapped[str] = mapped_column(String(32), index=True)
    route: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), index=True)
    size_base: Mapped[str] = mapped_column(String(40))
    estimated_gross: Mapped[str] = mapped_column(String(32))
    estimated_net: Mapped[str] = mapped_column(String(32))
    estimated_worst_case: Mapped[str] = mapped_column(String(32))
    actual_gross: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actual_fees: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actual_gas: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actual_net: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prediction_error: Mapped[str | None] = mapped_column(String(32), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON)  # full TradeRecord (legs, fills, events, costs)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    venue: Mapped[str] = mapped_column(String(40), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    amount: Mapped[str] = mapped_column(String(40))
    limit_price: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    filled: Mapped[str] = mapped_column(String(40), default="0")
    avg_price: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fee_quote: Mapped[str] = mapped_column(String(40), default="0")
    gas_cost_usd: Mapped[str] = mapped_column(String(40), default="0")
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FillRow(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(80), index=True)
    trade_id: Mapped[str] = mapped_column(String(40), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    venue: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    amount: Mapped[str] = mapped_column(String(40))
    price: Mapped[str] = mapped_column(String(40))
    fee_amount: Mapped[str] = mapped_column(String(40))
    fee_asset: Mapped[str] = mapped_column(String(16))
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BalanceSnapshot(Base):
    __tablename__ = "balances"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    venue: Mapped[str] = mapped_column(String(40), index=True)
    asset: Mapped[str] = mapped_column(String(16))
    free: Mapped[str] = mapped_column(String(40))
    used: Mapped[str] = mapped_column(String(40))
    usd_value: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PaperBalance(Base):
    """Persistent simulated ledger (paper / simulation modes)."""

    __tablename__ = "paper_balances"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    venue: Mapped[str] = mapped_column(String(40), index=True)
    asset: Mapped[str] = mapped_column(String(16))
    free: Mapped[str] = mapped_column(String(40))
    used: Mapped[str] = mapped_column(String(40), default="0")
    __table_args__ = (Index("ix_paper_balance_key", "mode", "venue", "asset", unique=True),)


class InventoryTarget(Base):
    __tablename__ = "inventory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    venue: Mapped[str] = mapped_column(String(40))
    asset: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(40), default="0")
    minimum: Mapped[str] = mapped_column(String(40), default="0")
    maximum: Mapped[str | None] = mapped_column(String(40), nullable=True)
    __table_args__ = (Index("ix_inventory_key", "mode", "venue", "asset", unique=True),)


class PnlDaily(Base):
    __tablename__ = "pnl_daily"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (UTC)
    gross: Mapped[str] = mapped_column(String(40), default="0")
    trading_fees: Mapped[str] = mapped_column(String(40), default="0")
    gas: Mapped[str] = mapped_column(String(40), default="0")
    funding: Mapped[str] = mapped_column(String(40), default="0")
    slippage: Mapped[str] = mapped_column(String(40), default="0")
    rebalancing: Mapped[str] = mapped_column(String(40), default="0")
    other: Mapped[str] = mapped_column(String(40), default="0")
    net: Mapped[str] = mapped_column(String(40), default="0")
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (Index("ix_pnl_key", "mode", "day", unique=True),)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[str] = mapped_column(String(16))  # info | warning | critical
    kind: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CircuitBreakerRow(Base):
    __tablename__ = "circuit_breakers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reason: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text)
    auto: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    tripped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(40), default="system")
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ExperienceStat(Base):
    """Learned execution statistics per route key (EWMA)."""

    __tablename__ = "experience"
    key: Mapped[str] = mapped_column(
        String(160), primary_key=True
    )  # mode|strategy|buy_venue|sell_venue|pair|size_bucket
    samples: Mapped[int] = mapped_column(Integer, default=0)
    error_pct_ewma: Mapped[str] = mapped_column(String(40), default="0")  # (estimated-actual)/notional %
    slippage_bias_pct: Mapped[str] = mapped_column(String(40), default="0")
    fee_variance_pct: Mapped[str] = mapped_column(String(40), default="0")
    gas_variance_pct: Mapped[str] = mapped_column(String(40), default="0")
    fill_reliability: Mapped[str] = mapped_column(String(40), default="1")
    latency_ms_ewma: Mapped[str] = mapped_column(String(40), default="0")
    extra_buffer_pct: Mapped[str] = mapped_column(String(40), default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ShadowRecord(Base):
    __tablename__ = "shadow_records"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    would_trade: Mapped[bool] = mapped_column(Boolean, index=True)
    strategy: Mapped[str] = mapped_column(String(24))
    pair: Mapped[str] = mapped_column(String(32))
    route: Mapped[str] = mapped_column(String(120))
    size_base: Mapped[str] = mapped_column(String(40))
    predicted_profit: Mapped[str] = mapped_column(String(40))
    worst_case_profit: Mapped[str] = mapped_column(String(40))
    estimated_costs: Mapped[str] = mapped_column(String(40))
    hypothetical_profit: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )  # re-priced after latency
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Rebalance(Base):
    __tablename__ = "rebalances"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(24))  # recommended | dismissed | executed
    from_venue: Mapped[str] = mapped_column(String(40))
    to_venue: Mapped[str] = mapped_column(String(40))
    asset: Mapped[str] = mapped_column(String(16))
    amount: Mapped[str] = mapped_column(String(40))
    estimated_cost_usd: Mapped[str] = mapped_column(String(40))
    estimated_benefit_usd: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExecutionError(Base):
    __tablename__ = "execution_errors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    mode: Mapped[str] = mapped_column(String(16))
    venue: Mapped[str | None] = mapped_column(String(40), nullable=True)
    trade_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    label: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)


class MarketCache(Base):
    __tablename__ = "markets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String(40), index=True)
    symbol: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (Index("ix_market_key", "venue", "symbol", unique=True),)
