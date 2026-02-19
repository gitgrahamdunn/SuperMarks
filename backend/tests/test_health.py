import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app.main import app


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
