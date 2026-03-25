from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from app import db
from app.main import app
from app.models import Exam, ExamBulkUploadFile, Submission, SubmissionFile, SubmissionPage, SubmissionStatus
from app.routers import exams as exams_router
from app.routers import submissions as submissions_router
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


def test_exam_intake_persists_bulk_upload_source_manifest(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    uploaded_paths: list[str] = []

    def fake_upload_bytes(pathname: str, data: bytes, content_type: str) -> dict[str, str]:
        uploaded_paths.append(pathname)
        return {
            "pathname": pathname,
            "url": f"https://blob.example/{pathname}",
            "contentType": content_type,
            "downloadUrl": f"https://blob.example/{pathname}",
        }

    monkeypatch.setattr(exams_router, "upload_bytes", fake_upload_bytes)
    monkeypatch.setattr(exams_router, "_spawn_exam_intake_job_thread", lambda job_id: None)

    with TestClient(app) as client:
        response = client.post(
            "/api/exams/intake",
            data={"name": "Durable Intake"},
            files=[
                ("files", ("student-1.png", _tiny_png_bytes(), "image/png")),
                ("files", ("student-2.png", _tiny_png_bytes(), "image/png")),
            ],
        )

    assert response.status_code == 201

    with Session(db.engine) as session:
        bulk = session.exec(select(ExamBulkUploadFile)).one()
        manifest = json.loads(bulk.source_manifest_json or "[]")

    assert len(manifest) == 2
    assert manifest[0]["original_filename"] == "student-1.png"
    assert manifest[0]["blob_pathname"].startswith("exams/")
    assert manifest[0]["local_name"] == "source_0001.png"
    assert uploaded_paths == [
        f"exams/1/bulk/{bulk.id}/source/source_0001.png",
        f"exams/1/bulk/{bulk.id}/source/source_0002.png",
    ]


def test_render_stored_bulk_upload_files_restores_sources_from_blob_manifest(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    source_manifest = [
        {
            "local_name": "source_0001.png",
            "original_filename": "student-1.png",
            "blob_pathname": "exams/1/bulk/1/source/source_0001.png",
            "blob_url": "https://blob.example/exams/1/bulk/1/source/source_0001.png",
            "content_type": "image/png",
            "size_bytes": len(_tiny_png_bytes()),
        }
    ]

    with Session(db.engine) as session:
        exam = Exam(name="Render Durable")
        session.add(exam)
        session.flush()
        bulk = ExamBulkUploadFile(
            exam_id=exam.id,
            original_filename="student-1.png",
            stored_path="",
            source_manifest_json=json.dumps(source_manifest),
        )
        session.add(bulk)
        session.commit()
        session.refresh(bulk)
        bulk_id = bulk.id

    async def fake_download_blob_bytes(pathname: str) -> tuple[bytes, str | None]:
        assert pathname == "exams/1/bulk/1/source/source_0001.png"
        return _tiny_png_bytes(), "image/png"

    monkeypatch.setattr(exams_router, "download_blob_bytes", fake_download_blob_bytes)

    with Session(db.engine) as session:
        bulk = session.get(ExamBulkUploadFile, bulk_id)
        rendered_paths, page_count = exams_router._render_stored_bulk_upload_files(
            bulk,
            Path(settings.data_dir) / "exams" / "1" / "bulk" / str(bulk_id) / "pages",
        )

    assert page_count == 1
    assert len(rendered_paths) == 1
    assert rendered_paths[0].exists()


def test_submission_page_route_rebuilds_missing_page_from_blob_backed_source(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with Session(db.engine) as session:
        exam = Exam(name="Page Rebuild")
        session.add(exam)
        session.flush()

        submission = Submission(
            exam_id=exam.id,
            student_name="Jordan",
            status=SubmissionStatus.UPLOADED,
        )
        session.add(submission)
        session.flush()

        session.add(
            SubmissionFile(
                submission_id=submission.id,
                file_kind="image",
                original_filename="student-1.png",
                stored_path="exams/1/submissions/1/student-1.png",
                blob_pathname="exams/1/submissions/1/student-1.png",
                blob_url="https://blob.example/exams/1/submissions/1/student-1.png",
                content_type="image/png",
                size_bytes=len(_tiny_png_bytes()),
            )
        )
        session.add(
            SubmissionPage(
                submission_id=submission.id,
                page_number=1,
                image_path=str(Path(settings.data_dir) / "pages" / "1" / "1" / "missing.png"),
                width=1,
                height=1,
            )
        )
        session.commit()

    async def fake_materialize_object_to_path(key: str, cache_dir: Path) -> Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / "source.png"
        target.write_bytes(_tiny_png_bytes())
        return target

    monkeypatch.setattr(submissions_router, "materialize_object_to_path", fake_materialize_object_to_path)

    with TestClient(app) as client:
        response = client.get("/api/submissions/1/page/1")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"

    with Session(db.engine) as session:
        rebuilt = session.exec(select(SubmissionPage).where(SubmissionPage.submission_id == 1, SubmissionPage.page_number == 1)).one()
        assert Path(rebuilt.image_path).exists()
