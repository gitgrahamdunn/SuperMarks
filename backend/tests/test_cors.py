from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.index import app as api_app
from app.main import app


@pytest.mark.parametrize("path", ["/health", "/api/exams"])
def test_cors_headers_present(path: str) -> None:
    with TestClient(app) as client:
        response = client.get(path, headers={"Origin": "https://example.com"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.parametrize("client_app,path", [(app, "/api/exams"), (api_app, "/api/exams")])
def test_preflight_options_exams_allows_cors(client_app, path: str) -> None:
    with TestClient(client_app) as client:
        response = client.options(
            path,
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code in (200, 204)
    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] in {"*", "https://example.com"}
    assert "access-control-allow-methods" in response.headers
    assert "access-control-allow-headers" in response.headers


@pytest.mark.parametrize("client_app,path", [(app, "/api/exams"), (api_app, "/api/exams")])
def test_post_exams_still_available_after_preflight(client_app, path: str) -> None:
    with TestClient(client_app) as client:
        preflight = client.options(
            path,
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        created = client.post(path, json={"name": "Preflight Regression Exam"})

    assert preflight.status_code in (200, 204)
    assert "access-control-allow-origin" in preflight.headers
    assert created.status_code == 201
    payload = created.json()
    assert payload["name"] == "Preflight Regression Exam"
