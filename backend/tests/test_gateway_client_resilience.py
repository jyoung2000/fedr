"""Gateway client: token-bucket throttle, exponential backoff on transient errors for idempotent GETs only,
and no automatic retry of POSTs (a swap must never be sent twice)."""

from __future__ import annotations

import asyncio

import httpx

from fedr.connectors.dex.gateway_client import GatewayClient, GatewayError


def _client(handler):
    g = GatewayClient("http://gateway.test:15888", timeout_s=2)
    g._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://gateway.test:15888"
    )
    return g


def test_get_retries_transient_errors_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"message": "starting"})
        return httpx.Response(200, json={"status": "ok"})

    async def run():
        g = _client(handler)
        assert await g.ping() is True
        assert calls["n"] == 3 and g.retries == 2

    asyncio.run(run())


def test_get_gives_up_after_max_retries_and_non_transient_is_immediate():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, json={"message": "slow down"})

    async def run():
        g = _client(handler)
        try:
            await g._request("GET", "/config/chains")
        except GatewayError as exc:
            assert exc.status == 429
        assert calls["n"] == GatewayClient.MAX_RETRIES
        calls["n"] = 0

        def bad(req):
            calls["n"] += 1
            return httpx.Response(400, json={"message": "bad request"})

        g2 = _client(bad)
        try:
            await g2._request("GET", "/config/chains")
        except GatewayError as exc:
            assert exc.status == 400
        assert calls["n"] == 1  # not retried

    asyncio.run(run())


def test_post_is_never_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503, json={"message": "node timeout"})

    async def run():
        g = _client(handler)
        try:
            await g._request("POST", "/connectors/jupiter/execute-swap", json={"quoteId": "x"})
        except GatewayError as exc:
            assert exc.status == 503
        assert calls["n"] == 1

    asyncio.run(run())


def test_throttle_spaces_bursts():
    def handler(req):
        return httpx.Response(200, json={"status": "ok"})

    async def run():
        g = _client(handler)
        g.BURST = 3.0
        g.RATE_PER_SECOND = 50.0
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        for _ in range(6):
            await g._request("GET", "/")
        elapsed = loop.time() - t0
        assert elapsed >= 1 / 50.0  # requests beyond the burst had to wait for tokens

    asyncio.run(run())
