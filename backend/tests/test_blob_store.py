from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
import pytest

from app import db
from app.blob_service import BlobDownloadError, download_blob_bytes, normalize_blob_path
from app.main import app
from app.models import SubmissionFile
from app.settings import settings


def test_normalize_blob_path_for_path_and_full_url() -> None:
    assert normalize_blob_path("exams/1/key/a.png") == "exams/1/key/a.png"
    assert normalize_blob_path("https://example-r2.dev/exams/1/key/a.png") == "exams/1/key/a.png"


def test_materialize_object_to_path_normalizes_blob_url(tmp_path: Path, monkeypatch) -> None:
    from app.storage_provider import materialize_object_to_path

    captured: list[str] = []

    async def _fake_download(pathname: str) -> tuple[bytes, str | None]:
        captured.append(pathname)
        return b"blob-bytes", "image/png"

    monkeypatch.setattr("app.storage_provider.download_blob_bytes", _fake_download)
    asyncio.run(materialize_object_to_path("exams/12/key/sample.png", tmp_path / "cache"))
    asyncio.run(materialize_object_to_path("https://example-r2.dev/exams/34/key/from-url.png", tmp_path / "cache"))

    assert captured == ["exams/12/key/sample.png", "exams/34/key/from-url.png"]


def test_download_blob_bytes_reads_from_local_storage(tmp_path: Path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.storage_backend = "local"
    monkeypatch.setenv("SUPERMARKS_STORAGE_BACKEND", "local")
    monkeypatch.delenv("BLOB_MOCK", raising=False)

    target = Path(settings.data_dir) / "objects" / "exams/1/key/file.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello-world", encoding="utf-8")

    data, content_type = asyncio.run(download_blob_bytes("exams/1/key/file.txt"))
    assert data == b"hello-world"
    assert content_type is None


def test_download_blob_bytes_raises_on_missing_local(tmp_path: Path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.storage_backend = "local"
    monkeypatch.setenv("SUPERMARKS_STORAGE_BACKEND", "local")
    monkeypatch.delenv("BLOB_MOCK", raising=False)

    with pytest.raises(BlobDownloadError):
        asyncio.run(download_blob_bytes("exams/1/key/file.pdf"))


def test_build_pages_download_uses_stored_pathname_not_blob_url(tmp_path: Path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    captured: dict[str, str] = {}

    async def _fake_download(pathname: str) -> tuple[bytes, str | None]:
        captured["pathname"] = pathname
        return (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
            b"\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
            b"\x0b\xe7\x02\x9d"
            b"\x00\x00\x00\x00IEND\xaeB`\x82",
            "image/png",
        )

    monkeypatch.setattr("app.storage_provider.download_blob_bytes", _fake_download)
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
                    blob_url="https://example-r2.dev/wrong-public-url.png",
                    content_type="image/png",
                    size_bytes=12,
                )
            )
            session.commit()

        pages = client.post(f"/api/submissions/{submission_id}/build-pages")
        assert pages.status_code == 200

    assert captured["pathname"].endswith("correct-path.png")


def test_upload_bytes_writes_local_object(tmp_path: Path, monkeypatch) -> None:
    from app import blob_store

    settings.data_dir = str(tmp_path / "data")
    settings.storage_backend = "local"
    monkeypatch.setenv("SUPERMARKS_STORAGE_BACKEND", "local")
    monkeypatch.delenv("BLOB_MOCK", raising=False)

    result = blob_store.upload_bytes("exams/1/key/a.png", b"png-data", "image/png")

    stored = Path(settings.data_dir) / "objects" / "exams/1/key/a.png"
    assert stored.read_bytes() == b"png-data"
    assert result == {
        "url": "/api/files/local?key=exams/1/key/a.png",
        "pathname": "exams/1/key/a.png",
        "contentType": "image/png",
        "downloadUrl": "/api/files/local?key=exams/1/key/a.png",
    }
