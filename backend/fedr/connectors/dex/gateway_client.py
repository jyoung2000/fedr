"""Async REST client for Hummingbot Gateway (v2.16.x API).

Route conventions verified against the Gateway source (main @ 2.16.0):
GET routes take query params, POST routes JSON bodies; ``amount`` is always in
``baseToken``; ``side`` is ``SELL``/``BUY``; ``slippagePct`` is a percentage.
Router quotes return a ``quoteId`` that ``execute-quote`` consumes.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from fedr.core.logging import get_logger
from fedr.core.models import now_ms
from fedr.core.money import D

log = get_logger("gateway")


class GatewayError(Exception):
    def __init__(self, message: str, status: int = 0, code: str | None = None, payload: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload or {}

    @property
    def retryable(self) -> bool:
        # Per Gateway semantics only a transaction timeout is safely retryable after checking the signature.
        return self.code in ("TRANSACTION_TIMEOUT",) or self.status in (502, 503)


@dataclass(slots=True)
class GatewayQuote:
    connector: str
    network: str
    base: str
    quote: str
    side: str
    amount: Decimal
    quote_id: str | None
    token_in: str
    token_out: str
    amount_in: Decimal
    amount_out: Decimal
    price: Decimal
    price_impact_pct: Decimal
    min_amount_out: Decimal | None
    max_amount_in: Decimal | None
    gas_estimate: int | None
    approximation: bool
    fetched_at_ms: int = field(default_factory=now_ms)
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class GatewayGas:
    chain: str
    network: str
    fee_per_compute_unit: Decimal  # EVM gwei / Solana lamports per CU
    denomination: str
    compute_units: int
    fee_asset: str
    fee_native: Decimal  # estimated fee for a default tx in native units
    gas_type: str | None
    max_priority_fee: Decimal | None
    fetched_at_ms: int = field(default_factory=now_ms)


class GatewayClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout_s: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout_s)
        self.retries = 0
        self.last_latency_ms: float | None = None

    async def close(self) -> None:
        await self._client.aclose()

    # Rate limiting + backoff. Gateway is a single local process: keep it well under its own limits and
    # never hammer it during an incident. Only *idempotent* GETs are retried; a POST (swap, wallet add)
    # is sent exactly once - a timed-out swap must be resolved by polling the transaction, not by resending.
    RATE_PER_SECOND = 20.0
    BURST = 20.0
    RETRY_STATUSES = (429, 502, 503, 504)
    MAX_RETRIES = 3

    async def _throttle(self) -> None:
        now = time.monotonic()
        if not hasattr(self, "_bucket"):
            self._bucket, self._bucket_ts = self.BURST, now
        self._bucket = min(self.BURST, self._bucket + (now - self._bucket_ts) * self.RATE_PER_SECOND)
        self._bucket_ts = now
        if self._bucket < 1:
            wait = (1 - self._bucket) / self.RATE_PER_SECOND
            await asyncio.sleep(wait)
            self._bucket = 0.0
        else:
            self._bucket -= 1

    async def _request(
        self, method: str, path: str, *, params: dict | None = None, json: dict | None = None
    ) -> Any:
        attempts = self.MAX_RETRIES if method.upper() == "GET" else 1
        delay = 0.25
        last: GatewayError | None = None
        for attempt in range(attempts):
            await self._throttle()
            try:
                return await self._request_once(method, path, params=params, json=json)
            except GatewayError as exc:
                last = exc
                transient = (
                    exc.status in self.RETRY_STATUSES or exc.status == 503 and "unreachable" in str(exc)
                )
                if attempt + 1 >= attempts or not transient:
                    raise
                self.retries += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, 2.0)
        raise last  # pragma: no cover

    async def _request_once(
        self, method: str, path: str, *, params: dict | None = None, json: dict | None = None
    ) -> Any:
        t0 = time.perf_counter()
        try:
            r = await self._client.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            raise GatewayError(f"gateway unreachable: {exc}", status=503) from exc
        finally:
            self.last_latency_ms = (time.perf_counter() - t0) * 1000
        if r.status_code >= 400:
            try:
                body = r.json()
            except Exception:
                body = {"message": r.text}
            raise GatewayError(
                str(body.get("message") or body.get("error") or r.text)[:500],
                status=r.status_code,
                code=body.get("code"),
                payload=body,
            )
        if not r.content:
            return None
        return r.json()

    # ---- system --------------------------------------------------------------------
    async def ping(self) -> bool:
        try:
            data = await self._request("GET", "/")
            return bool(data and data.get("status") == "ok")
        except GatewayError:
            return False

    async def connectors(self) -> list[dict]:
        data = await self._request("GET", "/config/connectors")
        return list((data or {}).get("connectors", []))

    async def chains(self) -> list[dict]:
        data = await self._request("GET", "/config/chains")
        return list((data or {}).get("chains", []))

    # ---- chain ---------------------------------------------------------------------
    async def chain_status(self, chain: str, network: str) -> dict:
        return await self._request("GET", f"/chains/{chain}/status", params={"network": network}) or {}

    async def estimate_gas(self, chain: str, network: str) -> GatewayGas:
        d = await self._request("GET", f"/chains/{chain}/estimate-gas", params={"network": network}) or {}
        return GatewayGas(
            chain=chain,
            network=network,
            fee_per_compute_unit=D(d.get("feePerComputeUnit") or 0),
            denomination=str(d.get("denomination") or ""),
            compute_units=int(d.get("computeUnits") or 0),
            fee_asset=str(d.get("feeAsset") or ""),
            fee_native=D(d.get("fee") or 0),
            gas_type=d.get("gasType"),
            max_priority_fee=D(d["maxPriorityFeePerGas"])
            if d.get("maxPriorityFeePerGas") is not None
            else None,
        )

    async def balances(
        self, chain: str, network: str, address: str, tokens: list[str] | None = None
    ) -> dict[str, Decimal]:
        body: dict[str, Any] = {"network": network, "address": address}
        if tokens:
            body["tokens"] = tokens
        d = await self._request("POST", f"/chains/{chain}/balances", json=body) or {}
        return {k: D(v) for k, v in (d.get("balances") or {}).items()}

    async def poll(self, chain: str, network: str, signature: str) -> dict:
        return (
            await self._request(
                "POST", f"/chains/{chain}/poll", json={"network": network, "signature": signature}
            )
            or {}
        )

    async def allowances(
        self, network: str, address: str, spender: str, tokens: list[str]
    ) -> dict[str, Decimal]:
        d = (
            await self._request(
                "POST",
                "/chains/ethereum/allowances",
                json={"network": network, "address": address, "spender": spender, "tokens": tokens},
            )
            or {}
        )
        return {k: D(v) for k, v in (d.get("approvals") or {}).items()}

    async def approve(
        self, network: str, address: str, spender: str, token: str, amount: str | None = None
    ) -> dict:
        body: dict[str, Any] = {"network": network, "address": address, "spender": spender, "token": token}
        if amount is not None:
            body["amount"] = amount
        return await self._request("POST", "/chains/ethereum/approve", json=body) or {}

    # ---- tokens --------------------------------------------------------------------
    async def tokens(self, chain: str, network: str, search: str | None = None) -> list[dict]:
        params = {"chain": chain, "network": network}
        if search:
            params["search"] = search
        d = await self._request("GET", "/tokens/", params=params) or {}
        return list(d.get("tokens", []))

    # ---- swaps -----------------------------------------------------------------------
    async def quote_swap(
        self,
        connector: str,
        trading_type: str,
        network: str,
        base: str,
        quote: str,
        amount: Decimal,
        side: str,
        slippage_pct: Decimal,
        *,
        wallet_address: str | None = None,
        pool_address: str | None = None,
    ) -> GatewayQuote:
        params: dict[str, Any] = {
            "network": network,
            "baseToken": base,
            "quoteToken": quote,
            "amount": str(amount),
            "side": side.upper(),
            "slippagePct": str(slippage_pct),
        }
        if wallet_address and trading_type == "router":
            params["walletAddress"] = wallet_address
        if pool_address and trading_type in ("amm", "clmm"):
            params["poolAddress"] = pool_address
        d = (
            await self._request("GET", f"/connectors/{connector}/{trading_type}/quote-swap", params=params)
            or {}
        )
        gas = d.get("gasEstimate")
        return GatewayQuote(
            connector=connector,
            network=network,
            base=base,
            quote=quote,
            side=side.upper(),
            amount=D(amount),
            quote_id=d.get("quoteId"),
            token_in=str(d.get("tokenIn") or ""),
            token_out=str(d.get("tokenOut") or ""),
            amount_in=D(d.get("amountIn") or 0),
            amount_out=D(d.get("amountOut") or 0),
            price=D(d.get("price") or 0),
            price_impact_pct=D(d.get("priceImpactPct") or 0),
            min_amount_out=D(d["minAmountOut"]) if d.get("minAmountOut") is not None else None,
            max_amount_in=D(d["maxAmountIn"]) if d.get("maxAmountIn") is not None else None,
            gas_estimate=int(D(gas)) if gas is not None else None,
            approximation=bool(d.get("approximation")),
            raw=d,
        )

    async def execute_quote(
        self, connector: str, network: str, wallet_address: str, quote_id: str, **extra: Any
    ) -> dict:
        body = {"walletAddress": wallet_address, "network": network, "quoteId": quote_id, **extra}
        return await self._request("POST", f"/connectors/{connector}/router/execute-quote", json=body) or {}

    async def execute_swap(
        self,
        connector: str,
        trading_type: str,
        network: str,
        wallet_address: str,
        base: str,
        quote: str,
        amount: Decimal,
        side: str,
        slippage_pct: Decimal,
        *,
        pool_address: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "walletAddress": wallet_address,
            "network": network,
            "baseToken": base,
            "quoteToken": quote,
            "amount": str(amount),
            "side": side.upper(),
            "slippagePct": str(slippage_pct),
        }
        if pool_address:
            body["poolAddress"] = pool_address
        return (
            await self._request("POST", f"/connectors/{connector}/{trading_type}/execute-swap", json=body)
            or {}
        )

    # ---- wallets ---------------------------------------------------------------------
    async def wallets(self) -> list[dict]:
        return list(await self._request("GET", "/wallet/") or [])

    async def add_wallet(self, chain: str, private_key: str, set_default: bool = True) -> str:
        d = (
            await self._request(
                "POST",
                "/wallet/add",
                json={"chain": chain, "privateKey": private_key, "setDefault": set_default},
            )
            or {}
        )
        return str(d.get("address") or "")

    async def remove_wallet(self, chain: str, address: str) -> None:
        await self._request("DELETE", "/wallet/remove", json={"chain": chain, "address": address})
