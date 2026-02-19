from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.index import app as api_app
from app.main import app


@pytest.mark.parametrize("path", ["/health", "/exams"])
def test_cors_headers_present(path: str) -> None:
    with TestClient(app) as client:
        response = client.get(path, headers={"Origin": "https://frontend.vercel.app"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_preflight_options_api_exams_allows_cors() -> None:
    with TestClient(api_app) as client:
        response = client.options(
            "/api/exams",
            headers={
                "Origin": "https://frontend.vercel.app",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code in (200, 204)
    assert "access-control-allow-origin" in response.headers
    assert "access-control-allow-methods" in response.headers
    assert "access-control-allow-headers" in response.headers
