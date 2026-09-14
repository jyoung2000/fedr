"""Offline tests for connector internals that cannot be exercised against live venues here."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from ccxt.base.errors import (
    AuthenticationError,
    ExchangeNotAvailable,
    InsufficientFunds,
    InvalidOrder,
    NotSupported,
    OnMaintenance,
    RateLimitExceeded,
)

from fedr.connectors.base import ConnectorError, InsufficientBalance, NotSupportedError, OrderRejected, VenueUnavailable
from fedr.connectors.cex.ccxt_connector import CcxtConnector, _map_error
from fedr.connectors.cex.registry import EXCHANGES
from fedr.connectors.dex.gateway_client import GatewayClient, GatewayError
from fedr.connectors.dex.gateway_connector import GatewayConnector
from fedr.connectors.dex.registry import DEXES
from fedr.core.enums import OrderSide, OrderStatus, TradingMode
from fedr.core.models import MarketInfo, OrderRequest, OrderResult
from fedr.core.enums import VenueKind

D = Decimal


@pytest.mark.parametrize(
    "exc,cls",
    [
        (RateLimitExceeded("slow down"), VenueUnavailable),
        (OnMaintenance("maint"), VenueUnavailable),
        (ExchangeNotAvailable("503"), VenueUnavailable),
        (InsufficientFunds("no"), InsufficientBalance),
        (InvalidOrder("bad"), OrderRejected),
        (AuthenticationError("key"), ConnectorError),
        (NotSupported("x"), NotSupportedError),
    ],
)
def test_ccxt_error_mapping(exc, cls):
    assert isinstance(_map_error(exc), cls)


def test_registry_ids_exist_in_ccxt():
    import ccxt

    for spec in EXCHANGES.values():
        assert hasattr(ccxt, spec.id), spec.id
        if spec.perp_id:
            assert hasattr(ccxt, spec.perp_id)
        # sandbox flag must match ccxt's urls['test'] presence (honesty check)
        ex = getattr(ccxt, spec.id)()
        assert bool(ex.urls.get("test")) == spec.sandbox, f"{spec.id}: sandbox flag mismatch"


def test_ccxt_connector_builds_every_registry_exchange_offline():
    async def run():
        for spec in EXCHANGES.values():
            c = CcxtConnector(spec.id, TradingMode.PAPER)
            ex = c._build()
            assert ex.has.get("fetchOrderBook")
            await ex.close()

    asyncio.run(run())


def _connector_with_market() -> CcxtConnector:
    c = CcxtConnector("kraken", TradingMode.PAPER)
    c.exchange = c._build()
    c.markets["SOL/USDC"] = MarketInfo(venue="kraken", symbol="SOL/USDC", base="SOL", quote="USDC", kind=VenueKind.CEX, amount_step=D("0.001"), price_step=D("0.01"), min_amount=D("0.1"), min_cost=D("5"), taker_fee_pct=D("0.26"), maker_fee_pct=D("0.16"))
    return c


def test_order_status_mapping_and_fee_conversion():
    c = _connector_with_market()
    req = OrderRequest(venue="kraken", symbol="SOL/USDC", side=OrderSide.BUY, amount=D("2"), limit_price=D("150"))
    res = OrderResult(request=req, order_id="1", status=OrderStatus.NEW)
    c._apply_order(res, {"id": "1", "status": "open", "filled": 0.5, "remaining": 1.5, "average": 149.9})
    assert res.status is OrderStatus.PARTIALLY_FILLED and res.filled == D("0.5")
    c._apply_order(res, {"id": "1", "status": "canceled", "filled": 0.5, "remaining": 1.5})
    assert res.status is OrderStatus.PARTIALLY_FILLED  # cancelled after a partial fill keeps the fill
    res2 = OrderResult(request=req, order_id="2", status=OrderStatus.NEW)
    c._apply_order(res2, {"id": "2", "status": "closed", "filled": 2, "remaining": 0, "average": 150.1, "trades": [{"id": "t1", "amount": 2, "price": 150.1, "fee": {"cost": 0.78, "currency": "USDC"}, "timestamp": 1}]})
    assert res2.status is OrderStatus.FILLED and res2.fills[0].fee_asset == "USDC" and res2.fee_quote == D("0.78")
    # fee in base asset converts at fill price; fee in a third asset falls back to the schedule and is flagged
    assert c._fee_to_quote(D("0.01"), "SOL", res2, D("150")) == D("1.5")
    assert c._fee_to_quote(D("0.001"), "KFEE", res2, D("150")) == res2.quote_amount * D("0.26") / 100
    assert res2.raw["fee_asset_unconverted"] == "KFEE"
    asyncio.run(c.exchange.close())


def test_quantize_rounds_down_and_enforces_minimums():
    c = _connector_with_market()
    amt, px = c._quantize("SOL/USDC", D("1.23456"), D("150.123456"))
    assert amt == D("1.234") and px == D("150.12")
    with pytest.raises(OrderRejected):
        c._quantize("SOL/USDC", D("0.05"), D("150"))
    with pytest.raises(OrderRejected):
        c._quantize("SOL/USDC", D("0.1"), D("10"))  # cost below minimum
    asyncio.run(c.exchange.close())


def test_orderbook_limit_snaps_to_exchange_allowed_values():
    c = CcxtConnector("kraken", TradingMode.PAPER)
    assert c._book_limit(50) == 100 and c._book_limit(5) == 10 and c._book_limit(9999) == 500
    b = CcxtConnector("binance", TradingMode.PAPER)
    assert b._book_limit(50) == 50 and b._book_limit(60) == 100


# --------------------------------------------------------------------------- Gateway (mock HTTP)


def _gateway(handler) -> GatewayClient:
    client = GatewayClient("http://gateway:15888")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gateway:15888")
    return client


def test_gateway_quote_parsing_and_errors():
    async def run():
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path, dict(request.url.params)))
            if request.url.path == "/":
                return httpx.Response(200, json={"status": "ok"})
            if request.url.path.endswith("/router/quote-swap"):
                return httpx.Response(200, json={"quoteId": "q1", "tokenIn": "USDC", "tokenOut": "SOL", "amountIn": "301.5", "amountOut": "2", "price": "150.75", "priceImpactPct": "0.12", "minAmountOut": "1.99", "maxAmountIn": "304.5"})
            if request.url.path.endswith("/router/execute-quote"):
                return httpx.Response(400, json={"statusCode": 400, "error": "Bad Request", "message": "Slippage tolerance exceeded", "code": "SLIPPAGE_EXCEEDED"})
            if request.url.path.endswith("/estimate-gas"):
                return httpx.Response(200, json={"feePerComputeUnit": 0.1, "denomination": "lamports", "computeUnits": 200000, "feeAsset": "SOL", "fee": 0.000025})
            if request.url.path == "/tokens/":
                return httpx.Response(200, json={"tokens": [{"symbol": "SOL", "address": "So111", "decimals": 9}, {"symbol": "USDC", "address": "EPjF", "decimals": 6}]})
            if request.url.path.endswith("/status"):
                return httpx.Response(200, json={"chain": "solana", "network": "mainnet-beta", "currentBlockNumber": 1})
            return httpx.Response(404, json={"message": "not found"})

        gw = _gateway(handler)
        assert await gw.ping()
        q = await gw.quote_swap("jupiter", "router", "mainnet-beta", "SOL", "USDC", D("2"), "buy", D("1"))
        assert q.quote_id == "q1" and q.amount_in == D("301.5") and q.price_impact_pct == D("0.12")
        assert calls[-1][2]["side"] == "BUY" and calls[-1][2]["amount"] == "2"
        gas = await gw.estimate_gas("solana", "mainnet-beta")
        assert gas.fee_per_compute_unit == D("0.1") and gas.compute_units == 200000
        with pytest.raises(GatewayError) as ei:
            await gw.execute_quote("jupiter", "mainnet-beta", "wallet", "q1")
        assert ei.value.code == "SLIPPAGE_EXCEEDED" and not ei.value.retryable
        # connector on top of the client
        spec = DEXES["jupiter"]
        c = GatewayConnector(spec, gw, TradingMode.PAPER, None, ["SOL/USDC", "BTC/USDC"], D("1"))
        await c.connect()
        assert "SOL/USDC" in c.markets and "BTC/USDC" not in c.markets  # BTC not in the token list
        eq = await c.get_quote("SOL/USDC", OrderSide.BUY, D("2"))
        assert eq.kind is VenueKind.DEX and eq.quote_amount == D("301.5") and eq.avg_price == D("150.75")
        assert eq.reference_price < eq.avg_price  # impact removed from the effective price
        assert eq.quote_id == "q1" and eq.fully_fillable
        # executing a stale/unknown quote id must fail closed
        req = OrderRequest(venue="jupiter", symbol="SOL/USDC", side=OrderSide.BUY, amount=D("2"), limit_price=D("151"), quote_id="unknown")
        c.wallet_address = "wallet"
        from fedr.connectors.base import QuoteExpired

        with pytest.raises(QuoteExpired):
            await c.place_order(req)
        req.quote_id = "q1"
        res = await c.place_order(req)
        assert res.status is OrderStatus.FAILED and "SLIPPAGE_EXCEEDED" in (res.error or "")
        await gw.close()

    asyncio.run(run())


def test_gateway_unreachable_is_venue_unavailable():
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        gw = _gateway(handler)
        assert not await gw.ping()
        c = GatewayConnector(DEXES["uniswap-base"], gw, TradingMode.PAPER, None, ["ETH/USDC"], D("1"))
        with pytest.raises(VenueUnavailable):
            await c.connect()
        assert not c.connected
        await gw.close()

    asyncio.run(run())
