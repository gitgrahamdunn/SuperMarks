from __future__ import annotations

from app.settings import Settings


def test_data_dir_defaults_to_tmp_on_vercel(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("SUPERMARKS_DATA_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)

    settings = Settings()

    assert settings.data_path.as_posix() == "/tmp/supermarks"


def test_data_dir_defaults_to_local_when_not_on_vercel(monkeypatch) -> None:
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("SUPERMARKS_VERCEL_ENVIRONMENT", raising=False)
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
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://frontend-a.vercel.app, https://frontend-b.vercel.app")

    settings = Settings()

    assert settings.cors_origin_list == [
        "https://frontend-a.vercel.app",
        "https://frontend-b.vercel.app",
    ]
