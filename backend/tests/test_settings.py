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
