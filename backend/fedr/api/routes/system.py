from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from fedr.api.auth import COOKIE, require_app
from fedr.api.serializers import opportunity_summary, trade_summary
from fedr.core.enums import CircuitBreakerReason
from fedr.core.models import now_ms
from fedr.engine.readiness import live_readiness, startup_health
from fedr.security.crypto import constant_time_equals

router = APIRouter()


@router.get("/system/health")
async def health(request: Request):
    app = getattr(request.app.state, "fedr", None)
    ok = app is not None and app.ctx is not None
    return {"ok": ok, "status": app.status_message if app else "starting", "ts": now_ms()}


@router.get("/auth/status")
async def auth_status(request: Request):
    app = getattr(request.app.state, "fedr", None)
    token = getattr(app, "auth_token", None) if app else None
    header = request.headers.get("authorization", "")
    presented = header[7:] if header.lower().startswith("bearer ") else request.cookies.get(COOKIE)
    authed = bool(token and presented and constant_time_equals(presented, token))
    return {
        "auth_required": True,
        "authenticated": authed,
        "token_source": getattr(app, "auth_token_source", "none") if app else "none",
        "hint": "Set FEDR_AUTH_TOKEN, or read the generated token from <data dir>/config/ui-token"
        if not authed
        else None,
    }


class LoginBody(BaseModel):
    token: str


# Login lockout: 5 failures within 10 minutes lock the client address for 10 minutes (in-memory; the
# constant-time compare + 0.5 s delay remain). The UI token is the only credential FEDR holds.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_S = 600
_LOGIN_FAILURES: dict[str, list[float]] = {}


def _login_failed(client: str) -> None:
    import time

    now = time.monotonic()
    hits = [t for t in _LOGIN_FAILURES.get(client, []) if now - t < LOGIN_WINDOW_S]
    hits.append(now)
    _LOGIN_FAILURES[client] = hits[-50:]


def _login_locked_for(client: str) -> int:
    import time

    now = time.monotonic()
    hits = [t for t in _LOGIN_FAILURES.get(client, []) if now - t < LOGIN_WINDOW_S]
    if len(hits) >= LOGIN_MAX_FAILURES:
        return max(1, int(LOGIN_WINDOW_S - (now - hits[0])))
    return 0


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response):
    app = getattr(request.app.state, "fedr", None)
    token = getattr(app, "auth_token", None) if app else None
    if not token:
        raise HTTPException(503, "application is starting")
    client = request.client.host if request.client else "unknown"
    locked = _login_locked_for(client)
    if locked:
        raise HTTPException(
            429, f"too many failed logins; retry in {locked}s", headers={"Retry-After": str(locked)}
        )
    if not constant_time_equals(body.token, token):
        _login_failed(client)
        await asyncio.sleep(0.5)
        raise HTTPException(401, "invalid token")
    _LOGIN_FAILURES.pop(client, None)
    response.set_cookie(
        COOKIE,
        body.token,
        httponly=True,
        samesite="strict",
        secure=app.env.secure_cookies,
        max_age=app.settings.security.session_timeout_minutes * 60,
        path="/",
    )
    return {"ok": True}


@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/system/status")
async def status(app=Depends(require_app)):
    ctx = app.ctx
    s = app.settings
    return {
        "mode": s.general.mode.value,
        "shadow_mode": s.general.shadow_mode,
        "bot_enabled": s.general.bot_enabled,
        "auto_execute": s.trading.auto_execute,
        "live_activated": s.live.activated,
        "live_allowed_by_env": app.env.live_trading_allowed,
        "emergency_stop": ctx.emergency_stop,
        "emergency_stop_reason": app.estop.reason if app.estop else None,
        "breakers": [b.as_dict() for b in ctx.breakers.active],
        "risk_profile": s.general.risk_profile.value,
        "advanced_mode": s.general.advanced_mode,
        "uptime_s": int((now_ms() - app.started_at_ms) / 1000) if app.started_at_ms else 0,
        "status_message": app.status_message,
        "startup": app.startup_check.as_dict() if app.startup_check else None,
        "gateway_ok": app.gateway_ok,
        "scan": {
            "count": app.opportunities.scan_count,
            "last_ms": app.opportunities.last_scan_ms,
            "duration_ms": app.opportunities.last_scan_duration_ms,
            "interval_ms": s.general.scan_interval_ms,
        },
        "open_trades": len(ctx.open_trade_ids),
        "gas": ctx.gas_oracle.all(),
        "auth_required": True,
        "auth_token_source": app.auth_token_source,
        "version": "0.1.0",
        "market_data_source": "synthetic (SIMULATION)" if app.synthetic is not None else "live",
        "strategies": s.strategies.model_dump(),
    }


@router.get("/system/metrics")
async def metrics(app=Depends(require_app)):
    ctx = app.ctx
    pnl = await app.repo.pnl_summary(app.mode)
    return {
        "clock": {
            c.name: {
                "drift_ms": c.health_tracker.clock_drift_ms,
                "checked_ms": c.health_tracker.clock_checked_ms,
            }
            for c in app.ctx.connectors.values()
        },
        "mode": app.mode.value,
        "uptime_s": int((now_ms() - app.started_at_ms) / 1000) if app.started_at_ms else 0,
        "process": app.metrics.as_dict(),
        "persisted": {
            "trades": pnl["trades"],
            "wins": pnl["wins"],
            **{k: v for k, v in pnl["total"].items()},
        },
        "market_data": {
            "books": len(ctx.hub.books),
            "venues": ctx.hub.stats,
            "rejections": ctx.hub.rejections,
        },
        "venues": {n: ctx.venue_health(n).value for n in ctx.connectors},
        "breakers": [b.as_dict() for b in ctx.breakers.active],
        "open_trades": len(ctx.open_trade_ids),
        "gas": ctx.gas_oracle.all(),
    }


@router.get("/system/startup-check")
async def startup(app=Depends(require_app)):
    return (await startup_health(app)).as_dict()


@router.get("/system/readiness")
async def readiness(app=Depends(require_app)):
    return (await live_readiness(app)).as_dict()


class StopBody(BaseModel):
    reason: str = "manual"


@router.post("/system/emergency-stop")
async def emergency_stop(body: StopBody, app=Depends(require_app)):
    rep = await app.estop.activate(body.reason or "manual")
    return rep.as_dict()


@router.post("/system/emergency-stop/release")
async def release_stop(app=Depends(require_app)):
    await app.estop.release()
    return {"ok": True}


class BreakerReset(BaseModel):
    reason: str | None = None
    scope: str | None = None


@router.post("/system/breakers/reset")
async def reset_breakers(body: BreakerReset, app=Depends(require_app)):
    reason = CircuitBreakerReason(body.reason) if body.reason else None
    n = await app.ctx.breakers.reset(reason, body.scope)
    await app.repo.audit(
        app.mode,
        "circuit_breaker",
        f"Circuit breaker(s) reset by user ({n})",
        {"reason": body.reason, "scope": body.scope},
        actor="user",
    )
    return {"reset": n, "active": [b.as_dict() for b in app.ctx.breakers.active]}


class ActivateLive(BaseModel):
    confirmation: str


@router.post("/system/live/activate")
async def activate_live(body: ActivateLive, app=Depends(require_app)):
    if not app.env.live_trading_allowed:
        raise HTTPException(400, "FEDR_LIVE_TRADING_ALLOWED is not true in the environment")
    try:
        return await app.activate_live(body.confirmation)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/system/live/deactivate")
async def deactivate_live(app=Depends(require_app)):
    await app.deactivate_live()
    return {"ok": True, "mode": app.mode.value}


@router.get("/system/events")
async def events(request: Request, app=Depends(require_app)):
    """Server-sent events: aggregated state at ~1 Hz plus discrete events (trades, breakers, deposits)."""

    async def gen():
        q = app.subscribe()
        try:
            last_snapshot = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield f"event: {ev['kind']}\ndata: {json.dumps(ev)}\n\n"
                except TimeoutError:
                    pass
                if now_ms() - last_snapshot >= 1000:
                    last_snapshot = now_ms()
                    opps = app.opportunities.ranked()[:8]
                    snap = {
                        "ts": now_ms(),
                        "mode": app.mode.value,
                        "emergency_stop": app.ctx.emergency_stop,
                        "breakers": len(app.ctx.breakers.active),
                        "opportunities": [opportunity_summary(o) for o in opps],
                        "capital": app.capital_summary(),
                        "open_trades": [trade_summary(t) for t in app.executor.active.values()],
                        "daily_pnl": str(app.ctx.daily_pnl_usd),
                    }
                    yield f"event: snapshot\ndata: {json.dumps(snap)}\n\n"
        finally:
            app.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
