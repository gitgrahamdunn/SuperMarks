from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from app import db
from app.main import app
from app.models import ExamKeyFile
from app.settings import settings


def test_list_exams_returns_created_exams(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        assert client.get("/exams").status_code == 200

        first = client.post("/exams", json={"name": "Midterm"})
        second = client.post("/exams", json={"name": "Final"})
        assert first.status_code == 201
        assert second.status_code == 201

        response = client.get("/exams")
        assert response.status_code == 200

        payload = response.json()
        names = [exam["name"] for exam in payload]
        ids = {exam["id"] for exam in payload}

        assert "Midterm" in names
        assert "Final" in names
        assert first.json()["id"] in ids
        assert second.json()["id"] in ids


def test_parse_answer_key_creates_questions_and_sets_reviewing(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/exams", json={"name": "Physics Final"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        key_dir = Path(settings.data_dir) / "key_pages" / str(exam_id)
        key_dir.mkdir(parents=True, exist_ok=True)
        (key_dir / "page-1.png").write_bytes(b"fake-image-1")
        (key_dir / "page-2.png").write_bytes(b"fake-image-2")
        (key_dir / "page-3.png").write_bytes(b"fake-image-3")

        response = client.post(f"/exams/{exam_id}/key/parse")
        assert response.status_code == 200
        assert response.json()["questions_count"] == 2

        detail = client.get(f"/exams/{exam_id}")
        assert detail.status_code == 200
        payload = detail.json()

        assert payload["exam"]["status"] == "REVIEWING"
        assert len(payload["questions"]) == 2
        labels = {item["label"] for item in payload["questions"]}
        assert labels == {"Q1", "Q2"}


def test_upload_exam_key_files_stores_file_and_db_row(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/exams", json={"name": "Biology Midterm"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        files = [("files", ("key.png", b"\x89PNG\r\n\x1a\n", "image/png"))]
        response = client.post(f"/exams/{exam_id}/key/upload", files=files)

        assert response.status_code == 200
        assert response.json() == {"uploaded": 1}

        stored = Path(settings.data_dir) / "exams" / str(exam_id) / "key" / "key.png"
        assert stored.exists()

    with Session(db.engine) as session:
        rows = session.exec(select(ExamKeyFile).where(ExamKeyFile.exam_id == exam_id)).all()
        assert len(rows) == 1
        assert rows[0].original_filename == "key.png"
        assert rows[0].stored_path == str(stored)
