"""Vercel ASGI function entrypoint for the SuperMarks backend."""

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
                new_path = path[len(self.prefix) :]
                new_scope["path"] = new_path if new_path else "/"
                scope = new_scope

        await self.app(scope, receive, send)


app = StripPrefix(inner_app, "/api")
