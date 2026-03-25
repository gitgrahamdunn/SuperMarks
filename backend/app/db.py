"""Database engine and session helpers."""

from __future__ import annotations

import logging
from collections.abc import Generator
from urllib.parse import urlsplit, urlunsplit

from sqlmodel import Session, SQLModel, create_engine

from app.settings import settings

logger = logging.getLogger(__name__)


def _redact_database_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.username and not parsed.password:
        return url

    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"

    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo = f"{userinfo}:***"
        netloc = f"{userinfo}@{host}"
    else:
        netloc = host

    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _is_sqlite_url(url: str) -> bool:
    return url.lower().startswith("sqlite")


def _create_engine():
    database_url = settings.effective_database_url
    backend = "sqlite" if _is_sqlite_url(database_url) else "postgres"
    redacted_url = _redact_database_url(database_url)
    logger.info("database backend: %s", backend)
    logger.info("database url: %s", redacted_url)
    try:
        if _is_sqlite_url(database_url):
            return create_engine(database_url, connect_args={"check_same_thread": False})
        return create_engine(database_url)
    except Exception as exc:
        logger.exception(
            "failed to initialize database engine for backend=%s url=%s: %s",
            backend,
            redacted_url,
            exc,
        )
        raise


def get_database_backend_name() -> str:
    return "sqlite" if _is_sqlite_url(settings.effective_database_url) else "postgres"


def get_redacted_database_url() -> str:
    return _redact_database_url(settings.effective_database_url)


engine = _create_engine()


def _ensure_column(table: str, column: str, ddl: str) -> None:
    with engine.begin() as conn:
        if _is_sqlite_url(settings.effective_database_url):
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            existing = {row[1] for row in rows}
        else:
            rows = conn.exec_driver_sql(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = %(table)s
                  AND column_name = %(column)s
                """,
                {"table": table, "column": column},
            ).fetchall()
            existing = {row[0] for row in rows}

        if column not in existing:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    logger.info("ensured column %s.%s", table, column)


def create_db_and_tables() -> None:
    """Create all SQLModel tables if they do not exist."""
    SQLModel.metadata.create_all(engine)
    _ensure_column("examkeyfile", "blob_url", "blob_url VARCHAR")
    _ensure_column("examkeyfile", "blob_pathname", "blob_pathname VARCHAR")
    _ensure_column("submissionfile", "blob_url", "blob_url VARCHAR")
    _ensure_column("submissionfile", "blob_pathname", "blob_pathname VARCHAR")
    _ensure_column("examkeypage", "blob_url", "blob_url VARCHAR")
    _ensure_column("examkeypage", "blob_pathname", "blob_pathname VARCHAR")
    _ensure_column("submission", "capture_mode", "capture_mode VARCHAR DEFAULT 'question_level'")
    _ensure_column("submission", "first_name", "first_name VARCHAR DEFAULT ''")
    _ensure_column("submission", "last_name", "last_name VARCHAR DEFAULT ''")
    _ensure_column("submission", "front_page_totals_json", "front_page_totals_json TEXT")
    _ensure_column("submission", "front_page_candidates_json", "front_page_candidates_json TEXT")
    _ensure_column("submission", "front_page_usage_json", "front_page_usage_json TEXT")
    _ensure_column("submission", "front_page_reviewed_at", "front_page_reviewed_at VARCHAR")
    _ensure_column("exam", "front_page_template_json", "front_page_template_json TEXT")
    _ensure_column("exam", "class_list_json", "class_list_json TEXT")
    _ensure_column("exam", "class_list_source_json", "class_list_source_json TEXT")
    _ensure_column("examintakejob", "attempt_count", "attempt_count INTEGER DEFAULT 0")
    _ensure_column("examintakejob", "runner_id", "runner_id VARCHAR")
    _ensure_column("examintakejob", "lease_expires_at", "lease_expires_at VARCHAR")
    _ensure_column("examintakejob", "started_at", "started_at VARCHAR")
    _ensure_column("examintakejob", "finished_at", "finished_at VARCHAR")
    _ensure_column("examintakejob", "metrics_json", "metrics_json TEXT")
    _ensure_column("examintakejob", "pages_built", "pages_built INTEGER DEFAULT 0")
    _ensure_column("examintakejob", "candidates_ready", "candidates_ready INTEGER DEFAULT 0")
    _ensure_column("examintakejob", "review_open_threshold", "review_open_threshold INTEGER DEFAULT 0")
    _ensure_column("examintakejob", "initial_review_ready", "initial_review_ready BOOLEAN DEFAULT 0")
    _ensure_column("examintakejob", "fully_warmed", "fully_warmed BOOLEAN DEFAULT 0")
    _ensure_column("examintakejob", "review_ready", "review_ready BOOLEAN DEFAULT 0")
    _ensure_column("examintakejob", "thinking_level", "thinking_level VARCHAR DEFAULT 'low'")
    _ensure_column("examintakejob", "last_progress_at", "last_progress_at VARCHAR")
    _ensure_column("bulkuploadpage", "front_page_usage_json", "front_page_usage_json TEXT")
    _ensure_column("exambulkuploadfile", "source_manifest_json", "source_manifest_json TEXT")



def get_session() -> Generator[Session, None, None]:
    """Yield a database session for request-scoped dependency injection."""
    with Session(engine) as session:
        yield session
