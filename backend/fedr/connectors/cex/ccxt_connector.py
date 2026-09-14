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
    ExchangeNotAvailable,
    InsufficientFunds,
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
            raw["bids"],
            raw["asks"],
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
                list(raw["bids"])[:depth],
                list(raw["asks"])[:depth],
                ts_ms=raw.get("timestamp") or now_ms(),
                source="ws",
                sequence=raw.get("nonce"),
            )

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

    def _quantize(
        self, symbol: str, amount: Decimal, price: Decimal | None, side: OrderSide = OrderSide.BUY
    ) -> tuple[Decimal, Decimal | None]:
        m = self.markets.get(symbol)
        if m is None:
            return amount, price
        amt = floor_to_step(amount, m.amount_step)
        if amt < m.min_amount:
            raise OrderRejected(f"amount {amt} below minimum {m.min_amount} on {self.name}")
        if price is not None:
            # conservative rounding: a buy limit rounds down, a sell limit rounds up (never loosens the limit)
            price = (
                floor_to_step(price, m.price_step)
                if side is OrderSide.BUY
                else ceil_to_step(price, m.price_step)
            )
        if price is not None and m.min_cost and amt * price < m.min_cost:
            raise OrderRejected(f"order cost {amt * price} below minimum {m.min_cost} on {self.name}")
        return amt, price

    async def place_order(self, req: OrderRequest) -> OrderResult:
        if not self.credentials:
            raise ConnectorError(f"{self.name}: no credentials - cannot place orders")
        amount, price = self._quantize(req.symbol, req.amount, req.limit_price, req.side)
        params: dict[str, Any] = {"clientOrderId": req.client_order_id}
        if price is None:
            raise OrderRejected("market orders without a limit price are not allowed (use IOC limit)")
        if req.time_in_force in ("IOC", "FOK"):
            params["timeInForce"] = req.time_in_force
        result = OrderResult(request=req, order_id="", status=OrderStatus.NEW)
        t0 = time.perf_counter()
        try:
            try:
                raw = await self.exchange.create_order(
                    req.symbol, "limit", req.side.value, float(amount), float(price), params
                )
            except InvalidOrder as exc:
                if "timeInForce" in str(exc) or "time in force" in str(exc).lower():
                    params.pop("timeInForce", None)
                    raw = await self.exchange.create_order(
                        req.symbol, "limit", req.side.value, float(amount), float(price), params
                    )
                    result.raw["ioc_fallback"] = True
                else:
                    raise
        except Exception as exc:
            self.health_tracker.record_error()
            self.health_tracker.record_order(False)
            result.status = OrderStatus.REJECTED
            result.error = str(exc)
            result.completed_at_ms = now_ms()
            mapped = _map_error(exc)
            if isinstance(mapped, VenueUnavailable):
                raise mapped from exc
            return result
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        result.order_id = str(raw.get("id") or req.client_order_id)
        self._apply_order(result, raw)
        deadline = time.monotonic() + (req.deadline_ms or 8000) / 1000
        while not result.is_terminal and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            fetched = await self._fetch_raw_order(result.order_id, req.symbol)
            if fetched:
                self._apply_order(result, fetched)
        if not result.is_terminal:
            try:
                await self.exchange.cancel_order(result.order_id, req.symbol)
            except OrderNotFound:
                pass
            except Exception as exc:
                log.warning("cancel failed", venue=self.name, order=result.order_id, error=str(exc))
            fetched = await self._fetch_raw_order(result.order_id, req.symbol)
            if fetched:
                self._apply_order(result, fetched)
            if not result.is_terminal:
                result.status = OrderStatus.CANCELLED if result.filled == 0 else OrderStatus.PARTIALLY_FILLED
        if result.filled > 0 and not result.fills:
            await self._load_fills(result)
        result.completed_at_ms = now_ms()
        self.health_tracker.record_order(
            result.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCELLED)
        )
        if result.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            self.permissions["trade"] = True
        return result

    async def _fetch_raw_order(self, order_id: str, symbol: str) -> dict | None:
        for attempt in range(3):
            try:
                return await self.exchange.fetch_order(order_id, symbol)
            except OrderNotFound:
                await asyncio.sleep(0.3 * (attempt + 1))
            except Exception as exc:
                self.health_tracker.record_error()
                log.warning("fetch_order failed", venue=self.name, error=str(exc))
                await asyncio.sleep(0.3)
        return None

    def _apply_order(self, result: OrderResult, raw: dict) -> None:
        status = _STATUS_MAP.get(str(raw.get("status") or "").lower())
        filled = D(raw.get("filled") or 0)
        result.filled = max(result.filled, filled)
        if raw.get("average"):
            result.avg_price = D(raw["average"])
        if status is OrderStatus.FILLED or (raw.get("remaining") == 0 and filled > 0):
            result.status = OrderStatus.FILLED
        elif status in (OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED):
            result.status = (
                OrderStatus.PARTIALLY_FILLED
                if result.filled > 0 and status is not OrderStatus.REJECTED
                else status
            )
            if status is OrderStatus.CANCELLED and result.filled > 0:
                result.status = OrderStatus.PARTIALLY_FILLED
        elif status is OrderStatus.OPEN:
            result.status = OrderStatus.PARTIALLY_FILLED if result.filled > 0 else OrderStatus.OPEN
        trades = raw.get("trades") or []
        if trades and not result.fills:
            for t in trades:
                result.fills.append(self._fill_from_trade(result, t))
            result.fee_quote = self._fees_in_quote(result)
        elif raw.get("fee") and raw["fee"].get("cost") is not None and not result.fills:
            result.raw["fee"] = raw["fee"]
        result.raw["last"] = {
            k: raw.get(k) for k in ("id", "status", "filled", "remaining", "average", "cost", "fee")
        }

    def _fill_from_trade(self, result: OrderResult, t: dict) -> Fill:
        fee = t.get("fee") or {}
        return Fill(
            order_id=result.order_id,
            venue=self.name,
            symbol=result.request.symbol,
            side=result.request.side,
            amount=D(t.get("amount") or 0),
            price=D(t.get("price") or 0),
            fee_amount=D(fee.get("cost") or 0),
            fee_asset=str(fee.get("currency") or ""),
            ts_ms=int(t.get("timestamp") or now_ms()),
            trade_id=str(t.get("id") or ""),
        )

    async def _load_fills(self, result: OrderResult) -> None:
        try:
            trades = await self.exchange.fetch_my_trades(
                result.request.symbol, since=result.submitted_at_ms - 60_000, limit=100
            )
        except Exception as exc:
            log.warning("fetch_my_trades failed", venue=self.name, error=str(exc))
            return
        for t in trades:
            if str(t.get("order")) == result.order_id:
                result.fills.append(self._fill_from_trade(result, t))
        if result.fills:
            result.fee_quote = self._fees_in_quote(result)
            total = sum((f.amount for f in result.fills), ZERO)
            if total > 0:
                result.avg_price = sum((f.amount * f.price for f in result.fills), ZERO) / total
        elif result.raw.get("fee"):
            fee = result.raw["fee"]
            result.fee_quote = self._fee_to_quote(
                D(fee.get("cost") or 0), str(fee.get("currency") or ""), result
            )

    def _fees_in_quote(self, result: OrderResult) -> Decimal:
        return sum(
            (self._fee_to_quote(f.fee_amount, f.fee_asset, result, f.price) for f in result.fills), ZERO
        )

    def _fee_to_quote(
        self, amount: Decimal, asset: str, result: OrderResult, price: Decimal | None = None
    ) -> Decimal:
        m = self.markets.get(result.request.symbol)
        if m is None or amount == 0:
            return amount
        if asset == m.quote:
            return amount
        px = price or result.avg_price or result.request.limit_price or ZERO
        if asset == m.base:
            return amount * px
        # Fee paid in a third asset (e.g. BNB/KCS): we cannot convert here without a price index;
        # fall back to the schedule estimate and flag it so reconciliation can revisit.
        result.raw["fee_asset_unconverted"] = asset
        fee = self._fee_cache.get(result.request.symbol)
        rate = fee.taker_pct if (fee and fee.taker_pct is not None) else m.taker_fee_pct
        if rate is None and self.spec is not None:
            rate = self.spec.fallback_taker_pct
        return result.quote_amount * rate / 100 if rate is not None else ZERO

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
        raw = await self._fetch_raw_order(order_id, symbol)
        if raw is None:
            return None
        req = OrderRequest(
            venue=self.name,
            symbol=symbol,
            side=OrderSide(raw.get("side") or "buy"),
            amount=D(raw.get("amount") or 0),
            limit_price=D(raw["price"]) if raw.get("price") else None,
        )
        res = OrderResult(request=req, order_id=order_id, status=OrderStatus.OPEN)
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
