"""Vercel ASGI function entrypoint for the SuperMarks backend."""
from starlette.responses import Response

from app.main import app as inner_app

class StripPrefix:
    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path.startswith(self.prefix):
                new_scope = dict(scope)
                new_path = path[len(self.prefix):]
                new_scope["path"] = new_path if new_path else "/"
                scope = new_scope

        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
            origin = headers.get("origin", "*")
            requested_headers = headers.get("access-control-request-headers", "*")
            response = Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
                    "Access-Control-Allow-Headers": requested_headers,
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

app = StripPrefix(inner_app, "/api")
