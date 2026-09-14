from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from fedr.api.auth import require_app
from fedr.api.serializers import opportunity_detail, opportunity_summary, trade_detail
from fedr.core.enums import Strategy

router = APIRouter()


@router.get("/opportunities")
async def list_opportunities(app=Depends(require_app), include_blocked: bool = True, limit: int = 50):
    opps = app.opportunities.ranked()
    if not include_blocked:
        opps = [o for o in opps if o.is_executable]
    return {
        "items": [opportunity_summary(o) for o in opps[:limit]],
        "scan": {"count": app.opportunities.scan_count, "last_ms": app.opportunities.last_scan_ms},
    }


@router.get("/opportunities/{opp_id}")
async def get_opportunity(opp_id: str, app=Depends(require_app)):
    o = app.opportunities.get(opp_id)
    if o is None:
        row = None
        for r in await app.repo.list_opportunities(app.mode, limit=500):
            if r.id == opp_id:
                row = r
                break
        if row is None:
            raise HTTPException(404, "opportunity expired")
        return {"expired": True, **row.payload}
    return opportunity_detail(o)


@router.post("/opportunities/{opp_id}/execute")
async def execute(opp_id: str, app=Depends(require_app)):
    o = app.opportunities.get(opp_id)
    if o is None:
        raise HTTPException(404, "opportunity expired - wait for the next scan")
    if app.settings.general.shadow_mode:
        raise HTTPException(
            400, "shadow mode is on: nothing is submitted. Turn shadow mode off in Trading to execute."
        )
    if o.strategy is Strategy.FLASH_LOAN:
        tr = await app.flashloan.execute(o, app.opportunities, trigger="manual")
    else:
        tr = await app.executor.execute(o, trigger="manual")
    return trade_detail(tr)
