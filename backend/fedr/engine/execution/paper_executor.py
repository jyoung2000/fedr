"""Realistic paper execution simulator.

Borrowed concepts (HftBacktest): latency-delayed arrival, walking displayed
depth for IOC/limit takers, partial fills, fee models. Added realism knobs from
settings: adverse quote drift during latency, a pessimistic *stress multiplier*
that thins depth / raises impact, random partial fills and failures, and gas
charged to the chain's native balance for DEX legs.

Paper legs go through the SAME Profit Guard / Risk Engine as live legs - this
module only replaces the venue's order placement.
"""
from __future__ import annotations

import asyncio
import random
from decimal import Decimal
from typing import Callable

from fedr.config.schema import PaperSettings
from fedr.connectors.base import VenueConnector
from fedr.connectors.paper.ledger import PaperLedger
from fedr.core.enums import Chain, OrderSide, OrderStatus, VenueKind
from fedr.core.models import ExecutionQuote, Fill, OrderRequest, OrderResult, new_id, now_ms
from fedr.core.money import HUNDRED, ZERO, D
from fedr.engine.gas_guard import GasGuard, GasSnapshot


def ledger_venue(connector: VenueConnector) -> str:
    """Paper balances for DEX venues live in the chain wallet, not per DEX."""
    if connector.kind is VenueKind.DEX and connector.chain is not None:
        return connector.chain.value
    return connector.name


class PaperExecutor:
    def __init__(
        self,
        ledger: PaperLedger,
        settings: Callable[[], PaperSettings],
        gas_snapshot: Callable[[Chain], GasSnapshot | None],
        gas_guard: GasGuard | None = None,
        rng: random.Random | None = None,
        sleep: Callable[[float], "asyncio.Future"] | None = None,
    ):
        self.ledger = ledger
        self.settings = settings
        self.gas_snapshot = gas_snapshot
        self.gas_guard = gas_guard
        self.rng = rng or random.Random()
        self._sleep = sleep or asyncio.sleep

    async def execute_leg(self, connector: VenueConnector, req: OrderRequest, quote: ExecutionQuote) -> OrderResult:
        ps = self.settings()
        result = OrderResult(request=req, order_id=new_id("paper"), status=OrderStatus.NEW)
        base, quote_asset = req.symbol.split("/")
        lv = ledger_venue(connector)
        # 1. latency
        latency = max(0, ps.latency_ms + int(self.rng.uniform(-ps.latency_jitter_ms, ps.latency_jitter_ms)))
        if latency:
            await self._sleep(latency / 1000)
        # 2. random failure (network / exchange rejection)
        if self.rng.random() < float(ps.failure_probability):
            result.status = OrderStatus.REJECTED
            result.error = "simulated venue rejection"
            result.completed_at_ms = now_ms()
            connector.health_tracker.record_order(False)
            return result
        # 3. fresh execution at arrival time (adverse drift + stress)
        stress = D(ps.stress_multiplier)
        drift = D(ps.quote_drift_pct) / HUNDRED
        fill_base, avg_price, gas_usd, gas_native = await self._simulate_fill(connector, req, quote, stress, drift)
        if fill_base <= 0:
            result.status = OrderStatus.CANCELLED
            result.error = "IOC not fillable within limit price after simulated latency"
            result.completed_at_ms = now_ms()
            connector.health_tracker.record_order(False)
            return result
        # 4. partial fill
        if fill_base >= req.amount and self.rng.random() < float(ps.partial_fill_probability):
            fill_base = (req.amount * D(ps.fill_ratio_on_partial)).quantize(Decimal("0.00000001"))
        fill_base = min(fill_base, req.amount)
        quote_amount = fill_base * avg_price
        fee_pct = D(quote.fee_pct) if quote.fee_pct is not None else ZERO
        fee_quote = quote_amount * fee_pct / HUNDRED if connector.kind is not VenueKind.DEX else ZERO
        # 5. settle ledger
        try:
            await self.ledger.settle_fill(lv, base, quote_asset, req.side, fill_base, quote_amount, fee_quote, reserved=True)
            if connector.kind is VenueKind.DEX and connector.chain is not None and gas_native > 0:
                await self.ledger.charge_gas(lv, connector.chain.native_token, gas_native)
        except ValueError as exc:
            result.status = OrderStatus.REJECTED
            result.error = f"paper ledger: {exc}"
            result.completed_at_ms = now_ms()
            connector.health_tracker.record_order(False)
            return result
        result.fills.append(
            Fill(order_id=result.order_id, venue=connector.name, symbol=req.symbol, side=req.side, amount=fill_base, price=avg_price, fee_amount=fee_quote, fee_asset=quote_asset, gas_cost_usd=gas_usd, tx_hash=f"paper-{result.order_id}" if connector.kind is VenueKind.DEX else None)
        )
        result.filled = fill_base
        result.avg_price = avg_price
        result.fee_quote = fee_quote
        result.gas_cost_usd = gas_usd
        result.status = OrderStatus.FILLED if fill_base >= req.amount else OrderStatus.PARTIALLY_FILLED
        result.completed_at_ms = now_ms()
        result.raw["simulated"] = {"latency_ms": latency, "stress": str(stress), "drift_pct": str(ps.quote_drift_pct)}
        connector.health_tracker.record_order(True)
        return result

    async def _simulate_fill(self, connector: VenueConnector, req: OrderRequest, quote: ExecutionQuote, stress: Decimal, drift: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """Return (filled_base, avg_price, gas_usd, gas_native)."""
        adverse = (1 + drift) if req.side is OrderSide.BUY else (1 - drift)
        if connector.kind is VenueKind.DEX:
            fresh = await connector.get_quote(req.symbol, req.side, req.amount)
            if not fresh.fully_fillable or fresh.avg_price <= 0:
                return ZERO, ZERO, ZERO, ZERO
            # stress raises impact pessimistically
            extra_impact = fresh.price_impact_pct * (stress - 1) / HUNDRED
            px = fresh.avg_price * adverse * ((1 + extra_impact) if req.side is OrderSide.BUY else (1 - extra_impact))
            # respect minimum received / max spent like an on-chain slippage check
            if req.side is OrderSide.SELL and req.min_received is not None and px * req.amount < req.min_received:
                return ZERO, ZERO, ZERO, ZERO
            if req.side is OrderSide.BUY and req.limit_price is not None and px > req.limit_price:
                return ZERO, ZERO, ZERO, ZERO
            gas_usd, gas_native = self._gas_cost(connector, quote, stress)
            return req.amount, px, gas_usd, gas_native
        book = await connector.fetch_order_book(req.symbol)
        book = book.apply_stress(stress)
        side_levels = book.asks if req.side is OrderSide.BUY else book.bids
        limit = req.limit_price
        remaining = req.amount
        filled = ZERO
        cost = ZERO
        for lv in side_levels:
            px = lv.price * adverse
            if limit is not None and ((req.side is OrderSide.BUY and px > limit) or (req.side is OrderSide.SELL and px < limit)):
                break
            take = min(lv.amount, remaining)
            filled += take
            cost += take * px
            remaining -= take
            if remaining <= 0:
                break
        if filled <= 0:
            return ZERO, ZERO, ZERO, ZERO
        return filled, cost / filled, ZERO, ZERO

    def _gas_cost(self, connector: VenueConnector, quote: ExecutionQuote, stress: Decimal) -> tuple[Decimal, Decimal]:
        if connector.chain is None:
            return ZERO, ZERO
        snap = self.gas_snapshot(connector.chain)
        if snap is None or self.gas_guard is None:
            return ZERO, ZERO
        usd = self.gas_guard.estimate_cost_usd(snap, quote.gas_limit or 300_000) * stress
        native = usd / snap.native_usd if snap.native_usd > 0 else ZERO
        return usd, native
