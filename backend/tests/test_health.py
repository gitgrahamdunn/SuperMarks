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
    monkeypatch.delenv("SUPERMARKS_FRONT_PAGE_API_KEY", raising=False)
    monkeypatch.delenv("SUPERMARKS_FRONT_PAGE_PROVIDER", raising=False)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200

    payload = response.json()
    assert payload["ok"] is True
    assert payload["openai_configured"] is expected_openai_configured
    assert payload["front_page_openai_configured"] is expected_openai_configured


def test_health_reports_front_page_provider_override(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_LLM_PROVIDER", "doubleword")
    monkeypatch.setenv("SUPERMARKS_LLM_API_KEY", "doubleword-key")
    monkeypatch.setenv("SUPERMARKS_LLM_BASE_URL", "https://api.doubleword.ai/v1")
    monkeypatch.setenv("SUPERMARKS_FRONT_PAGE_PROVIDER", "openai_compatible")
    monkeypatch.setenv("SUPERMARKS_FRONT_PAGE_API_KEY", "front-page-openai-key")
    monkeypatch.delenv("SUPERMARKS_FRONT_PAGE_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_provider"] == "doubleword"
    assert payload["front_page_llm_provider"] == "openai_compatible"
    assert payload["front_page_openai_configured"] is True
    assert payload["front_page_llm_base_url_configured"] is False


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


def test_root_serves_frontend_index_when_enabled(tmp_path, monkeypatch) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!doctype html><html><body>SuperMarks UI</body></html>", encoding="utf-8")

    monkeypatch.setattr(settings, "serve_frontend", True)
    monkeypatch.setattr(settings, "frontend_dist_dir", str(dist_dir))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "SuperMarks UI" in response.text


def test_unknown_non_api_path_serves_frontend_index_when_enabled(tmp_path, monkeypatch) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!doctype html><html><body>SPA shell</body></html>", encoding="utf-8")
    (dist_dir / "assets").mkdir()
    (dist_dir / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")

    monkeypatch.setattr(settings, "serve_frontend", True)
    monkeypatch.setattr(settings, "frontend_dist_dir", str(dist_dir))

    with TestClient(app) as client:
        route_response = client.get("/exams/123")
        asset_response = client.get("/assets/app.js")

    assert route_response.status_code == 200
    assert "SPA shell" in route_response.text
    assert asset_response.status_code == 200
    assert "console.log('ok')" in asset_response.text
