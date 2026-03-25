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


def test_download_blob_bytes_reads_content_from_get_blob_result(monkeypatch) -> None:

    class _FakeResult:
        status_code = 200
        content = b"hello-world"
        content_type = "text/plain"

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

    class _FakeBlob:
        content_type = "image/png"

    class _FakeResult:
        status_code = 200
        content = b"png-bytes"
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


def test_download_blob_bytes_reads_from_response_aiter_bytes(monkeypatch) -> None:

    class _FakeResponse:
        async def aiter_bytes(self):
            for part in [b"a", b"b", b"c"]:
                yield part

    class _FakeResult:
        status_code = 200
        response = _FakeResponse()
        content_type = "application/octet-stream"

    class _FakeClient:
        async def get(self, pathname: str, access: str):
            return _FakeResult()

    monkeypatch.setattr("app.blob_service.AsyncBlobClient", _FakeClient)

    data, content_type = asyncio.run(download_blob_bytes("exams/1/key/fallback.bin"))
    assert data == b"abc"
    assert content_type == "application/octet-stream"


def test_download_blob_bytes_raises_on_unsupported_shape(monkeypatch) -> None:

    class _FakeResult:
        status_code = 200
        pathname = "exams/1/key/odd.bin"

    class _FakeClient:
        async def get(self, pathname: str, access: str):
            return _FakeResult()

    monkeypatch.setattr("app.blob_service.AsyncBlobClient", _FakeClient)

    with pytest.raises(BlobDownloadError, match="Unsupported blob result type"):
        asyncio.run(download_blob_bytes("exams/1/key/odd.bin"))


def test_upload_bytes_uses_sdk_put_signature(monkeypatch) -> None:
    monkeypatch.setenv("BLOB_MOCK", "")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "test-token")
    monkeypatch.setenv("BLOB_PUBLIC_ACCESS", "public")

    import types
    import sys
    from app import blob_store

    calls: dict[str, object] = {}

    def _fake_put(path: str, body: bytes, *, access: str, content_type: str | None = None, add_random_suffix: bool = False, token: str | None = None):
        calls["path"] = path
        calls["body"] = body
        calls["access"] = access
        calls["content_type"] = content_type
        calls["add_random_suffix"] = add_random_suffix
        calls["token"] = token
        return {
            "url": "https://blob.vercel-storage.com/exams/1/key/a.png",
            "pathname": path,
            "contentType": content_type,
        }

    fake_blob_module = types.ModuleType("vercel.blob")
    fake_blob_module.put = _fake_put  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "vercel.blob", fake_blob_module)

    result = blob_store.upload_bytes("exams/1/key/a.png", b"png-data", "image/png")

    assert result["pathname"] == "exams/1/key/a.png"
    assert result["url"].endswith("/exams/1/key/a.png")
    assert result["contentType"] == "image/png"
    assert calls == {
        "path": "exams/1/key/a.png",
        "body": b"png-data",
        "access": "public",
        "content_type": "image/png",
        "add_random_suffix": False,
        "token": "test-token",
    }


def test_upload_bytes_accepts_sdk_result_object_shape(monkeypatch) -> None:
    monkeypatch.setenv("BLOB_MOCK", "")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "test-token")
    monkeypatch.setenv("BLOB_PUBLIC_ACCESS", "private")

    import sys
    import types
    from app import blob_store

    class _PutBlobResult:
        url = "https://blob.vercel-storage.com/exams/1/key/object.png"
        pathname = "exams/1/key/object.png"
        content_type = "image/png"
        download_url = "https://blob.vercel-storage.com/exams/1/key/object.png?download=1"

    def _fake_put(path: str, body: bytes, *, access: str, content_type: str | None = None, add_random_suffix: bool = False, token: str | None = None):
        assert path == "exams/1/key/object.png"
        assert body == b"png-data"
        assert access == "private"
        assert content_type == "image/png"
        assert add_random_suffix is False
        assert token == "test-token"
        return _PutBlobResult()

    fake_blob_module = types.ModuleType("vercel.blob")
    fake_blob_module.put = _fake_put  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "vercel.blob", fake_blob_module)

    result = blob_store.upload_bytes("exams/1/key/object.png", b"png-data", "image/png")

    assert result == {
        "url": "https://blob.vercel-storage.com/exams/1/key/object.png",
        "pathname": "exams/1/key/object.png",
        "contentType": "image/png",
        "downloadUrl": "https://blob.vercel-storage.com/exams/1/key/object.png?download=1",
    }


def test_upload_bytes_raises_clear_error_on_sdk_signature_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("BLOB_MOCK", "")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "test-token")

    import types
    import sys
    from app import blob_store

    def _bad_put(path: str, body: bytes):
        return {"url": "https://blob.vercel-storage.com/bad.png", "pathname": path, "contentType": "image/png"}

    fake_blob_module = types.ModuleType("vercel.blob")
    fake_blob_module.put = _bad_put  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "vercel.blob", fake_blob_module)

    with pytest.raises(blob_store.BlobUploadError, match="signature=.*attempted_call_mode"):
        blob_store.upload_bytes("exams/1/key/a.png", b"png-data", "image/png")
