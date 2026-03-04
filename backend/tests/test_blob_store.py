from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import SQLModel, Session, create_engine

from app import db
from app.blob_store import download_blob_bytes, get_signed_read_url
from app.main import app
from app.models import SubmissionFile
from app.settings import settings


def test_blob_store_mock_helpers(monkeypatch) -> None:
    monkeypatch.setenv("BLOB_MOCK", "1")
    signed = asyncio.run(get_signed_read_url("exams/1/key/file.pdf"))
    data, content_type = asyncio.run(download_blob_bytes("exams/1/key/file.pdf"))

    assert signed == "https://example.com/mock"
    assert data.startswith(b"%PDF-1.4")
    assert content_type == "application/pdf"


def _png_bytes() -> bytes:
    image = Image.new("RGB", (16, 16), "white")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_build_pages_download_uses_stored_pathname_not_blob_url(tmp_path: Path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    captured: dict[str, str] = {}

    async def _fake_download(pathname: str) -> tuple[bytes, str]:
        captured["pathname"] = pathname
        return _png_bytes(), "image/png"

    monkeypatch.setattr("app.storage_provider.download_blob_bytes", _fake_download)
    monkeypatch.setenv("BLOB_MOCK", "")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "token")

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Blob Path Exam"})
        exam_id = exam.json()["id"]
        submission = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "S"})
        submission_id = submission.json()["id"]

        with Session(db.engine) as session:
            session.add(
                SubmissionFile(
                    submission_id=submission_id,
                    file_kind="image",
                    original_filename="a.png",
                    stored_path=f"exams/{exam_id}/submissions/{submission_id}/correct-path.png",
                    blob_url="https://blob.vercel-storage.com/wrong-public-url.png",
                    content_type="image/png",
                    size_bytes=12,
                )
            )
            session.commit()

        pages = client.post(f"/api/submissions/{submission_id}/build-pages")
        assert pages.status_code == 200

    assert captured["pathname"].endswith("correct-path.png")
