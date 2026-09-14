from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fedr.api.auth import require_app
from fedr.connectors.cex.registry import EXCHANGES
from fedr.connectors.dex.registry import DEXES
from fedr.core.enums import ConnectorStatus

router = APIRouter()


def _connector_level(app, name: str) -> str:
    c = app.ctx.connectors.get(name)
    if c is None or not c.connected:
        return ConnectorStatus.SUPPORTED.value
    h = app.ctx.venue_health(name)
    if h.value not in ("healthy", "degraded"):
        return ConnectorStatus.CONNECTED.value
    if not c.trading_enabled and app.mode.value in ("live", "testnet"):
        return ConnectorStatus.HEALTHY.value
    if h.value == "healthy" and any(
        cand.buy.name == name or cand.sell.name == name for cand in app.opportunities.candidates()
    ):
        return ConnectorStatus.ARBITRAGE_ELIGIBLE.value
    return ConnectorStatus.TRADEABLE.value if h.value == "healthy" else ConnectorStatus.HEALTHY.value


@router.get("/exchanges")
async def list_exchanges(app=Depends(require_app)):
    ctx = app.ctx
    accounts = {a.exchange_id: a for a in app.exchange_accounts}
    cex = []
    for spec in EXCHANGES.values():
        c = ctx.connectors.get(spec.id)
        h = ctx.health.get(spec.id)
        acct = accounts.get(spec.id)
        cex.append(
            {
                "id": spec.id,
                "display_name": spec.display_name,
                "tier": spec.tier,
                "sandbox": spec.sandbox,
                "sandbox_note": spec.sandbox_note,
                "perps": spec.perps,
                "needs_password": spec.needs_password,
                "auth_style": spec.auth_style,
                "notes": spec.notes,
                "verification": spec.verification.value,
                "status": _connector_level(app, spec.id),
                "connected": bool(c and c.connected),
                "trading_enabled": bool(c and c.trading_enabled),
                "health": ctx.venue_health(spec.id).value if c else "unknown",
                "health_reasons": h.reasons if h else [],
                "latency_ms": round(h.api_latency_ms) if h and h.api_latency_ms else None,
                "ws": ctx.hub.stats.get(spec.id, {}).get("ws"),
                "market_data_updates": ctx.hub.stats.get(spec.id, {}).get("updates", 0),
                "markets": len(c.markets) if c else 0,
                "last_error": c.last_error if c else None,
                "account": {
                    "id": acct.id,
                    "label": acct.label,
                    "mode": acct.mode,
                    "enabled": acct.enabled,
                    "sandbox": acct.sandbox,
                    "status": acct.status,
                    "permissions": acct.permissions,
                    "last_error": acct.last_error,
                    "last_verified_at": acct.last_verified_at.isoformat() if acct.last_verified_at else None,
                }
                if acct
                else None,
                "permissions": getattr(c, "permissions", None) if c else None,
                "in_use": c is not None,
            }
        )
    dex = []
    for spec in DEXES.values():
        c = ctx.connectors.get(spec.id)
        h = ctx.health.get(spec.id)
        dex.append(
            {
                "id": spec.id,
                "display_name": spec.display_name,
                "chain": spec.chain.value,
                "connector": spec.connector,
                "trading_type": spec.trading_type,
                "network": spec.network,
                "testnet_network": spec.testnet_network,
                "notes": spec.notes,
                "verification": spec.verification.value,
                "status": _connector_level(app, spec.id),
                "connected": bool(c and c.connected),
                "health": ctx.venue_health(spec.id).value if c else "unknown",
                "health_reasons": h.reasons if h else [],
                "in_use": c is not None,
                "last_error": c.last_error if c else None,
            }
        )
    return {
        "cex": cex,
        "dex": dex,
        "gateway_ok": app.gateway_ok,
        "gateway_enabled": app.env.gateway_enabled,
        "mode": app.mode.value,
        "accounts": [
            {
                "id": a.id,
                "exchange_id": a.exchange_id,
                "label": a.label,
                "mode": a.mode,
                "enabled": a.enabled,
                "sandbox": a.sandbox,
                "status": a.status,
                "permissions": a.permissions,
                "last_error": a.last_error,
            }
            for a in app.exchange_accounts
        ],
    }


class AccountBody(BaseModel):
    exchange_id: str
    label: str = ""
    mode: str = "live"
    sandbox: bool | None = None
    api_key: str = ""
    secret: str = ""
    password: str = ""
    uid: str = ""
    wallet_address: str = ""
    private_key: str = ""


@router.post("/exchanges/accounts")
async def add_account(body: AccountBody, app=Depends(require_app)):
    creds = {
        "apiKey": body.api_key,
        "secret": body.secret,
        "password": body.password,
        "uid": body.uid,
        "walletAddress": body.wallet_address,
        "privateKey": body.private_key,
    }
    try:
        return await app.add_exchange_account(body.exchange_id, body.label, creds, body.mode, body.sandbox)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/exchanges/accounts/{account_id}/test")
async def test_account(account_id: str, app=Depends(require_app)):
    try:
        return await app.test_exchange_account(account_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class EnableBody(BaseModel):
    enabled: bool


@router.post("/exchanges/accounts/{account_id}/enabled")
async def enable_account(account_id: str, body: EnableBody, app=Depends(require_app)):
    await app.set_exchange_enabled(account_id, body.enabled)
    return {"ok": True}


@router.delete("/exchanges/accounts/{account_id}")
async def delete_account(account_id: str, app=Depends(require_app)):
    await app.remove_exchange_account(account_id)
    return {"ok": True}


@router.post("/exchanges/{venue}/reconnect")
async def reconnect(venue: str, app=Depends(require_app)):
    c = app.ctx.connectors.get(venue)
    if c is None:
        raise HTTPException(404, "venue not active in this mode")
    try:
        await c.close()
        await c.connect()
    except Exception as exc:
        c.last_error = str(exc)[:200]
        raise HTTPException(400, f"reconnect failed: {exc}") from exc
    app.ctx.health[venue] = await c.health_check()
    return c.status_dict()


@router.get("/exchanges/{venue}/permissions")
async def permissions(venue: str, app=Depends(require_app)):
    c = app.ctx.connectors.get(venue)
    if c is None:
        raise HTTPException(404, "venue not active")
    return {
        "venue": venue,
        "permissions": getattr(c, "permissions", {}),
        "note": "READ verified by fetching balances; TRADE is verified by the first successful order; WITHDRAW can only be verified on venues exposing key restrictions (Binance). Never enable withdrawal permission on trading keys.",
    }
