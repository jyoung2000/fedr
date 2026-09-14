"""API authentication: optional bearer token (header or cookie) + CSRF guard for cookie auth."""

from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from fedr.security.crypto import constant_time_equals

COOKIE = "fedr_session"
PUBLIC_PATHS = {"/api/system/health", "/api/auth/login", "/api/auth/status"}


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str | None):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not self.token or not path.startswith("/api") or path in PUBLIC_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        presented = header[7:] if header.lower().startswith("bearer ") else request.cookies.get(COOKIE)
        if not presented or not constant_time_equals(presented, self.token):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        if request.cookies.get(COOKIE) and not header and request.method not in ("GET", "HEAD", "OPTIONS"):
            # cookie-authenticated state change: require the custom header (CSRF guard) and same-origin
            if request.headers.get("x-fedr-request") != "1":
                return JSONResponse({"detail": "missing CSRF header"}, status_code=403)
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if origin and host and not origin.endswith(host):
                return JSONResponse({"detail": "cross-origin request rejected"}, status_code=403)
        return await call_next(request)


def require_app(request: Request):
    app = getattr(request.app.state, "fedr", None)
    if app is None or app.ctx is None:
        raise HTTPException(503, "application is starting")
    return app
