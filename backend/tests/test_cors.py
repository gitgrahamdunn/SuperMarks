from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.parametrize("path", ["/health", "/exams"])
def test_cors_headers_present(path: str) -> None:
    with TestClient(app) as client:
        response = client.get(path, headers={"Origin": "https://frontend.vercel.app"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
