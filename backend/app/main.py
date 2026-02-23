"""FastAPI application entrypoint."""

import os
from uuid import uuid4

from fastapi import Depends
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from fastapi.responses import Response
from sqlalchemy import text
from sqlmodel import Session

from app.auth import require_api_key
from app import db
from app.db import create_db_and_tables
from app.routers.exams import public_router as public_exams_router
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


class StrategyBCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        return PlainTextResponse("OK", status_code=204, headers=dict(response.headers))


app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    StrategyBCORSMiddleware,
    allow_origins=_resolve_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public_exams_router, prefix="/api")
app.include_router(exams_router, prefix="/api", dependencies=[Depends(require_api_key)])
app.include_router(questions_router, prefix="/api", dependencies=[Depends(require_api_key)])
app.include_router(submissions_router, prefix="/api", dependencies=[Depends(require_api_key)])


@app.on_event("startup")
def on_startup() -> None:
    ensure_dir(settings.data_path)
    create_db_and_tables()


@app.get("/", tags=["meta"])
def root() -> dict[str, bool | str]:
    return {"ok": True, "service": "supermarks-backend"}


@app.get("/favicon.ico", include_in_schema=False, tags=["meta"])
@app.get("/favicon.png", include_in_schema=False, tags=["meta"])
def favicon() -> Response:
    return Response(status_code=204)


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
        with Session(db.engine) as session:
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


@app.options("/api/{path:path}", include_in_schema=False)
async def api_preflight(path: str) -> Response:
    del path
    return Response(status_code=204)
