"""API tests against the real FastAPI app in SIMULATION mode with a temporary data directory."""

from __future__ import annotations

import asyncio

import httpx
import pytest

TOKEN = "test-token-0123456789abcdef"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FEDR_DEFAULT_MODE", "simulation")
    monkeypatch.setenv("FEDR_GATEWAY_ENABLED", "false")
    monkeypatch.setenv("FEDR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FEDR_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("FEDR_DEMO_SEED", "3")
    from fedr.config import env as envmod

    envmod._env = None
    from fedr.main import create_app

    app = create_app()
    return app


async def _client(app, token: str | None = TOKEN):
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers)


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
        assert (
            r.headers["x-frame-options"] == "DENY"
            and "default-src 'self'" in r.headers["content-security-policy"]
        )
        s = (await c.get("/api/system/status")).json()
        assert s["mode"] == "simulation" and s["live_activated"] is False and s["emergency_stop"] is False
        assert s["market_data_source"].startswith("synthetic")
        d = (await c.get("/api/dashboard")).json()
        assert d["capital"]["total"] and "best" in d and d["counts"]["routes"] > 0
        assert all(
            v["health"] in ("healthy", "degraded", "unhealthy", "blocked", "unknown") for v in d["venues"]
        )

    _run(api, t)


def test_settings_patch_profiles_and_live_guard(api):
    async def t(c):
        s = (await c.get("/api/settings")).json()
        assert (
            s["settings"]["general"]["mode"] == "simulation"
            and "confirmation_phrase_hash" not in s["settings"]["live"]
        )
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
        r = await c.post(
            "/api/trading/paper/funds", json={"venue": "kraken", "asset": "USDC", "amount": "100"}
        )
        assert r.status_code == 200
        r = await c.post(
            "/api/trading/paper/funds", json={"venue": "kraken", "asset": "USDC", "amount": "-999999"}
        )
        assert r.status_code == 400
        assert (await c.post("/api/trading/paper/reset")).status_code == 200
        opps = (await c.get("/api/opportunities")).json()
        assert "items" in opps
        if opps["items"]:
            detail = (await c.get(f"/api/opportunities/{opps['items'][0]['id']}")).json()
            assert (
                "costs" in detail
                and "explanation" in detail
                and detail["status"] in ("SAFE TO EXECUTE", "BLOCKED")
            )
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
        assert len(listing["bot_wallets"]) == 2 and all(
            "key" not in k for w in listing["bot_wallets"] for k in w
        )
        r = await c.post(f"/api/wallets/bot/{wid}/backup", json={"passphrase": "short"})
        assert r.status_code == 400
        r = await c.post(f"/api/wallets/bot/{wid}/backup", json={"passphrase": "a-strong-passphrase-123"})
        assert (
            r.status_code == 200 and "crypto" in r.json() and "attachment" in r.headers["content-disposition"]
        )
        assert (await c.post(f"/api/wallets/bot/{wid}/confirm-backup")).status_code == 200
        assert (await c.get("/api/wallets")).json()["bot_wallets"][0]["backed_up"] is True
        dep = (await c.get("/api/wallets/deposit?chain=base&asset=USDC")).json()
        assert dep["address"].startswith("0x") and dep["qr_svg"].startswith("<") and "Base" in dep["warning"]
        r = await c.post(
            "/api/wallets/withdraw",
            json={
                "chain": "base",
                "asset": "ETH",
                "amount": "0.1",
                "destination": "0x" + "1" * 40,
                "confirm": True,
            },
        )
        assert r.status_code == 400  # not available in simulation
        r = await c.post(
            "/api/wallets/external",
            json={"family": "evm", "address": "0x" + "2" * 40, "provider": "metamask"},
        )
        assert r.json()["kind"] == "external"

    _run(api, t)


def test_exchanges_listing_is_honest(api):
    async def t(c):
        ex = (await c.get("/api/exchanges")).json()
        assert ex["gateway_ok"] is False
        kraken = next(e for e in ex["cex"] if e["id"] == "kraken")
        assert kraken["verification"] == "supported_not_verified_live"
        assert kraken["sandbox"] is False
        r = await c.post(
            "/api/exchanges/accounts",
            json={"exchange_id": "kraken", "mode": "testnet", "api_key": "x", "secret": "y"},
        )
        assert r.status_code == 400 and "sandbox" in r.json()["detail"]
        r = await c.post(
            "/api/exchanges/accounts",
            json={"exchange_id": "nope", "mode": "live", "api_key": "x", "secret": "y"},
        )
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


def test_auth_is_always_required_and_csrf_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("FEDR_DEFAULT_MODE", "simulation")
    monkeypatch.setenv("FEDR_GATEWAY_ENABLED", "false")
    monkeypatch.setenv("FEDR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "FEDR_AUTH_TOKEN", ""
    )  # no token configured -> one is generated into <data>/config/ui-token
    from fedr.config import env as envmod

    envmod._env = None
    from fedr.main import create_app

    app = create_app()

    async def t(c):
        assert (await c.get("/api/system/health")).status_code == 200
        assert (await c.get("/api/system/status")).status_code == 401
        generated = (tmp_path / "config" / "ui-token").read_text().strip()
        assert (
            len(generated) >= 32 and oct((tmp_path / "config" / "ui-token").stat().st_mode & 0o777) == "0o600"
        )
        st = (await c.get("/api/auth/status")).json()
        assert (
            st["auth_required"] is True and st["authenticated"] is False and st["token_source"] == "generated"
        )
        assert (
            await c.get("/api/system/status", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        assert (
            await c.get("/api/system/status", headers={"Authorization": f"Bearer {generated}"})
        ).status_code == 200
        r = await c.post("/api/auth/login", json={"token": generated})
        assert r.status_code == 200 and "fedr_session" in r.headers.get("set-cookie", "")
        c.cookies.set("fedr_session", generated)
        assert (await c.get("/api/system/status")).status_code == 200
        assert (
            await c.post("/api/trading/bot", json={"enabled": True})
        ).status_code == 403  # missing CSRF header
        assert (
            await c.post("/api/trading/bot", json={"enabled": True}, headers={"X-Fedr-Request": "1"})
        ).status_code == 200

    async def main():
        async with app.router.lifespan_context(app):
            async with await _client(app, token=None) as c:
                await t(c)

    asyncio.run(main())
    envmod._env = None


def test_health_endpoints_are_public_and_readiness_reflects_state(api):
    async def t(c):
        c.headers.pop("Authorization", None)
        assert (await c.get("/health/live")).json() == {"status": "ok"}
        h = await c.get("/health")
        assert h.status_code == 200 and h.json()["status"] == "ok" and "token" not in h.text
        r = await c.get("/health/ready")
        assert (
            r.status_code == 200 and r.json()["status"] == "ready" and r.json()["checks"]["database"] is True
        )
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        await c.post("/api/system/emergency-stop", json={"reason": "t"})
        r = await c.get("/health/ready")
        assert r.status_code == 503 and r.json()["checks"]["not_emergency_stopped"] is False
        await c.post("/api/system/emergency-stop/release")
        assert (await c.get("/health/ready")).status_code == 200
        m = (await c.get("/api/system/metrics")).json()
        assert "process" in m and "opportunities_evaluated" in m["process"] and "persisted" in m

    _run(api, t)


def test_startup_refuses_live_gate_without_auth_token(tmp_path, monkeypatch):
    monkeypatch.setenv("FEDR_DEFAULT_MODE", "simulation")
    monkeypatch.setenv("FEDR_GATEWAY_ENABLED", "false")
    monkeypatch.setenv("FEDR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FEDR_AUTH_TOKEN", "")
    monkeypatch.setenv("FEDR_LIVE_TRADING_ALLOWED", "true")
    from fedr.config import env as envmod

    envmod._env = None
    from fedr.app import FedrApp
    from fedr.config.env import EnvSettings

    async def main():
        app = FedrApp(EnvSettings())
        with pytest.raises(RuntimeError) as ei:
            await app.start()
        assert "FEDR_AUTH_TOKEN" in str(ei.value)

    asyncio.run(main())
    envmod._env = None


def test_startup_refuses_live_boot_default(tmp_path, monkeypatch):
    monkeypatch.setenv("FEDR_DEFAULT_MODE", "live")
    monkeypatch.setenv("FEDR_DATA_DIR", str(tmp_path))
    from fedr.config.env import EnvSettings

    problems = EnvSettings().validate_startup()
    assert any("boot default" in p for p in problems)


def test_login_lockout_after_repeated_failures(api):
    from fedr.api.routes import system as sysmod

    async def t(c):
        sysmod._LOGIN_FAILURES.clear()
        for _ in range(5):
            r = await c.post(
                "/api/auth/login", json={"token": "wrong-token-xxxxxxxxxxxx"}, headers={"X-Fedr-Request": "1"}
            )
            assert r.status_code == 401
        r = await c.post(
            "/api/auth/login", json={"token": "wrong-token-xxxxxxxxxxxx"}, headers={"X-Fedr-Request": "1"}
        )
        assert r.status_code == 429 and "Retry-After" in r.headers
        r = await c.post("/api/auth/login", json={"token": TOKEN}, headers={"X-Fedr-Request": "1"})
        assert r.status_code == 429  # even the right token waits out the lockout
        sysmod._LOGIN_FAILURES.clear()
        r = await c.post("/api/auth/login", json={"token": TOKEN}, headers={"X-Fedr-Request": "1"})
        assert r.status_code == 200

    _run(api, t)


def test_market_data_rejections_trip_venue_breaker_and_recover(api):
    """The hub's quality gate is wired to the app: repeated rejections mark the venue's data UNHEALTHY,
    trip a venue-scoped MARKET_DATA_FAILURE breaker, and a clean feed lifts both again."""
    from decimal import Decimal

    from fedr.core.enums import CircuitBreakerReason
    from fedr.core.models import now_ms
    from fedr.marketdata.orderbook import Level, OrderBook

    async def t(c):
        fedr = api.state.fedr
        venue = next(v for v, conn in fedr.ctx.connectors.items() if conn.kind.value == "cex")
        sym = next(iter(fedr.ctx.connectors[venue].markets))
        await fedr.ctx.hub.ingest(await fedr.ctx.connectors[venue].fetch_order_book(sym))
        mid = fedr.ctx.hub.get_book(venue, sym).mid  # stay within cross-source tolerance of the other venues
        bad = OrderBook(
            venue=venue,
            symbol=sym,
            bids=[Level(price=mid * Decimal("1.001"), amount=Decimal("1"))],
            asks=[Level(price=mid * Decimal("0.999"), amount=Decimal("1"))],  # crossed book
            ts_ms=now_ms(),
            source="test",
        )
        for _ in range(fedr.ctx.hub.quality.policy.consecutive_rejections_unhealthy + 1):
            await fedr.ctx.hub.ingest(bad)
        assert fedr.ctx.connectors[venue].health_tracker.data_unhealthy is True
        assert any(
            b.reason is CircuitBreakerReason.MARKET_DATA_FAILURE and b.scope == f"venue:{venue}"
            for b in fedr.ctx.breakers._active.values()
        )
        assert fedr.ctx.breakers.is_tripped(f"venue:{venue}")
        st = (await c.get("/api/system/metrics")).json()
        assert st["market_data"]["rejections"].get(venue, 0) >= 1
        # clean books restore health; the health loop lifts the venue breaker
        good = OrderBook(
            venue=venue,
            symbol=sym,
            bids=[Level(price=mid * Decimal("0.9995"), amount=Decimal("1"))],
            asks=[Level(price=mid * Decimal("1.0005"), amount=Decimal("1"))],
            ts_ms=now_ms() + 1,
            source="test",
        )
        for _ in range(fedr.ctx.hub.quality.policy.consecutive_rejections_unhealthy + 1):
            good.ts_ms += 1
            await fedr.ctx.hub.ingest(good)
        await fedr._health_check_all()
        assert fedr.ctx.connectors[venue].health_tracker.data_unhealthy is False
        assert not fedr.ctx.breakers.is_tripped(f"venue:{venue}")

    _run(api, t)
