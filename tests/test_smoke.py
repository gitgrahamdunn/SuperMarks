from app.settings import settings


def test_settings_exposes_sqlite_url() -> None:
    assert settings.sqlite_url.startswith("sqlite:///")
