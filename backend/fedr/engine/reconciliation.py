"""Reconciliation: expected vs actual balances / orders / on-chain transactions.

Any discrepancy beyond tolerance pauses trading via a circuit breaker. In
PAPER/SIMULATION the ledger is checked for internal consistency (no negative
balances, reservations released) and persisted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from fedr.connectors.base import ConnectorError
from fedr.core.enums import CircuitBreakerReason, OrderStatus, VenueKind
from fedr.core.logging import get_logger
from fedr.core.models import now_ms
from fedr.engine.context import EngineContext

log = get_logger("reconcile")

BALANCE_TOLERANCE_PCT = Decimal("0.5")
BALANCE_TOLERANCE_ABS_USD = Decimal("1.00")


@dataclass(slots=True)
class ReconciliationReport:
    ok: bool
    checked_venues: list[str] = field(default_factory=list)
    discrepancies: list[str] = field(default_factory=list)
    unresolved_orders: list[str] = field(default_factory=list)
    ts_ms: int = field(default_factory=now_ms)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checked_venues": self.checked_venues, "discrepancies": self.discrepancies, "unresolved_orders": self.unresolved_orders, "ts_ms": self.ts_ms}


class Reconciler:
    def __init__(self, ctx: EngineContext):
        self.ctx = ctx
        self.last: ReconciliationReport | None = None
        self.expected: dict[tuple[str, str], Decimal] = {}  # (ledger venue, asset) -> expected total

    def set_expected(self, venue: str, asset: str, total: Decimal) -> None:
        self.expected[(venue, asset)] = total

    async def run_once(self) -> ReconciliationReport:
        ctx = self.ctx
        rep = ReconciliationReport(ok=True)
        if ctx.is_simulated_execution:
            await self._reconcile_paper(rep)
        else:
            await self._reconcile_real(rep)
        rep.ok = not rep.discrepancies
        self.last = rep
        if not rep.ok:
            await ctx.breakers.trip(CircuitBreakerReason.BALANCE_DISCREPANCY, "; ".join(rep.discrepancies)[:250])
            if ctx.repo:
                await ctx.repo.add_risk_event(ctx.mode, "critical", "reconciliation", "; ".join(rep.discrepancies), rep.as_dict())
                await ctx.repo.audit(ctx.mode, "reconciliation", "Reconciliation found discrepancies - trading paused", rep.as_dict())
        return rep

    async def _reconcile_paper(self, rep: ReconciliationReport) -> None:
        ctx = self.ctx
        if ctx.ledger is None:
            return
        for venue, assets in ctx.ledger.dump().items():
            rep.checked_venues.append(venue)
            for asset, (free, used) in assets.items():
                if free < 0:
                    rep.discrepancies.append(f"paper ledger negative balance: {asset} on {venue} = {free}")
                if used > 0 and not ctx.open_trade_ids:
                    # a reservation survived its trade -> release it (self-healing, but logged)
                    await ctx.ledger.release(venue, asset, used)
                    log.warning("released stale paper reservation", venue=venue, asset=asset, amount=str(used))
        if ctx.repo:
            await ctx.repo.save_paper_balances(ctx.mode, ctx.ledger.dump())

    async def _reconcile_real(self, rep: ReconciliationReport) -> None:
        ctx = self.ctx
        for c in ctx.connectors.values():
            if not c.connected:
                continue
            lv = ctx.ledger_venue_for(c)
            try:
                balances = await c.fetch_balances()
            except ConnectorError as exc:
                rep.discrepancies.append(f"{c.name}: balance fetch failed ({exc})")
                continue
            rep.checked_venues.append(c.name)
            for asset, b in balances.items():
                exp = self.expected.get((lv, asset))
                if exp is None:
                    continue
                px = ctx.price_usd(asset) or Decimal("1")
                diff = abs(b.total - exp)
                if diff * px > BALANCE_TOLERANCE_ABS_USD and (exp == 0 or diff / exp * 100 > BALANCE_TOLERANCE_PCT):
                    rep.discrepancies.append(f"{c.name}: {asset} expected {exp} but exchange reports {b.total}")
                self.expected[(lv, asset)] = b.total  # after reporting, adopt reality so we don't repeat forever
            if c.kind in (VenueKind.CEX, VenueKind.PERP):
                try:
                    open_orders = await c.fetch_open_orders()
                except ConnectorError:
                    open_orders = []
                for o in open_orders:
                    oid = str(o.get("id"))
                    rep.unresolved_orders.append(f"{c.name}:{oid}")
                    if not ctx.open_trade_ids:
                        rep.discrepancies.append(f"{c.name}: untracked open order {oid} ({o.get('symbol')})")
        # unresolved DEX transactions from recent trades
        for tr in ctx.extra.get("recent_trades", []):
            for leg in (tr.buy, tr.sell, tr.hedge):
                if leg is not None and leg.status is OrderStatus.OPEN and leg.tx_hash:
                    rep.unresolved_orders.append(f"{leg.request.venue}:{leg.tx_hash}")
                    rep.discrepancies.append(f"{leg.request.venue}: transaction {leg.tx_hash[:12]}… still unconfirmed")
