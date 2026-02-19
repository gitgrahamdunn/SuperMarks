"""FastAPI application entrypoint."""

import os
from uuid import uuid4

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import Response
from sqlalchemy import text
from sqlmodel import Session

from app.db import create_db_and_tables
from app.db import engine
from app.routers.exams import router as exams_router
from app.routers.questions import router as questions_router
from app.routers.submissions import router as submissions_router
from app.settings import settings
from app.storage import ensure_dir


def _resolve_cors_origins() -> list[str]:
    configured_cors_origins = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if not configured_cors_origins:
        return ["*"]
    return [origin.strip() for origin in configured_cors_origins.split(",") if origin.strip()]

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_PUBLIC_PATHS = {
    "/health",
    "/health/deep",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/favicon.ico",
    "/favicon.png",
}


@app.middleware("http")
async def enforce_api_key(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    if request.url.path in _PUBLIC_PATHS:
        return await call_next(request)

    expected_api_key = os.getenv("BACKEND_API_KEY", "").strip()
    if expected_api_key:
        received_api_key = request.headers.get("X-API-Key", "")
        if received_api_key != expected_api_key:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)

app.include_router(exams_router)
app.include_router(questions_router)
app.include_router(submissions_router)


@app.on_event("startup")
def on_startup() -> None:
    ensure_dir(settings.data_path)
    create_db_and_tables()


@app.get("/health", tags=["meta"])
def health() -> dict[str, bool]:
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    return {"ok": True, "openai_configured": bool(openai_api_key.strip())}


@app.get("/health/deep", tags=["meta"])
def deep_health() -> dict[str, bool | str]:
    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    data_dir = settings.data_path

    storage_writable = False
    try:
        ensure_dir(data_dir)
        probe_path = data_dir / f".health_probe_{uuid4().hex}"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
        storage_writable = True
    except OSError:
        storage_writable = False

    db_ok = False
    try:
        with Session(engine) as session:
            session.exec(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "ok": True,
        "openai_configured": bool(openai_api_key.strip()),
        "storage_writable": storage_writable,
        "data_dir": str(data_dir),
        "db_ok": db_ok,
    }


@app.options("/{path:path}", include_in_schema=False)
async def preflight(path: str) -> Response:
    del path
    return Response(status_code=204)
