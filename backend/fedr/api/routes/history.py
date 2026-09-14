from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from fedr.api.auth import require_app
from fedr.api.serializers import trade_row_from_db
from fedr.core.enums import TradingMode

router = APIRouter()


@router.get("/history/trades")
async def trades(app=Depends(require_app), limit: int = 100, offset: int = 0, mode: str | None = None):
    m = TradingMode(mode) if mode else app.mode
    rows = await app.repo.list_trades(m, limit=limit, offset=offset)
    return {"mode": m.value, "items": [trade_row_from_db(r) for r in rows]}


@router.get("/history/trades/{trade_id}")
async def trade(trade_id: str, app=Depends(require_app)):
    row = await app.repo.get_trade(trade_id)
    if row is None:
        raise HTTPException(404, "unknown trade")
    return {**trade_row_from_db(row), "detail": row.payload}


@router.get("/history/pnl")
async def pnl(app=Depends(require_app), mode: str | None = None):
    m = TradingMode(mode) if mode else app.mode
    return await app.repo.pnl_summary(m)


@router.get("/history/opportunities")
async def opportunities(app=Depends(require_app), limit: int = 100):
    rows = await app.repo.list_opportunities(app.mode, limit=limit)
    return {
        "items": [
            {
                "id": r.id,
                "strategy": r.strategy,
                "pair": r.pair,
                "route": r.route,
                "decision": r.decision,
                "gross_pct": r.gross_pct,
                "expected_net_usd": r.expected_net_usd,
                "worst_case_usd": r.worst_case_usd,
                "risk_score": r.risk_score,
                "created_at": r.created_at.isoformat(),
                "explanation": r.payload.get("explanation"),
                "block_reasons": r.payload.get("block_reasons", []),
            }
            for r in rows
        ]
    }


@router.get("/history/audit")
async def audit(app=Depends(require_app), limit: int = 100, event_type: str | None = None):
    rows = await app.repo.list_audit(limit=limit, event_type=event_type)
    return {
        "items": [
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "mode": r.mode,
                "event_type": r.event_type,
                "actor": r.actor,
                "summary": r.summary,
                "payload": r.payload,
            }
            for r in rows
        ]
    }


@router.get("/history/risk-events")
async def risk_events(app=Depends(require_app), limit: int = 50):
    rows = await app.repo.list_risk_events(app.mode, limit=limit)
    return {
        "items": [
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "severity": r.severity,
                "kind": r.kind,
                "detail": r.detail,
                "payload": r.payload,
            }
            for r in rows
        ]
    }


@router.get("/history/experience")
async def experience(app=Depends(require_app)):
    return {"items": app.ctx.experience.summary()}


@router.get("/history/backtests")
async def backtests(app=Depends(require_app)):
    rows = await app.repo.list_backtests()
    return {
        "items": [
            {"id": r.id, "ts": r.ts.isoformat(), "label": r.label, "params": r.params, "results": r.results}
            for r in rows
        ]
    }
