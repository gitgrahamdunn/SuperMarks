from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from app import db
from api.index import app as api_app
from app.main import app
from app.settings import settings


def test_exams_requires_api_key_when_configured(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BACKEND_API_KEY", "test-api-key")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        unauthorized = client.get("/exams")
        assert unauthorized.status_code == 401

        authorized = client.get("/exams", headers={"X-API-Key": "test-api-key"})
        assert authorized.status_code == 200



def test_preflight_bypasses_auth_but_get_requires_api_key(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BACKEND_API_KEY", "test-api-key")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(api_app) as client:
        preflight = client.options("/api/exams")
        cors_preflight = client.options(
            "/api/exams",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        unauthorized_get = client.get("/api/exams")
        authorized_get = client.get("/api/exams", headers={"X-API-Key": "test-api-key"})

    assert preflight.status_code == 204
    assert cors_preflight.status_code in (200, 204)
    assert "access-control-allow-origin" in cors_preflight.headers
    assert unauthorized_get.status_code == 401
    assert authorized_get.status_code == 200


def test_public_routes_bypass_auth_when_configured(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BACKEND_API_KEY", "test-api-key")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(api_app) as client:
        root_response = client.get("/api/")
        health_response = client.get("/api/health")
        deep_health_response = client.get("/api/health/deep")
        docs_response = client.get("/api/docs")
        openapi_response = client.get("/api/openapi.json")
        redoc_response = client.get("/api/redoc")
        favicon_ico_response = client.get("/api/favicon.ico")
        favicon_png_response = client.get("/api/favicon.png")

    assert root_response.status_code == 200
    assert root_response.json() == {"ok": True, "service": "supermarks-backend"}
    assert health_response.status_code == 200
    assert deep_health_response.status_code == 200
    assert docs_response.status_code == 200
    assert openapi_response.status_code == 200
    assert redoc_response.status_code == 200
    assert favicon_ico_response.status_code == 204
    assert favicon_png_response.status_code == 204
