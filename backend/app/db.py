"""Database engine and session helpers."""

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy import text

from app.settings import settings


engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    """Create all SQLModel tables if they do not exist."""
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info('examkeyparserun')") if len(row) > 1}
        if "input_tokens" not in columns:
            conn.execute(text("ALTER TABLE examkeyparserun ADD COLUMN input_tokens INTEGER DEFAULT 0"))
        if "output_tokens" not in columns:
            conn.execute(text("ALTER TABLE examkeyparserun ADD COLUMN output_tokens INTEGER DEFAULT 0"))
        if "total_cost" not in columns:
            conn.execute(text("ALTER TABLE examkeyparserun ADD COLUMN total_cost FLOAT DEFAULT 0.0"))
        if "model_used" not in columns:
            conn.execute(text("ALTER TABLE examkeyparserun ADD COLUMN model_used VARCHAR DEFAULT ''"))


def get_session() -> Generator[Session, None, None]:
    """Yield a database session for request-scoped dependency injection."""
    with Session(engine) as session:
        yield session
