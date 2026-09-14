"""API tests against the real FastAPI app in SIMULATION mode with a temporary data directory."""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FEDR_DEFAULT_MODE", "simulation")
    monkeypatch.setenv("FEDR_GATEWAY_ENABLED", "false")
    monkeypatch.setenv("FEDR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FEDR_AUTH_TOKEN", "")
    monkeypatch.setenv("FEDR_DEMO_SEED", "3")
    from fedr.config import env as envmod

    envmod._env = None
    from fedr.main import create_app

    app = create_app()
    return app


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _run(app, coro_fn):
    async def main():
        async with app.router.lifespan_context(app):
            async with await _client(app) as c:
                return await coro_fn(c)

    return asyncio.run(main())


def test_health_status_and_security_headers(api):
    async def t(c):
        r = await c.get("/api/system/health")
        assert r.status_code == 200 and r.json()["ok"] is True
        assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
        s = (await c.get("/api/system/status")).json()
        assert s["mode"] == "simulation" and s["live_activated"] is False and s["emergency_stop"] is False
        assert s["market_data_source"].startswith("synthetic")
        d = (await c.get("/api/dashboard")).json()
        assert d["capital"]["total"] and "best" in d and d["counts"]["routes"] > 0
        assert all(v["health"] in ("healthy", "degraded", "unhealthy", "blocked", "unknown") for v in d["venues"])

    _run(api, t)


def test_settings_patch_profiles_and_live_guard(api):
    async def t(c):
        s = (await c.get("/api/settings")).json()
        assert s["settings"]["general"]["mode"] == "simulation" and "confirmation_phrase_hash" not in s["settings"]["live"]
        r = await c.put("/api/settings", json={"patch": {"trading": {"min_profit_usd": "3.5"}}})
        assert r.status_code == 200 and r.json()["settings"]["trading"]["min_profit_usd"] == "3.5"
        assert r.json()["settings"]["general"]["risk_profile"] == "custom"
        r = await c.post("/api/settings/risk-profile", json={"profile": "balanced"})
        assert r.json()["settings"]["trading"]["max_trade_size_usd"] == "1000"
        # live can never be set through settings or the mode endpoint
        r = await c.put("/api/settings", json={"patch": {"general": {"mode": "live"}}})
        assert r.status_code == 400
        r = await c.post("/api/trading/mode", json={"mode": "live"})
        assert r.status_code == 400
        r = await c.put("/api/settings", json={"patch": {"live": {"activated": True}}})
        assert r.status_code == 400
        r = await c.post("/api/system/live/activate", json={"confirmation": "ACTIVATE LIVE TRADING"})
        assert r.status_code == 400  # env gate off
        ready = (await c.get("/api/system/readiness")).json()
        assert ready["ready"] is False and any(i["key"] == "env_gate" and not i["ok"] for i in ready["items"])

    _run(api, t)


def test_trading_toggles_and_paper_controls(api):
    async def t(c):
        assert (await c.post("/api/trading/bot", json={"enabled": False})).json()["bot_enabled"] is False
        assert (await c.post("/api/trading/shadow", json={"enabled": True})).json()["shadow_mode"] is True
        st = (await c.get("/api/trading/state")).json()
        assert st["bot_enabled"] is False and st["shadow_mode"] is True and st["paper"]["editable"]
        r = await c.post("/api/trading/paper/funds", json={"venue": "kraken", "asset": "USDC", "amount": "100"})
        assert r.status_code == 200
        r = await c.post("/api/trading/paper/funds", json={"venue": "kraken", "asset": "USDC", "amount": "-999999"})
        assert r.status_code == 400
        assert (await c.post("/api/trading/paper/reset")).status_code == 200
        opps = (await c.get("/api/opportunities")).json()
        assert "items" in opps
        if opps["items"]:
            detail = (await c.get(f"/api/opportunities/{opps['items'][0]['id']}")).json()
            assert "costs" in detail and "explanation" in detail and detail["status"] in ("SAFE TO EXECUTE", "BLOCKED")
            r = await c.post(f"/api/opportunities/{opps['items'][0]['id']}/execute")
            assert r.status_code == 400  # shadow mode on

    _run(api, t)


def test_emergency_stop_and_breakers(api):
    async def t(c):
        r = await c.post("/api/system/emergency-stop", json={"reason": "test"})
        assert r.status_code == 200 and "reconciliation" in r.json()
        s = (await c.get("/api/system/status")).json()
        assert s["emergency_stop"] is True and any(b["reason"] == "manual" for b in s["breakers"])
        opps = (await c.get("/api/opportunities")).json()["items"]
        assert all(o["status"] == "BLOCKED" for o in opps)
        assert (await c.post("/api/system/emergency-stop/release")).status_code == 200
        assert (await c.get("/api/system/status")).json()["emergency_stop"] is False
        r = await c.post("/api/system/breakers/reset", json={})
        assert r.status_code == 200
        audit = (await c.get("/api/history/audit?event_type=emergency_stop")).json()["items"]
        assert len(audit) >= 2

    _run(api, t)


def test_wallets_create_backup_deposit(api):
    async def t(c):
        r = await c.post("/api/wallets/bot", json={"family": "evm", "label": "test"})
        assert r.status_code == 200 and r.json()["address"].startswith("0x")
        wid = r.json()["id"]
        r = await c.post("/api/wallets/bot", json={"family": "solana"})
        assert r.status_code == 200 and len(r.json()["address"]) >= 32
        listing = (await c.get("/api/wallets")).json()
        assert len(listing["bot_wallets"]) == 2 and all("key" not in k for w in listing["bot_wallets"] for k in w)
        r = await c.post(f"/api/wallets/bot/{wid}/backup", json={"passphrase": "short"})
        assert r.status_code == 400
        r = await c.post(f"/api/wallets/bot/{wid}/backup", json={"passphrase": "a-strong-passphrase-123"})
        assert r.status_code == 200 and "crypto" in r.json() and "attachment" in r.headers["content-disposition"]
        assert (await c.post(f"/api/wallets/bot/{wid}/confirm-backup")).status_code == 200
        assert (await c.get("/api/wallets")).json()["bot_wallets"][0]["backed_up"] is True
        dep = (await c.get("/api/wallets/deposit?chain=base&asset=USDC")).json()
        assert dep["address"].startswith("0x") and dep["qr_svg"].startswith("<") and "Base" in dep["warning"]
        r = await c.post("/api/wallets/withdraw", json={"chain": "base", "asset": "ETH", "amount": "0.1", "destination": "0x" + "1" * 40, "confirm": True})
        assert r.status_code == 400  # not available in simulation
        r = await c.post("/api/wallets/external", json={"family": "evm", "address": "0x" + "2" * 40, "provider": "metamask"})
        assert r.json()["kind"] == "external"

    _run(api, t)


def test_exchanges_listing_is_honest(api):
    async def t(c):
        ex = (await c.get("/api/exchanges")).json()
        assert ex["gateway_ok"] is False
        kraken = next(e for e in ex["cex"] if e["id"] == "kraken")
        assert kraken["verification"] == "supported_not_verified_live"
        assert kraken["sandbox"] is False
        r = await c.post("/api/exchanges/accounts", json={"exchange_id": "kraken", "mode": "testnet", "api_key": "x", "secret": "y"})
        assert r.status_code == 400 and "sandbox" in r.json()["detail"]
        r = await c.post("/api/exchanges/accounts", json={"exchange_id": "nope", "mode": "live", "api_key": "x", "secret": "y"})
        assert r.status_code == 400

    _run(api, t)


def test_backtest_runs_and_is_labelled(api):
    async def t(c):
        r = await c.post("/api/backtest/run", json={"ticks": 25, "seed": 11, "label": "t"})
        assert r.status_code == 200
        body = r.json()
        assert body["kind"] == "BACKTEST" and "Not a prediction" in body["disclaimer"]
        assert body["results"]["ticks"] == 25
        assert (await c.get("/api/history/backtests")).json()["items"]

    _run(api, t)


def test_auth_required_when_token_set(tmp_path, monkeypatch):
    monkeypatch.setenv("FEDR_DEFAULT_MODE", "simulation")
    monkeypatch.setenv("FEDR_GATEWAY_ENABLED", "false")
    monkeypatch.setenv("FEDR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FEDR_AUTH_TOKEN", "supersecrettoken123")
    from fedr.config import env as envmod

    envmod._env = None
    from fedr.main import create_app

    app = create_app()

    async def t(c):
        assert (await c.get("/api/system/health")).status_code == 200
        assert (await c.get("/api/system/status")).status_code == 401
        assert (await c.get("/api/system/status", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await c.get("/api/system/status", headers={"Authorization": "Bearer supersecrettoken123"})).status_code == 200
        r = await c.post("/api/auth/login", json={"token": "supersecrettoken123"})
        assert r.status_code == 200 and "fedr_session" in r.headers.get("set-cookie", "")
        # cookie without CSRF header is rejected for mutations, accepted for GET
        c.cookies.set("fedr_session", "supersecrettoken123")
        assert (await c.get("/api/system/status")).status_code == 200
        assert (await c.post("/api/trading/bot", json={"enabled": True})).status_code == 403
        assert (await c.post("/api/trading/bot", json={"enabled": True}, headers={"X-Fedr-Request": "1"})).status_code == 200

    _run(app, t)
    envmod._env = None
