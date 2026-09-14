"""FastAPI entrypoint: API under /api, UI served from fedr/static (SPA fallback)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fedr.api.auth import AuthMiddleware
from fedr.api.routes import (
    backtest,
    dashboard,
    exchanges,
    history,
    opportunities,
    settings,
    system,
    trading,
    wallets,
)
from fedr.app import FedrApp
from fedr.config.env import get_env

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    fedr = FedrApp(get_env())
    app.state.fedr = fedr
    task = asyncio.create_task(fedr.start())
    try:
        await task
    except Exception as exc:  # keep the API up so /health can report the failure
        fedr.status_message = f"startup failed: {exc}"
        raise
    try:
        yield
    finally:
        await fedr.stop()


def create_app() -> FastAPI:
    env = get_env()
    app = FastAPI(
        title="FEDR",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if env.log_level.upper() == "DEBUG" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if env.log_level.upper() == "DEBUG" else None,
    )
    origins = [o.strip() for o in env.allowed_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(AuthMiddleware)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        return resp

    for r in (system, dashboard, opportunities, trading, wallets, exchanges, history, settings, backtest):
        app.include_router(r.router, prefix="/api")

    # ---- health endpoints (unauthenticated, no secrets; used by Docker healthcheck and orchestrators) ----
    @app.get("/health", include_in_schema=False)
    async def health_root(request: Request):
        fedr = getattr(request.app.state, "fedr", None)
        ok = fedr is not None and fedr.ctx is not None
        return JSONResponse(
            {"status": "ok" if ok else "starting", "message": fedr.status_message if fedr else "starting"},
            status_code=200 if ok else 503,
        )

    @app.get("/health/live", include_in_schema=False)
    async def health_live():
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def health_ready(request: Request):
        fedr = getattr(request.app.state, "fedr", None)
        checks: dict[str, bool] = {}
        if fedr is None or fedr.ctx is None:
            return JSONResponse({"status": "not-ready", "checks": {"application": False}}, status_code=503)
        checks["database"] = await fedr.db.health() if fedr.db else False
        checks["profit_guard"] = fedr.ctx.profit_guard is not None
        checks["risk_engine"] = fedr.ctx.risk_engine is not None
        checks["strategy_engine"] = fedr.opportunities is not None
        checks["scan_loop"] = fedr.opportunities is not None and (
            fedr.opportunities.last_scan_ms > 0
            or (fedr.started_at_ms and (__import__("time").time() * 1000 - fedr.started_at_ms) < 60_000)
        )
        dex_needed = (
            fedr.env.gateway_enabled
            and (
                fedr.settings.strategies.cex_dex
                or fedr.settings.strategies.dex_dex
                or fedr.settings.strategies.flash_loan
            )
            and fedr.mode.value not in ("simulation",)
        )
        checks["gateway"] = (not dex_needed) or fedr.gateway_ok
        checks["not_emergency_stopped"] = not fedr.ctx.emergency_stop
        ready = all(checks.values())
        return JSONResponse(
            {"status": "ready" if ready else "not-ready", "checks": checks, "mode": fedr.mode.value},
            status_code=200 if ready else 503,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        return JSONResponse({"detail": "internal error", "type": type(exc).__name__}, status_code=500)

    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            candidate = STATIC_DIR / full_path
            if full_path and candidate.is_file() and candidate.resolve().is_relative_to(STATIC_DIR.resolve()):
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()


def run() -> None:
    env = get_env()
    uvicorn.run(
        "fedr.main:app", host=env.bind, port=env.port, log_level=env.log_level.lower(), proxy_headers=True
    )


if __name__ == "__main__":
    run()
