from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fedr.api.auth import require_app
from fedr.core.enums import RiskProfile
from fedr.engine.readiness import live_readiness
from fedr.engine.strategies.catalog import CATALOG

router = APIRouter()


@router.get("/settings")
async def get_settings(app=Depends(require_app)):
    return {
        "settings": app.settings.to_public_dict(),
        "strategies_catalog": [
            {
                "key": i.key.value,
                "name": i.name,
                "description": i.description,
                "default_on": i.default_on,
                "risks": i.risks,
                "carry": i.carry,
                "atomic": i.atomic,
            }
            for i in CATALOG.values()
        ],
        "env": {
            "live_trading_allowed": app.env.live_trading_allowed,
            "auth_enabled": bool(app.env.auth_token),
            "gateway_enabled": app.env.gateway_enabled,
            "gateway_url": app.env.gateway_url,
            "private_rpc": bool(app.env.evm_private_rpc),
            "rpc": {
                c: bool(app.env.rpc_for(c))
                for c in ("ethereum", "base", "arbitrum", "optimism", "polygon", "bsc", "avalanche", "solana")
            },
            "flashloan_contracts": {
                c: bool(app.env.flashloan_contract_for(c))
                for c in ("ethereum", "base", "arbitrum", "optimism", "polygon")
            },
            "telemetry": app.env.telemetry_enabled,
            "data_dir": str(app.env.data_dir),
        },
    }


class Patch(BaseModel):
    patch: dict[str, Any]


@router.put("/settings")
async def put_settings(body: Patch, app=Depends(require_app)):
    if "live" in body.patch and any(
        k not in ("paper_completed", "shadow_reviewed") for k in body.patch["live"]
    ):
        raise HTTPException(400, "live activation state can only be changed through the activation workflow")
    try:
        new = await app.update_settings(body.patch)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"invalid settings: {exc}") from exc
    return {"settings": new.to_public_dict()}


class ProfileBody(BaseModel):
    profile: str


@router.post("/settings/risk-profile")
async def risk_profile(body: ProfileBody, app=Depends(require_app)):
    try:
        p = RiskProfile(body.profile)
    except ValueError as exc:
        raise HTTPException(400, "unknown profile") from exc
    new = await app.update_settings({"general": {"risk_profile": p.value}})
    return {"settings": new.to_public_dict()}


@router.get("/settings/readiness")
async def readiness(app=Depends(require_app)):
    return (await live_readiness(app)).as_dict()
