import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_openai_configuration_status(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200

    payload = response.json()
    assert payload["ok"] is True
    assert "openai_configured" in payload
