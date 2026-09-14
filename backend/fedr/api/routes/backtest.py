from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fedr.api.auth import require_app
from fedr.backtest.replay import run_backtest
from fedr.db.models import BacktestRun

router = APIRouter()


class BacktestBody(BaseModel):
    ticks: int = 300
    seed: int = 7
    label: str = "backtest"
    recorded_file: str | None = None


@router.post("/backtest/run")
async def run(body: BacktestBody, app=Depends(require_app)):
    if body.ticks < 1 or body.ticks > 5000:
        raise HTTPException(400, "ticks must be between 1 and 5000")
    recorded = None
    if body.recorded_file:
        p = (app.env.data_dir / "market-data" / Path(body.recorded_file).name).resolve()
        if not str(p).startswith(str((app.env.data_dir / "market-data").resolve())) or not p.exists():
            raise HTTPException(400, "recorded file not found under data/market-data")
        recorded = p
    result = await asyncio.wait_for(run_backtest(app.settings, ticks=body.ticks, seed=body.seed, label=body.label, recorded=recorded), timeout=600)
    await app.repo.save_backtest(BacktestRun(id=result["id"], label=result["label"], params=result["params"], results=result["results"]))
    return result
