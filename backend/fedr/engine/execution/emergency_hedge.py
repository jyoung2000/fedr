"""Emergency hedge engine: neutralise unbalanced exposure after a one-leg fill.

Priorities: liquidity, execution certainty, low slippage, venue health.
Profit is irrelevant during recovery - risk reduction comes first.
"""

from __future__ import annotations

from decimal import Decimal

from fedr.connectors.base import ConnectorError, VenueConnector
from fedr.core.enums import OrderSide, OrderStatus, VenueHealth, VenueKind
from fedr.core.logging import get_logger
from fedr.core.models import ExecutionQuote, OrderRequest, OrderResult, TradeRecord, new_id
from fedr.core.money import HUNDRED, ZERO, D

log = get_logger("hedge")


class EmergencyHedger:
    def __init__(self, ctx):
        self.ctx = ctx

    async def hedge(
        self,
        trade: TradeRecord,
        pair: str,
        exposure_base: Decimal,
        exclude: set[str] | None = None,
        before_submit=None,
        submit=None,
    ) -> OrderResult | None:
        """Positive exposure = we are long base (need to SELL); negative = short (need to BUY).

        Bounded policy: at most three venue attempts, each for the CONFIRMED remaining imbalance only. Every
        attempt is journaled (``before_submit``) before transport. An attempt whose outcome is unknown ends
        the hedge immediately - trying another venue could double the hedge."""
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
                client_order_id=new_id("fh"),
                min_received=(q.quote_amount * (1 - tol)) if side is OrderSide.SELL else None,
                quote_id=q.quote_id,
                deadline_ms=self.ctx.settings.trading.execution_timeout_ms,
                extra={"quoted_price": str(q.avg_price), "emergency": True},
            )
            trade.log("hedge_attempt", venue=venue.name, side=side.value, amount=amount, limit=limit)
            if before_submit is not None:
                try:
                    await before_submit(OrderResult(request=req, order_id="", status=OrderStatus.SUBMITTING))
                except Exception as exc:
                    trade.log("hedge_not_journaled", venue=venue.name, error=str(exc)[:120])
                    return None  # never transmit without a durable intent
            paper = self.ctx.is_simulated_execution and self.ctx.paper_executor is not None
            lv = self.ctx.ledger_venue_for(venue)
            reserved = ZERO
            try:
                if paper:
                    reserved = (
                        amount
                        if side is OrderSide.SELL
                        else q.quote_amount * (1 + tol) * (1 + D(q.fee_pct or 0) / HUNDRED)
                    )
                    await self.ctx.ledger.reserve(lv, base if side is OrderSide.SELL else quote, reserved)
            except ValueError as exc:
                trade.log("hedge_error", venue=venue.name, error=str(exc))
                continue
            try:
                if submit is not None:
                    res = await submit(venue, req, q)
                elif paper:
                    res = await self.ctx.paper_executor.execute_leg(venue, req, q)
                else:
                    res = await venue.place_order(req)
            except ConnectorError as exc:  # pre-transport validation: nothing was sent
                trade.log("hedge_error", venue=venue.name, error=str(exc))
                res = OrderResult(request=req, order_id="", status=OrderStatus.REJECTED, error=str(exc)[:200])
            except Exception as exc:
                res = OrderResult(request=req, order_id="", status=OrderStatus.UNKNOWN, error=str(exc)[:200])
                res.raw["unknown_reason"] = f"{type(exc).__name__} during hedge submission"
            if paper:
                used = res.filled if side is OrderSide.SELL else (res.quote_value or ZERO) + res.fee_quote
                await self.ctx.ledger.release(
                    lv, base if side is OrderSide.SELL else quote, max(ZERO, reserved - used)
                )
            if res.outcome_unknown:
                trade.log("hedge_outcome_unknown", venue=venue.name, order=req.client_order_id)
                return res
            if res.filled > 0:
                trade.log("hedge_filled", venue=venue.name, filled=res.filled, price=res.avg_price)
                return res
            if before_submit is not None:
                await before_submit(res)  # journal the final (unfilled) state of this attempt
        return None
