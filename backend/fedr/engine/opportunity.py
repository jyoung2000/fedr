"""Opportunity engine / router.

Builds the route graph for every enabled strategy and configured pair, prices
both legs with *executable* quotes, and runs the full guard stack:
Profit Guard -> Gas Guard -> Slippage Guard -> Latency Guard -> Risk Engine ->
Circuit breakers. Ranking is by worst-case net, never by gross spread.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fedr.connectors.base import ConnectorError, VenueConnector
from fedr.core.enums import Chain, Decision, GasRegime, OrderSide, Strategy, VenueKind
from fedr.core.logging import get_logger
from fedr.core.models import ExecutionQuote, GasAssessment, Opportunity, RiskAssessment, new_id, now_ms, to_jsonable
from fedr.core.money import HUNDRED, ONE, ZERO, D, floor_to_step, fmt_money
from fedr.engine.context import EngineContext
from fedr.engine.experience import route_key
from fedr.engine.profit_guard import FlashLoanTerms, ProfitGuardInputs
from fedr.engine.risk_engine import PortfolioState
from fedr.engine.strategies.carry import CarryQuote, evaluate_carry
from fedr.engine.strategies.catalog import CATALOG, StrategyInfo, candidate_pairs, strategy_enabled

log = get_logger("opportunity")

FLASH_LOAN_GAS_LIMIT = 450_000
FLASH_LOAN_FEE_PCT = {"aave_v3": Decimal("0.05"), "balancer_v2": Decimal("0"), "morpho_blue": Decimal("0")}


@dataclass(slots=True)
class RouteCandidate:
    strategy: Strategy
    pair: str
    buy: VenueConnector
    sell: VenueConnector
    flash_loan: bool = False

    @property
    def key(self) -> str:
        return f"{self.strategy.value}|{self.pair}|{self.buy.name}->{self.sell.name}|{'fl' if self.flash_loan else 'pf'}"


class OpportunityEngine:
    def __init__(self, ctx: EngineContext):
        self.ctx = ctx
        self.latest: dict[str, Opportunity] = {}  # route key -> most recent evaluation
        self._last_persist: dict[str, int] = {}
        self.scan_count = 0
        self.last_scan_ms = 0
        self.last_scan_duration_ms = 0
        self._scan_lock = asyncio.Lock()

    # ------------------------------------------------------------------ candidates
    def candidates(self, pairs: list[str] | None = None) -> list[RouteCandidate]:
        s = self.ctx.settings
        venues = [c for c in self.ctx.connectors.values() if c.connected]
        out: list[RouteCandidate] = []
        for strategy, info in CATALOG.items():
            if not strategy_enabled(s, strategy):
                continue
            if strategy is Strategy.FLASH_LOAN and not s.flash_loan.enabled:
                continue
            for pair in pairs or s.general.pairs:
                for b, sl in candidate_pairs(info, venues, pair):
                    out.append(RouteCandidate(strategy, pair, b, sl, flash_loan=info.atomic))
        return out

    def candidate_for(self, opp: Opportunity) -> RouteCandidate | None:
        b = self.ctx.connectors.get(opp.buy.venue)
        s = self.ctx.connectors.get(opp.sell.venue)
        if b is None or s is None:
            return None
        return RouteCandidate(opp.strategy, opp.pair, b, s, flash_loan=opp.strategy is Strategy.FLASH_LOAN)

    # ------------------------------------------------------------------ portfolio
    def portfolio_state(self) -> PortfolioState:
        ctx = self.ctx
        inv = ctx.inventory
        total = inv.total_usd()
        cap = ctx.risk_engine.capital_breakdown(total, self._gas_reserve_usd(), ZERO)
        return PortfolioState(
            total_capital_usd=total,
            usable_capital_usd=cap["usable"],
            exposure_by_venue_usd=inv.exposure_by(lambda l: l.venue),
            exposure_by_chain_usd=inv.exposure_by(lambda l: l.venue if l.kind == "chain" else None),
            exposure_by_asset_usd=inv.exposure_by(lambda l: l.asset),
            open_trades=len(ctx.open_trade_ids),
            daily_realized_pnl_usd=ctx.daily_pnl_usd,
            failed_trades_last_hour=ctx.failed_trades_last_hour,
            venue_health={n: ctx.venue_health(n) for n in ctx.connectors},
            available_balances=inv.balances_map(),
            unhedged_seconds=int((now_ms() - ctx.unhedged_since_ms) / 1000) if ctx.unhedged_since_ms else 0,
            active_breakers=[],
            gas_reserve_ok={c.value: ok for c in Chain if (ok := ctx.gas_reserve_ok(c)) is not None},
        )

    def _gas_reserve_usd(self) -> Decimal:
        total = ZERO
        for chain_name, amount in self.ctx.settings.gas.gas_reserve.items():
            try:
                chain = Chain(chain_name)
            except ValueError:
                continue
            px = self.ctx.price_usd(chain.native_token)
            if px is not None and self.ctx.inventory.available(chain.value, chain.native_token) > 0:
                total += min(self.ctx.inventory.available(chain.value, chain.native_token), D(amount)) * px
        return total

    # ------------------------------------------------------------------ sizing
    def pick_size(self, cand: RouteCandidate, price_usd: Decimal, portfolio: PortfolioState) -> tuple[Decimal, list[str]]:
        s = self.ctx.settings
        base, quote = cand.pair.split("/")
        notes: list[str] = []
        target_usd = min(s.trading.max_trade_size_usd, portfolio.usable_capital_usd * s.risk.max_capital_usage_pct / HUNDRED) if not cand.flash_loan else min(s.flash_loan.max_loan_usd, s.risk.max_flash_loan_usd)
        if target_usd <= 0:
            return ZERO, ["no usable capital"]
        size = target_usd / price_usd
        # liquidity caps from cached CEX books
        for venue, side in ((cand.buy, OrderSide.BUY), (cand.sell, OrderSide.SELL)):
            if venue.kind in (VenueKind.CEX, VenueKind.PERP):
                book = self.ctx.hub.get_book(venue.name, cand.pair if venue.kind is VenueKind.CEX else f"{base}/{quote}:{quote}")
                if book is not None:
                    limit = self.ctx.slippage_guard.limit_for(base, venue.name, f"{cand.buy.name}->{cand.sell.name}", target_usd)
                    cap = book.max_size_within_slippage(side, limit)
                    if cap < size:
                        notes.append(f"size capped by {venue.name} depth ({cap:.6f} {base} within {limit}% slippage)")
                        size = cap
        # inventory caps (prefunded strategies only)
        if not cand.flash_loan:
            quote_avail = self.ctx.inventory.available(self.ctx.ledger_venue_for(cand.buy), quote)
            base_avail = self.ctx.inventory.available(self.ctx.ledger_venue_for(cand.sell), base)
            max_by_quote = quote_avail / price_usd * Decimal("0.98")  # leave room for fees
            if max_by_quote < size:
                notes.append(f"size capped by {quote} inventory on {cand.buy.name}")
                self.ctx.rebalancer.note_blocked_by_inventory(self.ctx.ledger_venue_for(cand.buy), quote, ZERO)
                size = max_by_quote
            if base_avail < size:
                notes.append(f"size capped by {base} inventory on {cand.sell.name}")
                self.ctx.rebalancer.note_blocked_by_inventory(self.ctx.ledger_venue_for(cand.sell), base, ZERO)
                size = base_avail
        m = cand.buy.market(cand.pair) or cand.sell.market(cand.pair)
        step = m.amount_step if m else Decimal("0.000001")
        size = floor_to_step(max(ZERO, size), step)
        return size, notes

    # ------------------------------------------------------------------ evaluation
    FLASH_LOAN_SIZE_LADDER = (Decimal("1"), Decimal("0.5"), Decimal("0.25"), Decimal("0.1"), Decimal("0.04"))

    async def evaluate(self, cand: RouteCandidate, size: Decimal | None = None) -> Opportunity | None:
        ctx = self.ctx
        s = ctx.settings
        base, quote = cand.pair.split("/")
        info: StrategyInfo = CATALOG[cand.strategy]
        portfolio = self.portfolio_state()
        px = ctx.price_usd(base)
        if px is None or px <= 0:
            return None
        reasons: list[str] = []
        if size is None and cand.flash_loan:
            # Borrowed size is free to choose: evaluate a ladder of loan sizes and keep the strongest worst case.
            max_size, notes = self.pick_size(cand, px, portfolio)
            best: Opportunity | None = None
            for frac in self.FLASH_LOAN_SIZE_LADDER:
                cand_size = floor_to_step(max_size * frac * Decimal("0.98"), Decimal("0.000001"))
                if cand_size <= 0 or cand_size * px < s.trading.min_trade_size_usd:
                    continue
                o = await self.evaluate(cand, size=cand_size)
                if o is None:
                    continue
                if best is None or (o.decision is Decision.SAFE_TO_EXECUTE, o.profit.worst_case_profit) > (best.decision is Decision.SAFE_TO_EXECUTE, best.profit.worst_case_profit):
                    best = o
            if best is not None:
                best.block_reasons.extend(n for n in notes if n not in best.block_reasons)
                best.extra["size_ladder"] = [str(f) for f in self.FLASH_LOAN_SIZE_LADDER]
            return best
        if size is None:
            size, notes = self.pick_size(cand, px, portfolio)
            reasons.extend(notes)
        if size <= 0 or size * px < s.trading.min_trade_size_usd:
            reasons.append(f"tradeable size {fmt_money(size * px)} below minimum {fmt_money(s.trading.min_trade_size_usd)}")
            size = max(size, floor_to_step(s.trading.min_trade_size_usd / px, Decimal("0.000001")))
        # ---- quotes (concurrently) ----
        try:
            if info.carry:
                buy_q, sell_q, carry = await evaluate_carry(ctx, cand.strategy, cand.buy, cand.sell, cand.pair, size)
            else:
                carry = None
                buy_q, sell_q = await asyncio.gather(self._leg_quote(cand.buy, cand.pair, OrderSide.BUY, size), self._leg_quote(cand.sell, cand.pair, OrderSide.SELL, size))
        except ConnectorError as exc:
            log.debug("quote failed", route=cand.key, error=str(exc))
            return None
        except Exception as exc:  # pragma: no cover
            log.warning("quote error", route=cand.key, error=str(exc))
            return None
        if buy_q is None or sell_q is None:
            return None
        return await self._assess(cand, info, buy_q, sell_q, size, px, portfolio, reasons, carry)

    async def _leg_quote(self, venue: VenueConnector, pair: str, side: OrderSide, size: Decimal) -> ExecutionQuote | None:
        book = self.ctx.hub.get_book(venue.name, pair) if venue.kind in (VenueKind.CEX, VenueKind.PERP) else None
        if venue.kind in (VenueKind.CEX, VenueKind.PERP) and book is None:
            try:
                book = await venue.fetch_order_book(pair, self.ctx.settings.advanced.orderbook_depth)
                await self.ctx.hub.ingest(book)
            except ConnectorError:
                return None
        return await venue.get_quote(pair, side, size, order_book=book)

    async def _assess(self, cand: RouteCandidate, info: StrategyInfo, buy_q: ExecutionQuote, sell_q: ExecutionQuote, size: Decimal, px: Decimal, portfolio: PortfolioState, pre_reasons: list[str], carry: CarryQuote | None) -> Opportunity:
        ctx = self.ctx
        s = ctx.settings
        base, quote = cand.pair.split("/")
        notional = size * px
        route = f"{cand.buy.name}->{cand.sell.name}"
        reasons = list(pre_reasons)
        # ---- gas ----
        gas: GasAssessment | None = None
        on_chain_legs = [q for q in (buy_q, sell_q) if q.kind is VenueKind.DEX]
        gross_ref = (sell_q.reference_price - buy_q.reference_price) * size
        if cand.flash_loan and buy_q.chain is not None:
            snap = ctx.gas_oracle.snapshot(buy_q.chain)
            gas = ctx.gas_guard.assess(snap, FLASH_LOAN_GAS_LIMIT, gross_ref, max_gas_override_usd=s.flash_loan.max_gas_usd, legs=1)
        elif on_chain_legs:
            assessments = []
            for q in on_chain_legs:
                snap = ctx.gas_oracle.snapshot(q.chain) if q.chain else None
                assessments.append(ctx.gas_guard.assess(snap, q.gas_limit or 300_000, gross_ref, legs=1))
            gas = _merge_gas(assessments)
        # ---- flash loan terms ----
        fl_terms = None
        if cand.flash_loan:
            provider = s.flash_loan.provider if s.flash_loan.provider != "auto" else "aave_v3"
            sim_ok = None
            if ctx.flash_loan_simulator is not None:
                try:
                    sim_ok = await ctx.flash_loan_simulator(cand, buy_q, sell_q, size)
                except Exception as exc:
                    reasons.append(f"flash loan simulation error: {exc}")
                    sim_ok = False
            fl_terms = FlashLoanTerms(provider=provider, loan_amount_usd=buy_q.quote_amount, fee_pct=FLASH_LOAN_FEE_PCT.get(provider, Decimal("0.09")), simulation_passed=sim_ok, atomic=buy_q.chain is not None and buy_q.chain is sell_q.chain)
        # ---- profit guard ----
        rk = route_key(ctx.mode, cand.strategy, cand.buy.name, cand.sell.name, cand.pair, notional)
        haircut = ZERO
        quote_rate = ONE
        qpx = ctx.price_usd(quote)
        if qpx is not None:
            quote_rate = qpx
        inputs = ProfitGuardInputs(
            buy=buy_q,
            sell=sell_q,
            settings=s,
            gas=gas,
            quote_usd_rate=quote_rate,
            experience_extra_pct=ctx.experience.extra_buffer_pct(rk),
            flash_loan=fl_terms,
            funding_cost_usd=carry.funding_cost_usd if carry else ZERO,
            stablecoin_haircut_pct=haircut,
            round_trips=2 if info.carry else 1,
        )
        profit = ctx.profit_guard.assess(inputs)
        reasons.extend(profit.reasons)
        # ---- slippage / latency ----
        for q in (buy_q, sell_q):
            reasons.extend(ctx.slippage_guard.check(q, route, notional, base))
        reasons.extend(ctx.latency_guard.check(buy_q, sell_q))
        # ---- breakers ----
        breaker_reasons = ctx.breakers.blocks(venue=cand.buy.name, strategy=cand.strategy.value) + ctx.breakers.blocks(venue=cand.sell.name, chain=(buy_q.chain or sell_q.chain).value if (buy_q.chain or sell_q.chain) else None)
        reasons.extend(dict.fromkeys(breaker_reasons))
        if ctx.emergency_stop:
            reasons.append("EMERGENCY STOP is active")
        if not s.general.bot_enabled:
            reasons.append("bot is paused")
        # ---- risk ----
        portfolio.active_breakers = list(dict.fromkeys(breaker_reasons))
        risk: RiskAssessment = ctx.risk_engine.assess(
            strategy=cand.strategy,
            buy=buy_q,
            sell=sell_q,
            base=base,
            quote=quote,
            notional_usd=notional,
            capital_required_usd=profit.capital_required,
            portfolio=portfolio,
            gas_usd=(gas.expected_gas_cost_usd + gas.priority_fee_usd) if gas else ZERO,
            flash_loan_usd=fl_terms.loan_amount_usd if fl_terms else None,
        )
        reasons.extend(r for r in risk.reasons if r not in reasons)
        if gas is not None and gas.regime is GasRegime.EXTREME and on_chain_legs:
            reasons.append("gas regime EXTREME - on-chain trading paused")
        reasons = list(dict.fromkeys(reasons))
        decision = Decision.SAFE_TO_EXECUTE if not reasons else Decision.BLOCKED
        explanation = profit.explanation if decision is Decision.SAFE_TO_EXECUTE else "Blocked because " + "; ".join(reasons[:3]) + "."
        opp = Opportunity(
            id=new_id("opp"),
            mode=ctx.mode,
            strategy=cand.strategy,
            base=base,
            quote=quote,
            buy=buy_q,
            sell=sell_q,
            size_base=size,
            profit=profit,
            gas=gas,
            risk=risk,
            decision=decision,
            block_reasons=reasons,
            explanation=explanation,
            expires_at_ms=now_ms() + s.advanced.opportunity_ttl_ms,
            extra={"route_key": rk, "notional_usd": str(notional), "flash_loan": bool(cand.flash_loan), "carry": to_jsonable(carry) if carry else None, "experience_extra_pct": str(ctx.experience.extra_buffer_pct(rk))},
        )
        return opp

    # ------------------------------------------------------------------ scanning
    async def scan(self, pairs: list[str] | None = None) -> list[Opportunity]:
        async with self._scan_lock:
            t0 = now_ms()
            cands = self.candidates(pairs)
            results = await asyncio.gather(*(self.evaluate(c) for c in cands), return_exceptions=True)
            opps: list[Opportunity] = []
            for cand, res in zip(cands, results):
                if isinstance(res, Exception):
                    log.warning("evaluation failed", route=cand.key, error=str(res))
                    continue
                if res is None:
                    continue
                opps.append(res)
                self.latest[cand.key] = res
            # strategy selection: prefunded vs flash loan on the same route -> keep both but annotate the choice
            self._annotate_alternatives(opps)
            self.scan_count += 1
            self.last_scan_ms = now_ms()
            self.last_scan_duration_ms = self.last_scan_ms - t0
            await self._persist(opps)
            return self.ranked()

    def _annotate_alternatives(self, opps: list[Opportunity]) -> None:
        by_route: dict[str, list[Opportunity]] = {}
        for o in opps:
            by_route.setdefault(f"{o.pair}|{o.buy.venue}->{o.sell.venue}", []).append(o)
        for group in by_route.values():
            if len(group) < 2:
                continue
            best = max(group, key=lambda o: (o.decision is Decision.SAFE_TO_EXECUTE, o.profit.worst_case_profit, o.profit.expected_net_profit))
            for o in group:
                o.alternatives = [
                    {"strategy": a.strategy.value, "expected_net": str(a.profit.expected_net_profit), "worst_case": str(a.profit.worst_case_profit), "decision": a.decision.value, "chosen": a is best}
                    for a in group
                ]
                if o is not best:
                    o.block_reasons.append(f"{best.strategy.value} route preferred: stronger worst-case economics ({fmt_money(best.profit.worst_case_profit)} vs {fmt_money(o.profit.worst_case_profit)})")
                    o.decision = Decision.BLOCKED
                    o.explanation = "Blocked because " + o.block_reasons[-1] + "."

    def ranked(self) -> list[Opportunity]:
        now = now_ms()
        live = [o for o in self.latest.values() if o.expires_at_ms > now]
        return sorted(live, key=lambda o: (o.decision is not Decision.SAFE_TO_EXECUTE, -o.profit.worst_case_profit, -o.profit.expected_net_profit, o.risk.score))

    def get(self, opp_id: str) -> Opportunity | None:
        for o in self.latest.values():
            if o.id == opp_id:
                return o
        return None

    async def _persist(self, opps: list[Opportunity]) -> None:
        repo = self.ctx.repo
        if repo is None:
            return
        now = now_ms()
        for o in opps:
            key = f"{o.strategy.value}|{o.pair}|{o.route}"
            throttle = 0 if o.decision is Decision.SAFE_TO_EXECUTE else 60_000
            if now - self._last_persist.get(key, 0) < throttle:
                continue
            self._last_persist[key] = now
            try:
                await repo.save_opportunity(o)
                await repo.audit(o.mode, "decision", o.explanation, {"opportunity_id": o.id, "strategy": o.strategy.value, "pair": o.pair, "route": o.route, "size_base": str(o.size_base), "buy_price": str(o.buy.avg_price), "sell_price": str(o.sell.avg_price), "market_ts": {"buy": o.buy.market_ts_ms, "sell": o.sell.market_ts_ms}, "costs": o.profit.expected_costs.as_dict(), "worst_case_costs": o.profit.worst_case_costs.as_dict(), "gross": str(o.profit.gross_profit), "expected_net": str(o.profit.expected_net_profit), "worst_case": str(o.profit.worst_case_profit), "risk_score": o.risk.score, "decision": o.decision.value, "reasons": o.block_reasons})
            except Exception as exc:  # pragma: no cover
                log.warning("persist opportunity failed", error=str(exc))


def _merge_gas(items: list[GasAssessment]) -> GasAssessment:
    if len(items) == 1:
        return items[0]
    order = [GasRegime.NORMAL, GasRegime.ELEVATED, GasRegime.HIGH, GasRegime.EXTREME]
    worst = max((i.regime for i in items), key=order.index)
    return GasAssessment(
        chain=items[0].chain,
        regime=worst,
        current_gas_cost_usd=sum((i.current_gas_cost_usd for i in items), ZERO),
        expected_gas_cost_usd=sum((i.expected_gas_cost_usd for i in items), ZERO),
        stress_gas_cost_usd=sum((i.stress_gas_cost_usd for i in items), ZERO),
        gas_pct_of_gross=sum((i.gas_pct_of_gross for i in items), ZERO),
        passed=all(i.passed for i in items),
        reasons=[r for i in items for r in i.reasons],
        priority_fee_usd=sum((i.priority_fee_usd for i in items), ZERO),
    )


__all__ = ["OpportunityEngine", "RouteCandidate", "datetime", "timedelta", "timezone"]
