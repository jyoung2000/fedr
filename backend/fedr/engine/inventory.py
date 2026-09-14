"""Inventory manager: per venue / chain / wallet / asset view with reservations."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from fedr.core.models import Balance
from fedr.core.money import ZERO, D


@dataclass(slots=True)
class InventoryLine:
    venue: str
    asset: str
    available: Decimal = ZERO
    reserved: Decimal = ZERO
    pending: Decimal = ZERO
    target: Decimal = ZERO
    minimum: Decimal = ZERO
    maximum: Decimal | None = None
    usd_value: Decimal | None = None
    kind: str = "cex"  # cex | chain

    @property
    def total(self) -> Decimal:
        return self.available + self.reserved + self.pending


class InventoryManager:
    def __init__(self, price_usd: Callable[[str], Decimal | None]):
        self.price_usd = price_usd
        self._lines: dict[tuple[str, str], InventoryLine] = {}
        self._reservations: dict[str, list[tuple[str, str, Decimal]]] = {}
        self._lock = asyncio.Lock()
        self._targets: dict[tuple[str, str], tuple[Decimal, Decimal, Decimal | None]] = {}

    def set_targets(self, venue: str, asset: str, target: Decimal, minimum: Decimal, maximum: Decimal | None) -> None:
        self._targets[(venue, asset)] = (target, minimum, maximum)

    def update_balances(self, venue: str, balances: dict[str, Balance], kind: str = "cex") -> None:
        for asset, b in balances.items():
            line = self._lines.get((venue, asset)) or InventoryLine(venue=venue, asset=asset, kind=kind)
            line.available = b.free
            line.reserved = b.used
            t = self._targets.get((venue, asset))
            if t:
                line.target, line.minimum, line.maximum = t
            px = self.price_usd(asset)
            line.usd_value = (b.free + b.used) * px if px is not None else None
            line.kind = kind
            self._lines[(venue, asset)] = line
        # remove assets no longer reported
        for key in [k for k in self._lines if k[0] == venue and k[1] not in balances]:
            self._lines.pop(key, None)

    def available(self, venue: str, asset: str) -> Decimal:
        line = self._lines.get((venue, asset))
        if line is None:
            return ZERO
        return max(ZERO, line.available - self._reserved_for(venue, asset))

    def _reserved_for(self, venue: str, asset: str) -> Decimal:
        return sum((amt for res in self._reservations.values() for v, a, amt in res if v == venue and a == asset), ZERO)

    async def reserve(self, trade_id: str, needs: list[tuple[str, str, Decimal]]) -> None:
        async with self._lock:
            for venue, asset, amount in needs:
                if self.available(venue, asset) < amount:
                    raise ValueError(f"insufficient {asset} on {venue}: need {amount}, available {self.available(venue, asset)}")
            self._reservations[trade_id] = [(v, a, D(x)) for v, a, x in needs]

    async def release(self, trade_id: str) -> None:
        async with self._lock:
            self._reservations.pop(trade_id, None)

    def lines(self) -> list[InventoryLine]:
        return sorted(self._lines.values(), key=lambda l: (l.venue, l.asset))

    def balances_map(self) -> dict[tuple[str, str], Decimal]:
        return {k: self.available(*k) for k in self._lines}

    def exposure_by(self, group: Callable[[InventoryLine], str | None], volatile_only: bool = True) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for line in self._lines.values():
            if line.usd_value is None:
                continue
            if volatile_only and line.asset in ("USDC", "USDT", "USD", "DAI"):
                continue
            g = group(line)
            if g is None:
                continue
            out[g] = out.get(g, ZERO) + line.usd_value
        return out

    def total_usd(self) -> Decimal:
        return sum((l.usd_value for l in self._lines.values() if l.usd_value is not None), ZERO)

    def below_minimum(self) -> list[InventoryLine]:
        return [l for l in self._lines.values() if l.minimum > 0 and l.total < l.minimum]
