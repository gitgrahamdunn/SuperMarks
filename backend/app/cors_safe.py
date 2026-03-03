"""ASGI CORS middleware that avoids mutating response bodies/content-length."""

from __future__ import annotations

import os


def _allowed_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class SafeCORSMiddleware:
    def __init__(self, app):
        self.app = app
        self.origins = _allowed_origins()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        origin = None
        for key, value in scope.get("headers") or []:
            if key == b"origin":
                origin = value.decode("utf-8")
                break

        allow_origin = "*"
        if self.origins != ["*"] and origin in self.origins:
            allow_origin = origin
        elif self.origins != ["*"]:
            allow_origin = self.origins[0]

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"access-control-allow-origin", allow_origin.encode("utf-8")))
                headers.append((b"access-control-allow-methods", b"GET,POST,PUT,PATCH,DELETE,OPTIONS"))
                headers.append((b"access-control-allow-headers", b"Content-Type, X-API-Key, Authorization"))
                headers.append((b"access-control-expose-headers", b"Content-Type"))
                headers.append((b"vary", b"Origin"))
                message["headers"] = headers
            await send(message)

        if scope.get("method") == "OPTIONS":
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [(b"content-length", b"0")],
                }
            )
            await send_wrapper({"type": "http.response.body", "body": b""})
            return

        await self.app(scope, receive, send_wrapper)
