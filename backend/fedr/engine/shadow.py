"""Shadow mode: evaluate real opportunities, submit nothing, record what would have happened."""
from __future__ import annotations

import asyncio

from fedr.core.enums import Decision, OrderSide
from fedr.core.logging import get_logger
from fedr.core.models import Opportunity, new_id, now_ms
from fedr.core.money import ZERO
from fedr.db.models import ShadowRecord

log = get_logger("shadow")


class ShadowRecorder:
    def __init__(self, ctx, reprice_delay_s: float = 2.0):
        self.ctx = ctx
        self.reprice_delay_s = reprice_delay_s
        self._seen: dict[str, int] = {}
        self.stats = {"evaluated": 0, "would_trade": 0}

    async def record(self, opp: Opportunity) -> None:
        key = f"{opp.strategy.value}|{opp.pair}|{opp.route}"
        throttle = 0 if opp.decision is Decision.SAFE_TO_EXECUTE else 30_000
        if now_ms() - self._seen.get(key, 0) < throttle:
            return
        self._seen[key] = now_ms()
        would = opp.decision is Decision.SAFE_TO_EXECUTE
        self.stats["evaluated"] += 1
        self.stats["would_trade"] += 1 if would else 0
        rec = ShadowRecord(
            id=new_id("shd"),
            would_trade=would,
            strategy=opp.strategy.value,
            pair=opp.pair,
            route=opp.route,
            size_base=str(opp.size_base),
            predicted_profit=str(opp.profit.expected_net_profit),
            worst_case_profit=str(opp.profit.worst_case_profit),
            estimated_costs=str(opp.profit.expected_costs.total()),
            reason=opp.explanation,
            payload={"costs": opp.profit.expected_costs.as_dict(), "risk": opp.risk.score, "buy_price": str(opp.buy.avg_price), "sell_price": str(opp.sell.avg_price)},
        )
        if self.ctx.repo:
            await self.ctx.repo.add_shadow(rec)
        if would:
            asyncio.create_task(self._reprice(rec, opp))

    async def _reprice(self, rec: ShadowRecord, opp: Opportunity) -> None:
        """After a realistic delay, re-quote both legs to estimate hypothetical execution."""
        await asyncio.sleep(self.reprice_delay_s)
        try:
            b = self.ctx.connectors.get(opp.buy.venue)
            s = self.ctx.connectors.get(opp.sell.venue)
            if b is None or s is None:
                return
            bq, sq = await asyncio.gather(b.get_quote(opp.pair, OrderSide.BUY, opp.size_base, order_book=self.ctx.hub.get_book(b.name, opp.pair)), s.get_quote(opp.sell.symbol, OrderSide.SELL, opp.size_base, order_book=self.ctx.hub.get_book(s.name, opp.sell.symbol)))
            costs = opp.profit.expected_costs
            non_exec = costs.total() - (costs.slippage + costs.price_impact + costs.dex_swap_fee + costs.buy_trading_fee + costs.sell_trading_fee)
            buy_cost = bq.quote_amount * (1 + (bq.fee_pct or ZERO) / 100)
            sell_proceeds = sq.quote_amount * (1 - (sq.fee_pct or ZERO) / 100)
            hypo = sell_proceeds - buy_cost - non_exec
            rec.hypothetical_profit = str(hypo)
            rec.payload = {**rec.payload, "reprice_buy": str(bq.avg_price), "reprice_sell": str(sq.avg_price), "reprice_delay_s": self.reprice_delay_s}
            if self.ctx.repo:
                await self.ctx.repo.add_shadow(rec)
        except Exception as exc:  # pragma: no cover
            log.debug("shadow reprice failed", error=str(exc))
