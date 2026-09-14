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

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from fedr.core.enums import CircuitBreakerReason, OrderSide, OrderStatus, Strategy, TradeStatus
from fedr.core.logging import get_logger
from fedr.core.models import Opportunity, OrderRequest, TradeRecord, new_id, now_ms
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
        """Open both legs through the paired executor. Only a fully filled pair becomes a position."""
        carry_in = opp.extra.get("carry") or {}
        fr = carry_in.get("funding_rate")
        if fr is not None and await self._funding_anomaly(D(fr), f"{opp.sell.venue} {opp.sell.symbol}"):
            tr = TradeRecord(
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
                explanation="Aborted: funding rate anomaly (FUNDING_ANOMALY breaker tripped).",
            )
            return tr, None
        tr = await self.executor.execute(opp, trigger=trigger, allow_carry=True)
        if tr.status is not TradeStatus.FILLED or tr.buy is None or tr.sell is None:
            return tr, None
        carry = opp.extra.get("carry") or {}
        pos = Position(
            id=new_id("pos"),
            mode=self.ctx.mode.value,
            strategy=opp.strategy.value,
            pair=opp.pair,
            spot_venue=opp.buy.venue,
            perp_venue=opp.sell.venue,
            perp_symbol=opp.sell.symbol,
            size_base=str(tr.buy.filled),
            status="open",
            entry_spot_price=str(tr.buy.avg_price),
            entry_perp_price=str(tr.sell.avg_price),
            entry_basis_pct=str((tr.sell.avg_price - tr.buy.avg_price) / tr.buy.avg_price * HUNDRED),
            funding_collected_usd="0",  # ORM defaults only apply on flush: set explicitly for in-memory use
            opened_at=utcnow(),
            fees_usd=str(tr.buy.fee_quote + tr.sell.fee_quote),
            payload={
                "trade_id": tr.id,
                "funding_rate_at_entry": carry.get("funding_rate"),
                "funding_periods": [],
                "negative_funding_streak": 0,
            },
        )
        self.open[pos.id] = pos
        await self._save(pos)
        if self.ctx.repo:
            await self.ctx.repo.audit(
                self.ctx.mode,
                "execution",
                f"Carry position opened: {pos.pair} long spot on {pos.spot_venue} / short perp on {pos.perp_venue}, basis {D(pos.entry_basis_pct):.3f}%",
                {"position_id": pos.id, "trade_id": tr.id},
            )
        return tr, pos

    # ---------------------------------------------------------------- monitor
    async def mark(self, pos: Position) -> dict:
        """Current basis, unrealized P&L, liquidation distance for an open position."""
        spot_book = self.ctx.hub.get_book(pos.spot_venue, pos.pair)
        perp_book = self.ctx.hub.get_book(pos.perp_venue, pos.perp_symbol)
        size = D(pos.size_base)
        spot_px = spot_book.best_bid if spot_book and spot_book.best_bid else D(pos.entry_spot_price)
        perp_px = perp_book.best_ask if perp_book and perp_book.best_ask else D(pos.entry_perp_price)
        basis_pct = (perp_px - spot_px) / spot_px * HUNDRED if spot_px > 0 else ZERO
        # long spot marked at bid, short perp bought back at ask
        spot_pnl = (spot_px - D(pos.entry_spot_price)) * size
        perp_pnl = (D(pos.entry_perp_price) - perp_px) * size
        funding = D(pos.funding_collected_usd or "0")
        unrealized = spot_pnl + perp_pnl + funding - D(pos.fees_usd or "0")
        # 1x collateral short: liquidation when the perp price rises ~100% above entry (minus maintenance); report distance
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

    async def accrue_funding(self, pos: Position, funding_rate: Decimal | None) -> None:
        """Called once per funding interval with the settled rate (short perp receives positive funding)."""
        if funding_rate is None:
            return
        await self._funding_anomaly(funding_rate, f"{pos.perp_venue} {pos.perp_symbol}")
        size = D(pos.size_base)
        mark = await self.mark(pos)
        payment = funding_rate * mark["perp_price"] * size  # positive rate -> we receive
        pos.funding_collected_usd = str(D(pos.funding_collected_usd or "0") + payment)
        periods = list(pos.payload.get("funding_periods", []))
        periods.append({"ts": now_ms(), "rate": str(funding_rate), "payment_usd": str(payment)})
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

    async def accrue_due_funding(self, rate_provider) -> int:
        """Accrue one funding period for every open position whose next settlement is due.

        ``rate_provider(perp_venue, perp_symbol)`` is awaited and must return the settled rate as a
        fraction (or None when unknown). Returns the number of positions updated.
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
            try:
                rate = await rate_provider(pos.perp_venue, pos.perp_symbol)
            except Exception as exc:  # a venue hiccup must not stop monitoring
                log.warning("funding rate unavailable", position=pos.id, error=str(exc)[:120])
                continue
            pos.payload = {**pos.payload, "last_funding_ms": now_ms()}
            await self.accrue_funding(pos, D(rate) if rate is not None else None)
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
    async def close(self, pos: Position, reason: str, trigger: str = "auto") -> TradeRecord | None:
        pos.status = "closing"
        await self._save(pos)
        spot = self.ctx.connectors.get(pos.spot_venue)
        perp = self.ctx.connectors.get(pos.perp_venue)
        if spot is None or perp is None:
            pos.status = "open"
            await self._save(pos)
            log.error("cannot close position: venue missing", position=pos.id)
            return None
        size = D(pos.size_base)
        base, _quote = pos.pair.split("/")
        spot_lv = self.ctx.ledger_venue_for(spot)
        # Reserve the spot inventory being sold (same accounting path as the paired executor). The perp
        # close needs no reservation: it releases collateral. In simulation the paper ledger must hold it.
        try:
            await self.ctx.inventory.reserve(f"close:{pos.id}", [(spot_lv, base, size)])
        except ValueError as exc:  # stale inventory view must not block a close-out; the venue decides
            log.warning("close-out without inventory reservation", position=pos.id, error=str(exc))
        if self.ctx.is_simulated_execution and self.ctx.ledger is not None:
            try:
                await self.ctx.ledger.reserve(spot_lv, base, size)
            except ValueError as exc:
                await self.ctx.inventory.release(f"close:{pos.id}")
                pos.status = "open"
                await self._save(pos)
                log.error("cannot close position: paper inventory missing", position=pos.id, error=str(exc))
                return None
        spot_book = self.ctx.hub.get_book(spot.name, pos.pair)
        perp_book = self.ctx.hub.get_book(perp.name, pos.perp_symbol)
        try:
            sell_q = await spot.get_quote(pos.pair, OrderSide.SELL, size, order_book=spot_book)
            buy_q = await perp.get_quote(pos.perp_symbol, OrderSide.BUY, size, order_book=perp_book)
        except Exception as exc:
            await self._release_close_reservation(pos, spot_lv, base, size, ZERO)
            pos.status = "open"
            await self._save(pos)
            log.error("cannot quote close legs", position=pos.id, error=str(exc))
            return None
        tol = self.ctx.settings.trading.leg_price_tolerance_pct / HUNDRED * 3  # closing prioritises certainty
        sell_req = OrderRequest(
            venue=spot.name,
            symbol=pos.pair,
            side=OrderSide.SELL,
            amount=size,
            limit_price=sell_q.avg_price * (1 - tol),
            time_in_force="IOC",
            extra={"quoted_price": str(sell_q.avg_price), "close_position": pos.id},
        )
        buy_req = OrderRequest(
            venue=perp.name,
            symbol=pos.perp_symbol,
            side=OrderSide.BUY,
            amount=size,
            limit_price=buy_q.avg_price * (1 + tol),
            time_in_force="IOC",
            reduce_only=True,
            extra={"quoted_price": str(buy_q.avg_price), "close_position": pos.id},
        )
        tr = TradeRecord(
            id=new_id("cls"),
            mode=self.ctx.mode,
            strategy=Strategy(pos.strategy),
            opportunity_id=pos.id,
            pair=pos.pair,
            route=f"{spot.name} → {perp.name} (close)",
            size_base=size,
            status=TradeStatus.EXECUTING,
            estimated_gross=ZERO,
            estimated_net=ZERO,
            estimated_worst_case=ZERO,
            estimated_costs={},
        )
        tr.log("close_start", reason=reason)
        tr.sell = await self.executor.submit_leg(spot, sell_req, sell_q, perp_close=False)
        tr.buy = await self.executor.submit_leg(perp, buy_req, buy_q, perp_close=True)
        await self._release_close_reservation(pos, spot_lv, base, size, tr.sell.filled)
        spot_ok = tr.sell.status in (OrderStatus.FILLED,) and tr.sell.filled >= size * Decimal("0.999")
        perp_ok = tr.buy.status in (OrderStatus.FILLED,) and tr.buy.filled >= size * Decimal("0.999")
        if spot_ok and perp_ok:
            exit_spot, exit_perp = tr.sell.avg_price, tr.buy.avg_price
            spot_pnl = (exit_spot - D(pos.entry_spot_price)) * size
            perp_pnl = (D(pos.entry_perp_price) - exit_perp) * size
            fees = D(pos.fees_usd or "0") + tr.sell.fee_quote + tr.buy.fee_quote
            net = spot_pnl + perp_pnl + D(pos.funding_collected_usd or "0") - fees
            pos.status, pos.exit_reason, pos.exit_spot_price, pos.exit_perp_price = (
                "closed",
                reason,
                str(exit_spot),
                str(exit_perp),
            )
            pos.realized_net_usd, pos.fees_usd, pos.closed_at = str(net), str(fees), utcnow()
            tr.status = TradeStatus.FILLED
            tr.actual_gross, tr.actual_fees, tr.actual_gas, tr.actual_net = (
                spot_pnl + perp_pnl + D(pos.funding_collected_usd or "0"),
                fees,
                ZERO,
                net,
            )
            funding_usd = D(pos.funding_collected_usd or "0")
            tr.explanation = f"Carry position closed ({reason}): spot {fmt_money(spot_pnl)}, perp {fmt_money(perp_pnl)}, funding {fmt_money(funding_usd)}, fees {fmt_money(fees)} → net {fmt_money(net)}."
            if self.ctx.repo:
                await self.ctx.repo.add_pnl(
                    self.ctx.mode,
                    gross=tr.actual_gross,
                    trading_fees=fees,
                    gas=ZERO,
                    funding=-D(pos.funding_collected_usd or "0"),
                    net=net,
                    win=net > 0,
                )
            self.open.pop(pos.id, None)
        else:
            pos.status = "open" if not (spot_ok or perp_ok) else "closing"
            tr.status = TradeStatus.RECOVERING if (spot_ok != perp_ok) else TradeStatus.FAILED
            tr.explanation = f"Close attempt incomplete (spot {tr.sell.status.value}, perp {tr.buy.status.value}); position remains {pos.status}."
            await self.ctx.breakers.trip(
                CircuitBreakerReason.POSITION_DISCREPANCY,
                f"carry close incomplete for {pos.pair}: {tr.explanation}"[:200],
                scope="strategy:carry",
            )
            if self.ctx.repo:
                await self.ctx.repo.add_risk_event(
                    self.ctx.mode,
                    "critical",
                    "position_close_incomplete",
                    tr.explanation,
                    {"position_id": pos.id},
                )
        tr.completed_at_ms = now_ms()
        await self._save(pos)
        if self.ctx.repo:
            await self.ctx.repo.save_trade(tr)
            await self.ctx.repo.audit(
                self.ctx.mode,
                "execution",
                tr.explanation,
                {"position_id": pos.id, "trade_id": tr.id, "trigger": trigger},
            )
        return tr

    async def _release_close_reservation(
        self, pos: Position, spot_lv: str, base: str, size: Decimal, sold: Decimal
    ) -> None:
        await self.ctx.inventory.release(f"close:{pos.id}")
        if self.ctx.is_simulated_execution and self.ctx.ledger is not None:
            await self.ctx.ledger.release(spot_lv, base, max(ZERO, size - sold))

    async def reconcile(self, actual_positions: dict[str, Decimal]) -> list[str]:
        """Compare the perp venue's reported short size per symbol with our open positions."""
        problems = []
        expected: dict[str, Decimal] = {}
        for pos in self.open.values():
            expected[pos.perp_symbol] = expected.get(pos.perp_symbol, ZERO) + D(pos.size_base)
        for sym, exp in expected.items():
            act = actual_positions.get(sym, ZERO)
            if abs(act - exp) > exp * Decimal("0.01"):
                problems.append(f"{sym}: expected short {exp}, venue reports {act}")
        for sym, act in actual_positions.items():
            if sym not in expected and act != 0:
                problems.append(f"{sym}: untracked position of size {act}")
        if problems:
            await self.ctx.breakers.trip(CircuitBreakerReason.POSITION_DISCREPANCY, "; ".join(problems)[:200])
        return problems

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
