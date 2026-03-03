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
    assert response.headers["access-control-allow-origin"] == "https://example.com"
    assert response.headers["vary"] == "Origin"


@pytest.mark.parametrize("client_app,path", [(app, "/api/exams"), (api_app, "/api/exams")])
def test_preflight_options_exams_allows_cors(client_app, path: str) -> None:
    with TestClient(client_app) as client:
        response = client.options(
            path,
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-api-key,content-type",
            },
        )

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["access-control-allow-origin"] == "https://example.com"
    assert response.headers["access-control-allow-methods"] == "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    assert response.headers["access-control-allow-headers"] == "Content-Type, X-API-Key, Authorization"
    assert response.headers["access-control-expose-headers"] == "Content-Type"


@pytest.mark.parametrize("client_app,path", [(app, "/api/exams"), (api_app, "/api/exams")])
def test_get_exams_includes_cors_header_when_origin_present(client_app, path: str) -> None:
    with TestClient(client_app) as client:
        response = client.get(path, headers={"Origin": "https://example.com"})

    assert response.status_code in (200, 401)
    assert response.headers["access-control-allow-origin"] == "https://example.com"


def test_non_api_options_passthrough() -> None:
    with TestClient(app) as client:
        options_response = client.options("/health", headers={"Origin": "https://example.com"})

    assert options_response.status_code == 405
    assert options_response.headers["access-control-allow-origin"] == "https://example.com"
