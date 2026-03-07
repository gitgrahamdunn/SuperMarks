from app.db import _redact_database_url


def test_redacts_database_url_password() -> None:
    redacted = _redact_database_url("postgresql://demo:secret@db.example.com:5432/supermarks")
    assert redacted == "postgresql://demo:***@db.example.com:5432/supermarks"


def test_leaves_database_url_without_credentials() -> None:
    url = "sqlite:////tmp/supermarks.db"
    assert _redact_database_url(url) == url
