import os

from fastapi import Header
from fastapi import HTTPException
from fastapi import Request


def require_api_key(request: Request, x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if request.method == "OPTIONS":
        return

    expected = os.getenv("BACKEND_API_KEY", "").strip()
    if not expected:
        return

    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
