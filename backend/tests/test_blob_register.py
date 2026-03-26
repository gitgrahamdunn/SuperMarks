from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from app import db
from app.main import app
from app.models import ExamKeyFile, SubmissionFile
from app.settings import settings


def test_blob_mock_endpoints_and_register_rows(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BLOB_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Blob Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        register_key = client.post(
            f"/api/exams/{exam_id}/key/register",
            json={
                "files": [
                    {
                        "original_filename": "key.pdf",
                        "blob_pathname": f"exams/{exam_id}/key/file-1.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 123,
                    }
                ]
            },
        )
        assert register_key.status_code == 200
        assert register_key.json()["registered"] == 1

        create_submission = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Ada"})
        assert create_submission.status_code == 201
        submission_id = create_submission.json()["id"]

        register_submission = client.post(
            f"/api/submissions/{submission_id}/files/register",
            json={
                "files": [
                    {
                        "original_filename": "student.pdf",
                        "blob_pathname": f"exams/{exam_id}/submissions/{submission_id}/file-1.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 456,
                    }
                ]
            },
        )
        assert register_submission.status_code == 200
        assert register_submission.json()["registered"] == 1

        signed_url = client.post("/api/blob/signed-url", json={"pathname": "exams/1/key/file-1.pdf"})
        assert signed_url.status_code == 200
        assert signed_url.json()["url"] == "https://example.com/mock"

    with Session(db.engine) as session:
        key_rows = session.exec(select(ExamKeyFile)).all()
        submission_rows = session.exec(select(SubmissionFile)).all()
        assert len(key_rows) == 1
        assert key_rows[0].stored_path.endswith("file-1.pdf")
        assert len(submission_rows) == 1
        assert submission_rows[0].stored_path.endswith("file-1.pdf")
