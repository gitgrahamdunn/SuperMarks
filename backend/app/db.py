"""Database engine and session helpers."""

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.settings import settings


engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    """Create all SQLModel tables if they do not exist."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """Yield a database session for request-scoped dependency injection."""
    with Session(engine) as session:
        yield session
