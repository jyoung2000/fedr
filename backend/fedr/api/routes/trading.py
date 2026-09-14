from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fedr.api.auth import require_app
from fedr.api.serializers import trade_detail, trade_summary
from fedr.core.enums import TradingMode

router = APIRouter()


class Toggle(BaseModel):
    enabled: bool


@router.post("/trading/bot")
async def toggle_bot(body: Toggle, app=Depends(require_app)):
    await app.update_settings({"general": {"bot_enabled": body.enabled}})
    return {"bot_enabled": body.enabled}


@router.post("/trading/auto-execute")
async def toggle_auto(body: Toggle, app=Depends(require_app)):
    await app.update_settings({"trading": {"auto_execute": body.enabled}})
    return {"auto_execute": body.enabled}


@router.post("/trading/shadow")
async def toggle_shadow(body: Toggle, app=Depends(require_app)):
    await app.update_settings({"general": {"shadow_mode": body.enabled}})
    return {"shadow_mode": body.enabled}


class ModeBody(BaseModel):
    mode: str


@router.post("/trading/mode")
async def set_mode(body: ModeBody, app=Depends(require_app)):
    try:
        mode = TradingMode(body.mode)
    except ValueError as exc:
        raise HTTPException(400, "unknown mode") from exc
    if mode is TradingMode.LIVE:
        raise HTTPException(400, "use the live activation workflow (Settings > Live) to enter LIVE mode")
    try:
        await app.update_settings({"general": {"mode": mode.value}})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"mode": app.mode.value}


@router.get("/trading/state")
async def state(app=Depends(require_app)):
    s = app.settings
    ctx = app.ctx
    return {
        "mode": s.general.mode.value,
        "bot_enabled": s.general.bot_enabled,
        "auto_execute": s.trading.auto_execute,
        "shadow_mode": s.general.shadow_mode,
        "emergency_stop": ctx.emergency_stop,
        "active": [trade_detail(t) for t in app.executor.active.values()],
        "recent": [trade_summary(t) for t in reversed(app.executor.recent[-30:])],
        "shadow": {**(await app.repo.shadow_summary()), **app.shadow.stats},
        "rebalance": [
            {
                "from": r.from_venue,
                "to": r.to_venue,
                "asset": r.asset,
                "amount": str(r.amount),
                "cost_usd": str(r.estimated_cost_usd),
                "benefit_usd": str(r.estimated_benefit_usd),
                "net_usd": str(r.net_benefit_usd),
                "reason": r.reason,
            }
            for r in ctx.rebalancer.recommend()
        ],
        "inventory": [
            {
                "venue": l.venue,
                "asset": l.asset,
                "available": str(l.available),
                "reserved": str(l.reserved),
                "pending": str(l.pending),
                "target": str(l.target),
                "minimum": str(l.minimum),
                "maximum": str(l.maximum) if l.maximum is not None else None,
                "usd": str(l.usd_value) if l.usd_value is not None else None,
                "kind": l.kind,
            }
            for l in ctx.inventory.lines()
        ],
        "paper": {
            "editable": app.mode in (TradingMode.PAPER, TradingMode.SIMULATION),
            "starting_balances": {
                v: {a: str(x) for a, x in b.items()} for v, b in s.paper.starting_balances.items()
            },
            "stress_multiplier": str(s.paper.stress_multiplier),
            "latency_ms": s.paper.latency_ms,
        },
        "experience": ctx.experience.summary()[:20],
    }


@router.post("/trading/paper/reset")
async def paper_reset(app=Depends(require_app)):
    try:
        await app.reset_paper()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


class Funds(BaseModel):
    venue: str
    asset: str
    amount: Decimal


@router.post("/trading/paper/funds")
async def paper_funds(body: Funds, app=Depends(require_app)):
    try:
        await app.adjust_paper_funds(body.venue, body.asset.upper(), body.amount)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.get("/trading/shadow/records")
async def shadow_records(app=Depends(require_app), limit: int = 100):
    rows = await app.repo.list_shadow(limit)
    return {
        "summary": await app.repo.shadow_summary(),
        "items": [
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "would_trade": r.would_trade,
                "strategy": r.strategy,
                "pair": r.pair,
                "route": r.route,
                "size_base": r.size_base,
                "predicted_profit": r.predicted_profit,
                "worst_case_profit": r.worst_case_profit,
                "estimated_costs": r.estimated_costs,
                "hypothetical_profit": r.hypothetical_profit,
                "reason": r.reason,
            }
            for r in rows
        ],
    }


@router.post("/trading/shadow/clear")
async def shadow_clear(app=Depends(require_app)):
    await app.repo.clear_shadow()
    app.shadow.stats = {"evaluated": 0, "would_trade": 0}
    return {"ok": True}


class InventoryTargetBody(BaseModel):
    venue: str
    asset: str
    target: Decimal = Decimal(0)
    minimum: Decimal = Decimal(0)
    maximum: Decimal | None = None


@router.post("/trading/inventory/target")
async def inventory_target(body: InventoryTargetBody, app=Depends(require_app)):
    app.ctx.inventory.set_targets(body.venue, body.asset.upper(), body.target, body.minimum, body.maximum)
    await app.repo.upsert_inventory(
        app.mode, body.venue, body.asset.upper(), body.target, body.minimum, body.maximum
    )
    return {"ok": True}
