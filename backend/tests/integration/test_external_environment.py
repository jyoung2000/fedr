"""Checks that need systems this machine may not have. Each skips with EXTERNAL ENVIRONMENT REQUIRED
and names the enabling variables. When enabled they perform the real check - never a placeholder pass."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from decimal import Decimal

import httpx
import pytest

from tests.integration.conftest import env_flag, external

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# --------------------------------------------------------------------------- Docker stack
def test_docker_stack_healthy_and_gateway_not_published():
    if not env_flag("FEDR_IT_DOCKER"):
        external("running compose stack (set FEDR_IT_DOCKER=1 after `docker compose up -d`)")
    if not shutil.which("docker"):
        external("docker CLI not installed")
    out = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    rows = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
    app = [r for r in rows if r.get("Service") == "app"]
    assert app and app[0].get("State") == "running", rows
    url = os.environ.get("FEDR_IT_URL", "http://127.0.0.1:8935")
    r = httpx.get(url + "/health", timeout=10)
    assert r.status_code in (200, 503) and r.json().get("status")
    assert httpx.get(url + "/health/live", timeout=10).status_code == 200
    gw = [r for r in rows if r.get("Service") == "gateway"]
    if gw:
        ports = subprocess.run(
            ["docker", "port", gw[0]["Name"]], capture_output=True, text=True, timeout=30
        ).stdout.strip()
        assert ports == "", f"gateway must not publish host ports: {ports}"


# --------------------------------------------------------------------------- live market data (public)
def test_public_market_data_passes_quality_gate():
    if not env_flag("FEDR_IT_NETWORK"):
        external("internet egress to exchange public APIs (set FEDR_IT_NETWORK=1)")
    import asyncio

    import ccxt.async_support as ccxt

    from fedr.marketdata.quality import MarketDataQuality

    async def run():
        ex = ccxt.binance({"enableRateLimit": True})
        try:
            ob = await ex.fetch_order_book("BTC/USDT", 20)
        finally:
            await ex.close()
        from fedr.core.models import OrderBook, OrderBookLevel

        book = OrderBook(
            venue="binance",
            symbol="BTC/USDT",
            bids=[OrderBookLevel(price=Decimal(str(p)), amount=Decimal(str(a))) for p, a in ob["bids"]],
            asks=[OrderBookLevel(price=Decimal(str(p)), amount=Decimal(str(a))) for p, a in ob["asks"]],
            ts_ms=ob.get("timestamp") or 0,
            source="rest",
        )
        return MarketDataQuality().validate(book, [])

    assert asyncio.run(run()) is None


# --------------------------------------------------------------------------- CEX sandbox (keys)
def test_cex_sandbox_balance_and_order_round_trip():
    ex_id = os.environ.get("FEDR_IT_CCXT_EXCHANGE")
    if not (ex_id and os.environ.get("FEDR_IT_CCXT_KEY") and os.environ.get("FEDR_IT_CCXT_SECRET")):
        external(
            "exchange SANDBOX API keys (FEDR_IT_CCXT_EXCHANGE, FEDR_IT_CCXT_KEY, FEDR_IT_CCXT_SECRET, FEDR_IT_CCXT_SANDBOX=1)"
        )
    if not env_flag("FEDR_IT_CCXT_SANDBOX"):
        pytest.fail(
            "refusing to run order tests without FEDR_IT_CCXT_SANDBOX=1 (never against a live account)"
        )
    import asyncio

    from fedr.connectors.cex.ccxt_connector import CcxtConnector

    async def run():
        c = CcxtConnector(
            ex_id,
            credentials={
                "apiKey": os.environ["FEDR_IT_CCXT_KEY"],
                "secret": os.environ["FEDR_IT_CCXT_SECRET"],
                "password": os.environ.get("FEDR_IT_CCXT_PASSPHRASE", ""),
            },
            testnet=True,
        )
        await c.connect()
        try:
            bals = await c.fetch_balances()
            assert isinstance(bals, dict)
            sym = os.environ.get("FEDR_IT_CCXT_SYMBOL", "BTC/USDT")
            ob = await c.fetch_order_book(sym, 5)
            assert ob.best_bid and ob.best_ask
            # far-from-market limit order that cannot fill, then cancel: proves auth + order plumbing
            from fedr.core.enums import OrderSide
            from fedr.core.models import OrderRequest

            req = OrderRequest(
                venue=c.name,
                symbol=sym,
                side=OrderSide.BUY,
                amount=Decimal("0.001"),
                limit_price=ob.best_bid * Decimal("0.5"),
                time_in_force="GTC",
            )
            res = await c.place_order(req)
            assert res.order_id
            await c.cancel_order(res.order_id, sym)
        finally:
            await c.close()

    asyncio.run(run())


# --------------------------------------------------------------------------- Gateway
def test_gateway_reachable_and_chains_listed():
    url = os.environ.get("FEDR_IT_GATEWAY_URL")
    if not url:
        external("a running Hummingbot Gateway (set FEDR_IT_GATEWAY_URL, e.g. http://127.0.0.1:15888)")
    import asyncio

    from fedr.connectors.dex.gateway_client import GatewayClient

    async def run():
        g = GatewayClient(url, os.environ.get("FEDR_IT_GATEWAY_API_KEY"), timeout_s=10)
        assert await g.ping()
        chains = await g.chains()
        assert chains
        await g.close()

    asyncio.run(run())


# --------------------------------------------------------------------------- testnet
def test_testnet_rpc_and_funded_wallet():
    if not env_flag("FEDR_IT_TESTNET"):
        external(
            "testnet RPC + funded test wallet (FEDR_IT_TESTNET=1, FEDR_RPC_SEPOLIA, FEDR_IT_TESTNET_ADDRESS)"
        )
    rpc = os.environ.get("FEDR_RPC_SEPOLIA")
    addr = os.environ.get("FEDR_IT_TESTNET_ADDRESS")
    assert rpc and addr, "FEDR_RPC_SEPOLIA and FEDR_IT_TESTNET_ADDRESS required"
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
    assert w3.eth.chain_id == 11155111
    assert w3.eth.get_balance(Web3.to_checksum_address(addr)) > 0, "test wallet must be funded"
