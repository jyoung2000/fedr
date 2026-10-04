"""Carry position lifecycle (spot ↔ perpetual, funding-rate, basis).

Deterministic and auditable: a position is opened only after the paired executor filled BOTH legs
(spot buy + perp short), then tracked with funding accrual, basis, liquidation distance and exit rules:

* target convergence: basis has closed to ``exit_basis_pct`` or below
* funding flips against us for ``funding_flip_periods`` consecutive settlements
* max hold time reached
* liquidation distance (perp mark vs entry, 1x collateral) below the configured minimum
* forced close (user, emergency stop, circuit breaker, reconciliation mismatch)

Closing = sell spot + buy back the perp with the same executor / paper simulator. The whole manager is
exercised in paper mode; it has NOT been verified against a live derivatives venue in this build.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from fedr.core.enums import CircuitBreakerReason, OrderSide, OrderStatus, Strategy, TradeStatus
from fedr.core.logging import get_logger
from fedr.core.models import Opportunity, OrderRequest, OrderResult, TradeRecord, new_id, now_ms
from fedr.core.money import HUNDRED, ZERO, D, fmt_money
from fedr.db.models import Position, utcnow

log = get_logger("positions")


@dataclass(slots=True)
class CarryPolicy:
    exit_basis_pct: Decimal = Decimal("0.05")  # close when basis converges to this or below
    funding_flip_periods: int = 2  # consecutive negative funding periods before exiting
    max_hold_hours: int = 72
    min_liquidation_distance_pct: Decimal = Decimal("25")  # 1x collateral: distance to the liquidation price
    max_basis_widening_pct: Decimal = Decimal("1.5")  # stop-loss on basis widening beyond entry
    funding_interval_hours: Decimal = Decimal("8")
    # An emergency stop or a global circuit breaker suspends *automatic* exits: a delta-neutral position is
    # not directionally exposed, while a forced close during a venue incident risks a one-legged failure.
    # Manual close (API / UI) stays available. Set True to flatten everything on emergency stop instead.
    flatten_on_emergency_stop: bool = False
    # |rate| per interval above this is treated as a data/market anomaly: no new carry positions, breaker trips
    max_abs_funding_rate: Decimal = Decimal("0.003")


class PositionManager:
    def __init__(self, ctx, executor, policy: CarryPolicy | None = None):
        self.ctx = ctx
        self.executor = executor
        self.policy = policy or CarryPolicy()
        self.open: dict[str, Position] = {}
        # the executor creates the position when a carry entry settles - also when it settles late,
        # after an unknown leg was resolved - and hands close trades back here once their legs are known
        executor.carry_opener = self._position_from_trade
        executor.close_settler = self._settle_close_trade

    # ---------------------------------------------------------------- persistence
    async def load(self) -> None:
        if self.ctx.repo is None:
            return
        for p in await self.ctx.repo.list_positions(self.ctx.mode, open_only=True):
            self.open[p.id] = p

    async def _save(self, p: Position) -> None:
        if self.ctx.repo is not None:
            await self.ctx.repo.save_position(p)

    # ---------------------------------------------------------------- open
    async def open_from_opportunity(
        self, opp: Opportunity, trigger: str = "auto"
    ) -> tuple[TradeRecord, Position | None]:
        """Open both legs through the paired executor. The matched part of the pair becomes a position."""
        carry_in = opp.extra.get("carry") or {}
        fr = carry_in.get("funding_rate")
        if fr is not None and await self._funding_anomaly(D(fr), f"{opp.sell.venue} {opp.sell.symbol}"):
            tr = self._aborted(opp, "Aborted: funding rate anomaly (FUNDING_ANOMALY breaker tripped).")
            return tr, None
        if not self.ctx.is_simulated_execution:
            # FEDR's carry risk model (1x, isolated, one-way, linear) must match the venue account
            perp = self.ctx.connectors.get(opp.sell.venue)
            verify = getattr(perp, "verify_derivative_settings", None)
            problems = (
                await verify(opp.sell.symbol)
                if verify is not None
                else [f"{opp.sell.venue} cannot verify its derivative account settings"]
            )
            if problems:
                tr = self._aborted(
                    opp, "Aborted: derivative account settings not verified - " + "; ".join(problems[:3])
                )
                if self.ctx.repo:
                    await self.ctx.repo.save_trade(tr)
                return tr, None
        tr = await self.executor.execute(opp, trigger=trigger, allow_carry=True, carry=carry_in)
        pos = next((p for p in self.open.values() if p.payload.get("trade_id") == tr.id), None)
        return tr, pos

    def _aborted(self, opp: Opportunity, why: str) -> TradeRecord:
        return TradeRecord(
            id=new_id("trd"),
            mode=self.ctx.mode,
            strategy=opp.strategy,
            opportunity_id=opp.id,
            pair=opp.pair,
            route=f"{opp.buy.venue} → {opp.sell.venue}",
            size_base=opp.size_base,
            status=TradeStatus.ABORTED,
            estimated_gross=opp.profit.gross_profit,
            estimated_net=opp.profit.expected_net_profit,
            estimated_worst_case=opp.profit.worst_case_profit,
            estimated_costs=opp.profit.expected_costs.as_dict(),
            explanation=why,
            completed_at_ms=now_ms(),
        )

    async def _position_from_trade(self, tr: TradeRecord) -> Position | None:
        """Create the carry position for the MATCHED part of a settled entry (spot long + perp short).

        Called by the executor for every settled carry entry, including a partial or hedged pair - the
        matched part is a live carry position that must be monitored even if the imbalance was hedged."""
        if tr.buy is None or tr.sell is None:
            return None
        matched = min(tr.buy.filled, tr.sell.filled)
        if matched <= 0 or any(p.payload.get("trade_id") == tr.id for p in self.open.values()):
            return None
        spot_px = (tr.buy.quote_value / tr.buy.filled) if tr.buy.quote_value else tr.buy.avg_price
        perp_px = (tr.sell.quote_value / tr.sell.filled) if tr.sell.quote_value else tr.sell.avg_price
        carry = tr.extra.get("carry") or {}
        entry_booked = tr.extra.get("entry_booked_usd")
        pos = Position(
            id=new_id("pos"),
            mode=self.ctx.mode.value,
            strategy=tr.strategy.value,
            pair=tr.pair,
            spot_venue=tr.buy.request.venue,
            perp_venue=tr.sell.request.venue,
            perp_symbol=tr.sell.request.symbol,
            size_base=str(matched),
            status="open",
            entry_spot_price=str(spot_px),
            entry_perp_price=str(perp_px),
            entry_basis_pct=str((perp_px - spot_px) / spot_px * HUNDRED) if spot_px else "0",
            funding_collected_usd="0",  # ORM defaults only apply on flush: set explicitly for in-memory use
            opened_at=utcnow(),
            fees_usd=str(
                tr.actual_fees if tr.actual_fees is not None else tr.buy.fee_quote + tr.sell.fee_quote
            ),
            payload={
                "trade_id": tr.id,
                "funding_rate_at_entry": carry.get("funding_rate"),
                "funding_periods": [],
                "negative_funding_streak": 0,
                # realized P&L already booked when the entry settled (fees/gas only - audit F06); the close
                # books lifecycle net minus this, so every position is booked exactly once in total
                **(
                    {"entry_booked_usd": str(entry_booked)}
                    if entry_booked is not None
                    else {"entry_booking_unknown": True}
                ),
                "closed_spot": "0",
                "closed_perp": "0",
                "exit_spot_quote": "0",
                "exit_perp_quote": "0",
                "exit_fees_usd": "0",
                "close_trades": [],
            },
        )
        self.open[pos.id] = pos
        await self._save(pos)
        if self.ctx.repo:
            await self.ctx.repo.audit(
                self.ctx.mode,
                "execution",
                f"Carry position opened: {pos.pair} long spot on {pos.spot_venue} / short perp on {pos.perp_venue}, basis {D(pos.entry_basis_pct):.3f}%",
                {"position_id": pos.id, "trade_id": tr.id, "size": str(matched)},
            )
        return pos

    # ---------------------------------------------------------------- monitor
    async def mark(self, pos: Position) -> dict:
        """Current basis, unrealized P&L, liquidation distance for an open position."""
        spot_book = self.ctx.hub.get_book(pos.spot_venue, pos.pair)
        perp_book = self.ctx.hub.get_book(pos.perp_venue, pos.perp_symbol)
        spot_rem, perp_rem = self.remaining(pos)
        spot_px = spot_book.best_bid if spot_book and spot_book.best_bid else D(pos.entry_spot_price)
        perp_px = perp_book.best_ask if perp_book and perp_book.best_ask else D(pos.entry_perp_price)
        basis_pct = (perp_px - spot_px) / spot_px * HUNDRED if spot_px > 0 else ZERO
        # long spot marked at bid, short perp bought back at ask
        spot_pnl = (spot_px - D(pos.entry_spot_price)) * spot_rem
        perp_pnl = (D(pos.entry_perp_price) - perp_px) * perp_rem
        funding = D(pos.funding_collected_usd or "0")
        unrealized = spot_pnl + perp_pnl + funding - D(pos.fees_usd or "0")
        venue_liq = (pos.payload or {}).get("venue_liquidation_price")
        if venue_liq and perp_px > 0:
            # the venue's own liquidation price (live/testnet, refreshed by refresh_venue_state)
            liq_distance_pct = (D(venue_liq) - perp_px) / perp_px * HUNDRED
        else:
            # paper model, 1x collateral short: liquidation when the perp rises ~100% above entry
            liq_distance_pct = (
                (Decimal(2) * D(pos.entry_perp_price) * Decimal("0.95") - perp_px) / perp_px * HUNDRED
                if perp_px > 0
                else ZERO
            )
        return {
            "spot_price": spot_px,
            "perp_price": perp_px,
            "basis_pct": basis_pct,
            "unrealized_usd": unrealized,
            "funding_usd": funding,
            "liquidation_distance_pct": liq_distance_pct,
            "hours_open": (now_ms() - int(pos.opened_at.timestamp() * 1000)) / 3_600_000
            if pos.opened_at
            else ZERO,
        }

    async def _funding_anomaly(self, rate: Decimal | None, where: str) -> bool:
        if rate is None or abs(rate) <= self.policy.max_abs_funding_rate:
            return False
        await self.ctx.breakers.trip(
            CircuitBreakerReason.FUNDING_ANOMALY,
            f"funding rate {rate} per interval on {where} exceeds ±{self.policy.max_abs_funding_rate}",
            scope="strategy:carry",
        )
        if self.ctx.repo:
            await self.ctx.repo.add_risk_event(
                self.ctx.mode, "warning", "funding_anomaly", f"abnormal funding rate {rate} on {where}", {}
            )
        return True

    async def accrue_funding(
        self, pos: Position, funding_rate: Decimal | None, paid_usd: Decimal | None = None
    ) -> None:
        """Called once per funding interval (short perp receives positive funding).

        ``paid_usd`` is the venue-reported settled payment (funding history) when available - authoritative.
        Without it the payment is ESTIMATED from the rate: in paper/simulation that estimate is the simulated
        settlement and is credited to the paper ledger; on a real venue it is flagged so the close cannot be
        booked as realized until the actual payments are known."""
        if funding_rate is None and paid_usd is None:
            return
        if funding_rate is not None:
            await self._funding_anomaly(funding_rate, f"{pos.perp_venue} {pos.perp_symbol}")
        _spot_rem, perp_rem = self.remaining(pos)
        mark = await self.mark(pos)
        if paid_usd is not None:
            payment, source = paid_usd, "venue_history"
        else:
            payment = funding_rate * mark["perp_price"] * perp_rem  # positive rate -> we receive
            source = "paper_settlement" if self.ctx.is_simulated_execution else "rate_estimate"
        pos.funding_collected_usd = str(D(pos.funding_collected_usd or "0") + payment)
        if source == "paper_settlement" and self.ctx.ledger is not None:
            perp = self.ctx.connectors.get(pos.perp_venue)
            quote = pos.perp_symbol.split(":")[0].split("/")[1]
            lv = self.ctx.ledger_venue_for(perp) if perp is not None else pos.perp_venue
            await self.ctx.ledger.adjust(lv, quote, payment, allow_negative=True)
        if source == "rate_estimate":
            pos.payload = {**pos.payload, "funding_estimated": True}
        periods = list(pos.payload.get("funding_periods", []))
        periods.append(
            {
                "ts": now_ms(),
                "rate": str(funding_rate) if funding_rate is not None else None,
                "payment_usd": str(payment),
                "source": source,
            }
        )
        pos.payload = {
            **pos.payload,
            "funding_periods": periods[-200:],
            "negative_funding_streak": (pos.payload.get("negative_funding_streak", 0) + 1)
            if funding_rate < 0
            else 0,
        }
        await self._save(pos)

    def exit_reason(self, pos: Position, mark: dict) -> str | None:
        p = self.policy
        if mark["basis_pct"] <= p.exit_basis_pct:
            return f"basis converged to {mark['basis_pct']:.3f}% (target {p.exit_basis_pct}%)"
        if pos.payload.get("negative_funding_streak", 0) >= p.funding_flip_periods:
            return f"funding negative for {pos.payload['negative_funding_streak']} periods"
        if mark["hours_open"] >= p.max_hold_hours:
            return f"max hold time {p.max_hold_hours}h reached"
        if mark["liquidation_distance_pct"] < p.min_liquidation_distance_pct:
            return f"liquidation distance {mark['liquidation_distance_pct']:.1f}% below minimum {p.min_liquidation_distance_pct}%"
        if mark["basis_pct"] - D(pos.entry_basis_pct) > p.max_basis_widening_pct:
            return f"basis widened by {mark['basis_pct'] - D(pos.entry_basis_pct):.2f}% (stop)"
        return None

    async def monitor(self) -> list[dict]:
        """One monitoring pass: mark every open position and close those that hit an exit rule."""
        out = []
        for pos in list(self.open.values()):
            if pos.status != "open":
                continue
            m = await self.mark(pos)
            reason = self.exit_reason(pos, m)
            suspended = None
            if self.ctx.emergency_stop:
                if self.policy.flatten_on_emergency_stop:
                    reason = "emergency stop (flatten)"
                else:
                    suspended, reason = "emergency stop: automatic exits suspended", None
            elif self.ctx.breakers.is_tripped():
                suspended, reason = "circuit breaker active: automatic exits suspended", None
            out.append(
                {
                    "id": pos.id,
                    **{k: str(v) for k, v in m.items()},
                    "exit_reason": reason,
                    "suspended": suspended,
                }
            )
            if reason:
                await self.close(pos, reason)
        return out

    async def accrue_due_funding(self, rate_provider, paid_provider=None) -> int:
        """Accrue one funding period for every open position whose next settlement is due.

        ``rate_provider(perp_venue, perp_symbol)`` is awaited and must return the settled rate as a
        fraction (or None when unknown). ``paid_provider(perp_venue, perp_symbol, since_ms)`` (optional,
        real venues) returns the venue-reported payments in USD since ``since_ms`` or None.
        Returns the number of positions updated.
        """
        n = 0
        interval_ms = int(self.policy.funding_interval_hours * 3_600_000)
        for pos in list(self.open.values()):
            if pos.status != "open":
                continue
            last = int(
                pos.payload.get("last_funding_ms")
                or (pos.opened_at.timestamp() * 1000 if pos.opened_at else 0)
            )
            if now_ms() - last < interval_ms:
                continue
            paid = None
            try:
                rate = await rate_provider(pos.perp_venue, pos.perp_symbol)
                if paid_provider is not None and not self.ctx.is_simulated_execution:
                    paid = await paid_provider(pos.perp_venue, pos.perp_symbol, last)
            except Exception as exc:  # a venue hiccup must not stop monitoring
                log.warning("funding rate unavailable", position=pos.id, error=str(exc)[:120])
                continue
            pos.payload = {**pos.payload, "last_funding_ms": now_ms()}
            await self.accrue_funding(pos, D(rate) if rate is not None else None, paid_usd=paid)
            n += 1
        return n

    async def close_all(self, reason: str, trigger: str = "user") -> list[TradeRecord]:
        out = []
        for pos in list(self.open.values()):
            if pos.status == "open":
                tr = await self.close(pos, reason, trigger=trigger)
                if tr is not None:
                    out.append(tr)
        return out

    # ---------------------------------------------------------------- close
    @staticmethod
    def remaining(pos: Position) -> tuple[Decimal, Decimal]:
        """(spot still held, perp short still open) in base units."""
        size = D(pos.size_base)
        pl = pos.payload or {}
        return size - D(pl.get("closed_spot") or "0"), size - D(pl.get("closed_perp") or "0")

    async def close(self, pos: Position, reason: str, trigger: str = "auto") -> TradeRecord | None:
        """Close (or finish closing) a position: sell the remaining spot and buy back the remaining perp short.

        Risk-reducing, so it is admitted during an emergency stop, but it still passes the admission
        boundary (recovery barrier, mode/generation) and is journaled before transport. Legs with an
        unknown outcome keep the position in ``closing`` until resolved; a partial close is tracked
        cumulatively so calling close again only trades what is left."""
        ctx = self.ctx
        if pos.status not in ("open", "closing"):
            return None
        if any(t.extra.get("position_close") == pos.id for t in self.executor.unresolved.values()):
            log.warning("close already in flight with an unknown outcome", position=pos.id)
            return None
        spot = ctx.connectors.get(pos.spot_venue)
        perp = ctx.connectors.get(pos.perp_venue)
        if spot is None or perp is None:
            log.error("cannot close position: venue missing", position=pos.id)
            return None
        spot_rem, perp_rem = self.remaining(pos)
        base, _quote = pos.pair.split("/")
        if not ctx.is_simulated_execution and perp_rem > 0:
            perp_rem = await self._cap_to_venue_short(pos, perp, perp_rem)
        spot_book = ctx.hub.get_book(spot.name, pos.pair)
        perp_book = ctx.hub.get_book(perp.name, pos.perp_symbol)
        try:
            sell_q = (
                await spot.get_quote(pos.pair, OrderSide.SELL, spot_rem, order_book=spot_book)
                if spot_rem > 0
                else None
            )
            buy_q = (
                await perp.get_quote(pos.perp_symbol, OrderSide.BUY, perp_rem, order_book=perp_book)
                if perp_rem > 0
                else None
            )
        except Exception as exc:
            log.error("cannot quote close legs", position=pos.id, error=str(exc))
            return None
        tol = ctx.settings.trading.leg_price_tolerance_pct / HUNDRED * 3  # closing prioritises certainty
        tr = TradeRecord(
            id=new_id("cls"),
            mode=ctx.mode,
            strategy=Strategy(pos.strategy),
            opportunity_id=pos.id,
            pair=pos.pair,
            route=f"{spot.name} → {perp.name} (close)",
            size_base=max(spot_rem, perp_rem),
            status=TradeStatus.EXECUTING,
            estimated_gross=ZERO,
            estimated_net=ZERO,
            estimated_worst_case=ZERO,
            estimated_costs={},
            extra={"position_close": pos.id, "no_hedge": True, "close_reason": reason, "trigger": trigger},
        )
        gen0, mode0 = ctx.admission.generation, ctx.mode
        spot_lv = ctx.ledger_venue_for(spot)
        async with ctx.admission.lock:
            block = ctx.admission_block(generation=gen0, mode=mode0, risk_increasing=False)
            if block:
                log.warning("close not admitted", position=pos.id, reason=block)
                return None
            if spot_rem > 0:
                try:
                    await ctx.inventory.reserve(tr.id, [(spot_lv, base, spot_rem)])
                except (
                    ValueError
                ) as exc:  # stale inventory view must not block a close-out; the venue decides
                    log.warning("close-out without inventory reservation", position=pos.id, error=str(exc))
                if ctx.is_simulated_execution and ctx.ledger is not None:
                    try:
                        await ctx.ledger.reserve(spot_lv, base, spot_rem)
                    except ValueError as exc:
                        await ctx.inventory.release(tr.id)
                        log.error(
                            "cannot close position: paper inventory missing", position=pos.id, error=str(exc)
                        )
                        return None
                    tr.reservations.append(
                        {
                            "kind": "paper",
                            "venue": spot_lv,
                            "asset": base,
                            "amount": str(spot_rem),
                            "role": "close",
                        }
                    )
            if sell_q is not None:
                tr.sell = OrderResult(
                    request=OrderRequest(
                        venue=spot.name,
                        symbol=pos.pair,
                        side=OrderSide.SELL,
                        amount=spot_rem,
                        limit_price=sell_q.avg_price * (1 - tol),
                        time_in_force="IOC",
                        client_order_id=new_id("cs"),
                        deadline_ms=ctx.settings.trading.execution_timeout_ms,
                        extra={"quoted_price": str(sell_q.avg_price), "close_position": pos.id},
                    ),
                    order_id="",
                    status=OrderStatus.SUBMITTING,
                )
            if buy_q is not None:
                tr.buy = OrderResult(
                    request=OrderRequest(
                        venue=perp.name,
                        symbol=pos.perp_symbol,
                        side=OrderSide.BUY,
                        amount=perp_rem,
                        limit_price=buy_q.avg_price * (1 + tol),
                        time_in_force="IOC",
                        client_order_id=new_id("cp"),
                        reduce_only=True,
                        deadline_ms=ctx.settings.trading.execution_timeout_ms,
                        extra={
                            "quoted_price": str(buy_q.avg_price),
                            "close_position": pos.id,
                            # the paper ledger releases only this position's collateral
                            "entry_notional": str(D(pos.entry_perp_price) * perp_rem),
                        },
                    ),
                    order_id="",
                    status=OrderStatus.SUBMITTING,
                )
            pos.status = "closing"
            tr.log("close_start", reason=reason, spot=spot_rem, perp=perp_rem)
            try:
                await self._save(pos)
                await self.executor._journal(tr)
            except Exception as exc:
                await self._release_close(tr)
                pos.status = (
                    "open" if spot_rem == D(pos.size_base) and perp_rem == D(pos.size_base) else "closing"
                )
                log.error("close not journaled - nothing submitted", position=pos.id, error=str(exc))
                return None
        jobs = []
        if tr.sell is not None:
            jobs.append(self.executor.submit_leg(spot, tr.sell.request, sell_q))
        if tr.buy is not None:
            jobs.append(self.executor.submit_leg(perp, tr.buy.request, buy_q, perp_close=True))
        results = await asyncio.gather(*jobs)
        i = 0
        if tr.sell is not None:
            tr.sell = results[i]
            i += 1
        if tr.buy is not None:
            tr.buy = results[i]
        await self.executor._persist(tr)
        if tr.unresolved_legs:
            await self.executor._hold_unknown(tr)  # position stays "closing" until resolved
            return tr
        await self._settle_close_trade(tr)
        self.executor._finish(tr)
        return tr

    async def _cap_to_venue_short(self, pos: Position, perp, wanted: Decimal) -> Decimal:
        """Never send a reduce-only buy larger than the short the venue actually holds."""
        fetch = getattr(perp, "venue_positions", None)
        if fetch is None:
            return wanted
        try:
            held = sum(
                (
                    p["size_base"]
                    for p in await fetch([pos.perp_symbol])
                    if p["symbol"] == pos.perp_symbol and p["side"] == "short"
                ),
                ZERO,
            )
        except Exception as exc:
            log.warning("venue position unavailable before close", position=pos.id, error=str(exc)[:120])
            return wanted
        if held < wanted * Decimal("0.999"):
            await self.ctx.breakers.trip(
                CircuitBreakerReason.POSITION_DISCREPANCY,
                f"{pos.perp_venue} {pos.perp_symbol}: venue short {held} < expected {wanted}",
                scope="strategy:carry",
            )
            return held
        return wanted

    async def _release_close(self, tr: TradeRecord) -> None:
        ctx = self.ctx
        await ctx.inventory.release(tr.id)
        if ctx.ledger is not None:
            for r in tr.reservations:
                if r["kind"] == "paper":
                    sold = tr.sell.filled if tr.sell else ZERO
                    await ctx.ledger.release(r["venue"], r["asset"], max(ZERO, D(r["amount"]) - sold))
        tr.reservations = []

    async def _settle_close_trade(self, tr: TradeRecord) -> None:
        """Every close leg is known: apply cumulative close progress; book the lifecycle once when flat."""
        from fedr.engine.accounting import leg_value

        ctx = self.ctx
        pos = self.open.get(tr.extra.get("position_close", ""))
        if pos is None and ctx.repo is not None:
            pos = await ctx.repo.get_position(tr.extra.get("position_close", ""))
            if pos is not None and pos.mode != ctx.mode.value:
                pos = None
        await self._release_close(tr)
        if pos is None:
            log.error("close trade without its position", trade=tr.id)
            return
        pl = dict(pos.payload or {})
        if pos.status == "closed" and pl.get("close_booked_usd") is not None:
            return  # already booked (replay after a restart)
        _base, quote = pos.pair.split("/")
        qpx = ctx.price_usd(quote)
        contrib = {"spot": "0", "spot_quote": "0", "perp": "0", "perp_quote": "0", "fees": "0", "missing": []}
        for leg, k in ((tr.sell, "spot"), (tr.buy, "perp")):
            if leg is None or leg.filled == 0:
                continue
            lv = leg_value(leg, qpx or Decimal("1"), ctx.price_usd, f"{k} leg")
            contrib["missing"] += lv.missing + ([f"no USD price for {quote}"] if qpx is None else [])
            contrib[k] = str(leg.filled)
            contrib[f"{k}_quote"] = str(lv.quote_value or ZERO)
            contrib["fees"] = str(D(contrib["fees"]) + (lv.fee_usd or ZERO) + (lv.gas_usd or ZERO))
        # per-trade contributions are (re)written by trade id: re-applying the same close trade - after a
        # restart or an operator resolution of one of its legs - replaces, never adds
        contribs = {**pl.get("close_contrib", {}), tr.id: contrib}
        pl["close_contrib"] = contribs
        pl["close_trades"] = list(contribs)
        for key, field in (
            ("closed_spot", "spot"),
            ("closed_perp", "perp"),
            ("exit_spot_quote", "spot_quote"),
            ("exit_perp_quote", "perp_quote"),
            ("exit_fees_usd", "fees"),
        ):
            pl[key] = str(sum((D(c[field]) for c in contribs.values()), ZERO))
        pl["close_values_missing"] = [m for c in contribs.values() for m in c["missing"]][-20:]
        pos.payload = pl
        spot_rem, perp_rem = self.remaining(pos)
        size = D(pos.size_base)
        flat = spot_rem <= size * Decimal("0.001") and perp_rem <= size * Decimal("0.001")
        reason = tr.extra.get("close_reason", "close")
        if flat:
            await self._book_close(pos, tr, reason)
        else:
            pos.status = "closing"
            tr.status = TradeStatus.RECOVERING
            tr.explanation = (
                f"Close attempt incomplete (spot {tr.sell.status.value if tr.sell else 'n/a'}, "
                f"perp {tr.buy.status.value if tr.buy else 'n/a'}); remaining spot {spot_rem}, perp short "
                f"{perp_rem}. Position stays 'closing' - close again to finish the remaining legs."
            )
            await ctx.breakers.trip(
                CircuitBreakerReason.POSITION_DISCREPANCY,
                f"carry close incomplete for {pos.pair}: {tr.explanation}"[:200],
                scope="strategy:carry",
            )
            if ctx.repo:
                await ctx.repo.add_risk_event(
                    ctx.mode, "critical", "position_close_incomplete", tr.explanation, {"position_id": pos.id}
                )
        tr.completed_at_ms = now_ms()
        await self._save(pos)
        if ctx.repo:
            await ctx.repo.save_trade(tr)
            await ctx.repo.audit(
                ctx.mode,
                "execution",
                tr.explanation,
                {"position_id": pos.id, "trade_id": tr.id, "trigger": tr.extra.get("trigger")},
            )
        if ctx.ledger is not None and ctx.repo is not None and ctx.is_simulated_execution:
            await ctx.repo.save_paper_balances(ctx.mode, ctx.ledger.dump())
        if self.executor.on_settled is not None:
            await self.executor.on_settled(tr)

    async def _book_close(self, pos: Position, tr: TradeRecord, reason: str) -> None:
        """The position is flat: realize the lifecycle once (entry booking already counted)."""
        ctx = self.ctx
        pl = pos.payload
        size = D(pos.size_base)
        spot_pnl = D(pl["exit_spot_quote"]) - D(pos.entry_spot_price) * size
        perp_pnl = D(pos.entry_perp_price) * size - D(pl["exit_perp_quote"])
        funding = D(pos.funding_collected_usd or "0")
        entry_fees = D(pos.fees_usd or "0")
        exit_fees = D(pl.get("exit_fees_usd") or "0")
        lifecycle = spot_pnl + perp_pnl + funding - entry_fees - exit_fees
        exit_spot = D(pl["exit_spot_quote"]) / size if size else ZERO
        exit_perp = D(pl["exit_perp_quote"]) / size if size else ZERO
        pos.status, pos.exit_reason = "closed", reason[:80]
        pos.exit_spot_price, pos.exit_perp_price, pos.closed_at = str(exit_spot), str(exit_perp), utcnow()
        self.open.pop(pos.id, None)
        if pl.get("funding_estimated"):
            pl["close_values_missing"] = [
                *pl.get("close_values_missing", []),
                "funding payments estimated from rates (venue funding history unavailable)",
            ]
        if pl.get("close_values_missing") or pl.get("entry_booking_unknown"):
            pos.realized_net_usd = None
            tr.status = TradeStatus.UNRECONCILED
            tr.explanation = (
                f"Carry position closed ({reason}) but its realized P&L cannot be proven ("
                + "; ".join(
                    (pl.get("close_values_missing") or ["entry booking of a pre-upgrade position unknown"])[
                        :2
                    ]
                )
                + "). Nothing was booked; reconcile from the venue records."
            )
            if ctx.repo:
                await ctx.repo.add_risk_event(
                    ctx.mode, "warning", "settlement_incomplete", tr.explanation, {"position_id": pos.id}
                )
            return
        entry_booked = D(pl.get("entry_booked_usd") or "0")
        booked = lifecycle - entry_booked
        pos.realized_net_usd = str(lifecycle)
        pos.fees_usd = str(entry_fees + exit_fees)
        tr.status = TradeStatus.FILLED
        tr.actual_gross, tr.actual_fees, tr.actual_gas, tr.actual_net = (
            spot_pnl + perp_pnl + funding,
            entry_fees + exit_fees,
            ZERO,
            lifecycle,
        )
        tr.explanation = (
            f"Carry position closed ({reason}): spot {fmt_money(spot_pnl)}, perp {fmt_money(perp_pnl)}, "
            f"funding {fmt_money(funding)}, fees {fmt_money(entry_fees + exit_fees)} -> lifecycle net "
            f"{fmt_money(lifecycle)} (booked now {fmt_money(booked)}; at entry {fmt_money(entry_booked)})."
        )
        pl["close_booked_usd"] = str(booked)
        pos.payload = dict(pl)
        await ctx.book_realized(booked, f"position {pos.id}")
        if ctx.repo:
            await ctx.repo.add_pnl(
                ctx.mode,
                gross=spot_pnl + perp_pnl,
                trading_fees=exit_fees,
                gas=ZERO,
                funding=-funding,
                net=booked,
                win=lifecycle > 0,
            )

    async def reconcile(self, venue_positions: list[dict]) -> list[str]:
        """Compare venue positions (base units, keyed by venue + symbol + side) with our open positions."""
        problems = []
        expected: dict[tuple[str, str], Decimal] = {}
        for pos in self.open.values():
            _spot_rem, perp_rem = self.remaining(pos)
            k = (pos.perp_venue, pos.perp_symbol)
            expected[k] = expected.get(k, ZERO) + perp_rem
        actual_short: dict[tuple[str, str], Decimal] = {}
        for p in venue_positions:
            k = (p["venue"], p["symbol"])
            if p["side"] == "short":
                actual_short[k] = actual_short.get(k, ZERO) + D(p["size_base"])
            elif D(p["size_base"]) != 0:
                problems.append(
                    f"{p['venue']} {p['symbol']}: untracked {p['side'] or 'unknown-side'} position of {p['size_base']}"
                )
        for (venue, sym), exp in expected.items():
            act = actual_short.get((venue, sym), ZERO)
            if abs(act - exp) > exp * Decimal("0.01"):
                problems.append(f"{venue} {sym}: expected short {exp}, venue reports {act}")
        for (venue, sym), act in actual_short.items():
            if (venue, sym) not in expected and act != 0:
                problems.append(f"{venue} {sym}: untracked short position of size {act}")
        if problems:
            await self.ctx.breakers.trip(CircuitBreakerReason.POSITION_DISCREPANCY, "; ".join(problems)[:200])
        return problems

    async def refresh_venue_state(self) -> None:
        """Live/testnet: store the venue's own liquidation price for each open position (used by mark())."""
        if self.ctx.is_simulated_execution:
            return
        by_venue: dict[str, list[Position]] = {}
        for p in self.open.values():
            by_venue.setdefault(p.perp_venue, []).append(p)
        for venue, positions in by_venue.items():
            fetch = getattr(self.ctx.connectors.get(venue), "venue_positions", None)
            if fetch is None:
                continue
            try:
                rows = await fetch([p.perp_symbol for p in positions])
            except Exception as exc:
                log.warning("venue positions unavailable", venue=venue, error=str(exc)[:120])
                continue
            for p in positions:
                liq = next(
                    (
                        r["liquidation_price"]
                        for r in rows
                        if r["symbol"] == p.perp_symbol and r["side"] == "short"
                    ),
                    None,
                )
                p.payload = {**p.payload, "venue_liquidation_price": str(liq) if liq else None}

    async def detailed(self) -> list[dict]:
        """Open positions with live marks (no side effects)."""
        out = []
        for p in self.open.values():
            m = await self.mark(p)
            out.append({**self._row(p), **{k: str(v) for k, v in m.items()}})
        return out

    @staticmethod
    def _row(p: Position) -> dict:
        return {
            "id": p.id,
            "strategy": p.strategy,
            "pair": p.pair,
            "spot_venue": p.spot_venue,
            "perp_venue": p.perp_venue,
            "perp_symbol": p.perp_symbol,
            "size_base": p.size_base,
            "status": p.status,
            "entry_spot_price": p.entry_spot_price,
            "entry_perp_price": p.entry_perp_price,
            "entry_basis_pct": p.entry_basis_pct,
            "funding_collected_usd": p.funding_collected_usd,
            "fees_usd": p.fees_usd,
            "exit_spot_price": p.exit_spot_price,
            "exit_perp_price": p.exit_perp_price,
            "realized_net_usd": p.realized_net_usd,
            "exit_reason": p.exit_reason,
            "funding_periods": len(p.payload.get("funding_periods", [])),
            "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        }

    def summary(self) -> list[dict]:
        return [
            {
                "id": p.id,
                "strategy": p.strategy,
                "pair": p.pair,
                "spot_venue": p.spot_venue,
                "perp_venue": p.perp_venue,
                "size_base": p.size_base,
                "status": p.status,
                "entry_basis_pct": p.entry_basis_pct,
                "funding_collected_usd": p.funding_collected_usd,
                "fees_usd": p.fees_usd,
                "opened_at": p.opened_at.isoformat() if p.opened_at else None,
            }
            for p in self.open.values()
        ]


__all__ = ["CarryPolicy", "PositionManager", "datetime", "timezone"]
