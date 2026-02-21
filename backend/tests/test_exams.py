from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from app import db
from app.main import app
from app.models import ExamKeyFile, ExamKeyPage, Question
from app.settings import settings

def _tiny_png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
        b"\x0b\xe7\x02\x9d"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_list_exams_returns_created_exams(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        assert client.get("/api/exams").status_code == 200

        first = client.post("/api/exams", json={"name": "Midterm"})
        second = client.post("/api/exams", json={"name": "Final"})
        assert first.status_code == 201
        assert second.status_code == 201

        response = client.get("/api/exams")
        assert response.status_code == 200

        payload = response.json()
        names = [exam["name"] for exam in payload]
        ids = {exam["id"] for exam in payload}

        assert "Midterm" in names
        assert "Final" in names
        assert first.json()["id"] in ids
        assert second.json()["id"] in ids


def test_parse_answer_key_builds_pages_from_uploaded_images(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Physics Final"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )
        assert upload.status_code == 200

        response = client.post(f"/api/exams/{exam_id}/key/parse")
        assert response.status_code == 200
        assert response.json()["questions_count"] == 2

        key_pages_dir = Path(settings.data_dir) / "exams" / str(exam_id) / "key_pages"
        page_files = sorted(key_pages_dir.glob("*.png"))
        assert page_files

    with Session(db.engine) as session:
        key_pages = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id)).all()
        questions = session.exec(select(Question).where(Question.exam_id == exam_id)).all()
        assert len(key_pages) == 1
        assert key_pages[0].page_number == 1
        assert Path(key_pages[0].image_path).exists()
        assert len(questions) == 2


def test_upload_exam_key_files_stores_file_and_db_row(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Biology Midterm"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        files = [("files", ("key.png", b"\x89PNG\r\n\x1a\n", "image/png"))]
        response = client.post(f"/api/exams/{exam_id}/key/upload", files=files)

        assert response.status_code == 200
        assert response.json() == {"uploaded": 1}

        stored = Path(settings.data_dir) / "exams" / str(exam_id) / "key" / "key.png"
        assert stored.exists()

    with Session(db.engine) as session:
        rows = session.exec(select(ExamKeyFile).where(ExamKeyFile.exam_id == exam_id)).all()
        assert len(rows) == 1
        assert rows[0].original_filename == "key.png"
        assert rows[0].stored_path == str(stored)


def test_parse_answer_key_without_uploaded_files_returns_actionable_400(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Chemistry"})
        exam_id = exam.json()["id"]

        response = client.post(f"/api/exams/{exam_id}/key/parse")
        assert response.status_code == 400
        assert response.json()["detail"] == f"No key files uploaded. Call /api/exams/{exam_id}/key/upload first."
