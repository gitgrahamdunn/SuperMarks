"""Repository modules and provider registry for the staged D1 migration."""

from __future__ import annotations

import os
from functools import lru_cache

from app.repositories.contracts import RepositoryProvider
from app.repositories.sqlmodel_provider import provider as sqlmodel_provider

_SUPPORTED_BACKENDS = {"sqlmodel", "d1", "d1-bridge"}


def repository_backend_name() -> str:
    backend = (os.getenv("SUPERMARKS_REPOSITORY_BACKEND", "sqlmodel") or "sqlmodel").strip().lower()
    if backend not in _SUPPORTED_BACKENDS:
        raise RuntimeError(f"Unsupported repository backend: {backend}")
    return backend


@lru_cache(maxsize=1)
def get_repository_provider() -> RepositoryProvider:
    backend = repository_backend_name()
    if backend == "sqlmodel":
        return sqlmodel_provider

    from app.repositories.d1_provider import get_provider

    return get_provider()
