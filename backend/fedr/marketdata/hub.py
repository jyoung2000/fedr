"""Market data hub: order-book cache, feed tasks (WS with REST fallback) and a USD price index.

Event driven: every book update triggers ``on_update(venue, symbol)`` so the
scanner can re-evaluate only the routes that changed. Nothing here streams raw
updates to the browser - the API aggregates.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from decimal import Decimal

from fedr.connectors.base import NotSupportedError, VenueConnector, VenueUnavailable
from fedr.core.enums import VenueKind
from fedr.core.logging import get_logger
from fedr.core.models import now_ms
from fedr.core.money import D
from fedr.marketdata.orderbook import OrderBook

log = get_logger("marketdata")

STABLES = {
    "USDC": Decimal("1"),
    "USDT": Decimal("1"),
    "USD": Decimal("1"),
    "DAI": Decimal("1"),
    "FDUSD": Decimal("1"),
    "UST": Decimal("1"),
}
WRAPPED = {
    "WETH": "ETH",
    "WBTC": "BTC",
    "WSOL": "SOL",
    "WBNB": "BNB",
    "WMATIC": "POL",
    "MATIC": "POL",
    "WAVAX": "AVAX",
    "USDC.E": "USDC",
}


class PriceIndex:
    """USD prices per asset derived from observed mids (or an external provider)."""

    def __init__(self):
        self._prices: dict[str, tuple[Decimal, int]] = {}
        self.external: Callable[[str], Decimal | None] | None = None

    def update_from_book(self, book: OrderBook) -> None:
        mid = book.mid
        if mid is None:
            return
        base, _, quote = book.symbol.partition("/")
        quote = quote.split(":")[0]
        qpx = STABLES.get(quote) or self.get(quote)
        if qpx is None:
            return
        self.set(base, mid * qpx)

    def set(self, asset: str, price: Decimal) -> None:
        self._prices[WRAPPED.get(asset, asset)] = (D(price), now_ms())

    def get(self, asset: str, max_age_ms: int = 120_000) -> Decimal | None:
        asset = WRAPPED.get(asset, asset)
        if asset in STABLES:
            return STABLES[asset]
        entry = self._prices.get(asset)
        if entry and now_ms() - entry[1] <= max_age_ms:
            return entry[0]
        if self.external:
            px = self.external(asset)
            if px is not None:
                return px
        return entry[0] if entry else None

    def snapshot(self) -> dict[str, str]:
        return {a: str(p) for a, (p, _) in self._prices.items()}


class MarketDataHub:
    def __init__(self, on_update: Callable[[str, str], Awaitable[None]] | None = None):
        self.books: dict[tuple[str, str], OrderBook] = {}
        self.prices = PriceIndex()
        self._tasks: list[asyncio.Task] = []
        self._on_update = on_update
        self.ws_enabled = True
        self.rest_interval_ms = 2000
        self.depth = 50
        self.stats: dict[str, dict] = {}
        self._running = False

    # ---- access ---------------------------------------------------------------
    def get_book(self, venue: str, symbol: str) -> OrderBook | None:
        return self.books.get((venue, symbol))

    def books_for(self, symbol: str) -> dict[str, OrderBook]:
        return {v: b for (v, s), b in self.books.items() if s == symbol}

    async def ingest(self, book: OrderBook) -> None:
        if not book.is_valid():
            self._stat(book.venue)["invalid"] += 1
            return
        self.books[(book.venue, book.symbol)] = book
        self.prices.update_from_book(book)
        st = self._stat(book.venue)
        st["updates"] += 1
        st["last_ms"] = now_ms()
        st["source"] = book.source
        if self._on_update:
            try:
                await self._on_update(book.venue, book.symbol)
            except Exception as exc:  # pragma: no cover - callback errors must not kill feeds
                log.error("on_update callback failed", error=str(exc))

    def _stat(self, venue: str) -> dict:
        return self.stats.setdefault(
            venue, {"updates": 0, "invalid": 0, "errors": 0, "last_ms": 0, "source": None, "ws": None}
        )

    # ---- lifecycle --------------------------------------------------------------
    async def start(
        self, connectors: list[VenueConnector], symbols_for: Callable[[VenueConnector], list[str]]
    ) -> None:
        self._running = True
        for c in connectors:
            if c.kind is VenueKind.DEX:
                continue  # DEX quotes are pulled on demand for the exact size
            symbols = [s for s in symbols_for(c) if c.supports_symbol(s)]
            if not symbols:
                continue
            if self.ws_enabled and "ws" in c.capabilities and hasattr(c, "watch_order_book"):
                for s in symbols:
                    self._tasks.append(asyncio.create_task(self._ws_loop(c, s), name=f"ws:{c.name}:{s}"))
            else:
                self._tasks.append(asyncio.create_task(self._rest_loop(c, symbols), name=f"rest:{c.name}"))

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()

    async def _ws_loop(self, c: VenueConnector, symbol: str) -> None:
        backoff = 1.0
        rest_until = 0.0
        while self._running:
            try:
                self._stat(c.name)["ws"] = True
                async for book in c.watch_order_book(symbol, self.depth):  # type: ignore[attr-defined]
                    await self.ingest(book)
                    backoff = 1.0
            except asyncio.CancelledError:
                raise
            except NotSupportedError:
                self._stat(c.name)["ws"] = False
                await self._rest_loop(c, [symbol])
                return
            except Exception as exc:
                self._stat(c.name)["errors"] += 1
                self._stat(c.name)["ws"] = False
                log.warning(
                    "ws feed error - falling back to REST while reconnecting",
                    venue=c.name,
                    symbol=symbol,
                    error=str(exc)[:200],
                )
                # REST fallback during backoff so the venue is DEGRADED rather than blind
                end = asyncio.get_event_loop().time() + backoff
                while self._running and asyncio.get_event_loop().time() < end:
                    await self._poll_once(c, symbol)
                    await asyncio.sleep(self.rest_interval_ms / 1000)
                backoff = min(backoff * 2, 30.0)
        _ = rest_until

    async def _rest_loop(self, c: VenueConnector, symbols: list[str]) -> None:
        while self._running:
            for s in symbols:
                await self._poll_once(c, s)
            await asyncio.sleep(self.rest_interval_ms / 1000)

    async def _poll_once(self, c: VenueConnector, symbol: str) -> None:
        try:
            book = await c.fetch_order_book(symbol, self.depth)
            await self.ingest(book)
        except asyncio.CancelledError:
            raise
        except VenueUnavailable as exc:
            self._stat(c.name)["errors"] += 1
            log.warning("rest poll failed", venue=c.name, symbol=symbol, error=str(exc)[:200])
        except Exception as exc:
            self._stat(c.name)["errors"] += 1
            log.error("rest poll error", venue=c.name, symbol=symbol, error=str(exc)[:200])
