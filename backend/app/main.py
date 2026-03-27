"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import text

from app.auth import SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS, build_api_session_cookie_value, require_api_key
from app.ai.openai_vision import (
    _front_page_provider_api_key,
    _front_page_provider_base_url,
    _front_page_provider_name,
)
from app.db import create_db_and_tables, get_database_backend_name, get_redacted_database_url
from app.persistence import open_repository_session
from app.routers.exams import public_router as public_exams_router
from app.routers.exams import _resume_pending_exam_intake_jobs
from app.routers.exams import class_lists_router
from app.routers.exams import router as exams_router
from app.routers.questions import router as questions_router
from app.routers.submissions import router as submissions_router
from app.routers.files import router as files_router
from app.routers.blob import router as blob_router
from app.settings import settings
from app.storage import ensure_dir
from app.cors_safe import SafeCORSMiddleware
from app.d1_bridge import get_d1_bridge_client


logger = logging.getLogger(__name__)


def _frontend_dist_path() -> Path:
    return Path(settings.frontend_dist_dir).resolve()


def _frontend_index_path() -> Path:
    return _frontend_dist_path() / "index.html"


def _should_serve_frontend() -> bool:
    return settings.serve_frontend and _frontend_index_path().is_file()


def _resolve_frontend_file_path(relative_path: str) -> Path | None:
    dist_path = _frontend_dist_path()
    requested_path = relative_path.strip("/")
    if not requested_path:
        return _frontend_index_path()
    candidate = (dist_path / requested_path).resolve()
    try:
        candidate.relative_to(dist_path)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None



def resolve_app_version() -> str:
    app_version = os.getenv("APP_VERSION", "").strip()
    if app_version:
        return app_version
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_dir(settings.data_path)
    if settings.hosted_d1_bridge_enabled and not settings.has_d1_bridge:
        raise RuntimeError(
            "Hosted d1-bridge runtime requires SUPERMARKS_D1_BRIDGE_URL and SUPERMARKS_D1_BRIDGE_TOKEN."
        )
    logger.info(
        "Database backend: %s (%s)",
        get_database_backend_name(),
        get_redacted_database_url(),
    )
    logger.info(
        "Frontend serving: %s (%s)",
        "enabled" if _should_serve_frontend() else "disabled",
        settings.frontend_dist_dir,
    )
    if settings.hosted_d1_bridge_enabled:
        logger.info("Skipping SQLModel bootstrap in hosted d1-bridge mode")
        logger.info("Skipping intake auto-resume at startup in hosted d1-bridge mode")
    else:
        create_db_and_tables()
        _resume_pending_exam_intake_jobs()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(SafeCORSMiddleware)


@app.middleware("http")
async def seed_api_session_cookie(request: Request, call_next):
    response = await call_next(request)
    expected_api_key = os.getenv("BACKEND_API_KEY", "").strip()
    presented_api_key = request.headers.get("x-api-key", "").strip()
    if expected_api_key and presented_api_key == expected_api_key:
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=build_api_session_cookie_value(expected_api_key),
            max_age=SESSION_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            path="/",
        )
    return response

app.include_router(public_exams_router, prefix="/api")
app.include_router(exams_router, prefix="/api", dependencies=[Depends(require_api_key)])
app.include_router(class_lists_router, prefix="/api", dependencies=[Depends(require_api_key)])
app.include_router(questions_router, prefix="/api", dependencies=[Depends(require_api_key)])
app.include_router(submissions_router, prefix="/api", dependencies=[Depends(require_api_key)])
app.include_router(files_router, prefix="/api", dependencies=[Depends(require_api_key)])
app.include_router(blob_router, prefix="/api", dependencies=[Depends(require_api_key)])


@app.get("/", tags=["meta"], response_model=None)
def root() -> Response | dict[str, bool | str]:
    if _should_serve_frontend():
        return FileResponse(_frontend_index_path())
    return {"ok": True, "service": "supermarks-backend"}


@app.get("/favicon.ico", include_in_schema=False, tags=["meta"])
@app.get("/favicon.png", include_in_schema=False, tags=["meta"])
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health", tags=["meta"])
def health() -> dict[str, bool | str]:
    llm_api_key = os.getenv("SUPERMARKS_LLM_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    llm_provider = os.getenv("SUPERMARKS_LLM_PROVIDER", "openai_compatible")
    llm_base_url = os.getenv("SUPERMARKS_LLM_BASE_URL", "") or os.getenv("OPENAI_BASE_URL", "")
    return {
        "ok": True,
        "openai_configured": bool(llm_api_key.strip()),
        "llm_provider": llm_provider,
        "llm_base_url_configured": bool(str(llm_base_url).strip()),
        "front_page_openai_configured": bool(_front_page_provider_api_key().strip()),
        "front_page_llm_provider": _front_page_provider_name(),
        "front_page_llm_base_url_configured": bool(str(_front_page_provider_base_url() or "").strip()),
    }


@app.get("/version", tags=["meta"])
def version() -> dict[str, bool | str]:
    return {"ok": True, "version": resolve_app_version()}


@app.get("/health/deep", tags=["meta"])
def deep_health() -> dict[str, bool | str]:
    llm_api_key = os.getenv("SUPERMARKS_LLM_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    llm_provider = os.getenv("SUPERMARKS_LLM_PROVIDER", "openai_compatible")
    llm_base_url = os.getenv("SUPERMARKS_LLM_BASE_URL", "") or os.getenv("OPENAI_BASE_URL", "")
    front_page_api_key = _front_page_provider_api_key()
    front_page_provider = _front_page_provider_name()
    front_page_base_url = _front_page_provider_base_url()
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
        if settings.hosted_d1_bridge_enabled:
            bridge_health = get_d1_bridge_client().health()
            db_ok = bool(bridge_health.get("ok"))
        else:
            with open_repository_session() as session:
                session.exec(text("SELECT 1"))
            db_ok = True
    except Exception:
        db_ok = False

    return {
        "ok": True,
        "openai_configured": bool(llm_api_key.strip()),
        "llm_provider": llm_provider,
        "llm_base_url_configured": bool(str(llm_base_url).strip()),
        "front_page_openai_configured": bool(front_page_api_key.strip()),
        "front_page_llm_provider": front_page_provider,
        "front_page_llm_base_url_configured": bool(str(front_page_base_url or "").strip()),
        "storage_writable": storage_writable,
        "data_dir": str(data_dir),
        "db_ok": db_ok,
    }

@app.get("/{full_path:path}", include_in_schema=False, response_model=None)
def frontend_spa(full_path: str) -> Response:
    if not _should_serve_frontend():
        raise HTTPException(status_code=404, detail="Not Found")

    resolved_path = _resolve_frontend_file_path(full_path)
    if resolved_path is not None:
        return FileResponse(resolved_path)
    return FileResponse(_frontend_index_path())
