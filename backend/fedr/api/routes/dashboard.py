from __future__ import annotations

from fastapi import APIRouter, Depends

from fedr.api.auth import require_app
from fedr.api.serializers import opportunity_summary, trade_summary
from fedr.core.enums import VenueKind

router = APIRouter()


@router.get("/dashboard")
async def dashboard(app=Depends(require_app)):
    ctx = app.ctx
    pnl = await app.repo.pnl_summary(app.mode)
    opps = app.opportunities.ranked()
    venues = []
    for c in ctx.connectors.values():
        h = ctx.health.get(c.name)
        venues.append(
            {
                "name": c.name,
                "display_name": c.display_name,
                "kind": c.kind.value,
                "connected": c.connected,
                "health": ctx.venue_health(c.name).value,
                "reasons": h.reasons if h else [],
                "latency_ms": round(h.api_latency_ms) if h and h.api_latency_ms else None,
            }
        )
    lines = ctx.inventory.lines()
    balances = [
        {
            "venue": l.venue,
            "asset": l.asset,
            "available": str(l.available),
            "reserved": str(l.reserved),
            "usd": str(l.usd_value.quantize(__import__("decimal").Decimal("0.01")))
            if l.usd_value is not None
            else None,
            "kind": l.kind,
        }
        for l in lines
        if l.total > 0
    ]
    return {
        "mode": app.mode.value,
        "shadow_mode": app.settings.general.shadow_mode,
        "bot_enabled": app.settings.general.bot_enabled,
        "emergency_stop": ctx.emergency_stop,
        "breakers": [b.as_dict() for b in ctx.breakers.active],
        "capital": app.capital_summary(),
        "pnl": pnl,
        "best": [opportunity_summary(o) for o in opps[:6]],
        "counts": {
            "executable": sum(1 for o in opps if o.is_executable),
            "blocked": sum(1 for o in opps if not o.is_executable),
            "routes": len(app.opportunities.candidates()),
        },
        "venues": venues,
        "balances": balances,
        "active_trades": [trade_summary(t) for t in app.executor.active.values()],
        "recent_trades": [trade_summary(t) for t in reversed(app.executor.recent[-5:])],
        "risk": {
            "daily_pnl": str(ctx.daily_pnl_usd),
            "max_daily_loss": str(app.settings.risk.max_daily_loss_usd),
            "failed_last_hour": ctx.failed_trades_last_hour,
            "max_failed": app.settings.risk.max_failed_trades_per_hour,
            "open_trades": len(ctx.open_trade_ids),
            "max_concurrent": app.settings.risk.max_concurrent_trades,
        },
        "gas": ctx.gas_oracle.all(),
        "market_data": {
            "books": len(ctx.hub.books),
            "source": "synthetic (SIMULATION)" if app.synthetic is not None else "live",
            "prices": ctx.hub.prices.snapshot(),
        },
        "dex_available": any(c.kind is VenueKind.DEX and c.connected for c in ctx.connectors.values()),
    }
