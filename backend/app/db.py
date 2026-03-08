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
    if not _is_sqlite_url(settings.effective_database_url):
        return
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        existing = {row[1] for row in rows}
        if column not in existing:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def create_db_and_tables() -> None:
    """Create all SQLModel tables if they do not exist."""
    SQLModel.metadata.create_all(engine)
    _ensure_column("examkeyfile", "blob_url", "blob_url VARCHAR")
    _ensure_column("examkeyfile", "blob_pathname", "blob_pathname VARCHAR")
    _ensure_column("submissionfile", "blob_url", "blob_url VARCHAR")
    _ensure_column("submissionfile", "blob_pathname", "blob_pathname VARCHAR")



def get_session() -> Generator[Session, None, None]:
    """Yield a database session for request-scoped dependency injection."""
    with Session(engine) as session:
        yield session
