"""Order book representation and depth walking (VWAP execution estimates).

The Profit Guard never trades from best bid/ask. Every CEX leg is priced by
walking the live book for the exact size, in the spirit of Hummingbot's
``OrderBook.get_vwap_for_volume`` / ``get_price_for_volume``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Sequence

from fedr.core.enums import OrderSide
from fedr.core.models import now_ms
from fedr.core.money import ZERO, D, pct


@dataclass(slots=True)
class Level:
    price: Decimal
    amount: Decimal


@dataclass(slots=True)
class WalkResult:
    side: OrderSide
    requested_base: Decimal
    filled_base: Decimal
    quote_amount: Decimal  # sum(price * amount) over consumed depth
    avg_price: Decimal
    reference_price: Decimal  # top of book on that side
    slippage_pct: Decimal  # adverse deviation of avg from reference
    levels_consumed: int
    fully_filled: bool
    last_price: Decimal  # worst price touched (use as IOC limit)
    depth_available_base: Decimal


@dataclass(slots=True)
class OrderBook:
    venue: str
    symbol: str
    bids: list[Level] = field(default_factory=list)  # sorted desc by price
    asks: list[Level] = field(default_factory=list)  # sorted asc by price
    ts_ms: int = field(default_factory=now_ms)  # exchange timestamp of the snapshot
    received_ms: int = field(default_factory=now_ms)  # local receipt time
    sequence: int | None = None
    source: str = "rest"  # rest | ws | replay | synthetic

    @classmethod
    def from_raw(
        cls,
        venue: str,
        symbol: str,
        bids: Iterable[Sequence],
        asks: Iterable[Sequence],
        ts_ms: int | None = None,
        source: str = "rest",
        sequence: int | None = None,
    ) -> OrderBook:
        b = sorted((Level(D(p), D(a)) for p, a, *_ in bids if D(a) > 0), key=lambda lv: lv.price, reverse=True)
        a = sorted((Level(D(p), D(q)) for p, q, *_ in asks if D(q) > 0), key=lambda lv: lv.price)
        return cls(venue=venue, symbol=symbol, bids=b, asks=a, ts_ms=ts_ms or now_ms(), source=source, sequence=sequence)

    # --- basics -----------------------------------------------------------------
    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_pct(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None or self.mid == 0:
            return None
        return pct(self.best_ask - self.best_bid, self.mid)

    @property
    def age_ms(self) -> int:
        return now_ms() - self.ts_ms

    def is_crossed(self) -> bool:
        return self.best_bid is not None and self.best_ask is not None and self.best_bid >= self.best_ask

    def is_valid(self) -> bool:
        return bool(self.bids) and bool(self.asks) and not self.is_crossed()

    def depth_base(self, side: OrderSide, levels: int | None = None) -> Decimal:
        book = self.asks if side is OrderSide.BUY else self.bids
        sel = book if levels is None else book[:levels]
        return sum((lv.amount for lv in sel), ZERO)

    # --- walking -----------------------------------------------------------------
    def walk(self, side: OrderSide, base_amount: Decimal) -> WalkResult:
        """Simulate a taker order of ``base_amount`` against the book.

        BUY consumes asks (ascending), SELL consumes bids (descending).
        """
        base_amount = D(base_amount)
        book = self.asks if side is OrderSide.BUY else self.bids
        if base_amount <= 0:
            raise ValueError("base_amount must be positive")
        if not book:
            return WalkResult(side, base_amount, ZERO, ZERO, ZERO, ZERO, ZERO, 0, False, ZERO, ZERO)
        reference = book[0].price
        remaining = base_amount
        quote = ZERO
        filled = ZERO
        levels = 0
        last = reference
        for lv in book:
            if remaining <= 0:
                break
            take = min(lv.amount, remaining)
            quote += take * lv.price
            filled += take
            remaining -= take
            levels += 1
            last = lv.price
        avg = quote / filled if filled > 0 else ZERO
        if side is OrderSide.BUY:
            slip = pct(avg - reference, reference) if filled > 0 else ZERO
        else:
            slip = pct(reference - avg, reference) if filled > 0 else ZERO
        return WalkResult(
            side=side,
            requested_base=base_amount,
            filled_base=filled,
            quote_amount=quote,
            avg_price=avg,
            reference_price=reference,
            slippage_pct=max(ZERO, slip),
            levels_consumed=levels,
            fully_filled=remaining <= 0,
            last_price=last,
            depth_available_base=sum((lv.amount for lv in book), ZERO),
        )

    def walk_quote(self, side: OrderSide, quote_amount: Decimal) -> WalkResult:
        """Simulate spending/receiving ``quote_amount`` of quote currency."""
        quote_amount = D(quote_amount)
        book = self.asks if side is OrderSide.BUY else self.bids
        if quote_amount <= 0:
            raise ValueError("quote_amount must be positive")
        if not book:
            return WalkResult(side, ZERO, ZERO, ZERO, ZERO, ZERO, ZERO, 0, False, ZERO, ZERO)
        reference = book[0].price
        remaining_quote = quote_amount
        base = ZERO
        spent = ZERO
        levels = 0
        last = reference
        for lv in book:
            if remaining_quote <= 0:
                break
            level_quote = lv.amount * lv.price
            take_quote = min(level_quote, remaining_quote)
            take_base = take_quote / lv.price
            base += take_base
            spent += take_quote
            remaining_quote -= take_quote
            levels += 1
            last = lv.price
        avg = spent / base if base > 0 else ZERO
        slip = pct(avg - reference, reference) if side is OrderSide.BUY else pct(reference - avg, reference)
        return WalkResult(
            side=side,
            requested_base=base,
            filled_base=base,
            quote_amount=spent,
            avg_price=avg,
            reference_price=reference,
            slippage_pct=max(ZERO, slip) if base > 0 else ZERO,
            levels_consumed=levels,
            fully_filled=remaining_quote <= 0,
            last_price=last,
            depth_available_base=sum((lv.amount for lv in book), ZERO),
        )

    def max_size_within_slippage(self, side: OrderSide, max_slippage_pct: Decimal) -> Decimal:
        """Largest base size whose VWAP stays within ``max_slippage_pct`` of top of book."""
        book = self.asks if side is OrderSide.BUY else self.bids
        if not book:
            return ZERO
        reference = book[0].price
        cum_base = ZERO
        cum_quote = ZERO
        result = ZERO
        for lv in book:
            cum_base += lv.amount
            cum_quote += lv.amount * lv.price
            avg = cum_quote / cum_base
            slip = pct(avg - reference, reference) if side is OrderSide.BUY else pct(reference - avg, reference)
            if slip > max_slippage_pct:
                # binary-ish search inside this level is overkill; stay conservative: stop at previous level
                break
            result = cum_base
        return result

    def apply_stress(self, multiplier: Decimal) -> OrderBook:
        """Return a thinner book (depth divided by multiplier) for pessimistic simulations."""
        m = D(multiplier)
        if m <= 1:
            return self
        return OrderBook(
            venue=self.venue,
            symbol=self.symbol,
            bids=[Level(lv.price, lv.amount / m) for lv in self.bids],
            asks=[Level(lv.price, lv.amount / m) for lv in self.asks],
            ts_ms=self.ts_ms,
            received_ms=self.received_ms,
            sequence=self.sequence,
            source=self.source,
        )

    def consume(self, side: OrderSide, base_amount: Decimal) -> None:
        """Mutate the book as if ``base_amount`` was taken (paper-mode realism)."""
        book = self.asks if side is OrderSide.BUY else self.bids
        remaining = D(base_amount)
        while book and remaining > 0:
            lv = book[0]
            if lv.amount <= remaining:
                remaining -= lv.amount
                book.pop(0)
            else:
                lv.amount -= remaining
                remaining = ZERO
