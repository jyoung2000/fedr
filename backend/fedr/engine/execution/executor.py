"""Execution engine: paired (two-leg) execution with re-validation, a single final admission boundary,
a durable intent journal, uncertainty handling, emergency recovery, reconciliation and accounting.

Live, testnet and paper share this exact code path; only the leg submission differs (PaperExecutor vs
connector.place_order).

Lifecycle of one trade (invariants A-C of docs/REPAIR_STATUS.md):

1. cheap pre-checks, then re-validation with fresh quotes (never trade on the displayed spread);
2. FINAL ADMISSION under ``ctx.admission.lock``: every stop/mode/breaker/capacity check again, capital
   reservation, and the durable journal write (trade row + one order row per leg with status
   ``submitting`` and its client order id) - nothing is sent before the journal write succeeds;
3. linearization point: a synchronous emergency-stop / generation check right before the transport tasks
   are created; the pair is then submitted as a pair;
4. every leg result is journaled. A leg whose outcome is not known (transport loss, cancel failure,
   unconfirmed tx, timeout) makes the trade ``UNKNOWN``: its reservations are kept, its venues are
   quarantined (``UNRESOLVED_ORDER`` breaker) and NO hedge is placed on the assumption that it did not
   fill. ``resolve_unresolved`` (loop + startup barrier) looks the leg up by client order id / tx hash;
5. once every leg is known: imbalance recovery (bounded hedge), authoritative accounting (no fabricated
   values), realized P&L booked once with immediate daily-loss enforcement, reservations released.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal

from fedr.connectors.base import ConnectorError, QuoteExpired, VenueConnector
from fedr.core.enums import (
    CircuitBreakerReason,
    Decision,
    OrderSide,
    OrderStatus,
    Strategy,
    TradeStatus,
    TradingMode,
    VenueKind,
)
from fedr.core.logging import get_logger
from fedr.core.models import (
    ExecutionQuote,
    Opportunity,
    OrderRequest,
    OrderResult,
    TradeRecord,
    new_id,
    now_ms,
    trade_from_dict,
)
from fedr.core.money import HUNDRED, ZERO, D, fmt_money
from fedr.engine.accounting import apply_outcome, compute_outcome
from fedr.engine.context import EngineContext
from fedr.engine.execution.emergency_hedge import EmergencyHedger

log = get_logger("execution")

CARRY = (Strategy.SPOT_PERP, Strategy.FUNDING, Strategy.BASIS)
# A client order id the venue has never heard of, confirmed this many times over at least this long after
# submission, is evidence that the request was never accepted (as opposed to a transport failure).
NOT_FOUND_CONFIRMATIONS = 3
NOT_FOUND_MIN_AGE_MS = 120_000


class ExecutionEngine:
    def __init__(
        self,
        ctx: EngineContext,
        opportunity_engine,
        on_trade: Callable[[TradeRecord], Awaitable[None]] | None = None,
    ):
        self.ctx = ctx
        self.opps = opportunity_engine
        self.on_trade = on_trade
        self.hedger = EmergencyHedger(ctx)
        self.active: dict[str, TradeRecord] = {}
        self.recent: list[TradeRecord] = []
        self.unresolved: dict[str, TradeRecord] = {}  # trades holding exposure with an unknown leg
        self.foreign_unresolved: list[str] = []  # unresolved trades of another mode found at startup
        self._resolve_lock = asyncio.Lock()
        # optional hook (reconciliation ledger): called with every trade whose fills are final
        self.on_settled: Callable[[TradeRecord], Awaitable[None]] | None = None
        # position manager hooks: create a carry position from a settled entry; finish a close trade
        self.carry_opener: Callable[[TradeRecord], Awaitable[object]] | None = None
        self.close_settler: Callable[[TradeRecord], Awaitable[None]] | None = None
        # flash-loan engine hook: resolve an unknown flash transaction by receipt lookup
        self.flash_resolver: Callable[[TradeRecord], Awaitable[None]] | None = None

    # ------------------------------------------------------------------ entry
    def _precheck(self, opp: Opportunity, allow_carry: bool) -> str | None:
        ctx = self.ctx
        s = ctx.settings
        if not ctx.admission.recovery_complete:
            return f"startup recovery has not completed ({ctx.admission.recovery_note})"
        if ctx.emergency_stop:
            return "EMERGENCY STOP is active"
        if ctx.mode is TradingMode.LIVE and not s.live.activated:
            return "live trading is not activated"
        if s.general.shadow_mode:
            return "shadow mode: evaluation only, nothing submitted"
        if len(ctx.open_trade_ids) >= s.risk.max_concurrent_trades:
            return "max concurrent trades reached"
        if ctx.breakers.is_tripped():
            return "circuit breaker active"
        if opp.strategy is Strategy.FLASH_LOAN:
            return "flash-loan execution is routed through the flash-loan engine"
        if opp.strategy in CARRY and not allow_carry:
            return "carry strategies open positions through the position manager, not the spot executor"
        return None

    def _trading_enabled_block(self, *venues: VenueConnector) -> str | None:
        ctx = self.ctx
        if ctx.mode in (TradingMode.LIVE, TradingMode.TESTNET) and ctx.paper_executor is None:
            if not all(v.trading_enabled for v in venues):
                return "a venue is not trading-enabled"
        return None

    async def execute(
        self,
        opp: Opportunity,
        trigger: str = "auto",
        allow_carry: bool = False,
        carry: dict | None = None,
    ) -> TradeRecord:
        ctx = self.ctx
        s = ctx.settings
        gen0, mode0 = ctx.admission.generation, ctx.mode
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
        reason = self._precheck(opp, allow_carry)
        if reason:
            return await self._abort(tr, reason)
        # ---- re-validate with fresh quotes (never trade on the displayed spread) ----
        cand = self.opps.candidate_for(opp)
        if cand is None:
            return await self._abort(tr, "venue no longer available")
        fresh = await self.opps.evaluate(cand, size=opp.size_base)
        if fresh is None:
            return await self._abort(tr, "could not re-quote both legs")
        tr.opportunity_id = fresh.id
        tr.estimated_gross, tr.estimated_net, tr.estimated_worst_case = (
            fresh.profit.gross_profit,
            fresh.profit.expected_net_profit,
            fresh.profit.worst_case_profit,
        )
        tr.estimated_costs = fresh.profit.expected_costs.as_dict()
        if fresh.decision is not Decision.SAFE_TO_EXECUTE:
            return await self._abort(tr, "re-validation failed: " + "; ".join(fresh.block_reasons[:3]))
        timeline = ctx.latency_guard.timeline_for(fresh.buy, fresh.sell)
        timeline.mark_decision()
        base, quote = opp.pair.split("/")
        buy_v = ctx.connectors.get(fresh.buy.venue)
        sell_v = ctx.connectors.get(fresh.sell.venue)
        if buy_v is None or sell_v is None:
            return await self._abort(tr, "venue no longer available")
        tol = s.trading.leg_price_tolerance_pct / HUNDRED
        buy_fee = D(fresh.buy.fee_pct or 0) / HUNDRED
        quote_needed = fresh.buy.quote_amount * (1 + tol) * (1 + buy_fee)
        perp_short = fresh.sell.kind is VenueKind.PERP
        sell_asset = quote if perp_short else base
        sell_needed = fresh.sell.quote_amount if perp_short else fresh.sell.base_amount
        buy_req = OrderRequest(
            venue=buy_v.name,
            symbol=opp.pair,
            side=OrderSide.BUY,
            amount=fresh.size_base,
            limit_price=fresh.buy.avg_price * (1 + tol),
            time_in_force="IOC",
            client_order_id=new_id("fb"),
            quote_id=fresh.buy.quote_id,
            deadline_ms=s.trading.execution_timeout_ms,
            extra={"quoted_price": str(fresh.buy.avg_price), "account_ref": self._account_ref(buy_v)},
        )
        sell_req = OrderRequest(
            venue=sell_v.name,
            symbol=fresh.sell.symbol,
            side=OrderSide.SELL,
            amount=fresh.size_base,
            limit_price=fresh.sell.avg_price * (1 - tol),
            time_in_force="IOC",
            client_order_id=new_id("fs"),
            min_received=fresh.sell.min_received or fresh.sell.quote_amount * (1 - tol),
            quote_id=fresh.sell.quote_id,
            deadline_ms=s.trading.execution_timeout_ms,
            extra={"quoted_price": str(fresh.sell.avg_price), "account_ref": self._account_ref(sell_v)},
        )
        chains = tuple({q.chain.value for q in (fresh.buy, fresh.sell) if q.chain is not None})
        tr.extra.update(
            {
                "carry_entry": opp.strategy in CARRY,
                "carry": {k: str(v) for k, v in (carry or {}).items()} if carry else None,
                "notional_usd": str(fresh.extra.get("notional_usd", "0")),
                "route_key": fresh.extra.get("route_key"),
                "quote_asset": quote,
                "base_asset": base,
                "perp_short": perp_short,
                "trigger": trigger,
                "generation": gen0,
            }
        )
        # ---- FINAL ADMISSION (single boundary; see AdmissionGate) ----
        block: str | None = None
        async with ctx.admission.lock:
            block = ctx.admission_block(
                generation=gen0,
                mode=mode0,
                venues=(buy_v.name, sell_v.name),
                chains=chains,
                strategy=opp.strategy.value,
                symbol=opp.pair,
            ) or self._trading_enabled_block(buy_v, sell_v)
            if block is None:
                try:
                    await self._reserve(tr, buy_v, quote, quote_needed, sell_v, sell_asset, sell_needed)
                except ValueError as exc:
                    block = f"reservation failed: {exc}"
            if block is None:
                ctx.open_trade_ids.add(tr.id)
                self.active[tr.id] = tr
                tr.status = TradeStatus.EXECUTING
                tr.buy = OrderResult(request=buy_req, order_id="", status=OrderStatus.SUBMITTING)
                tr.sell = OrderResult(request=sell_req, order_id="", status=OrderStatus.SUBMITTING)
                tr.log("admitted", generation=gen0)
                try:
                    await self._journal(tr)  # durable intent BEFORE any transport
                except Exception as exc:
                    block = f"intent journal write failed ({str(exc)[:120]}): nothing submitted"
                    await self._release_all(tr)
                    ctx.open_trade_ids.discard(tr.id)
                    self.active.pop(tr.id, None)
                    tr.buy = tr.sell = None
        if block is not None:
            return await self._abort(tr, block)
        # ---- linearization point: no await between this check and the creation of the transport tasks ----
        if ctx.emergency_stop or ctx.admission.generation != gen0 or ctx.mode is not mode0:
            why = "EMERGENCY STOP" if ctx.emergency_stop else "configuration change"
            for leg in (tr.buy, tr.sell):
                leg.status = OrderStatus.REJECTED
                leg.error = f"not submitted: {why} before transport"
                leg.completed_at_ms = now_ms()
            await self._release_all(tr)
            self._finish(tr)
            return await self._abort(tr, f"{why} was activated after admission and before submission")
        await self._notify(tr)
        timeline.mark_submission()
        tr.log("submit", buy=buy_v.name, sell=sell_v.name, size=fresh.size_base)
        try:
            tr.buy, tr.sell = await asyncio.gather(
                self._run_leg(buy_v, buy_req, fresh.buy), self._run_leg(sell_v, sell_req, fresh.sell)
            )
            timeline.mark_fill()
            tr.events.append(
                {"ts": now_ms(), "event": "timeline", **{k: str(v) for k, v in timeline.as_dict().items()}}
            )
            tr.extra["submit_to_fill_ms"] = int(timeline.as_dict()["submit_to_fill_ms"])
            tr.log(
                "legs_done",
                buy_status=tr.buy.status.value,
                buy_filled=tr.buy.filled,
                sell_status=tr.sell.status.value,
                sell_filled=tr.sell.filled,
            )
            await self._journal(tr)
            if tr.unresolved_legs:
                await self._hold_unknown(tr)
                return tr
            await self._settle(tr)
        except Exception as exc:  # pragma: no cover - last line of defence: never lose the journal entry
            log.error("execution error", trade=tr.id, error=str(exc), exc_info=True)
            for _role, leg in tr.legs():
                if leg.status is OrderStatus.SUBMITTING:
                    leg.status = OrderStatus.UNKNOWN
                    leg.raw["unknown_reason"] = f"execution error: {str(exc)[:120]}"
            tr.explanation = f"Execution error: {exc}"
            await ctx.breakers.trip(
                CircuitBreakerReason.REPEATED_ORDER_FAILURE, f"execution exception: {exc}"[:200]
            )
            if tr.unresolved_legs:
                await self._hold_unknown(tr)
            else:
                tr.status = TradeStatus.FAILED
                self._finish(tr)
                await self._release_all(tr)
                await self._persist(tr)
        return tr

    @staticmethod
    def _account_ref(v: VenueConnector) -> str:
        """Durable account identity of a leg: mode + venue + credential/wallet scope."""
        ident = getattr(v, "wallet_address", None) or getattr(v, "account_id", None) or ""
        return f"{v.mode.value}:{v.name}:{ident}"

    # ------------------------------------------------------------------ legs
    async def _run_leg(self, venue: VenueConnector, req: OrderRequest, quote: ExecutionQuote) -> OrderResult:
        """Submit one leg and return a result whose status is honest about uncertainty."""
        timeout = venue.max_order_duration_s(req) + 5.0
        if self.ctx.is_simulated_execution and self.ctx.paper_executor is not None:
            timeout += self.ctx.settings.paper.latency_ms / 1000 * 2
        try:
            return await asyncio.wait_for(self._submit(venue, req, quote), timeout)
        except TimeoutError:
            res = OrderResult(request=req, order_id="", status=OrderStatus.UNKNOWN, error="leg timed out")
            res.raw["unknown_reason"] = (
                f"no result within {timeout:.0f}s (request may have reached the venue)"
            )
            return res
        except ConnectorError as exc:
            # raised by our own pre-transport validation (quantization, credentials, stale quote requote)
            res = OrderResult(request=req, order_id="", status=OrderStatus.REJECTED, error=str(exc)[:300])
            res.completed_at_ms = now_ms()
            return res
        except Exception as exc:
            res = OrderResult(request=req, order_id="", status=OrderStatus.UNKNOWN, error=str(exc)[:300])
            res.raw["unknown_reason"] = f"{type(exc).__name__} during submission"
            return res

    async def submit_leg(
        self, venue: VenueConnector, req: OrderRequest, quote: ExecutionQuote, perp_close: bool = False
    ) -> OrderResult:
        """Public single-leg submission used by the position manager for close-outs (same paper/live path,
        same uncertainty handling). The caller journals the leg before calling."""
        return await self._run_leg(venue, req, quote)

    async def _submit(self, venue: VenueConnector, req: OrderRequest, quote: ExecutionQuote) -> OrderResult:
        if self.ctx.is_simulated_execution and self.ctx.paper_executor is not None:
            return await self.ctx.paper_executor.execute_leg(venue, req, quote)
        try:
            return await venue.place_order(req)
        except QuoteExpired as exc:
            # a DEX quote went stale between validation and submission (nothing was sent): requote once,
            # re-check price, then execute
            fresh = await venue.get_quote(req.symbol, req.side, req.amount)
            worse = (
                req.side is OrderSide.BUY and fresh.avg_price > (req.limit_price or fresh.avg_price)
            ) or (req.side is OrderSide.SELL and fresh.avg_price < (req.limit_price or fresh.avg_price))
            if worse or not fresh.fully_fillable:
                res = OrderResult(
                    request=req,
                    order_id="",
                    status=OrderStatus.REJECTED,
                    error=f"requote worse than limit after quote expiry ({exc})",
                )
                res.completed_at_ms = now_ms()
                return res
            req.quote_id = fresh.quote_id
            return await venue.place_order(req)

    async def _note_dex_failures(self, tr: TradeRecord) -> None:
        """Failed/rejected DEX legs (reverts, simulation failures, RPC errors) count per chain; repeated
        failures trip DEX_TX_FAILURE for that chain (2 within 10 minutes)."""
        for _role, res in tr.legs():
            v = self.ctx.connectors.get(res.request.venue)
            if v is None or v.kind is not VenueKind.DEX or v.chain is None:
                continue
            if res.status in (OrderStatus.FAILED, OrderStatus.REJECTED) and res.filled == 0:
                n = self.ctx.breakers.record(f"dex_fail:{v.chain.value}", window_s=600)
                tr.log("dex_leg_failed", venue=v.name, error=(res.error or "")[:120], count=n)
                if n >= 2:
                    await self.ctx.breakers.trip(
                        CircuitBreakerReason.DEX_TX_FAILURE,
                        f"{n} DEX transaction failures on {v.chain.value} in 10 min (last: {v.name}: {(res.error or 'failed')[:80]})",
                        scope=f"chain:{v.chain.value}",
                    )

    # ------------------------------------------------------------------ reservations
    async def _reserve(
        self,
        tr: TradeRecord,
        buy_v,
        quote: str,
        quote_needed: Decimal,
        sell_v,
        sell_asset: str,
        sell_needed: Decimal,
    ) -> None:
        ctx = self.ctx
        lb, ls = ctx.ledger_venue_for(buy_v), ctx.ledger_venue_for(sell_v)
        needs = [(lb, quote, quote_needed), (ls, sell_asset, sell_needed)]
        await ctx.inventory.reserve(tr.id, needs)
        if ctx.is_simulated_execution and ctx.ledger is not None:
            try:
                await ctx.ledger.reserve(lb, quote, quote_needed)
            except ValueError:
                await ctx.inventory.release(tr.id)
                raise
            try:
                await ctx.ledger.reserve(ls, sell_asset, sell_needed)
            except ValueError:
                await ctx.ledger.release(lb, quote, quote_needed)
                await ctx.inventory.release(tr.id)
                raise
        tr.reservations = [
            {"kind": "inventory", "venue": v, "asset": a, "amount": str(x)} for v, a, x in needs
        ] + (
            [
                {"kind": "paper", "venue": lb, "asset": quote, "amount": str(quote_needed), "role": "buy"},
                {
                    "kind": "paper",
                    "venue": ls,
                    "asset": sell_asset,
                    "amount": str(sell_needed),
                    "role": "sell",
                },
            ]
            if ctx.is_simulated_execution and ctx.ledger is not None
            else []
        )
        tr.log("reserved", quote=quote_needed, sell_asset=sell_asset, sell_amount=sell_needed)

    async def _release_all(self, tr: TradeRecord) -> None:
        """Release every reservation of a trade whose legs did not use them (abort before transport)."""
        ctx = self.ctx
        await ctx.inventory.release(tr.id)
        if ctx.ledger is not None:
            for r in tr.reservations:
                if r["kind"] == "paper":
                    await ctx.ledger.release(r["venue"], r["asset"], D(r["amount"]))
        tr.reservations = []

    async def _release_unused(self, tr: TradeRecord) -> None:
        """After every leg is final: return what the paper legs did not consume; drop the inventory hold."""
        ctx = self.ctx
        if ctx.ledger is not None and ctx.is_simulated_execution:
            for r in tr.reservations:
                if r["kind"] != "paper":
                    continue
                reserved = D(r["amount"])
                if r.get("role") == "buy":
                    leg = tr.buy
                    used = ((leg.quote_value or ZERO) + leg.fee_quote) if leg else ZERO
                elif tr.extra.get("perp_short"):  # perp short: quote collateral was reserved, not base
                    leg = tr.sell
                    used = (leg.quote_value or ZERO) if leg and leg.filled > 0 else ZERO
                else:
                    leg = tr.sell
                    used = leg.filled if leg else ZERO
                await ctx.ledger.release(r["venue"], r["asset"], max(ZERO, reserved - used))
        await ctx.inventory.release(tr.id)
        tr.reservations = []

    # ------------------------------------------------------------------ uncertainty
    async def _hold_unknown(self, tr: TradeRecord) -> None:
        """A leg outcome is unknown: keep reservations, quarantine the venues, never assume no fill."""
        ctx = self.ctx
        tr.status = TradeStatus.UNKNOWN
        unknown = tr.unresolved_legs
        tr.explanation = (
            "Outcome unknown for "
            + ", ".join(f"{r} on {leg.request.venue} ({leg.status.value})" for r, leg in unknown)
            + ": capital stays reserved and the venue is paused until the order is resolved by lookup "
            "(client order id / transaction hash) or by an operator."
        )
        tr.log("outcome_unknown", legs=",".join(f"{r}:{leg.request.client_order_id}" for r, leg in unknown))
        self.unresolved[tr.id] = tr
        ctx.open_trade_ids.add(tr.id)
        self.active.pop(tr.id, None)
        for _r, leg in unknown:
            await ctx.breakers.trip(
                CircuitBreakerReason.UNRESOLVED_ORDER,
                f"order {leg.request.client_order_id} on {leg.request.venue} has an unknown outcome "
                f"({leg.raw.get('unknown_reason') or leg.error or leg.status.value})"[:200],
                scope=f"venue:{leg.request.venue}",
            )
        if ctx.repo:
            await ctx.repo.add_risk_event(
                ctx.mode, "critical", "order_outcome_unknown", tr.explanation, {"trade_id": tr.id}
            )
        await self._persist(tr)
        await self._notify(tr)

    async def resolve_unresolved(self) -> int:
        """Try to resolve every trade with an unknown leg. Returns the number of trades fully settled."""
        settled = 0
        async with self._resolve_lock:
            for tr in list(self.unresolved.values()):
                if tr.mode is not self.ctx.mode:
                    continue
                if tr.strategy is Strategy.FLASH_LOAN:
                    if self.flash_resolver is not None:
                        try:
                            await self.flash_resolver(tr)
                        except Exception as exc:
                            log.warning("flash resolution failed", trade=tr.id, error=str(exc)[:120])
                        settled += 0 if tr.id in self.unresolved else 1
                    continue
                changed = False
                for _role, leg in tr.unresolved_legs:
                    venue = self.ctx.connectors.get(leg.request.venue)
                    if venue is None or not venue.connected:
                        continue
                    before = (leg.status, leg.filled)
                    try:
                        await venue.resolve_order(leg)
                    except Exception as exc:
                        leg.raw["lookup"] = f"error: {str(exc)[:120]}"
                    self._apply_not_found_policy(leg)
                    changed = changed or (leg.status, leg.filled) != before
                if changed:
                    tr.log("resolution_progress", unresolved=len(tr.unresolved_legs))
                    await self._persist(tr)
                if not tr.unresolved_legs:
                    await self._after_resolution(tr)
                    settled += 1
        return settled

    @staticmethod
    def _apply_not_found_policy(leg: OrderResult) -> None:
        """Only repeated, definitive "venue has no such client order id" answers - never a transport
        failure - are accepted as evidence that the order was never accepted."""
        if leg.raw.get("lookup") != "not_found" or leg.order_id:
            return
        n = int(leg.raw.get("not_found_count", 0)) + 1
        leg.raw["not_found_count"] = n
        if n >= NOT_FOUND_CONFIRMATIONS and now_ms() - leg.submitted_at_ms >= NOT_FOUND_MIN_AGE_MS:
            leg.status = OrderStatus.REJECTED
            leg.error = (
                f"venue has no record of client order id {leg.request.client_order_id} after {n} lookups "
                f"over {(now_ms() - leg.submitted_at_ms) // 1000}s: treated as never accepted"
            )
            leg.completed_at_ms = now_ms()

    async def _after_resolution(self, tr: TradeRecord) -> None:
        ctx = self.ctx
        self.unresolved.pop(tr.id, None)
        venues = {leg.request.venue for _r, leg in tr.legs()}
        tr.log("resolved", venues=",".join(sorted(venues)))
        await self._settle(tr)
        still = {leg.request.venue for t in self.unresolved.values() for _r, leg in t.unresolved_legs}
        for v in venues - still:
            await ctx.breakers.reset(CircuitBreakerReason.UNRESOLVED_ORDER, scope=f"venue:{v}")

    async def operator_resolve_leg(
        self,
        trade_id: str,
        client_order_id: str,
        *,
        filled: Decimal,
        cost: Decimal | None,
        fee_amount: Decimal | None,
        fee_asset: str | None,
        note: str,
        actor: str = "user",
    ) -> TradeRecord:
        """Supported operator control for an unknown / unreconciled leg (e.g. confirmed on the venue's own
        UI). Records the evidence note; values are applied as an explicit, audited resolution."""
        tr = self.unresolved.get(trade_id) or next((t for t in self.recent if t.id == trade_id), None)
        if tr is None and self.ctx.repo is not None:
            row = await self.ctx.repo.get_trade(trade_id)
            if row is not None and row.mode == self.ctx.mode.value:
                tr = trade_from_dict(row.payload)
        if tr is None:
            raise ValueError("unknown trade (or trade of another mode)")
        if tr.status not in (TradeStatus.UNKNOWN, TradeStatus.UNRECONCILED):
            raise ValueError(f"trade is {tr.status.value}; only unknown/unreconciled trades can be resolved")
        leg = next((l for _r, l in tr.legs() if l.request.client_order_id == client_order_id), None)
        if leg is None:
            raise ValueError("no leg with that client order id")
        if filled < 0 or filled > leg.request.amount * Decimal("1.0001"):
            raise ValueError("filled must be between 0 and the order amount")
        if filled > 0 and (cost is None or cost <= 0):
            raise ValueError("a filled leg needs its quote cost")
        if filled > 0 and (fee_amount is None or not fee_asset):
            raise ValueError("a filled leg needs its fee amount and asset (0 and the quote asset if none)")
        leg.filled = filled
        leg.cost = cost if filled > 0 else None
        leg.avg_price = (cost / filled) if filled > 0 and cost else None
        leg.fills = []
        leg.fee_items = [(fee_amount, fee_asset)] if filled > 0 and fee_amount is not None else []
        leg.fees_complete = True
        leg.gas_known = True
        leg.status = (
            OrderStatus.FILLED
            if filled >= leg.request.amount
            else OrderStatus.PARTIALLY_FILLED
            if filled > 0
            else OrderStatus.CANCELLED
        )
        leg.completed_at_ms = now_ms()
        leg.raw["operator_resolution"] = {"actor": actor, "note": note[:500], "at_ms": now_ms()}
        tr.log("operator_resolution", order=client_order_id, filled=filled, actor=actor)
        if self.ctx.repo:
            await self.ctx.repo.audit(
                self.ctx.mode,
                "reconciliation",
                f"Operator resolved leg {client_order_id} of {trade_id}: filled {filled}",
                {"trade_id": trade_id, "note": note[:500], "cost": str(cost), "fee": str(fee_amount)},
                actor=actor,
            )
        if tr.status is TradeStatus.UNKNOWN:
            await self._persist(tr)
            if not tr.unresolved_legs:
                self.unresolved[tr.id] = tr
                async with self._resolve_lock:
                    await self._after_resolution(tr)
        elif tr.extra.get("position_close") and self.close_settler is not None:
            await self.close_settler(tr)
        else:  # UNRECONCILED: legs were final, values were missing - recompute and book once
            tr.status = TradeStatus.EXECUTING
            await self._account_and_book(tr)
            if tr.extra.get("carry_entry") and self.carry_opener is not None:
                await self.carry_opener(tr)
        return tr

    # ------------------------------------------------------------------ settlement
    async def _settle(self, tr: TradeRecord) -> None:
        """Every leg is known: release unused capital, handle imbalance, account, book, persist."""
        ctx = self.ctx
        if tr.extra.get("position_close") and self.close_settler is not None:
            try:
                await self.close_settler(tr)
            finally:
                self._finish(tr)
                await self._notify(tr)
            return
        try:
            await self._release_unused(tr)
            await self._note_dex_failures(tr)
            exposure = (tr.buy.filled if tr.buy else ZERO) - (tr.sell.filled if tr.sell else ZERO)
            if exposure != 0 and not tr.extra.get("no_hedge"):
                await self._recover(tr, tr.pair, exposure)
                if tr.status is TradeStatus.UNKNOWN:
                    return  # the hedge itself has an unknown outcome: held by _hold_unknown
            elif tr.buy and tr.sell and tr.buy.filled == 0 and tr.sell.filled == 0:
                tr.status = TradeStatus.FAILED
                tr.explanation = f"Both legs failed: buy {tr.buy.error or tr.buy.status.value}; sell {tr.sell.error or tr.sell.status.value}."
                ctx.failed_trades_last_hour += 1
                await ctx.breakers.record_and_check(
                    "order_failures",
                    ctx.settings.risk.max_failed_trades_per_hour,
                    CircuitBreakerReason.REPEATED_ORDER_FAILURE,
                    "repeated order failures",
                )
            else:
                tr.status = TradeStatus.FILLED
            await self._account_and_book(tr)
            if tr.extra.get("carry_entry") and self.carry_opener is not None:
                # the matched part of the pair is a live carry position even if the rest was hedged
                await self.carry_opener(tr)
        finally:
            if tr.status is not TradeStatus.UNKNOWN:
                self._finish(tr)
                await self._notify(tr)

    async def _account_and_book(self, tr: TradeRecord) -> None:
        ctx = self.ctx
        quote = tr.extra.get("quote_asset") or tr.pair.split("/")[1]
        qpx = ctx.price_usd(quote)
        outcome = compute_outcome(tr, qpx or Decimal("1"), tr.estimated_costs, price_usd=ctx.price_usd)
        if qpx is None and (tr.buy and tr.buy.filled or tr.sell and tr.sell.filled):
            outcome.complete = False
            outcome.missing.append(f"no USD price for quote asset {quote}")
        apply_outcome(tr, outcome)
        if tr.status is TradeStatus.FILLED and tr.actual_net is not None:
            tr.explanation = f"Executed: estimated net {fmt_money(tr.estimated_net)}, realized net {fmt_money(outcome.actual_net)} (difference {fmt_money(outcome.actual_net - tr.estimated_net)}); gas {fmt_money(outcome.gas)}, fees {fmt_money(outcome.trading_fees)}."
        elif tr.status is TradeStatus.UNRECONCILED:
            tr.explanation = (
                "Executed, but the venue did not report every value needed to settle it ("
                + "; ".join(outcome.missing[:3])
                + "). No realized P&L was booked; resolve it from the venue's records."
            )
            if ctx.repo:
                await ctx.repo.add_risk_event(
                    ctx.mode, "warning", "settlement_incomplete", tr.explanation, {"trade_id": tr.id}
                )
        await self._post_trade(tr, outcome)

    def _finish(self, tr: TradeRecord) -> None:
        tr.completed_at_ms = tr.completed_at_ms or now_ms()
        self.ctx.open_trade_ids.discard(tr.id)
        self.active.pop(tr.id, None)
        self.unresolved.pop(tr.id, None)
        if tr not in self.recent:
            self.recent.append(tr)
            self.recent = self.recent[-100:]

    async def _recover(self, tr: TradeRecord, pair: str, exposure: Decimal) -> None:
        ctx = self.ctx
        tr.status = TradeStatus.RECOVERING
        ctx.unhedged_since_ms = now_ms()
        tr.log("one_leg_imbalance", exposure=exposure)
        await self._notify(tr)
        if ctx.repo:
            await ctx.repo.add_risk_event(
                ctx.mode,
                "critical",
                "one_leg_fill",
                f"{pair}: unbalanced exposure {exposure} after paired execution",
                {"trade_id": tr.id},
            )
        hedge = None
        if ctx.emergency_stop and not ctx.settings.risk.hedge_during_emergency_stop:
            tr.log("hedge_skipped", reason="emergency stop and hedge_during_emergency_stop=false")
        else:

            async def journal_hedge(res: OrderResult) -> None:
                tr.hedge = res
                await self._journal(tr)

            hedge = await self.hedger.hedge(
                tr, pair, exposure, exclude=None, before_submit=journal_hedge, submit=self._run_leg
            )
        tr.hedge = hedge
        if hedge is not None and hedge.outcome_unknown:
            await self._hold_unknown(tr)
            return
        remaining = exposure - (
            hedge.filled
            if hedge and hedge.request.side is OrderSide.SELL
            else -hedge.filled
            if hedge
            else ZERO
        )
        if hedge is not None and abs(remaining) <= abs(exposure) * Decimal("0.02"):
            tr.status = TradeStatus.HEDGED
            tr.explanation = f"One leg failed ({'buy' if tr.buy.filled == 0 else 'sell'}): exposure {exposure} {pair.split('/')[0]} was neutralised on {hedge.request.venue}."
            ctx.unhedged_since_ms = None
        else:
            tr.status = TradeStatus.RECOVERING
            tr.explanation = f"One leg failed and the emergency hedge could not neutralise {remaining} {pair.split('/')[0]} - trading paused for investigation."
            await ctx.breakers.trip(CircuitBreakerReason.ONE_LEG_FILL, tr.explanation[:200])
            if ctx.repo:
                await ctx.repo.add_risk_event(
                    ctx.mode,
                    "critical",
                    "unhedged_exposure",
                    tr.explanation,
                    {"trade_id": tr.id, "remaining": str(remaining)},
                )
        ctx.failed_trades_last_hour += 1
        await ctx.breakers.record_and_check(
            "order_failures",
            ctx.settings.risk.max_failed_trades_per_hour,
            CircuitBreakerReason.REPEATED_ORDER_FAILURE,
            "repeated leg failures",
        )

    async def _abort(self, tr: TradeRecord, reason: str) -> TradeRecord:
        tr.status = TradeStatus.ABORTED
        tr.explanation = f"Aborted before submission because {reason}."
        tr.completed_at_ms = now_ms()
        tr.log("abort", reason=reason)
        if tr not in self.recent:
            self.recent.append(tr)
            self.recent = self.recent[-100:]
        if self.ctx.repo and not reason.startswith("shadow mode"):
            await self.ctx.repo.save_trade(tr)
            await self.ctx.repo.audit(
                self.ctx.mode,
                "execution",
                tr.explanation,
                {"trade_id": tr.id, "opportunity_id": tr.opportunity_id},
            )
        await self._notify(tr)
        return tr

    async def _journal(self, tr: TradeRecord) -> None:
        """Durable trade + order-intent state. Raises: callers must not transmit without it."""
        if self.ctx.repo is not None:
            await self.ctx.repo.save_trade(tr)

    async def _persist(self, tr: TradeRecord) -> None:
        try:
            await self._journal(tr)
        except Exception as exc:  # pragma: no cover - reported, the in-memory state is still conservative
            log.error("journal update failed", trade=tr.id, error=str(exc))

    async def _post_trade(self, tr: TradeRecord, outcome) -> None:
        ctx = self.ctx
        s = ctx.settings
        notional = D(tr.extra.get("notional_usd") or "0")
        carry_entry = bool(tr.extra.get("carry_entry"))
        booked_net = None
        if tr.actual_net is not None and tr.status in (
            TradeStatus.FILLED,
            TradeStatus.PARTIAL,
            TradeStatus.HEDGED,
        ):
            # Carry entry opens a position: only the cash cost (fees, gas) is realized now; the spread is part
            # of the position and is realized once, at close, by the position manager (audit F06).
            booked_net = -(outcome.trading_fees + outcome.gas) if carry_entry else tr.actual_net
            await ctx.book_realized(booked_net, f"trade {tr.id}")
        if tr.actual_net is not None and not carry_entry:
            executed = (
                tr.status in (TradeStatus.FILLED, TradeStatus.PARTIAL, TradeStatus.HEDGED)
                and outcome.matched_base > 0
            )
            if (
                executed
                and notional > 0
                and tr.prediction_error is not None
                and abs(tr.prediction_error) / notional * HUNDRED > s.risk.max_prediction_error_pct
            ):
                await ctx.breakers.trip(
                    CircuitBreakerReason.PREDICTION_ERROR,
                    f"prediction error {fmt_money(tr.prediction_error)} on {fmt_money(notional)} notional",
                )
            if (
                executed
                and outcome.slippage_variance > 0
                and notional > 0
                and outcome.slippage_variance / notional * HUNDRED > s.risk.max_slippage_pct
            ):
                await ctx.breakers.trip(
                    CircuitBreakerReason.ABNORMAL_SLIPPAGE,
                    f"realized slippage {fmt_money(outcome.slippage_variance)} exceeds limit",
                )
            if (
                executed
                and outcome.fee_variance > 0
                and notional > 0
                and outcome.fee_variance / notional * HUNDRED > Decimal("0.2")
            ):
                await ctx.breakers.trip(
                    CircuitBreakerReason.UNEXPECTED_FEES,
                    f"fees exceeded estimate by {fmt_money(outcome.fee_variance)}",
                )
        if carry_entry and tr.actual_net is not None:
            tr.extra["entry_booked_usd"] = str(booked_net if booked_net is not None else ZERO)
        if ctx.repo:
            await ctx.repo.save_trade(tr)
            if booked_net is not None:
                await ctx.repo.add_pnl(
                    ctx.mode,
                    gross=ZERO if carry_entry else outcome.actual_gross,
                    trading_fees=outcome.trading_fees,
                    gas=outcome.gas,
                    funding=outcome.funding,
                    slippage=ZERO if carry_entry else max(ZERO, outcome.slippage_variance),
                    net=booked_net,
                    win=booked_net > 0,
                )
            await ctx.repo.audit(
                ctx.mode,
                "execution",
                tr.explanation,
                {
                    "trade_id": tr.id,
                    "status": tr.status.value,
                    "estimated_net": str(tr.estimated_net),
                    "actual_net": str(tr.actual_net),
                    "booked_net": str(booked_net),
                    "prediction_error": str(tr.prediction_error),
                },
            )
        if ctx.ledger is not None and ctx.repo is not None and ctx.is_simulated_execution:
            await ctx.repo.save_paper_balances(ctx.mode, ctx.ledger.dump())
        if self.on_settled is not None:
            try:
                await self.on_settled(tr)
            except Exception as exc:  # pragma: no cover
                log.error("settlement hook failed", trade=tr.id, error=str(exc))
        rk = tr.extra.get("route_key")
        if rk and tr.actual_net is not None and not carry_entry:
            await ctx.experience.record(
                rk,
                notional_usd=D(tr.extra.get("notional_usd") or "1") or Decimal("1"),
                prediction_error_usd=tr.prediction_error or ZERO,
                slippage_variance_usd=outcome.slippage_variance,
                fee_variance_usd=outcome.fee_variance,
                gas_variance_usd=outcome.gas_variance,
                filled=tr.status is TradeStatus.FILLED,
                latency_ms=int(tr.extra.get("submit_to_fill_ms") or 0),
            )

    # ------------------------------------------------------------------ restart recovery
    async def recover_on_startup(self) -> dict:
        """Startup barrier: rebuild every unfinished trade from the journal, restore its reservations,
        quarantine its venues and try to resolve it. Must run before ``admission.recovery_complete``."""
        ctx = self.ctx
        report = {"loaded": 0, "resolved": 0, "still_unknown": 0, "aborted": 0, "foreign_mode": []}
        if ctx.repo is None:
            return report
        for row in await ctx.repo.unresolved_trades():
            try:
                tr = trade_from_dict(row.payload)
            except Exception as exc:
                log.error("journal entry unreadable", trade=row.id, error=str(exc))
                report["foreign_mode"].append(row.id)
                continue
            if tr.mode is not ctx.mode:
                report["foreign_mode"].append(f"{tr.id} ({tr.mode.value})")
                continue
            if tr.status is TradeStatus.RECOVERING and not tr.unresolved_legs:
                continue  # confirmed imbalance awaiting the operator (ONE_LEG_FILL breaker persisted)
            report["loaded"] += 1
            legs = tr.legs()
            if not legs:
                # journaled before any leg intent existed: nothing can have been transmitted
                tr.status = TradeStatus.ABORTED
                tr.explanation = (
                    "Aborted: the process stopped before submission (recovered from the journal)."
                )
                tr.completed_at_ms = now_ms()
                await self._persist(tr)
                report["aborted"] += 1
                continue
            for _r, leg in legs:
                if leg.status is OrderStatus.SUBMITTING:
                    leg.status = OrderStatus.UNKNOWN
                    leg.raw["unknown_reason"] = "process stopped while the order was being submitted"
            if ctx.is_simulated_execution:
                # simulated venues keep no state outside this process: an unfinished simulated leg did not
                # happen (the paper ledger is restored from its last persisted state)
                for _r, leg in tr.unresolved_legs:
                    leg.status = OrderStatus.FAILED
                    leg.error = "simulated leg lost at restart (no simulated venue state survives a restart)"
                    leg.completed_at_ms = now_ms()
            needs = [
                (r["venue"], r["asset"], D(r["amount"])) for r in tr.reservations if r["kind"] == "inventory"
            ]
            if needs:
                await ctx.inventory.restore(tr.id, needs)
            tr.log("recovered_from_journal")
            if tr.unresolved_legs:
                await self._hold_unknown(tr)
            else:
                self.unresolved[tr.id] = tr
                ctx.open_trade_ids.add(tr.id)
        before = len(self.unresolved)
        report["resolved"] = await self.resolve_unresolved()
        # trades whose legs were already final are settled by resolve_unresolved too
        report["still_unknown"] = len(self.unresolved)
        self.foreign_unresolved = list(report["foreign_mode"])
        if self.foreign_unresolved and ctx.repo:
            await ctx.repo.add_risk_event(
                ctx.mode,
                "critical",
                "foreign_mode_unresolved",
                "Unresolved trades of another mode exist; switch to that mode to resolve them: "
                + ", ".join(self.foreign_unresolved)[:300],
                {},
            )
        log.info(
            "journal recovery",
            **{k: (v if not isinstance(v, list) else len(v)) for k, v in report.items()},
            considered=before,
        )
        return report

    async def _notify(self, tr: TradeRecord) -> None:
        if self.on_trade:
            try:
                await self.on_trade(tr)
            except Exception as exc:  # pragma: no cover
                log.warning("trade callback failed", error=str(exc))
