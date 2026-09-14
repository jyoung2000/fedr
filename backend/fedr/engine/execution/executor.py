"""Execution engine: paired (two-leg) execution with re-validation, timeouts,
partial-fill handling, emergency recovery, reconciliation and accounting.

Live, testnet and paper share this exact code path; only the leg submission
differs (PaperExecutor vs connector.place_order).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Awaitable, Callable

from fedr.connectors.base import QuoteExpired, VenueConnector
from fedr.core.enums import CircuitBreakerReason, Decision, OrderSide, OrderStatus, Strategy, TradeStatus, TradingMode
from fedr.core.logging import get_logger
from fedr.core.models import ExecutionQuote, Opportunity, OrderRequest, OrderResult, TradeRecord, new_id, now_ms
from fedr.core.money import HUNDRED, ZERO, D, fmt_money
from fedr.engine.accounting import apply_outcome, compute_outcome
from fedr.engine.context import EngineContext
from fedr.engine.execution.emergency_hedge import EmergencyHedger
from fedr.engine.latency_guard import Timeline

log = get_logger("execution")


class ExecutionEngine:
    def __init__(self, ctx: EngineContext, opportunity_engine, on_trade: Callable[[TradeRecord], Awaitable[None]] | None = None):
        self.ctx = ctx
        self.opps = opportunity_engine
        self.on_trade = on_trade
        self.hedger = EmergencyHedger(ctx)
        self.active: dict[str, TradeRecord] = {}
        self.recent: list[TradeRecord] = []

    # ------------------------------------------------------------------ entry
    async def execute(self, opp: Opportunity, trigger: str = "auto") -> TradeRecord:
        ctx = self.ctx
        s = ctx.settings
        tr = TradeRecord(
            id=new_id("trd"),
            mode=ctx.mode,
            strategy=opp.strategy,
            opportunity_id=opp.id,
            pair=opp.pair,
            route=opp.route,
            size_base=opp.size_base,
            status=TradeStatus.PENDING,
            estimated_gross=opp.profit.gross_profit,
            estimated_net=opp.profit.expected_net_profit,
            estimated_worst_case=opp.profit.worst_case_profit,
            estimated_costs=opp.profit.expected_costs.as_dict(),
        )
        tr.log("start", trigger=trigger, route_key=opp.extra.get("route_key", ""))
        # ---- hard preconditions ----
        if ctx.emergency_stop:
            return await self._abort(tr, "EMERGENCY STOP is active")
        if ctx.mode is TradingMode.LIVE and not s.live.activated:
            return await self._abort(tr, "live trading is not activated")
        if ctx.mode in (TradingMode.LIVE, TradingMode.TESTNET) and ctx.paper_executor is None and not all(c.trading_enabled for c in (ctx.connectors.get(opp.buy.venue), ctx.connectors.get(opp.sell.venue)) if c):
            return await self._abort(tr, "a venue is not trading-enabled")
        if s.general.shadow_mode:
            return await self._abort(tr, "shadow mode: evaluation only, nothing submitted")
        if len(ctx.open_trade_ids) >= s.risk.max_concurrent_trades:
            return await self._abort(tr, "max concurrent trades reached")
        if ctx.breakers.is_tripped():
            return await self._abort(tr, "circuit breaker active")
        if opp.strategy is Strategy.FLASH_LOAN:
            return await self._abort(tr, "flash-loan execution is routed through the flash-loan engine")
        if opp.strategy in (Strategy.SPOT_PERP, Strategy.FUNDING, Strategy.BASIS):
            return await self._abort(tr, "carry strategies (spot/perp, funding, basis) are evaluation-only in this build: position margin, funding accrual and close-out are not implemented")
        # ---- re-validate with fresh quotes (never trade on the displayed spread) ----
        cand = self.opps.candidate_for(opp)
        if cand is None:
            return await self._abort(tr, "venue no longer available")
        fresh = await self.opps.evaluate(cand, size=opp.size_base)
        if fresh is None:
            return await self._abort(tr, "could not re-quote both legs")
        tr.opportunity_id = fresh.id
        tr.estimated_gross, tr.estimated_net, tr.estimated_worst_case = fresh.profit.gross_profit, fresh.profit.expected_net_profit, fresh.profit.worst_case_profit
        tr.estimated_costs = fresh.profit.expected_costs.as_dict()
        if fresh.decision is not Decision.SAFE_TO_EXECUTE:
            return await self._abort(tr, "re-validation failed: " + "; ".join(fresh.block_reasons[:3]))
        timeline = ctx.latency_guard.timeline_for(fresh.buy, fresh.sell)
        timeline.mark_decision()
        # ---- reserve inventory ----
        base, quote = opp.pair.split("/")
        buy_v = ctx.connectors[fresh.buy.venue]
        sell_v = ctx.connectors[fresh.sell.venue]
        tol = s.trading.leg_price_tolerance_pct / HUNDRED
        buy_fee = D(fresh.buy.fee_pct or 0) / HUNDRED
        quote_needed = fresh.buy.quote_amount * (1 + tol) * (1 + buy_fee)
        try:
            await self._reserve(tr, buy_v, quote, quote_needed, sell_v, base, fresh.sell.base_amount)
        except ValueError as exc:
            return await self._abort(tr, f"reservation failed: {exc}")
        ctx.open_trade_ids.add(tr.id)
        self.active[tr.id] = tr
        tr.status = TradeStatus.EXECUTING
        await self._notify(tr)
        try:
            buy_req = OrderRequest(venue=buy_v.name, symbol=opp.pair, side=OrderSide.BUY, amount=fresh.size_base, limit_price=fresh.buy.avg_price * (1 + tol), time_in_force="IOC", quote_id=fresh.buy.quote_id, deadline_ms=s.trading.execution_timeout_ms, extra={"quoted_price": str(fresh.buy.avg_price)})
            sell_req = OrderRequest(venue=sell_v.name, symbol=fresh.sell.symbol, side=OrderSide.SELL, amount=fresh.size_base, limit_price=fresh.sell.avg_price * (1 - tol), time_in_force="IOC", min_received=fresh.sell.min_received or fresh.sell.quote_amount * (1 - tol), quote_id=fresh.sell.quote_id, deadline_ms=s.trading.execution_timeout_ms, extra={"quoted_price": str(fresh.sell.avg_price)})
            timeline.mark_submission()
            tr.log("submit", buy=buy_v.name, sell=sell_v.name, size=fresh.size_base)
            timeout = (s.trading.execution_timeout_ms + 4000) / 1000
            results = await asyncio.gather(
                asyncio.wait_for(self._submit(buy_v, buy_req, fresh.buy), timeout),
                asyncio.wait_for(self._submit(sell_v, sell_req, fresh.sell), timeout),
                return_exceptions=True,
            )
            tr.buy = self._as_result(results[0], buy_req)
            tr.sell = self._as_result(results[1], sell_req)
            timeline.mark_fill()
            tr.log("legs_done", buy_status=tr.buy.status.value, buy_filled=tr.buy.filled, sell_status=tr.sell.status.value, sell_filled=tr.sell.filled)
            await self._release_unused(tr, buy_v, quote, quote_needed, sell_v, base)
            # ---- imbalance handling ----
            exposure = tr.buy.filled - tr.sell.filled
            if exposure != 0:
                await self._recover(tr, opp.pair, exposure, {buy_v.name, sell_v.name})
            elif tr.buy.filled == 0 and tr.sell.filled == 0:
                tr.status = TradeStatus.FAILED
                tr.explanation = f"Both legs failed: buy {tr.buy.error or tr.buy.status.value}; sell {tr.sell.error or tr.sell.status.value}."
                ctx.failed_trades_last_hour += 1
                await ctx.breakers.record_and_check("order_failures", s.risk.max_failed_trades_per_hour, CircuitBreakerReason.REPEATED_ORDER_FAILURE, "repeated order failures")
            else:
                tr.status = TradeStatus.FILLED
            # ---- accounting ----
            qpx = ctx.price_usd(quote) or Decimal("1")
            outcome = compute_outcome(tr, qpx, tr.estimated_costs)
            apply_outcome(tr, outcome)
            if tr.status is TradeStatus.FILLED:
                tr.explanation = f"Executed: estimated net {fmt_money(tr.estimated_net)}, realized net {fmt_money(outcome.actual_net)} (difference {fmt_money(outcome.actual_net - tr.estimated_net)}); gas {fmt_money(outcome.gas)}, fees {fmt_money(outcome.trading_fees)}."
            tr.events.append({"ts": now_ms(), "event": "timeline", **{k: str(v) for k, v in timeline.as_dict().items()}})
            await self._post_trade(tr, outcome, fresh, timeline)
        except Exception as exc:  # pragma: no cover - last line of defence
            log.error("execution error", trade=tr.id, error=str(exc), exc_info=True)
            tr.status = TradeStatus.FAILED
            tr.explanation = f"Execution error: {exc}"
            await ctx.breakers.trip(CircuitBreakerReason.REPEATED_ORDER_FAILURE, f"execution exception: {exc}"[:200])
        finally:
            tr.completed_at_ms = now_ms()
            ctx.open_trade_ids.discard(tr.id)
            self.active.pop(tr.id, None)
            self.recent.append(tr)
            self.recent = self.recent[-100:]
            await ctx.inventory.release(tr.id)
            await self._notify(tr)
        return tr

    # ------------------------------------------------------------------ helpers
    async def _submit(self, venue: VenueConnector, req: OrderRequest, quote: ExecutionQuote) -> OrderResult:
        if self.ctx.is_simulated_execution and self.ctx.paper_executor is not None:
            return await self.ctx.paper_executor.execute_leg(venue, req, quote)
        try:
            return await venue.place_order(req)
        except QuoteExpired as exc:
            # a DEX quote went stale between validation and submission: requote once, re-check price, then execute
            fresh = await venue.get_quote(req.symbol, req.side, req.amount)
            worse = (req.side is OrderSide.BUY and fresh.avg_price > (req.limit_price or fresh.avg_price)) or (req.side is OrderSide.SELL and fresh.avg_price < (req.limit_price or fresh.avg_price))
            if worse or not fresh.fully_fillable:
                res = OrderResult(request=req, order_id=req.client_order_id, status=OrderStatus.REJECTED, error=f"requote worse than limit after quote expiry ({exc})")
                res.completed_at_ms = now_ms()
                return res
            req.quote_id = fresh.quote_id
            return await venue.place_order(req)

    @staticmethod
    def _as_result(res, req: OrderRequest) -> OrderResult:
        if isinstance(res, OrderResult):
            return res
        err = str(res) if isinstance(res, BaseException) else "unknown"
        if isinstance(res, asyncio.TimeoutError):
            err = "leg timed out"
        r = OrderResult(request=req, order_id=req.client_order_id, status=OrderStatus.FAILED, error=err)
        r.completed_at_ms = now_ms()
        return r

    async def _reserve(self, tr: TradeRecord, buy_v, quote: str, quote_needed: Decimal, sell_v, base: str, base_needed: Decimal) -> None:
        ctx = self.ctx
        lb, ls = ctx.ledger_venue_for(buy_v), ctx.ledger_venue_for(sell_v)
        await ctx.inventory.reserve(tr.id, [(lb, quote, quote_needed), (ls, base, base_needed)])
        if ctx.is_simulated_execution and ctx.ledger is not None:
            await ctx.ledger.reserve(lb, quote, quote_needed)
            try:
                await ctx.ledger.reserve(ls, base, base_needed)
            except ValueError:
                await ctx.ledger.release(lb, quote, quote_needed)
                raise
        tr.log("reserved", quote=quote_needed, base=base_needed)

    async def _release_unused(self, tr: TradeRecord, buy_v, quote: str, quote_needed: Decimal, sell_v, base: str) -> None:
        ctx = self.ctx
        if not (ctx.is_simulated_execution and ctx.ledger is not None):
            return
        lb, ls = ctx.ledger_venue_for(buy_v), ctx.ledger_venue_for(sell_v)
        spent = (tr.buy.quote_amount + tr.buy.fee_quote) if tr.buy else ZERO
        await ctx.ledger.release(lb, quote, max(ZERO, quote_needed - spent))
        sold = tr.sell.filled if tr.sell else ZERO
        await ctx.ledger.release(ls, base, max(ZERO, tr.size_base - sold))

    async def _recover(self, tr: TradeRecord, pair: str, exposure: Decimal, legs: set[str]) -> None:
        ctx = self.ctx
        tr.status = TradeStatus.RECOVERING
        ctx.unhedged_since_ms = now_ms()
        tr.log("one_leg_imbalance", exposure=exposure)
        await self._notify(tr)
        if ctx.repo:
            await ctx.repo.add_risk_event(ctx.mode, "critical", "one_leg_fill", f"{pair}: unbalanced exposure {exposure} after paired execution", {"trade_id": tr.id})
        hedge = await self.hedger.hedge(tr, pair, exposure, exclude=None)
        tr.hedge = hedge
        remaining = exposure - (hedge.filled if hedge and hedge.request.side is OrderSide.SELL else -hedge.filled if hedge else ZERO)
        if hedge is not None and abs(remaining) <= abs(exposure) * Decimal("0.02"):
            tr.status = TradeStatus.HEDGED
            tr.explanation = f"One leg failed ({'buy' if tr.buy.filled == 0 else 'sell'}): exposure {exposure} {pair.split('/')[0]} was neutralised on {hedge.request.venue}."
            ctx.unhedged_since_ms = None
        else:
            tr.status = TradeStatus.RECOVERING
            tr.explanation = f"One leg failed and the emergency hedge could not neutralise {remaining} {pair.split('/')[0]} - trading paused for investigation."
            await ctx.breakers.trip(CircuitBreakerReason.ONE_LEG_FILL, tr.explanation[:200])
            if ctx.repo:
                await ctx.repo.add_risk_event(ctx.mode, "critical", "unhedged_exposure", tr.explanation, {"trade_id": tr.id, "remaining": str(remaining)})
        ctx.failed_trades_last_hour += 1
        await ctx.breakers.record_and_check("order_failures", ctx.settings.risk.max_failed_trades_per_hour, CircuitBreakerReason.REPEATED_ORDER_FAILURE, "repeated leg failures")

    async def _abort(self, tr: TradeRecord, reason: str) -> TradeRecord:
        tr.status = TradeStatus.ABORTED
        tr.explanation = f"Aborted before submission because {reason}."
        tr.completed_at_ms = now_ms()
        tr.log("abort", reason=reason)
        self.recent.append(tr)
        self.recent = self.recent[-100:]
        if self.ctx.repo and not reason.startswith("shadow mode"):
            await self.ctx.repo.save_trade(tr)
            await self.ctx.repo.audit(self.ctx.mode, "execution", tr.explanation, {"trade_id": tr.id, "opportunity_id": tr.opportunity_id})
        await self._notify(tr)
        return tr

    async def _post_trade(self, tr: TradeRecord, outcome, fresh: Opportunity, timeline: Timeline) -> None:
        ctx = self.ctx
        s = ctx.settings
        if tr.actual_net is not None:
            ctx.daily_pnl_usd += tr.actual_net
            if ctx.daily_pnl_usd <= -s.risk.max_daily_loss_usd:
                await ctx.breakers.trip(CircuitBreakerReason.DAILY_LOSS_LIMIT, f"daily realized loss {fmt_money(ctx.daily_pnl_usd)} reached the hard limit", auto=True)
            notional = D(fresh.extra.get("notional_usd", "0"))
            executed = tr.status in (TradeStatus.FILLED, TradeStatus.PARTIAL, TradeStatus.HEDGED) and outcome.matched_base > 0
            if executed and notional > 0 and tr.prediction_error is not None and abs(tr.prediction_error) / notional * HUNDRED > s.risk.max_prediction_error_pct:
                await ctx.breakers.trip(CircuitBreakerReason.PREDICTION_ERROR, f"prediction error {fmt_money(tr.prediction_error)} on {fmt_money(notional)} notional")
            if executed and outcome.slippage_variance > 0 and notional > 0 and outcome.slippage_variance / notional * HUNDRED > s.risk.max_slippage_pct:
                await ctx.breakers.trip(CircuitBreakerReason.ABNORMAL_SLIPPAGE, f"realized slippage {fmt_money(outcome.slippage_variance)} exceeds limit")
            if executed and outcome.fee_variance > 0 and notional > 0 and outcome.fee_variance / notional * HUNDRED > Decimal("0.2"):
                await ctx.breakers.trip(CircuitBreakerReason.UNEXPECTED_FEES, f"fees exceeded estimate by {fmt_money(outcome.fee_variance)}")
        if ctx.repo:
            await ctx.repo.save_trade(tr)
            if tr.actual_net is not None and tr.status in (TradeStatus.FILLED, TradeStatus.PARTIAL, TradeStatus.HEDGED):
                await ctx.repo.add_pnl(ctx.mode, gross=outcome.actual_gross, trading_fees=outcome.trading_fees, gas=outcome.gas, funding=outcome.funding, slippage=max(ZERO, outcome.slippage_variance), net=outcome.actual_net, win=outcome.actual_net > 0)
            await ctx.repo.audit(ctx.mode, "execution", tr.explanation, {"trade_id": tr.id, "status": tr.status.value, "estimated_net": str(tr.estimated_net), "actual_net": str(tr.actual_net), "prediction_error": str(tr.prediction_error), "timeline": timeline.as_dict()})
        if ctx.ledger is not None and ctx.repo is not None and ctx.is_simulated_execution:
            await ctx.repo.save_paper_balances(ctx.mode, ctx.ledger.dump())
        rk = fresh.extra.get("route_key")
        if rk and tr.actual_net is not None:
            await ctx.experience.record(rk, notional_usd=D(fresh.extra.get("notional_usd", "1")), prediction_error_usd=tr.prediction_error or ZERO, slippage_variance_usd=outcome.slippage_variance, fee_variance_usd=outcome.fee_variance, gas_variance_usd=outcome.gas_variance, filled=tr.status is TradeStatus.FILLED, latency_ms=int(timeline.as_dict()["submit_to_fill_ms"]))

    async def _notify(self, tr: TradeRecord) -> None:
        if self.on_trade:
            try:
                await self.on_trade(tr)
            except Exception as exc:  # pragma: no cover
                log.warning("trade callback failed", error=str(exc))
