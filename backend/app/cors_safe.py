"""ASGI CORS middleware that avoids mutating response bodies/content-length."""

from __future__ import annotations

import os
from collections.abc import Iterable

_ALLOWED_METHODS = b"GET,POST,PUT,PATCH,DELETE,OPTIONS"
_ALLOWED_HEADERS = b"Content-Type, X-API-Key, Authorization"
_EXPOSE_HEADERS = b"Content-Type"


def _allowed_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _origin_header(scope_headers: Iterable[tuple[bytes, bytes]]) -> str | None:
    for key, value in scope_headers:
        if key == b"origin":
            return value.decode("utf-8")
    return None


class SafeCORSMiddleware:
    def __init__(self, app):
        self.app = app
        self.origins = _allowed_origins()

    def _allow_origin(self, request_origin: str | None) -> str | None:
        if not request_origin:
            return None
        if self.origins == ["*"]:
            return request_origin
        if request_origin in self.origins:
            return request_origin
        return None

    @staticmethod
    def _append_cors_headers(headers: list[tuple[bytes, bytes]], allow_origin: str | None) -> list[tuple[bytes, bytes]]:
        if allow_origin:
            headers.append((b"access-control-allow-origin", allow_origin.encode("utf-8")))
            headers.append((b"access-control-allow-credentials", b"true"))
        headers.append((b"vary", b"Origin"))
        headers.append((b"access-control-allow-methods", _ALLOWED_METHODS))
        headers.append((b"access-control-allow-headers", _ALLOWED_HEADERS))
        headers.append((b"access-control-expose-headers", _EXPOSE_HEADERS))
        return headers

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        origin = _origin_header(scope.get("headers") or [])
        allow_origin = self._allow_origin(origin)

        if method == "OPTIONS":
            headers = self._append_cors_headers([(b"content-length", b"0")], allow_origin)
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": headers,
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                message["headers"] = self._append_cors_headers(headers, allow_origin)
            await send(message)

        await self.app(scope, receive, send_wrapper)
