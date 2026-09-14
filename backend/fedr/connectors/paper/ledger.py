"""Persistent simulated balances for PAPER / SIMULATION modes."""
from __future__ import annotations

import asyncio
from decimal import Decimal

from fedr.core.models import Balance
from fedr.core.money import ZERO, D


class PaperLedger:
    def __init__(self):
        self._bal: dict[str, dict[str, Balance]] = {}
        self._lock = asyncio.Lock()
        self.dirty = False

    # ---- setup -------------------------------------------------------------------
    def seed(self, balances: dict[str, dict[str, Decimal]]) -> None:
        self._bal = {v: {a: Balance(asset=a, free=D(x)) for a, x in assets.items()} for v, assets in balances.items()}
        self.dirty = True

    def load(self, balances: dict[str, dict[str, tuple[Decimal, Decimal]]]) -> None:
        self._bal = {v: {a: Balance(asset=a, free=f, used=u) for a, (f, u) in assets.items()} for v, assets in balances.items()}

    def dump(self) -> dict[str, dict[str, tuple[Decimal, Decimal]]]:
        return {v: {a: (b.free, b.used) for a, b in assets.items()} for v, assets in self._bal.items()}

    # ---- queries -----------------------------------------------------------------
    def venues(self) -> list[str]:
        return list(self._bal)

    def balances(self, venue: str) -> dict[str, Balance]:
        return {a: Balance(asset=a, free=b.free, used=b.used) for a, b in self._bal.get(venue, {}).items()}

    def get(self, venue: str, asset: str) -> Balance:
        b = self._bal.get(venue, {}).get(asset)
        return Balance(asset=asset, free=b.free, used=b.used) if b else Balance(asset=asset)

    def _acct(self, venue: str, asset: str) -> Balance:
        return self._bal.setdefault(venue, {}).setdefault(asset, Balance(asset=asset))

    # ---- mutations -------------------------------------------------------------------
    async def adjust(self, venue: str, asset: str, delta: Decimal, allow_negative: bool = False) -> Decimal:
        async with self._lock:
            b = self._acct(venue, asset)
            new = b.free + delta
            if new < 0 and not allow_negative:
                raise ValueError(f"insufficient {asset} on {venue}: have {b.free}, need {-delta}")
            b.free = new
            self.dirty = True
            return b.free

    async def reserve(self, venue: str, asset: str, amount: Decimal) -> None:
        async with self._lock:
            b = self._acct(venue, asset)
            if b.free < amount:
                raise ValueError(f"insufficient {asset} on {venue}: have {b.free}, need {amount}")
            b.free -= amount
            b.used += amount
            self.dirty = True

    async def release(self, venue: str, asset: str, amount: Decimal) -> None:
        async with self._lock:
            b = self._acct(venue, asset)
            take = min(amount, b.used)
            b.used -= take
            b.free += take
            self.dirty = True

    async def settle_fill(self, venue: str, base: str, quote: str, side, base_amount: Decimal, quote_amount: Decimal, fee_quote: Decimal, reserved: bool = True) -> None:
        """Apply a fill: BUY consumes quote (+fee) and adds base; SELL the opposite."""
        async with self._lock:
            b_base = self._acct(venue, base)
            b_quote = self._acct(venue, quote)
            if side.value == "buy":
                cost = quote_amount + fee_quote
                if reserved:
                    b_quote.used = max(ZERO, b_quote.used - cost)
                else:
                    b_quote.free -= cost
                b_base.free += base_amount
            else:
                if reserved:
                    b_base.used = max(ZERO, b_base.used - base_amount)
                else:
                    b_base.free -= base_amount
                b_quote.free += quote_amount - fee_quote
            self.dirty = True

    async def charge_gas(self, venue: str, native_asset: str, native_amount: Decimal) -> None:
        async with self._lock:
            b = self._acct(venue, native_asset)
            b.free -= native_amount
            self.dirty = True

    def total_usd(self, price_of) -> Decimal:
        total = ZERO
        for assets in self._bal.values():
            for a, b in assets.items():
                px = price_of(a)
                if px is not None:
                    total += (b.free + b.used) * px
        return total
