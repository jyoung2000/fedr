"""DEX venue connector backed by Hummingbot Gateway."""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Awaitable, Callable

from fedr.connectors.base import (
    ConnectorError,
    OrderRejected,
    QuoteExpired,
    VenueConnector,
    VenueUnavailable,
)
from fedr.connectors.dex.gateway_client import GatewayClient, GatewayError, GatewayQuote
from fedr.connectors.dex.registry import DexSpec
from fedr.core.enums import OrderSide, OrderStatus, TradingMode, VenueHealth, VenueKind
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
from fedr.core.money import HUNDRED, ZERO, D
from fedr.marketdata.orderbook import OrderBook

log = get_logger("dex")

QUOTE_MAX_AGE_MS = 8_000  # router quoteIds are cached in Gateway memory; we refuse to execute stale ones


class GatewayConnector(VenueConnector):
    def __init__(
        self,
        spec: DexSpec,
        client: GatewayClient,
        mode: TradingMode,
        wallet_address: str | None,
        pairs: list[str],
        slippage_tolerance_pct: Decimal,
        native_usd_price: Callable[[str], Decimal | None] | None = None,
        testnet: bool = False,
    ):
        super().__init__(spec.id, VenueKind.DEX, mode, display_name=spec.display_name)
        self.spec = spec
        self.client = client
        self.chain = spec.chain
        self.network = spec.testnet_network if (testnet and spec.testnet_network) else spec.network
        self.wallet_address = wallet_address
        self.pairs = pairs
        self.slippage_tolerance_pct = slippage_tolerance_pct
        self.native_usd_price = native_usd_price or (lambda _: None)
        self.capabilities.update({"quote", "swap", "gas"})
        self._tokens: dict[str, dict] = {}
        self._quotes: dict[str, GatewayQuote] = {}
        self._token_ttl_ms = 0

    # ------------------------------------------------------------------ lifecycle
    async def connect(self) -> None:
        if not await self.client.ping():
            raise VenueUnavailable("gateway not reachable")
        t0 = time.perf_counter()
        try:
            await self.client.chain_status(self.spec.gateway_chain, self.network)
        except GatewayError as exc:
            self.last_error = str(exc)
            raise VenueUnavailable(f"gateway chain status failed: {exc}") from exc
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        await self.load_markets()
        self.connected = True
        self.trading_enabled = bool(self.wallet_address)
        self.last_error = None

    async def close(self) -> None:
        self.connected = False

    async def load_markets(self) -> dict[str, MarketInfo]:
        if now_ms() > self._token_ttl_ms:
            try:
                tokens = await self.client.tokens(self.spec.gateway_chain, self.network)
            except GatewayError as exc:
                self.health_tracker.record_error()
                raise VenueUnavailable(f"token list failed: {exc}") from exc
            self._tokens = {t["symbol"].upper(): t for t in tokens if t.get("symbol")}
            self._token_ttl_ms = now_ms() + 3_600_000
        out: dict[str, MarketInfo] = {}
        for pair in self.pairs:
            base, quote = pair.split("/")
            if base in self._tokens and quote in self._tokens:
                bd = int(self._tokens[base].get("decimals") or 8)
                out[pair] = MarketInfo(
                    venue=self.name,
                    symbol=pair,
                    base=base,
                    quote=quote,
                    kind=VenueKind.DEX,
                    amount_step=Decimal(1).scaleb(-min(bd, 9)),
                    price_step=Decimal("0.00000001"),
                    taker_fee_pct=self.spec.typical_fee_pct,
                    chain=self.chain,
                    extra={"base_address": self._tokens[base].get("address"), "quote_address": self._tokens[quote].get("address")},
                )
        self.markets = out
        return out

    # ------------------------------------------------------------------ quotes
    async def get_quote(self, symbol: str, side: OrderSide, base_amount: Decimal, *, order_book: OrderBook | None = None) -> ExecutionQuote:
        base, quote = symbol.split("/")
        t0 = time.perf_counter()
        try:
            gq = await self.client.quote_swap(
                self.spec.connector,
                self.spec.trading_type,
                self.network,
                base,
                quote,
                base_amount,
                side.value,
                self.slippage_tolerance_pct,
                wallet_address=self.wallet_address,
            )
        except GatewayError as exc:
            self.health_tracker.record_error()
            if exc.code == "NO_ROUTE_FOUND":
                raise OrderRejected(f"no route on {self.name} for {symbol}") from exc
            if exc.code == "RATE_LIMITED" or exc.status == 429:
                self.health_tracker.rate_limited_until = time.monotonic() + 30
            raise VenueUnavailable(f"quote failed on {self.name}: {exc}") from exc
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        self.health_tracker.last_market_data_ms = now_ms()
        if gq.quote_id:
            self._quotes[gq.quote_id] = gq
            self._prune_quotes()
        impact = max(ZERO, gq.price_impact_pct)
        if side is OrderSide.BUY:
            quote_amount = gq.amount_in  # quote spent for `amount` base
            filled_base = gq.amount_out if gq.amount_out > 0 else base_amount
            avg = quote_amount / filled_base if filled_base > 0 else ZERO
            reference = avg / (1 + impact / HUNDRED) if avg > 0 else ZERO
            min_received = None
            max_in = gq.max_amount_in
        else:
            quote_amount = gq.amount_out
            filled_base = gq.amount_in if gq.amount_in > 0 else base_amount
            avg = quote_amount / filled_base if filled_base > 0 else ZERO
            reference = avg / (1 - impact / HUNDRED) if avg > 0 and impact < HUNDRED else avg
            min_received = gq.min_amount_out
            max_in = None
        fully = abs(filled_base - base_amount) <= base_amount * Decimal("0.001") and avg > 0
        return ExecutionQuote(
            venue=self.name,
            kind=VenueKind.DEX,
            symbol=symbol,
            side=side,
            base_amount=base_amount,
            quote_amount=quote_amount,
            avg_price=avg,
            reference_price=reference,
            slippage_pct=ZERO,
            price_impact_pct=impact,
            fully_fillable=fully,
            market_ts_ms=gq.fetched_at_ms,
            quote_ts_ms=gq.fetched_at_ms,
            fee_pct=self.spec.typical_fee_pct,
            fee_source="embedded_in_quote",
            gas_limit=gq.gas_estimate or self.spec.gas_limit,
            min_received=min_received,
            route=f"{self.display_name} {self.spec.trading_type} on {self.network}" + (" (approx. exact-out)" if gq.approximation else ""),
            quote_id=gq.quote_id,
            chain=self.chain,
            extra={"max_amount_in": str(max_in) if max_in is not None else None, "token_in": gq.token_in, "token_out": gq.token_out},
        )

    def _prune_quotes(self) -> None:
        cutoff = now_ms() - 60_000
        for k in [k for k, v in self._quotes.items() if v.fetched_at_ms < cutoff]:
            self._quotes.pop(k, None)

    async def fetch_fees(self, symbol: str) -> FeeSchedule:
        return FeeSchedule(self.spec.typical_fee_pct, None, "embedded_in_quote")

    # ------------------------------------------------------------------ account
    async def fetch_balances(self) -> dict[str, Balance]:
        if not self.wallet_address:
            return {}
        t0 = time.perf_counter()
        try:
            bal = await self.client.balances(self.spec.gateway_chain, self.network, self.wallet_address)
        except GatewayError as exc:
            self.health_tracker.record_error()
            raise VenueUnavailable(f"balances failed: {exc}") from exc
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        self.health_tracker.last_balance_ms = now_ms()
        return {k: Balance(asset=k, free=v) for k, v in bal.items() if v > 0}

    async def place_order(self, req: OrderRequest) -> OrderResult:
        if not self.wallet_address:
            raise ConnectorError(f"{self.name}: no bot wallet configured")
        base, quote = req.symbol.split("/")
        result = OrderResult(request=req, order_id=req.client_order_id, status=OrderStatus.NEW)
        t0 = time.perf_counter()
        try:
            if req.quote_id and self.spec.trading_type == "router":
                gq = self._quotes.get(req.quote_id)
                if gq is None or now_ms() - gq.fetched_at_ms > QUOTE_MAX_AGE_MS:
                    raise QuoteExpired(f"quote {req.quote_id} is stale or unknown - requote required")
                resp = await self.client.execute_quote(self.spec.connector, self.network, self.wallet_address, req.quote_id)
            else:
                resp = await self.client.execute_swap(
                    self.spec.connector, self.spec.trading_type, self.network, self.wallet_address, base, quote, req.amount, req.side.value, self.slippage_tolerance_pct
                )
        except QuoteExpired:
            raise
        except GatewayError as exc:
            self.health_tracker.record_error()
            self.health_tracker.record_order(False)
            result.completed_at_ms = now_ms()
            result.error = f"{exc.code or exc.status}: {exc}"
            if exc.code == "TRANSACTION_TIMEOUT":
                # the tx may still land: report as pending so reconciliation resolves it
                result.status = OrderStatus.OPEN
                sig = _extract_signature(str(exc))
                result.tx_hash = sig
                if sig:
                    return await self._await_confirmation(result, sig, req)
                raise VenueUnavailable(str(exc)) from exc
            result.status = OrderStatus.FAILED if exc.code in ("SIMULATION_FAILED", "SLIPPAGE_EXCEEDED", "INSUFFICIENT_BALANCE") else OrderStatus.REJECTED
            return result
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        sig = str(resp.get("signature") or "")
        status = int(resp.get("status") if resp.get("status") is not None else 0)
        result.tx_hash = sig
        result.raw["response"] = {k: v for k, v in resp.items() if k != "data"}
        if status == 1:
            self._apply_confirmed(result, resp.get("data") or {}, req)
        elif status == -1:
            result.status = OrderStatus.FAILED
            result.error = "transaction failed on-chain"
            self.health_tracker.record_order(False)
        else:
            return await self._await_confirmation(result, sig, req)
        result.completed_at_ms = now_ms()
        return result

    async def _await_confirmation(self, result: OrderResult, signature: str, req: OrderRequest) -> OrderResult:
        deadline = time.monotonic() + max(15.0, (req.deadline_ms or 30_000) / 1000)
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            try:
                p = await self.client.poll(self.spec.gateway_chain, self.network, signature)
            except GatewayError as exc:
                log.warning("poll failed", venue=self.name, error=str(exc))
                continue
            st = int(p.get("txStatus") if p.get("txStatus") is not None else 0)
            if st == 1:
                data = p.get("txData") or {}
                self._apply_confirmed(result, {"fee": p.get("fee")}, req, tx_data=data)
                result.completed_at_ms = now_ms()
                return result
            if st == -1:
                result.status = OrderStatus.FAILED
                result.error = str(p.get("error") or "transaction failed")
                result.completed_at_ms = now_ms()
                self.health_tracker.record_order(False)
                return result
        result.status = OrderStatus.OPEN  # unresolved: reconciliation must settle it
        result.error = "confirmation timeout - transaction still pending"
        return result

    def _apply_confirmed(self, result: OrderResult, data: dict, req: OrderRequest, tx_data: dict | None = None) -> None:
        base_change = D(data.get("baseTokenBalanceChange") or 0)
        quote_change = D(data.get("quoteTokenBalanceChange") or 0)
        if base_change == 0 and quote_change == 0:
            # execute-quote on EVM may only report amountIn/amountOut
            ai, ao = D(data.get("amountIn") or 0), D(data.get("amountOut") or 0)
            if req.side is OrderSide.BUY:
                base_change, quote_change = ao, -ai
            else:
                base_change, quote_change = -ai, ao
        filled = abs(base_change) if base_change else req.amount
        quote_amt = abs(quote_change)
        price = quote_amt / filled if filled > 0 and quote_amt > 0 else ZERO
        fee_native = D(data.get("fee") or 0)
        native_px = self.native_usd_price(self.chain.native_token) if self.chain else None
        gas_usd = fee_native * native_px if (native_px is not None and fee_native) else ZERO
        result.filled = filled
        result.avg_price = price
        result.gas_cost_usd = gas_usd
        result.fills.append(
            Fill(
                order_id=result.order_id,
                venue=self.name,
                symbol=req.symbol,
                side=req.side,
                amount=filled,
                price=price,
                fee_amount=ZERO,  # DEX pool fee is embedded in the executed price
                fee_asset="",
                tx_hash=result.tx_hash,
                gas_cost_usd=gas_usd,
            )
        )
        result.raw["gas_native"] = str(fee_native)
        result.raw["gas_usd_unpriced"] = native_px is None and fee_native > 0
        result.status = OrderStatus.FILLED
        self.health_tracker.record_order(True)

    # ------------------------------------------------------------------ health
    async def health_check(self) -> HealthReport:
        t0 = time.perf_counter()
        try:
            await self.client.chain_status(self.spec.gateway_chain, self.network)
            self.health_tracker.last_market_data_ms = max(self.health_tracker.last_market_data_ms, now_ms() - 5_000)
        except GatewayError as exc:
            self.health_tracker.record_error()
            self.last_error = str(exc)[:200]
            rep = self.health_tracker.classify(self.name)
            rep.health = VenueHealth.UNHEALTHY
            rep.reasons.append(f"gateway/RPC error: {exc}")
            return rep
        finally:
            self.health_tracker.record_latency((time.perf_counter() - t0) * 1000)
        return self.health_tracker.classify(self.name, market_data_max_age_ms=60_000)

    def status_dict(self) -> dict:
        d = super().status_dict()
        d.update({"network": self.network, "connector": self.spec.connector, "trading_type": self.spec.trading_type, "wallet": self.wallet_address, "maintained": self.spec.maintained})
        return d


def _extract_signature(msg: str) -> str | None:
    import re

    m = re.search(r"\b([1-9A-HJ-NP-Za-km-z]{80,90}|0x[0-9a-fA-F]{64})\b", msg)
    return m.group(1) if m else None
