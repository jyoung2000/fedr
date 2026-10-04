"""CCXT-based CEX connector (REST + ccxt.pro WebSocket order books).

Honesty notes
-------------
* Fees come from the exchange API when authenticated (``fetch_trading_fees``),
  otherwise from market metadata, otherwise from the documented default in the
  registry (flagged ``documented_fallback``), otherwise they are *unknown* and
  the Profit Guard blocks.
* Trade permission is only marked verified after a real order round-trip.
* Withdrawal permission is never required; it can only be verified on venues
  that expose key restrictions (Binance) - elsewhere the UI asks the user.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import ccxt.async_support as ccxt_async
import ccxt.pro as ccxtpro
from ccxt.base.decimal_to_precision import TICK_SIZE
from ccxt.base.errors import (
    AuthenticationError,
    BadSymbol,
    DDoSProtection,
    ExchangeError,
    ExchangeNotAvailable,
    InsufficientFunds,
    InvalidNonce,
    InvalidOrder,
    NetworkError,
    NotSupported,
    OnMaintenance,
    OrderNotFound,
    PermissionDenied,
    RateLimitExceeded,
    RequestTimeout,
)

from fedr.connectors.base import (
    ConnectorError,
    InsufficientBalance,
    NotSupportedError,
    OrderRejected,
    VenueConnector,
    VenueUnavailable,
)
from fedr.connectors.cex.registry import ExchangeSpec
from fedr.connectors.cex.registry import spec as get_spec
from fedr.core.enums import OrderSide, OrderStatus, TradingMode, VenueKind
from fedr.core.logging import get_logger
from fedr.core.models import (
    Balance,
    ExecutionQuote,
    FeeSchedule,
    Fill,
    HealthReport,
    MarketInfo,
    OrderRequest,
    OrderResult,
    now_ms,
)
from fedr.core.money import ZERO, D, ceil_to_step, floor_to_step
from fedr.marketdata.orderbook import OrderBook

log = get_logger("cex")

_STATUS_MAP = {
    "open": OrderStatus.OPEN,
    "closed": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
    "expired": OrderStatus.EXPIRED,
    "rejected": OrderStatus.REJECTED,
}


def _map_error(exc: Exception) -> ConnectorError:
    if isinstance(exc, (RateLimitExceeded, DDoSProtection)):
        e = VenueUnavailable(f"rate limited: {exc}")
        e.retryable = True
        return e
    if isinstance(exc, OnMaintenance):
        return VenueUnavailable(f"maintenance: {exc}")
    if isinstance(exc, (ExchangeNotAvailable, RequestTimeout, NetworkError)):
        return VenueUnavailable(str(exc))
    if isinstance(exc, InsufficientFunds):
        return InsufficientBalance(str(exc))
    if isinstance(exc, (InvalidOrder, OrderNotFound)):
        return OrderRejected(str(exc))
    if isinstance(exc, (AuthenticationError, PermissionDenied)):
        return ConnectorError(f"authentication/permission error: {exc}")
    if isinstance(exc, NotSupported):
        return NotSupportedError(str(exc))
    if isinstance(exc, BadSymbol):
        return ConnectorError(f"bad symbol: {exc}")
    return ConnectorError(str(exc))


def is_ambiguous_submit_error(exc: BaseException) -> bool:
    """True when a failed create_order may nevertheless have been accepted by the venue.

    A response the venue actually returned (ExchangeError family: invalid order, insufficient funds,
    auth, bad symbol) or an explicit throttle/maintenance answer is a definitive rejection. A timeout, a
    dropped connection or a 5xx gateway error can happen after the venue accepted the order."""
    if isinstance(exc, (RateLimitExceeded, DDoSProtection, OnMaintenance, InvalidNonce)):
        return False
    if isinstance(exc, (RequestTimeout, ExchangeNotAvailable, NetworkError)):
        return True
    if isinstance(exc, ExchangeError):
        return False
    return True  # anything unexpected (asyncio timeout, aiohttp error, bug) is treated as uncertain


def _fee_items(raw: dict) -> list[tuple[Decimal, str]]:
    """Fee entries reported on a ccxt order/trade as (amount, currency). Entries without a cost are skipped."""
    entries = raw.get("fees") or ([raw["fee"]] if raw.get("fee") else [])
    out: list[tuple[Decimal, str]] = []
    for f in entries:
        if not f or f.get("cost") is None:
            continue
        out.append((D(f["cost"]), str(f.get("currency") or "")))
    return out


class CcxtConnector(VenueConnector):
    def __init__(
        self,
        exchange_id: str,
        mode: TradingMode,
        credentials: dict[str, str] | None = None,
        *,
        sandbox: bool = False,
        market_type: str = "spot",
        name: str | None = None,
        timeout_s: float = 10.0,
        options: dict[str, Any] | None = None,
    ):
        sp = get_spec(exchange_id)
        display = sp.display_name if sp else exchange_id
        kind = VenueKind.PERP if market_type == "swap" else VenueKind.CEX
        super().__init__(name or exchange_id, kind, mode, display_name=display)
        self.exchange_id = exchange_id
        self.spec: ExchangeSpec | None = sp
        self.credentials = credentials or {}
        self.sandbox = sandbox
        self.market_type = market_type
        self.timeout_s = timeout_s
        self._options = options or {}
        self._fee_cache: dict[str, FeeSchedule] = {}
        self._fees_fetched_at = 0
        self.exchange: Any = None
        self.permissions: dict[str, bool | None] = {"read": None, "trade": None, "withdraw": None}
        self.capabilities.add("orderbook")

    # ------------------------------------------------------------------ lifecycle
    def _build(self) -> Any:
        cls = getattr(ccxtpro, self.exchange_id, None) or getattr(ccxt_async, self.exchange_id, None)
        if cls is None:
            raise NotSupportedError(f"ccxt has no exchange '{self.exchange_id}'")
        config: dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": int(self.timeout_s * 1000),
            "options": {"defaultType": self.market_type, "adjustForTimeDifference": True, **self._options},
        }
        for k in ("apiKey", "secret", "password", "uid", "walletAddress", "privateKey"):
            if self.credentials.get(k):
                config[k] = self.credentials[k]
        ex = cls(config)
        if getattr(ex, "has", {}).get("ws"):
            self.capabilities.add("ws")
        return ex

    async def connect(self) -> None:
        self.exchange = self._build()
        if self.sandbox:
            try:
                self.exchange.set_sandbox_mode(True)
                self.capabilities.add("sandbox")
            except NotSupported as exc:
                await self.close()
                raise NotSupportedError(f"{self.exchange_id} has no sandbox/testnet in ccxt: {exc}") from exc
        await self.load_markets()
        has = self.exchange.has
        for cap, key in (
            ("funding", "fetchFundingRate"),
            ("positions", "fetchPositions"),
            ("deposit_address", "fetchDepositAddress"),
            ("trading_fees", "fetchTradingFees"),
            ("status", "fetchStatus"),
        ):
            if has.get(key):
                self.capabilities.add(cap)
        if self.credentials:
            await self._verify_credentials()
        self.connected = True
        self.last_error = None

    async def close(self) -> None:
        if self.exchange is not None:
            try:
                await self.exchange.close()
            except Exception:  # pragma: no cover
                pass
        self.connected = False

    async def _verify_credentials(self) -> None:
        t0 = time.perf_counter()
        try:
            await self.exchange.fetch_balance()
            self.permissions["read"] = True
            self.health_tracker.last_balance_ms = now_ms()
        except Exception as exc:
            self.permissions["read"] = False
            raise _map_error(exc) from exc
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        # Withdrawal restriction check where the exchange exposes it (Binance family)
        if self.exchange_id in ("binance", "binanceus") and hasattr(
            self.exchange, "sapiGetAccountApiRestrictions"
        ):
            try:
                r = await self.exchange.sapiGetAccountApiRestrictions()
                self.permissions["withdraw"] = bool(r.get("enableWithdrawals"))
                self.permissions["trade"] = bool(r.get("enableSpotAndMarginTrading"))
            except Exception:
                pass

    # ------------------------------------------------------------------ markets
    async def load_markets(self) -> dict[str, MarketInfo]:
        t0 = time.perf_counter()
        try:
            markets = await self.exchange.load_markets()
        except Exception as exc:
            self.health_tracker.record_error()
            raise _map_error(exc) from exc
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        tick = self.exchange.precisionMode == TICK_SIZE
        out: dict[str, MarketInfo] = {}
        for symbol, m in markets.items():
            if not m.get("active", True):
                continue
            mtype = m.get("type")
            if self.market_type == "spot" and mtype != "spot":
                continue
            if self.market_type == "swap" and not (m.get("swap") and m.get("linear")):
                continue
            prec = m.get("precision") or {}
            amount_step = self._step(prec.get("amount"), tick)
            price_step = self._step(prec.get("price"), tick)
            limits = m.get("limits") or {}
            out[symbol] = MarketInfo(
                venue=self.name,
                symbol=symbol,
                base=m["base"],
                quote=m["quote"],
                kind=self.kind,
                active=True,
                amount_step=amount_step,
                price_step=price_step,
                min_amount=D((limits.get("amount") or {}).get("min") or 0),
                max_amount=D(limits["amount"]["max"]) if (limits.get("amount") or {}).get("max") else None,
                min_cost=D((limits.get("cost") or {}).get("min") or 0),
                taker_fee_pct=D(m["taker"]) * 100 if m.get("taker") is not None else None,
                maker_fee_pct=D(m["maker"]) * 100 if m.get("maker") is not None else None,
                contract_size=D(m.get("contractSize") or 1),
                settle=m.get("settle"),
            )
        self.markets = out
        return out

    @staticmethod
    def _step(value: Any, tick: bool) -> Decimal:
        if value is None:
            return Decimal("0.00000001")
        v = D(value)
        if tick:
            return v
        return Decimal(1).scaleb(-int(v))

    # ------------------------------------------------------------------ market data
    def _book_limit(self, depth: int) -> int | None:
        limits = self.spec.orderbook_limits if self.spec else ()
        if not limits:
            return depth
        for lim in limits:
            if lim >= depth:
                return lim
        return limits[-1]

    async def fetch_order_book(self, symbol: str, depth: int = 50) -> OrderBook:
        t0 = time.perf_counter()
        try:
            raw = await self.exchange.fetch_order_book(symbol, self._book_limit(depth))
        except Exception as exc:
            self.health_tracker.record_error()
            self._note_rate_limit(exc)
            raise _map_error(exc) from exc
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        ob = OrderBook.from_raw(
            self.name,
            symbol,
            self._levels_in_base(symbol, raw["bids"]),
            self._levels_in_base(symbol, raw["asks"]),
            ts_ms=raw.get("timestamp") or now_ms(),
            source="rest",
            sequence=raw.get("nonce"),
        )
        self.health_tracker.last_market_data_ms = now_ms()
        return ob

    async def watch_order_book(self, symbol: str, depth: int = 50) -> AsyncIterator[OrderBook]:
        """Async generator of order-book snapshots via ccxt.pro. Caller handles reconnects."""
        if not self.exchange.has.get("watchOrderBook"):
            raise NotSupportedError("watchOrderBook not supported")
        limit = self._book_limit(depth)
        while True:
            try:
                raw = await self.exchange.watch_order_book(symbol, limit)
            except (NetworkError, RequestTimeout) as exc:
                self.health_tracker.ws_connected = False
                self.health_tracker.record_error()
                raise VenueUnavailable(f"websocket error: {exc}") from exc
            self.health_tracker.ws_connected = True
            self.health_tracker.last_market_data_ms = now_ms()
            yield OrderBook.from_raw(
                self.name,
                symbol,
                self._levels_in_base(symbol, list(raw["bids"])[:depth]),
                self._levels_in_base(symbol, list(raw["asks"])[:depth]),
                ts_ms=raw.get("timestamp") or now_ms(),
                source="ws",
                sequence=raw.get("nonce"),
            )

    def _levels_in_base(self, symbol: str, levels: list) -> list:
        """Swap order books are quoted in CONTRACTS; the engine walks books in BASE units."""
        if self.market_type != "swap":
            return levels
        cs = self._contract_size(symbol)
        if cs == 1:
            return levels
        return [[lv[0], str(D(lv[1]) * cs)] + list(lv[2:]) for lv in levels]

    async def get_quote(
        self, symbol: str, side: OrderSide, base_amount: Decimal, *, order_book: OrderBook | None = None
    ) -> ExecutionQuote:
        ob = order_book or await self.fetch_order_book(symbol)
        fee = await self.fetch_fees(symbol)
        w = ob.walk(side, base_amount)
        m = self.markets.get(symbol)
        return ExecutionQuote(
            venue=self.name,
            kind=self.kind,
            symbol=symbol,
            side=side,
            base_amount=w.filled_base if w.fully_filled else base_amount,
            quote_amount=w.quote_amount,
            avg_price=w.avg_price,
            reference_price=w.reference_price,
            slippage_pct=w.slippage_pct,
            price_impact_pct=ZERO,
            fully_fillable=w.fully_filled,
            market_ts_ms=ob.ts_ms,
            fee_pct=fee.taker_pct,
            fee_source=fee.source,
            route=f"{self.display_name} order book ({w.levels_consumed} levels)",
            levels_consumed=w.levels_consumed,
            depth_available_base=w.depth_available_base,
            extra={
                "limit_price": str(w.last_price),
                "taker_fee_pct": str(fee.taker_pct) if fee.taker_pct is not None else None,
                "min_amount": str(m.min_amount) if m else None,
            },
        )

    # ------------------------------------------------------------------ fees
    async def fetch_fees(self, symbol: str) -> FeeSchedule:
        cached = self._fee_cache.get(symbol)
        if cached and now_ms() - cached.fetched_at_ms < 3_600_000:
            return cached
        if self.credentials and now_ms() - self._fees_fetched_at > 3_600_000:
            try:
                if self.exchange.has.get("fetchTradingFees"):
                    fees = await self.exchange.fetch_trading_fees()
                    for sym, f in fees.items():
                        if f.get("taker") is not None:
                            self._fee_cache[sym] = FeeSchedule(
                                D(f["taker"]) * 100,
                                D(f["maker"]) * 100 if f.get("maker") is not None else None,
                                "exchange_api",
                            )
                elif self.exchange.has.get("fetchTradingFee"):
                    f = await self.exchange.fetch_trading_fee(symbol)
                    if f.get("taker") is not None:
                        self._fee_cache[symbol] = FeeSchedule(
                            D(f["taker"]) * 100,
                            D(f["maker"]) * 100 if f.get("maker") is not None else None,
                            "exchange_api",
                        )
                self._fees_fetched_at = now_ms()
            except Exception as exc:
                log.warning("fee fetch failed", venue=self.name, error=str(exc))
                self._fees_fetched_at = now_ms()
        if symbol in self._fee_cache:
            return self._fee_cache[symbol]
        m = self.markets.get(symbol)
        if m and m.taker_fee_pct is not None:
            fs = FeeSchedule(m.taker_fee_pct, m.maker_fee_pct, "market_metadata")
        elif self.spec and self.spec.fallback_taker_pct is not None:
            fs = FeeSchedule(
                self.spec.fallback_taker_pct, self.spec.fallback_maker_pct, "documented_fallback"
            )
        else:
            fs = FeeSchedule(None, None, "unknown")
        self._fee_cache[symbol] = fs
        return fs

    # ------------------------------------------------------------------ account
    async def fetch_balances(self) -> dict[str, Balance]:
        if not self.credentials:
            return {}
        t0 = time.perf_counter()
        try:
            raw = await self.exchange.fetch_balance()
        except Exception as exc:
            self.health_tracker.record_error()
            raise _map_error(exc) from exc
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        out: dict[str, Balance] = {}
        for asset, free in (raw.get("free") or {}).items():
            used = (raw.get("used") or {}).get(asset) or 0
            if free or used:
                out[asset] = Balance(asset=asset, free=D(free or 0), used=D(used))
        self.health_tracker.last_balance_ms = now_ms()
        return out

    # ------------------------------------------------------------------ amounts (base <-> venue units)
    def _contract_size(self, symbol: str) -> Decimal:
        m = self.markets.get(symbol)
        cs = m.contract_size if m is not None else Decimal(1)
        return cs if cs and cs > 0 else Decimal(1)

    def to_venue_amount(self, symbol: str, base_amount: Decimal) -> Decimal:
        """Engine amounts are always in BASE units; linear swaps trade in contracts of ``contractSize`` base."""
        return base_amount / self._contract_size(symbol)

    def to_base_amount(self, symbol: str, venue_amount: Decimal) -> Decimal:
        return venue_amount * self._contract_size(symbol)

    def _quantize(
        self, symbol: str, amount: Decimal, price: Decimal | None, side: OrderSide = OrderSide.BUY
    ) -> tuple[Decimal, Decimal | None]:
        """Quantize a BASE amount into the venue's order amount (contracts for swaps) and a limit price."""
        m = self.markets.get(symbol)
        if m is None:
            if self.market_type == "swap":
                raise OrderRejected(f"{symbol} is not a loaded linear swap market on {self.name}")
            return amount, price
        amt = floor_to_step(self.to_venue_amount(symbol, amount), m.amount_step)
        if amt <= 0 or amt < m.min_amount:
            raise OrderRejected(f"amount {amt} below minimum {m.min_amount} on {self.name}")
        if price is not None:
            # conservative rounding: a buy limit rounds down, a sell limit rounds up (never loosens the limit)
            price = (
                floor_to_step(price, m.price_step)
                if side is OrderSide.BUY
                else ceil_to_step(price, m.price_step)
            )
        base_amt = self.to_base_amount(symbol, amt)
        if price is not None and m.min_cost and base_amt * price < m.min_cost:
            raise OrderRejected(f"order cost {base_amt * price} below minimum {m.min_cost} on {self.name}")
        return amt, price

    # ------------------------------------------------------------------ orders
    async def place_order(self, req: OrderRequest) -> OrderResult:
        """Submit one IOC limit order and follow it to a KNOWN outcome where possible.

        Uncertainty is never collapsed into "no fill": a transport failure after the request may have
        reached the venue, a failed cancel, or a status query that returns nothing all leave the order in
        ``UNKNOWN`` / ``CANCEL_PENDING`` (non-terminal). The caller keeps exposure and reservations for it and
        resolves it later through :meth:`resolve_order` using the persistent client order id.
        """
        if not self.credentials:
            raise ConnectorError(f"{self.name}: no credentials - cannot place orders")
        if req.reduce_only and self.market_type != "swap":
            raise OrderRejected("reduce_only is only valid on derivative markets")
        amount, price = self._quantize(req.symbol, req.amount, req.limit_price, req.side)
        params: dict[str, Any] = {"clientOrderId": req.client_order_id}
        if price is None:
            raise OrderRejected("market orders without a limit price are not allowed (use IOC limit)")
        if req.time_in_force in ("IOC", "FOK"):
            params["timeInForce"] = req.time_in_force
        if req.reduce_only:
            params["reduceOnly"] = True
        result = OrderResult(request=req, order_id="", status=OrderStatus.SUBMITTING)
        result.raw["client_order_id"] = req.client_order_id
        result.raw["venue_amount"] = str(amount)
        t0 = time.perf_counter()
        try:
            try:
                raw = await self.exchange.create_order(
                    req.symbol, "limit", req.side.value, float(amount), float(price), params
                )
            except InvalidOrder as exc:
                if "timeInForce" in str(exc) or "time in force" in str(exc).lower():
                    # a definitive rejection of the parameter: nothing was accepted, retry without it once
                    params.pop("timeInForce", None)
                    raw = await self.exchange.create_order(
                        req.symbol, "limit", req.side.value, float(amount), float(price), params
                    )
                    result.raw["ioc_fallback"] = True
                else:
                    raise
        except Exception as exc:
            self.health_tracker.record_error()
            self._note_rate_limit(exc)
            result.error = str(exc)[:300]
            if is_ambiguous_submit_error(exc):
                # The request may have been accepted. Resolve by client order id; otherwise stay UNKNOWN.
                result.status = OrderStatus.UNKNOWN
                result.raw["unknown_reason"] = f"transport failure after submit: {type(exc).__name__}"
                await self.resolve_order(result)
                return result
            self.health_tracker.record_order(False)
            result.status = OrderStatus.REJECTED
            result.completed_at_ms = now_ms()
            return result
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        result.order_id = str(raw.get("id") or "")
        if not result.order_id:
            result.raw["venue_id_missing"] = True
        self._apply_order(result, raw)
        deadline = time.monotonic() + (req.deadline_ms or 8000) / 1000
        while not result.is_terminal and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            fetched, _ = await self._fetch_raw_order(result)
            if fetched:
                self._apply_order(result, fetched)
        if not result.is_terminal:
            await self._cancel_and_confirm(result)
        await self._complete_fill_details(result)
        if result.is_terminal:
            result.completed_at_ms = now_ms()
            self.health_tracker.record_order(
                result.status
                in (
                    OrderStatus.FILLED,
                    OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.CANCELLED,
                    OrderStatus.EXPIRED,
                )
            )
        if result.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED) or result.filled > 0:
            self.permissions["trade"] = True
        return result

    async def _cancel_and_confirm(self, result: OrderResult) -> None:
        """Cancel a still-working order and confirm the final state. Never infers CANCELLED from silence."""
        req = result.request
        try:
            await self.exchange.cancel_order(result.order_id or None, req.symbol, self._id_params(result))
            result.raw["cancel"] = "requested"
        except OrderNotFound:
            result.raw["cancel"] = "not_found"  # already closed OR never known: must be confirmed by a fetch
        except Exception as exc:
            self.health_tracker.record_error()
            result.raw["cancel"] = f"failed: {str(exc)[:160]}"
            log.warning("cancel failed", venue=self.name, order=result.order_id, error=str(exc)[:160])
        for _ in range(4):
            fetched, _ = await self._fetch_raw_order(result)
            if fetched:
                self._apply_order(result, fetched)
                if result.is_terminal:
                    return
            await asyncio.sleep(0.3)
        if not result.is_terminal:
            result.status = (
                OrderStatus.CANCEL_PENDING
                if str(result.raw.get("cancel")) == "requested"
                else OrderStatus.UNKNOWN
            )
            result.error = (result.error or "") + " | final state not confirmed by the venue"
            result.raw["unknown_reason"] = (
                f"cancel {result.raw.get('cancel')}; status query returned no final state"
            )

    def _id_params(self, result: OrderResult) -> dict[str, Any]:
        return {"clientOrderId": result.request.client_order_id}

    async def _fetch_raw_order(self, result: OrderResult) -> tuple[dict | None, bool]:
        """(raw order, definitively_not_found). Errors are NOT reported as not-found."""
        symbol = result.request.symbol
        not_found = False
        for attempt in range(3):
            try:
                if result.order_id:
                    return await self.exchange.fetch_order(result.order_id, symbol), False
                return await self.exchange.fetch_order(None, symbol, self._id_params(result)), False
            except OrderNotFound:
                not_found = True
                await asyncio.sleep(0.3 * (attempt + 1))
            except Exception as exc:
                not_found = False
                self.health_tracker.record_error()
                log.warning("fetch_order failed", venue=self.name, error=str(exc)[:160])
                await asyncio.sleep(0.3)
        return None, not_found

    async def resolve_order(self, result: OrderResult) -> OrderResult:
        """Look an uncertain order up by venue id or persistent client order id and apply what the venue
        reports. Used after transport loss and by startup recovery. Leaves the order UNKNOWN when the venue
        cannot confirm its state."""
        raw, not_found = await self._fetch_raw_order(result)
        if raw is None and not result.order_id:
            raw = await self._find_by_client_id(result)
        if raw is not None:
            if not result.order_id:
                result.order_id = str(raw.get("id") or "")
            self._apply_order(result, raw)
            await self._complete_fill_details(result)
            if result.is_terminal:
                result.completed_at_ms = result.completed_at_ms or now_ms()
            elif result.status is OrderStatus.OPEN and result.raw.get("resolve_cancel", True):
                await self._cancel_and_confirm(result)
                await self._complete_fill_details(result)
            return result
        result.raw["lookup"] = "not_found" if not_found else "unavailable"
        if result.status not in (OrderStatus.UNKNOWN, OrderStatus.CANCEL_PENDING):
            result.status = OrderStatus.UNKNOWN
        return result

    async def _find_by_client_id(self, result: OrderResult) -> dict | None:
        cid = result.request.client_order_id
        sym = result.request.symbol
        since = result.submitted_at_ms - 120_000
        for name in ("fetch_open_orders", "fetch_closed_orders", "fetch_canceled_orders"):
            fn = getattr(self.exchange, name, None)
            key = {"fetch_open_orders": "fetchOpenOrders", "fetch_closed_orders": "fetchClosedOrders"}.get(
                name, "fetchCanceledOrders"
            )
            if fn is None or not self.exchange.has.get(key):
                continue
            try:
                orders = await (fn(sym) if name == "fetch_open_orders" else fn(sym, since))
            except Exception as exc:
                log.warning("client-id lookup failed", venue=self.name, method=name, error=str(exc)[:120])
                continue
            for o in orders or []:
                if str(o.get("clientOrderId") or "") == cid:
                    return o
        return None

    def _apply_order(self, result: OrderResult, raw: dict) -> None:
        """Apply a venue order snapshot. Cumulative fields only move forward; fills are de-duplicated."""
        sym = result.request.symbol
        status = _STATUS_MAP.get(str(raw.get("status") or "").lower())
        filled = self.to_base_amount(sym, D(raw.get("filled") or 0))
        result.filled = max(result.filled, filled)
        if raw.get("average"):
            result.avg_price = D(raw["average"])
        if raw.get("cost") is not None and D(raw["cost"]) > 0:
            result.cost = max(result.cost or ZERO, D(raw["cost"]))
        remaining = raw.get("remaining")
        if status is OrderStatus.FILLED or (remaining is not None and D(remaining) == 0 and filled > 0):
            result.status = OrderStatus.FILLED
        elif status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED):
            if result.filled > 0:
                result.status = OrderStatus.PARTIALLY_FILLED  # final partial fill
            else:
                result.status = status
        elif status is OrderStatus.OPEN:
            result.status = OrderStatus.OPEN  # still working (possibly with partial fills)
        elif result.status is OrderStatus.SUBMITTING:
            result.status = OrderStatus.OPEN  # acknowledged by the venue, state not yet reported
        for t in raw.get("trades") or []:
            self._add_fill(result, t)
        fee_items = _fee_items(raw)
        if fee_items:
            result.fee_items = fee_items
        result.raw["last"] = {
            k: raw.get(k)
            for k in ("id", "clientOrderId", "status", "filled", "remaining", "average", "cost", "fee")
        }
        self._update_fee_completeness(result)

    def _add_fill(self, result: OrderResult, t: dict) -> None:
        tid = str(t.get("id") or "")
        if tid and any(f.trade_id == tid for f in result.fills):
            return  # late / repeated trade arrays never double count
        if not tid and result.fills:
            return  # unidentifiable trade on an order that already has fills: cannot de-duplicate safely
        result.fills.append(self._fill_from_trade(result, t))

    def _fill_from_trade(self, result: OrderResult, t: dict) -> Fill:
        fees = _fee_items(t)
        fee_amount, fee_asset = fees[0] if len(fees) == 1 else (ZERO, "")
        sym = result.request.symbol
        return Fill(
            order_id=result.order_id,
            venue=self.name,
            symbol=sym,
            side=result.request.side,
            amount=self.to_base_amount(sym, D(t.get("amount") or 0)),
            price=D(t.get("price") or 0),
            fee_amount=fee_amount,
            fee_asset=fee_asset,
            ts_ms=int(t.get("timestamp") or now_ms()),
            trade_id=str(t.get("id") or ""),
            # a trade without (exactly one) fee entry has an UNKNOWN fee, not a zero fee
            fee_known=len(fees) == 1,
        )

    def _update_fee_completeness(self, result: OrderResult) -> None:
        if result.filled == 0:
            result.fees_complete = True
        elif result.fills_cover_filled and all(f.fee_known for f in result.fills):
            result.fees_complete = True
        elif result.fee_items:
            result.fees_complete = True
        else:
            result.fees_complete = False
        result.fee_quote = self._fees_in_quote(result)

    async def _complete_fill_details(self, result: OrderResult) -> None:
        """Fetch trades when the order snapshot did not carry fills covering the filled amount."""
        if result.filled > 0 and not result.fills_cover_filled:
            await self._load_fills(result)
        self._update_fee_completeness(result)

    async def _load_fills(self, result: OrderResult) -> None:
        if not result.order_id or not self.exchange.has.get("fetchMyTrades"):
            return
        try:
            trades = await self.exchange.fetch_my_trades(
                result.request.symbol, since=result.submitted_at_ms - 60_000, limit=100
            )
        except Exception as exc:
            log.warning("fetch_my_trades failed", venue=self.name, error=str(exc)[:160])
            result.raw["fills_unavailable"] = str(exc)[:160]
            return
        for t in trades or []:
            if str(t.get("order")) == result.order_id:
                self._add_fill(result, t)
        if result.fills_cover_filled:
            total = sum((f.amount for f in result.fills), ZERO)
            result.avg_price = sum((f.amount * f.price for f in result.fills), ZERO) / total

    def _fees_in_quote(self, result: OrderResult) -> Decimal:
        """Fees convertible from the order's own data (quote or base asset). Third-asset fees (BNB, KCS…)
        are left to accounting, which converts them with a price index or marks them unknown."""
        items = (
            [(f.fee_amount, f.fee_asset, f.price) for f in result.fills if f.fee_known]
            if result.fills
            else [(a, c, None) for a, c in result.fee_items]
        )
        m = self.markets.get(result.request.symbol)
        total = ZERO
        for amount, asset, px in items:
            if amount == 0:
                continue
            if m is not None and asset == m.quote:
                total += amount
            elif m is not None and asset == m.base:
                p = px or result.avg_price
                if p:
                    total += amount * p
                else:
                    result.raw["fee_asset_unconverted"] = asset
            else:
                result.raw["fee_asset_unconverted"] = asset
        return total

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            await self.exchange.cancel_order(order_id, symbol)
            return True
        except OrderNotFound:
            return True
        except Exception as exc:
            self.health_tracker.record_error()
            raise _map_error(exc) from exc

    async def fetch_order(self, order_id: str, symbol: str) -> OrderResult | None:
        req = OrderRequest(venue=self.name, symbol=symbol, side=OrderSide.BUY, amount=ZERO, limit_price=None)
        probe = OrderResult(request=req, order_id=order_id, status=OrderStatus.UNKNOWN)
        raw, _ = await self._fetch_raw_order(probe)
        if raw is None:
            return None
        req.side = OrderSide(raw.get("side") or "buy")
        req.amount = self.to_base_amount(symbol, D(raw.get("amount") or 0))
        req.limit_price = D(raw["price"]) if raw.get("price") else None
        res = OrderResult(request=req, order_id=order_id, status=OrderStatus.SUBMITTING)
        self._apply_order(res, raw)
        return res

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict]:
        try:
            return await self.exchange.fetch_open_orders(symbol)
        except Exception as exc:
            raise _map_error(exc) from exc

    # ------------------------------------------------------------------ perps / funding
    async def fetch_server_time_ms(self) -> int | None:
        if not self.exchange.has.get("fetchTime"):
            return None
        try:
            t = await self.exchange.fetch_time()
        except Exception as exc:
            self.health_tracker.record_error()
            raise _map_error(exc) from exc
        return int(t) if t is not None else None

    async def fetch_funding_rate(self, symbol: str) -> dict | None:
        if not self.exchange.has.get("fetchFundingRate"):
            return None
        try:
            return await self.exchange.fetch_funding_rate(symbol)
        except Exception as exc:
            self.health_tracker.record_error()
            raise _map_error(exc) from exc

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[dict]:
        if not self.exchange.has.get("fetchPositions"):
            return []
        try:
            return await self.exchange.fetch_positions(symbols)
        except Exception as exc:
            raise _map_error(exc) from exc

    async def verify_derivative_settings(self, symbol: str) -> list[str]:
        """Problems that prevent trusting FEDR's 1x / one-way / linear risk model on this swap market.
        Empty list = verified with the venue's own account settings. Unverifiable counts as a problem."""
        problems: list[str] = []
        if self.market_type != "swap":
            return [f"{self.name} is not a derivatives connector"]
        m = self.markets.get(symbol)
        if m is None:
            return [f"{symbol} is not a loaded linear swap market on {self.name}"]
        has = self.exchange.has
        if has.get("fetchPositionMode"):
            try:
                mode = await self.exchange.fetch_position_mode(symbol)
                if mode.get("hedged"):
                    problems.append(
                        "account is in hedge (dual-side) position mode; FEDR requires one-way mode"
                    )
            except Exception as exc:
                problems.append(f"position mode could not be verified ({str(exc)[:80]})")
        else:
            problems.append("venue/ccxt cannot report the position mode (one-way vs hedge)")
        if has.get("fetchLeverage"):
            try:
                lev = await self.exchange.fetch_leverage(symbol)
                values = [lev.get(k) for k in ("shortLeverage", "longLeverage", "leverage") if lev.get(k)]
                if not values:
                    problems.append("leverage not reported")
                elif any(D(v) > 1 for v in values):
                    problems.append(
                        f"leverage {max(D(v) for v in values)}x configured; FEDR's risk model is 1x"
                    )
                mm = str(lev.get("marginMode") or "").lower()
                if mm and mm != "isolated":
                    problems.append(f"margin mode {mm}; FEDR requires isolated margin per position")
            except Exception as exc:
                problems.append(f"leverage could not be verified ({str(exc)[:80]})")
        else:
            problems.append("venue/ccxt cannot report leverage for this market")
        return problems

    async def venue_positions(self, symbols: list[str] | None = None) -> list[dict]:
        """Positions normalised to BASE units: {symbol, side, size_base, liquidation_price, leverage}."""
        out = []
        for p in await self.fetch_positions(symbols):
            sym = str(p.get("symbol") or "")
            contracts = D(p.get("contracts") or 0)
            cs = D(p.get("contractSize") or 0) or self._contract_size(sym)
            if contracts == 0:
                continue
            out.append(
                {
                    "venue": self.name,
                    "symbol": sym,
                    "side": str(p.get("side") or "").lower(),
                    "size_base": abs(contracts) * cs,
                    "liquidation_price": D(p["liquidationPrice"]) if p.get("liquidationPrice") else None,
                    "leverage": D(p["leverage"]) if p.get("leverage") else None,
                }
            )
        return out

    async def fetch_deposit_address(self, asset: str, network: str | None = None) -> dict | None:
        if not self.credentials or not self.exchange.has.get("fetchDepositAddress"):
            return None
        try:
            params = {"network": network} if network else {}
            r = await self.exchange.fetch_deposit_address(asset, params)
            return {"address": r.get("address"), "tag": r.get("tag"), "network": r.get("network") or network}
        except Exception as exc:
            raise _map_error(exc) from exc

    # ------------------------------------------------------------------ health
    def _note_rate_limit(self, exc: Exception) -> None:
        if isinstance(exc, (RateLimitExceeded, DDoSProtection)):
            self.health_tracker.rate_limited_until = time.monotonic() + 30
        if isinstance(exc, OnMaintenance):
            self.health_tracker.maintenance = True

    async def health_check(self) -> HealthReport:
        if self.exchange is None:
            return HealthReport(
                venue=self.name,
                health=__import__("fedr.core.enums", fromlist=["VenueHealth"]).VenueHealth.UNHEALTHY,
                reasons=["not connected"],
            )
        t0 = time.perf_counter()
        try:
            if self.exchange.has.get("fetchStatus"):
                st = await self.exchange.fetch_status()
                self.health_tracker.maintenance = st.get("status") in ("maintenance", "shutdown")
            elif self.exchange.has.get("fetchTime"):
                await self.exchange.fetch_time()
        except Exception as exc:
            self.health_tracker.record_error()
            self._note_rate_limit(exc)
            self.last_error = str(exc)[:200]
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        return self.health_tracker.classify(self.name, expect_ws="ws" in self.capabilities)

    def status_dict(self) -> dict:
        d = super().status_dict()
        d.update(
            {
                "exchange_id": self.exchange_id,
                "sandbox": self.sandbox,
                "permissions": self.permissions,
                "market_type": self.market_type,
                "has_credentials": bool(self.credentials),
            }
        )
        return d
