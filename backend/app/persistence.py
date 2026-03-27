"""Persistence seam for the staged D1 migration.

This module intentionally keeps current SQLModel behavior intact while moving
the rest of the application away from direct `Session` dependency wiring.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, Protocol, runtime_checkable

from sqlmodel import Session

from app import db
from app.db import get_session


@runtime_checkable
class DbSession(Protocol):
    """Minimal session surface currently relied on by routers/services.

    Future D1-backed adapters do not need to emulate the full SQLAlchemy
    session API, only the operations that the application actually uses.
    """

    def get(self, entity: type[Any], ident: Any) -> Any: ...
    def add(self, instance: Any) -> None: ...
    def exec(self, statement: Any, /, *args: Any, **kwargs: Any) -> Any: ...
    def delete(self, instance: Any) -> None: ...
    def refresh(self, instance: Any) -> None: ...
    def flush(self) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class D1BridgeSessionPlaceholder:
    """Minimal placeholder session for repository-only D1 bridge paths."""

    _ERROR = (
        "Direct SQLModel session access is not available when hosted D1 bridge mode is active. "
        "Move this path behind repository methods before running without a SQL engine."
    )

    def get(self, entity: type[Any], ident: Any) -> Any:
        raise RuntimeError(self._ERROR)

    def add(self, instance: Any) -> None:
        raise RuntimeError(self._ERROR)

    def exec(self, statement: Any, /, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(self._ERROR)

    def delete(self, instance: Any) -> None:
        raise RuntimeError(self._ERROR)

    def refresh(self, instance: Any) -> None:
        raise RuntimeError(self._ERROR)

    def flush(self) -> None:
        raise RuntimeError(self._ERROR)

    def commit(self) -> None:
        raise RuntimeError(self._ERROR)

    def rollback(self) -> None:
        raise RuntimeError(self._ERROR)


def get_repository_session() -> Generator[DbSession, None, None]:
    """Request-scoped persistence dependency used by routers."""
    if db.engine is None:
        yield D1BridgeSessionPlaceholder()
        return
    yield from get_session()


@contextmanager
def open_repository_session() -> Generator[DbSession, None, None]:
    """Non-request persistence entrypoint used by startup/background code."""
    if db.engine is None:
        yield D1BridgeSessionPlaceholder()
        return
    with Session(db.engine) as session:
        yield session


def get_repository_backend() -> str:
    """Configured repository backend name."""

    from app.repositories import repository_backend_name

    return repository_backend_name()


def repository_provider():
    """Return the active repository provider.

    This is the selection seam for the later D1 cutover. The SQLModel-backed
    provider remains the default until D1 repository implementations exist.
    """

    from app.repositories import get_repository_provider

    return get_repository_provider()
