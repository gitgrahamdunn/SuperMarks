from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import SQLModel, Session, create_engine
import pytest

from app import db
from app.blob_service import BlobDownloadError, download_blob_bytes, normalize_blob_path
from app.main import app
from app.models import SubmissionFile
from app.settings import settings


def test_download_blob_bytes_streams_from_async_blob_client(monkeypatch) -> None:

    class _FakeStream:
        def __aiter__(self):
            self._parts = [b"hello", b"-", b"world"]
            return self

        async def __anext__(self):
            if not self._parts:
                raise StopAsyncIteration
            return self._parts.pop(0)

    class _FakeBlob:
        content_type = "text/plain"

    class _FakeResult:
        status_code = 200
        stream = _FakeStream()
        blob = _FakeBlob()

    class _FakeClient:
        async def get(self, pathname: str, access: str):
            assert pathname == "exams/1/key/file.txt"
            assert access == "private"
            return _FakeResult()

    monkeypatch.setattr("app.blob_service.AsyncBlobClient", _FakeClient)

    data, content_type = asyncio.run(download_blob_bytes("exams/1/key/file.txt"))
    assert data == b"hello-world"
    assert content_type == "text/plain"


def test_normalize_blob_path_for_path_and_full_url() -> None:
    assert normalize_blob_path("exams/1/key/a.png") == "exams/1/key/a.png"
    assert normalize_blob_path("https://blob.vercel-storage.com/exams/1/key/a.png") == "exams/1/key/a.png"


def test_materialize_object_to_path_normalizes_blob_url(tmp_path: Path, monkeypatch) -> None:
    from app.storage_provider import materialize_object_to_path

    captured: list[str] = []

    async def _fake_download(pathname: str) -> tuple[bytes, str | None]:
        captured.append(pathname)
        return b"blob-bytes", "image/png"

    monkeypatch.setattr("app.storage_provider.download_blob_bytes", _fake_download)
    asyncio.run(materialize_object_to_path("exams/12/key/sample.png", tmp_path / "cache"))
    asyncio.run(materialize_object_to_path("https://blob.vercel-storage.com/exams/34/key/from-url.png", tmp_path / "cache"))

    assert captured == ["exams/12/key/sample.png", "exams/34/key/from-url.png"]



def test_download_blob_bytes_with_blob_mock_uses_private_sdk_get(monkeypatch) -> None:
    monkeypatch.setenv("BLOB_MOCK", "1")

    class _FakeStream:
        def __aiter__(self):
            self._parts = [b"png", b"-", b"bytes"]
            return self

        async def __anext__(self):
            if not self._parts:
                raise StopAsyncIteration
            return self._parts.pop(0)

    class _FakeBlob:
        content_type = "image/png"

    class _FakeResult:
        status_code = 200
        stream = _FakeStream()
        blob = _FakeBlob()

    class _FakeClient:
        async def get(self, pathname: str, access: str):
            assert pathname == "exams/1/key/a.png"
            assert access == "private"
            return _FakeResult()

    monkeypatch.setattr("app.blob_service.AsyncBlobClient", _FakeClient)

    data, content_type = asyncio.run(download_blob_bytes("exams/1/key/a.png"))
    assert data == b"png-bytes"
    assert content_type == "image/png"



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

    async def _fake_download(pathname: str) -> tuple[bytes, str | None]:
        captured["pathname"] = pathname
        return _png_bytes(), "image/png"

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
                    blob_url="https://blob.vercel-storage.com/wrong-public-url.png",
                    content_type="image/png",
                    size_bytes=12,
                )
            )
            session.commit()

        pages = client.post(f"/api/submissions/{submission_id}/build-pages")
        assert pages.status_code == 200

    assert captured["pathname"].endswith("correct-path.png")


def test_download_blob_bytes_raises_on_missing(monkeypatch) -> None:
    monkeypatch.setenv("BLOB_MOCK", "")

    class _FakeClient:
        async def get(self, pathname: str, access: str):
            return None

    monkeypatch.setattr("app.blob_service.AsyncBlobClient", _FakeClient)

    with pytest.raises(BlobDownloadError):
        asyncio.run(download_blob_bytes("exams/1/key/file.pdf"))
