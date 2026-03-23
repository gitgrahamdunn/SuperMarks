from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine

from app import db
from api.index import app as api_app
from app.main import app
from app.models import Submission, SubmissionPage, SubmissionStatus
from app.pipeline.pages import preview_image_path_for_page
from app.settings import settings


def test_exams_requires_api_key_when_configured(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BACKEND_API_KEY", "test-api-key")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        unauthorized = client.get("/api/exams")
        assert unauthorized.status_code == 401

        authorized = client.get("/api/exams", headers={"X-API-Key": "test-api-key"})
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
        root_response = client.get("/")
        health_response = client.get("/health")
        deep_health_response = client.get("/health/deep")
        docs_response = client.get("/docs")
        openapi_response = client.get("/openapi.json")
        redoc_response = client.get("/redoc")
        favicon_ico_response = client.get("/favicon.ico")
        favicon_png_response = client.get("/favicon.png")

    assert root_response.status_code == 200
    assert root_response.json() == {"ok": True, "service": "supermarks-backend"}
    assert health_response.status_code == 200
    assert deep_health_response.status_code == 200
    assert docs_response.status_code == 200
    assert openapi_response.status_code == 200
    assert redoc_response.status_code == 200
    assert favicon_ico_response.status_code == 204
    assert favicon_png_response.status_code == 204


def test_openapi_lists_api_prefixed_exam_paths(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BACKEND_API_KEY", "test-api-key")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(api_app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json().get("paths", {})

    assert "/api/exams" in paths
    assert "/api/exams/{exam_id}/key/upload" in paths
    assert "/api/exams/{exam_id}/key/parse" in paths
    assert "/exams" not in paths


def test_api_key_seeds_session_cookie_for_asset_requests(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BACKEND_API_KEY", "test-api-key")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    image_path = tmp_path / "page.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
        b"\x0b\xe7\x02\x9d"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Cookie Exam"}, headers={"X-API-Key": "test-api-key"})
        exam_id = exam.json()["id"]
        with Session(db.engine) as session:
            submission = Submission(
                exam_id=exam_id,
                student_name="Jordan",
                status=SubmissionStatus.PAGES_READY,
            )
            session.add(submission)
            session.flush()
            session.add(SubmissionPage(submission_id=submission.id, page_number=1, image_path=str(image_path), width=1, height=1))
            session.commit()
            submission_id = submission.id

        bootstrap = client.get("/api/exams", headers={"X-API-Key": "test-api-key"})
        assert bootstrap.status_code == 200
        assert "sm_session" in client.cookies

        image_response = client.get(f"/api/submissions/{submission_id}/page/1")
        assert image_response.status_code == 200
        assert image_response.headers["content-type"].startswith("image/")

        preview_response = client.get(f"/api/submissions/{submission_id}/page/1/preview")
        assert preview_response.status_code == 200
        assert preview_response.headers["content-type"].startswith("image/jpeg")
        assert preview_image_path_for_page(image_path).exists()
