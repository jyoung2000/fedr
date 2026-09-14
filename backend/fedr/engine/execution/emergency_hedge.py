"""Emergency hedge engine: neutralise unbalanced exposure after a one-leg fill.

Priorities: liquidity, execution certainty, low slippage, venue health.
Profit is irrelevant during recovery - risk reduction comes first.
"""

from __future__ import annotations

from decimal import Decimal

from fedr.connectors.base import ConnectorError, VenueConnector
from fedr.core.enums import OrderSide, VenueHealth, VenueKind
from fedr.core.logging import get_logger
from fedr.core.models import ExecutionQuote, OrderRequest, OrderResult, TradeRecord, new_id
from fedr.core.money import HUNDRED, D

log = get_logger("hedge")


class EmergencyHedger:
    def __init__(self, ctx):
        self.ctx = ctx

    async def hedge(
        self, trade: TradeRecord, pair: str, exposure_base: Decimal, exclude: set[str] | None = None
    ) -> OrderResult | None:
        """Positive exposure = we are long base (need to SELL); negative = short (need to BUY)."""
        if exposure_base == 0:
            return None
        side = OrderSide.SELL if exposure_base > 0 else OrderSide.BUY
        amount = abs(exposure_base)
        base, quote = pair.split("/")
        candidates: list[tuple[int, VenueConnector, ExecutionQuote]] = []
        for c in self.ctx.connectors.values():
            if not c.connected or not c.supports_symbol(pair) or (exclude and c.name in exclude):
                continue
            h = self.ctx.venue_health(c.name)
            if h in (VenueHealth.UNHEALTHY, VenueHealth.BLOCKED):
                continue
            # must hold the asset we sell / the quote we spend
            lv = self.ctx.ledger_venue_for(c)
            need_asset = base if side is OrderSide.SELL else quote
            if self.ctx.inventory.available(lv, need_asset) <= 0:
                continue
            try:
                q = await c.get_quote(pair, side, amount, order_book=self.ctx.hub.get_book(c.name, pair))
            except ConnectorError:
                continue
            if not q.fully_fillable or q.avg_price <= 0:
                continue
            score = 0
            score += 0 if h is VenueHealth.HEALTHY else 40
            score += int((q.slippage_pct + q.price_impact_pct) * 100)
            score += (
                25 if c.kind is VenueKind.DEX else 0
            )  # certainty: CEX fills are more predictable than tx inclusion
            score -= min(20, q.levels_consumed)  # deeper books = better
            candidates.append((score, c, q))
        if not candidates:
            trade.log("hedge_no_venue", exposure=exposure_base)
            return None
        candidates.sort(key=lambda x: x[0])
        max_slip = self.ctx.settings.risk.max_slippage_pct * 3  # accept worse prices to get flat
        for _, venue, q in candidates[:3]:
            tol = max_slip / HUNDRED
            limit = q.avg_price * (1 - tol) if side is OrderSide.SELL else q.avg_price * (1 + tol)
            req = OrderRequest(
                venue=venue.name,
                symbol=pair,
                side=side,
                amount=amount,
                limit_price=limit,
                time_in_force="IOC",
                client_order_id=new_id("hedge"),
                min_received=(q.quote_amount * (1 - tol)) if side is OrderSide.SELL else None,
                quote_id=q.quote_id,
                deadline_ms=self.ctx.settings.trading.execution_timeout_ms,
                extra={"quoted_price": str(q.avg_price), "emergency": True},
            )
            trade.log("hedge_attempt", venue=venue.name, side=side.value, amount=amount, limit=limit)
            try:
                if self.ctx.is_simulated_execution and self.ctx.paper_executor is not None:
                    lv = self.ctx.ledger_venue_for(venue)
                    if side is OrderSide.SELL:
                        await self.ctx.ledger.reserve(lv, base, amount)
                    else:
                        await self.ctx.ledger.reserve(
                            lv, quote, q.quote_amount * (1 + tol) * (1 + D(q.fee_pct or 0) / HUNDRED)
                        )
                    res = await self.ctx.paper_executor.execute_leg(venue, req, q)
                    if res.filled < amount:
                        rem = amount - res.filled
                        await self.ctx.ledger.release(
                            lv,
                            base if side is OrderSide.SELL else quote,
                            rem if side is OrderSide.SELL else rem * q.avg_price,
                        )
                else:
                    res = await venue.place_order(req)
            except Exception as exc:
                trade.log("hedge_error", venue=venue.name, error=str(exc))
                continue
            if res.filled > 0:
                trade.log("hedge_filled", venue=venue.name, filled=res.filled, price=res.avg_price)
                return res
        return None
