"""Repository: all database access used by engines and the API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, desc, func, select, update

from fedr.core.enums import TradingMode
from fedr.core.models import Opportunity, TradeRecord, to_jsonable
from fedr.core.money import ZERO, D
from fedr.db.engine import Database
from fedr.db.models import (
    AuditLog,
    BacktestRun,
    BalanceSnapshot,
    CircuitBreakerRow,
    ExchangeAccount,
    ExecutionError,
    ExperienceStat,
    FillRow,
    InventoryTarget,
    MarketCache,
    OpportunityRow,
    Order,
    PaperBalance,
    PnlDaily,
    Rebalance,
    RiskEvent,
    SettingsRow,
    ShadowRecord,
    Trade,
    Wallet,
    utcnow,
)


def _day(ts: datetime | None = None) -> str:
    return (ts or utcnow()).astimezone(timezone.utc).strftime("%Y-%m-%d")


class Repo:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ settings
    async def get_settings_doc(self, key: str = "app") -> dict | None:
        async with self.db.session() as s:
            row = await s.get(SettingsRow, key)
            return row.value if row else None

    async def save_settings_doc(self, value: dict, key: str = "app") -> None:
        async with self.db.session() as s:
            row = await s.get(SettingsRow, key)
            if row is None:
                s.add(SettingsRow(key=key, value=value))
            else:
                row.value = value

    # ------------------------------------------------------------------ exchanges
    async def list_exchange_accounts(self) -> list[ExchangeAccount]:
        async with self.db.session() as s:
            return list((await s.execute(select(ExchangeAccount).order_by(ExchangeAccount.created_at))).scalars())

    async def get_exchange_account(self, account_id: str) -> ExchangeAccount | None:
        async with self.db.session() as s:
            return await s.get(ExchangeAccount, account_id)

    async def upsert_exchange_account(self, acct: ExchangeAccount) -> None:
        async with self.db.session() as s:
            await s.merge(acct)

    async def update_exchange_account(self, account_id: str, **fields: Any) -> None:
        async with self.db.session() as s:
            await s.execute(update(ExchangeAccount).where(ExchangeAccount.id == account_id).values(**fields))

    async def delete_exchange_account(self, account_id: str) -> None:
        async with self.db.session() as s:
            await s.execute(delete(ExchangeAccount).where(ExchangeAccount.id == account_id))

    # ------------------------------------------------------------------ wallets
    async def list_wallets(self) -> list[Wallet]:
        async with self.db.session() as s:
            return list((await s.execute(select(Wallet).order_by(Wallet.created_at))).scalars())

    async def get_wallet(self, wallet_id: str) -> Wallet | None:
        async with self.db.session() as s:
            return await s.get(Wallet, wallet_id)

    async def upsert_wallet(self, w: Wallet) -> None:
        async with self.db.session() as s:
            await s.merge(w)

    async def update_wallet(self, wallet_id: str, **fields: Any) -> None:
        async with self.db.session() as s:
            await s.execute(update(Wallet).where(Wallet.id == wallet_id).values(**fields))

    async def delete_wallet(self, wallet_id: str) -> None:
        async with self.db.session() as s:
            await s.execute(delete(Wallet).where(Wallet.id == wallet_id))

    # ------------------------------------------------------------------ opportunities
    async def save_opportunity(self, opp: Opportunity) -> None:
        async with self.db.session() as s:
            await s.merge(
                OpportunityRow(
                    id=opp.id,
                    mode=opp.mode.value,
                    strategy=opp.strategy.value,
                    pair=opp.pair,
                    route=opp.route,
                    decision=opp.decision.value,
                    gross_pct=str(opp.profit.gross_spread_pct),
                    expected_net_usd=str(opp.profit.expected_net_profit),
                    worst_case_usd=str(opp.profit.worst_case_profit),
                    risk_score=opp.risk.score,
                    payload=to_jsonable(opp),
                )
            )

    async def prune_opportunities(self, keep: int = 2000) -> None:
        async with self.db.session() as s:
            ids = (
                await s.execute(select(OpportunityRow.id).order_by(desc(OpportunityRow.created_at)).offset(keep))
            ).scalars().all()
            if ids:
                await s.execute(delete(OpportunityRow).where(OpportunityRow.id.in_(ids)))

    async def list_opportunities(self, mode: TradingMode, limit: int = 100) -> list[OpportunityRow]:
        async with self.db.session() as s:
            q = (
                select(OpportunityRow)
                .where(OpportunityRow.mode == mode.value)
                .order_by(desc(OpportunityRow.created_at))
                .limit(limit)
            )
            return list((await s.execute(q)).scalars())

    # ------------------------------------------------------------------ trades / orders / fills
    async def save_trade(self, tr: TradeRecord) -> None:
        payload = to_jsonable(tr)
        async with self.db.session() as s:
            await s.merge(
                Trade(
                    id=tr.id,
                    mode=tr.mode.value,
                    strategy=tr.strategy.value,
                    opportunity_id=tr.opportunity_id,
                    pair=tr.pair,
                    route=tr.route,
                    status=tr.status.value,
                    size_base=str(tr.size_base),
                    estimated_gross=str(tr.estimated_gross),
                    estimated_net=str(tr.estimated_net),
                    estimated_worst_case=str(tr.estimated_worst_case),
                    actual_gross=None if tr.actual_gross is None else str(tr.actual_gross),
                    actual_fees=None if tr.actual_fees is None else str(tr.actual_fees),
                    actual_gas=None if tr.actual_gas is None else str(tr.actual_gas),
                    actual_net=None if tr.actual_net is None else str(tr.actual_net),
                    prediction_error=None if tr.prediction_error is None else str(tr.prediction_error),
                    explanation=tr.explanation,
                    payload=payload,
                    started_at=datetime.fromtimestamp(tr.started_at_ms / 1000, tz=timezone.utc),
                    completed_at=(
                        datetime.fromtimestamp(tr.completed_at_ms / 1000, tz=timezone.utc) if tr.completed_at_ms else None
                    ),
                )
            )
            for leg in (tr.buy, tr.sell, tr.hedge):
                if leg is None:
                    continue
                await s.merge(
                    Order(
                        id=f"{tr.id}:{leg.order_id}",
                        trade_id=tr.id,
                        mode=tr.mode.value,
                        venue=leg.request.venue,
                        symbol=leg.request.symbol,
                        side=leg.request.side.value,
                        amount=str(leg.request.amount),
                        limit_price=None if leg.request.limit_price is None else str(leg.request.limit_price),
                        status=leg.status.value,
                        filled=str(leg.filled),
                        avg_price=None if leg.avg_price is None else str(leg.avg_price),
                        fee_quote=str(leg.fee_quote),
                        gas_cost_usd=str(leg.gas_cost_usd),
                        tx_hash=leg.tx_hash,
                        error=leg.error,
                        payload=to_jsonable(leg.raw),
                    )
                )
                existing = (
                    await s.execute(select(FillRow.trade_id).where(FillRow.order_id == f"{tr.id}:{leg.order_id}"))
                ).first()
                if existing is None:
                    for f in leg.fills:
                        s.add(
                            FillRow(
                                order_id=f"{tr.id}:{leg.order_id}",
                                trade_id=tr.id,
                                mode=tr.mode.value,
                                venue=f.venue,
                                symbol=f.symbol,
                                side=f.side.value,
                                amount=str(f.amount),
                                price=str(f.price),
                                fee_amount=str(f.fee_amount),
                                fee_asset=f.fee_asset,
                                tx_hash=f.tx_hash,
                                ts=datetime.fromtimestamp(f.ts_ms / 1000, tz=timezone.utc),
                            )
                        )

    async def list_trades(self, mode: TradingMode, limit: int = 100, offset: int = 0) -> list[Trade]:
        async with self.db.session() as s:
            q = (
                select(Trade)
                .where(Trade.mode == mode.value)
                .order_by(desc(Trade.started_at))
                .offset(offset)
                .limit(limit)
            )
            return list((await s.execute(q)).scalars())

    async def get_trade(self, trade_id: str) -> Trade | None:
        async with self.db.session() as s:
            return await s.get(Trade, trade_id)

    async def open_trades(self, mode: TradingMode) -> list[Trade]:
        async with self.db.session() as s:
            q = select(Trade).where(
                Trade.mode == mode.value, Trade.status.in_(["pending", "executing", "recovering", "partial"])
            )
            return list((await s.execute(q)).scalars())

    async def count_failed_trades_since(self, mode: TradingMode, since: datetime) -> int:
        async with self.db.session() as s:
            q = select(func.count(Trade.id)).where(
                Trade.mode == mode.value, Trade.status.in_(["failed", "recovering", "hedged"]), Trade.started_at >= since
            )
            return int((await s.execute(q)).scalar() or 0)

    # ------------------------------------------------------------------ P&L
    async def add_pnl(
        self,
        mode: TradingMode,
        *,
        gross: Decimal,
        trading_fees: Decimal,
        gas: Decimal,
        funding: Decimal = ZERO,
        slippage: Decimal = ZERO,
        rebalancing: Decimal = ZERO,
        other: Decimal = ZERO,
        net: Decimal,
        win: bool,
        day: str | None = None,
    ) -> None:
        day = day or _day()
        async with self.db.session() as s:
            row = (
                await s.execute(select(PnlDaily).where(PnlDaily.mode == mode.value, PnlDaily.day == day))
            ).scalar_one_or_none()
            if row is None:
                row = PnlDaily(mode=mode.value, day=day, gross="0", trading_fees="0", gas="0", funding="0", slippage="0", rebalancing="0", other="0", net="0", trades=0, wins=0)
                s.add(row)
            row.gross = str(D(row.gross) + gross)
            row.trading_fees = str(D(row.trading_fees) + trading_fees)
            row.gas = str(D(row.gas) + gas)
            row.funding = str(D(row.funding) + funding)
            row.slippage = str(D(row.slippage) + slippage)
            row.rebalancing = str(D(row.rebalancing) + rebalancing)
            row.other = str(D(row.other) + other)
            row.net = str(D(row.net) + net)
            row.trades += 1
            row.wins += 1 if win else 0

    async def pnl_summary(self, mode: TradingMode) -> dict[str, Any]:
        today = _day()
        async with self.db.session() as s:
            rows = list((await s.execute(select(PnlDaily).where(PnlDaily.mode == mode.value))).scalars())
        total = {k: ZERO for k in ("gross", "trading_fees", "gas", "funding", "slippage", "rebalancing", "other", "net")}
        today_net = ZERO
        trades = wins = 0
        for r in rows:
            for k in total:
                total[k] += D(getattr(r, k))
            trades += r.trades
            wins += r.wins
            if r.day == today:
                today_net = D(r.net)
        return {
            "mode": mode.value,
            "today_net": str(today_net),
            "total": {k: str(v) for k, v in total.items()},
            "trades": trades,
            "wins": wins,
            "days": [
                {"day": r.day, "net": r.net, "gross": r.gross, "fees": r.trading_fees, "gas": r.gas, "trades": r.trades}
                for r in sorted(rows, key=lambda r: r.day)[-30:]
            ],
        }

    async def reset_pnl(self, mode: TradingMode) -> None:
        async with self.db.session() as s:
            await s.execute(delete(PnlDaily).where(PnlDaily.mode == mode.value))

    # ------------------------------------------------------------------ paper ledger
    async def load_paper_balances(self, mode: TradingMode) -> dict[str, dict[str, tuple[Decimal, Decimal]]]:
        async with self.db.session() as s:
            rows = list((await s.execute(select(PaperBalance).where(PaperBalance.mode == mode.value))).scalars())
        out: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}
        for r in rows:
            out.setdefault(r.venue, {})[r.asset] = (D(r.free), D(r.used))
        return out

    async def save_paper_balances(self, mode: TradingMode, balances: dict[str, dict[str, tuple[Decimal, Decimal]]]) -> None:
        async with self.db.session() as s:
            await s.execute(delete(PaperBalance).where(PaperBalance.mode == mode.value))
            for venue, assets in balances.items():
                for asset, (free, used) in assets.items():
                    s.add(PaperBalance(mode=mode.value, venue=venue, asset=asset, free=str(free), used=str(used)))

    # ------------------------------------------------------------------ balances / inventory
    async def snapshot_balances(self, mode: TradingMode, venue: str, balances: dict[str, tuple[Decimal, Decimal, Decimal | None]]) -> None:
        async with self.db.session() as s:
            for asset, (free, used, usd) in balances.items():
                s.add(
                    BalanceSnapshot(
                        mode=mode.value, venue=venue, asset=asset, free=str(free), used=str(used), usd_value=None if usd is None else str(usd)
                    )
                )

    async def prune_balance_snapshots(self, keep_hours: int = 72) -> None:
        cutoff = utcnow() - timedelta(hours=keep_hours)
        async with self.db.session() as s:
            await s.execute(delete(BalanceSnapshot).where(BalanceSnapshot.ts < cutoff))

    async def list_inventory(self, mode: TradingMode) -> list[InventoryTarget]:
        async with self.db.session() as s:
            return list((await s.execute(select(InventoryTarget).where(InventoryTarget.mode == mode.value))).scalars())

    async def upsert_inventory(self, mode: TradingMode, venue: str, asset: str, target: Decimal, minimum: Decimal, maximum: Decimal | None) -> None:
        async with self.db.session() as s:
            row = (
                await s.execute(
                    select(InventoryTarget).where(
                        InventoryTarget.mode == mode.value, InventoryTarget.venue == venue, InventoryTarget.asset == asset
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = InventoryTarget(mode=mode.value, venue=venue, asset=asset)
                s.add(row)
            row.target, row.minimum, row.maximum = str(target), str(minimum), None if maximum is None else str(maximum)

    # ------------------------------------------------------------------ risk / breakers / audit
    async def add_risk_event(self, mode: TradingMode, severity: str, kind: str, detail: str, payload: dict | None = None) -> None:
        async with self.db.session() as s:
            s.add(RiskEvent(mode=mode.value, severity=severity, kind=kind, detail=detail, payload=to_jsonable(payload or {})))

    async def list_risk_events(self, mode: TradingMode, limit: int = 50) -> list[RiskEvent]:
        async with self.db.session() as s:
            q = select(RiskEvent).where(RiskEvent.mode == mode.value).order_by(desc(RiskEvent.ts)).limit(limit)
            return list((await s.execute(q)).scalars())

    async def save_breakers(self, breakers: list[Any]) -> None:
        async with self.db.session() as s:
            await s.execute(update(CircuitBreakerRow).where(CircuitBreakerRow.active.is_(True)).values(active=False, reset_at=utcnow()))
            for b in breakers:
                s.add(
                    CircuitBreakerRow(
                        reason=b.reason.value,
                        scope=b.scope,
                        detail=b.detail,
                        auto=b.auto,
                        active=True,
                        tripped_at=datetime.fromtimestamp(b.tripped_at_ms / 1000, tz=timezone.utc),
                    )
                )

    async def load_active_breakers(self) -> list[CircuitBreakerRow]:
        async with self.db.session() as s:
            return list((await s.execute(select(CircuitBreakerRow).where(CircuitBreakerRow.active.is_(True)))).scalars())

    async def audit(self, mode: TradingMode, event_type: str, summary: str, payload: dict | None = None, actor: str = "system") -> None:
        async with self.db.session() as s:
            s.add(AuditLog(mode=mode.value, event_type=event_type, actor=actor, summary=summary, payload=to_jsonable(payload or {})))

    async def list_audit(self, limit: int = 100, event_type: str | None = None) -> list[AuditLog]:
        async with self.db.session() as s:
            q = select(AuditLog).order_by(desc(AuditLog.ts)).limit(limit)
            if event_type:
                q = q.where(AuditLog.event_type == event_type)
            return list((await s.execute(q)).scalars())

    async def add_execution_error(self, mode: TradingMode, kind: str, message: str, venue: str | None = None, trade_id: str | None = None, payload: dict | None = None) -> None:
        async with self.db.session() as s:
            s.add(ExecutionError(mode=mode.value, kind=kind, message=message, venue=venue, trade_id=trade_id, payload=to_jsonable(payload or {})))

    # ------------------------------------------------------------------ experience
    async def get_experience(self, key: str) -> ExperienceStat | None:
        async with self.db.session() as s:
            return await s.get(ExperienceStat, key)

    async def list_experience(self, prefix: str | None = None, limit: int = 200) -> list[ExperienceStat]:
        async with self.db.session() as s:
            q = select(ExperienceStat).order_by(desc(ExperienceStat.updated_at)).limit(limit)
            if prefix:
                q = q.where(ExperienceStat.key.like(f"{prefix}%"))
            return list((await s.execute(q)).scalars())

    async def save_experience(self, stat: ExperienceStat) -> None:
        async with self.db.session() as s:
            await s.merge(stat)

    # ------------------------------------------------------------------ shadow
    async def add_shadow(self, rec: ShadowRecord) -> None:
        async with self.db.session() as s:
            await s.merge(rec)

    async def list_shadow(self, limit: int = 100) -> list[ShadowRecord]:
        async with self.db.session() as s:
            return list((await s.execute(select(ShadowRecord).order_by(desc(ShadowRecord.ts)).limit(limit))).scalars())

    async def shadow_summary(self) -> dict[str, Any]:
        async with self.db.session() as s:
            rows = list((await s.execute(select(ShadowRecord).where(ShadowRecord.would_trade.is_(True)))).scalars())
            total_rows = int((await s.execute(select(func.count(ShadowRecord.id)))).scalar() or 0)
        predicted = sum((D(r.predicted_profit) for r in rows), ZERO)
        hypo = sum((D(r.hypothetical_profit) for r in rows if r.hypothetical_profit is not None), ZERO)
        return {
            "evaluated": total_rows,
            "would_trade": len(rows),
            "predicted_pnl": str(predicted),
            "hypothetical_pnl": str(hypo),
        }

    async def clear_shadow(self) -> None:
        async with self.db.session() as s:
            await s.execute(delete(ShadowRecord))

    # ------------------------------------------------------------------ rebalances
    async def add_rebalance(self, r: Rebalance) -> None:
        async with self.db.session() as s:
            await s.merge(r)

    async def list_rebalances(self, mode: TradingMode, limit: int = 20) -> list[Rebalance]:
        async with self.db.session() as s:
            q = select(Rebalance).where(Rebalance.mode == mode.value).order_by(desc(Rebalance.ts)).limit(limit)
            return list((await s.execute(q)).scalars())

    # ------------------------------------------------------------------ backtests / markets
    async def save_backtest(self, run: BacktestRun) -> None:
        async with self.db.session() as s:
            await s.merge(run)

    async def list_backtests(self, limit: int = 20) -> list[BacktestRun]:
        async with self.db.session() as s:
            return list((await s.execute(select(BacktestRun).order_by(desc(BacktestRun.ts)).limit(limit))).scalars())

    async def cache_markets(self, venue: str, markets: dict[str, dict]) -> None:
        async with self.db.session() as s:
            await s.execute(delete(MarketCache).where(MarketCache.venue == venue))
            for symbol, payload in markets.items():
                s.add(MarketCache(venue=venue, symbol=symbol, payload=to_jsonable(payload)))

    async def load_cached_markets(self, venue: str) -> dict[str, dict]:
        async with self.db.session() as s:
            rows = list((await s.execute(select(MarketCache).where(MarketCache.venue == venue))).scalars())
        return {r.symbol: r.payload for r in rows}

    # ------------------------------------------------------------------ housekeeping
    async def purge_mode_results(self, mode: TradingMode) -> None:
        """Used by 'Reset Paper Account' - clears results of one mode only."""
        async with self.db.session() as s:
            for model in (Trade, Order, FillRow, OpportunityRow, PnlDaily, RiskEvent, BalanceSnapshot, Rebalance, ExecutionError):
                await s.execute(delete(model).where(model.mode == mode.value))
