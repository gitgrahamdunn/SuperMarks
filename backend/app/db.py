"""Database engine and session helpers."""

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.settings import settings


engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})


def _ensure_column(table: str, column: str, ddl: str) -> None:
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
