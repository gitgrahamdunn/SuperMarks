import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app.main import app
from app.settings import settings


@pytest.mark.parametrize(
    ("api_key", "expected_openai_configured"),
    [("test-key", True), ("   ", False)],
)
def test_health_returns_openai_configuration_status(monkeypatch, api_key: str, expected_openai_configured: bool) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", api_key)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200

    payload = response.json()
    assert payload["ok"] is True
    assert payload["openai_configured"] is expected_openai_configured


def test_health_deep_returns_storage_and_db_diagnostics(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with TestClient(app) as client:
        response = client.get("/health/deep")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["openai_configured"] is True
    assert payload["storage_writable"] is True
    assert payload["db_ok"] is True
    assert payload["data_dir"] == str(settings.data_path)


def test_health_cors_reports_origins_and_api_key_status(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://frontend.example.com,https://staging.example.com")
    monkeypatch.setenv("BACKEND_API_KEY", "test-api-key")

    with TestClient(app) as client:
        response = client.get("/health/cors")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["origins"] == ["https://frontend.example.com", "https://staging.example.com"]
    assert payload["has_api_key"] is True
