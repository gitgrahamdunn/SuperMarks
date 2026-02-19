from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from app import db
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
