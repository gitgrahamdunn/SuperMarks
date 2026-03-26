from __future__ import annotations

import pytest

from app.settings import Settings


def test_data_dir_defaults_to_tmp_on_managed_runtime(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT", "1")
    monkeypatch.delenv("SUPERMARKS_DATA_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)

    settings = Settings()

    assert settings.data_path.as_posix() == "/tmp/supermarks"


def test_data_dir_defaults_to_local_when_not_on_managed_runtime(monkeypatch) -> None:
    monkeypatch.delenv("SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT", raising=False)
    monkeypatch.delenv("MANAGED_RUNTIME_ENVIRONMENT", raising=False)
    monkeypatch.delenv("SUPERMARKS_DATA_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)

    settings = Settings()

    assert settings.data_path.as_posix().endswith("/backend/data")


def test_cors_allow_origins_defaults_to_wildcard(monkeypatch) -> None:
    monkeypatch.delenv("SUPERMARKS_CORS_ALLOW_ORIGINS", raising=False)
    monkeypatch.delenv("CORS_ALLOW_ORIGINS", raising=False)

    settings = Settings()

    assert settings.cors_origin_list == ["*"]


def test_cors_allow_origins_from_env(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://frontend-a.pages.dev, https://frontend-b.pages.dev")

    settings = Settings()

    assert settings.cors_origin_list == [
        "https://frontend-a.pages.dev",
        "https://frontend-b.pages.dev",
    ]


def test_database_url_defaults_to_sqlite_locally(monkeypatch) -> None:
    monkeypatch.delenv("SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT", raising=False)
    monkeypatch.delenv("MANAGED_RUNTIME_ENVIRONMENT", raising=False)
    monkeypatch.delenv("SUPERMARKS_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("SUPERMARKS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings()

    assert settings.effective_database_url.startswith("sqlite:///")


def test_database_url_required_in_production(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT", "1")
    monkeypatch.delenv("SUPERMARKS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SUPERMARKS_ALLOW_PRODUCTION_SQLITE", raising=False)

    settings = Settings()

    with pytest.raises(RuntimeError, match="DATABASE_URL is required in production unless"):
        _ = settings.effective_database_url


def test_database_url_allows_sqlite_for_self_hosted_production(monkeypatch) -> None:
    monkeypatch.delenv("SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT", raising=False)
    monkeypatch.delenv("MANAGED_RUNTIME_ENVIRONMENT", raising=False)
    monkeypatch.setenv("SUPERMARKS_ENV", "production")
    monkeypatch.setenv("SUPERMARKS_ALLOW_PRODUCTION_SQLITE", "1")
    monkeypatch.delenv("SUPERMARKS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings()

    assert settings.effective_database_url.startswith("sqlite:///")


def test_database_url_does_not_allow_sqlite_on_managed_runtime_even_when_opted_in(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_MANAGED_RUNTIME_ENVIRONMENT", "1")
    monkeypatch.setenv("SUPERMARKS_ALLOW_PRODUCTION_SQLITE", "1")
    monkeypatch.delenv("SUPERMARKS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings()

    with pytest.raises(RuntimeError, match="DATABASE_URL is required in production unless"):
        _ = settings.effective_database_url


def test_database_url_uses_database_url_when_present(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost:5432/supermarks")

    settings = Settings()

    assert settings.effective_database_url == "postgresql+psycopg://user:secret@localhost:5432/supermarks"


def test_database_url_normalizes_postgres_scheme(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://user:secret@localhost:5432/supermarks")

    settings = Settings()

    assert settings.effective_database_url == "postgresql+psycopg://user:secret@localhost:5432/supermarks"


def test_database_url_normalizes_postgresql_scheme_and_preserves_query(monkeypatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:secret@localhost:5432/supermarks?sslmode=require&connect_timeout=10",
    )

    settings = Settings()

    assert (
        settings.effective_database_url
        == "postgresql+psycopg://user:secret@localhost:5432/supermarks?sslmode=require&connect_timeout=10"
    )


def test_frontend_dist_dir_defaults_to_repo_frontend_dist(monkeypatch) -> None:
    monkeypatch.delenv("SUPERMARKS_FRONTEND_DIST_DIR", raising=False)
    monkeypatch.delenv("FRONTEND_DIST_DIR", raising=False)

    settings = Settings()

    assert settings.frontend_dist_dir.endswith("/frontend/dist")
