"""API key authentication dependency for protected routers."""

from __future__ import annotations

import os

from fastapi import Header
from fastapi import HTTPException
from fastapi import Request


_PUBLIC_PATHS = {
    "/",
    "/health",
    "/health/deep",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/favicon.ico",
    "/favicon.png",
}


def require_api_key(request: Request, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if request.method == "OPTIONS":
        return

    if request.url.path in _PUBLIC_PATHS:
        return

    expected = os.getenv("BACKEND_API_KEY", "").strip()
    if not expected:
        return

    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
