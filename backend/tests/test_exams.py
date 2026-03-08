from __future__ import annotations

import json
import logging

from pathlib import Path

import httpx
import pytest

pytest.importorskip("httpx")

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from app import db
from app.ai.openai_vision import OpenAIAnswerKeyParser, ParseResult
from app.main import app
from app.routers import exams as exams_router
from app.models import ExamKeyFile, ExamKeyPage, ExamKeyParseJob, ExamKeyParsePage, Question, Submission, SubmissionStatus
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


def _tiny_pdf_bytes() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


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
        payload = response.json()
        assert payload["questions_count"] == 2
        assert payload["model_used"] == "gpt-5-mini"
        assert payload["request_id"]
        assert payload["stage"] == "save_questions"
        assert isinstance(payload["timings"]["openai_ms"], int)
        assert "No key page images found" not in response.text

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
        for question in questions:
            rubric = json.loads(question.rubric_json)
            assert rubric["source_page_number"] == 1
            assert rubric["key_page_number"] == 1
            assert isinstance(rubric.get("original_label"), str)
            assert int(rubric["parse_order"]) > 0



def test_upload_exam_key_files_stores_file_and_db_row(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    monkeypatch.setenv("BLOB_MOCK", "1")
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
        payload = response.json()
        assert payload["uploaded"] == 1
        assert payload["urls"][0].startswith("https://blob.mock.local/exams/")

    with Session(db.engine) as session:
        rows = session.exec(select(ExamKeyFile).where(ExamKeyFile.exam_id == exam_id)).all()
        assert len(rows) == 1
        assert rows[0].original_filename == "key.png"
        assert rows[0].stored_path.startswith(f"exams/{exam_id}/key/")
        assert rows[0].blob_url and rows[0].blob_url.startswith("https://blob.mock.local/exams/")
        assert rows[0].content_type == "image/png"
        assert rows[0].size_bytes > 0


def test_list_exam_key_files_includes_signed_url(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    monkeypatch.setenv("BLOB_MOCK", "1")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Biology Midterm"})
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )
        assert upload.status_code == 200

        response = client.get(f"/api/exams/{exam_id}/key/files")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 1
        assert payload[0]["signed_url"] == "https://example.com/mock"
        assert payload[0]["stored_path"].startswith(f"exams/{exam_id}/key/")
        assert payload[0]["content_type"] == "image/png"


def test_patch_question_updates_fields_and_list_reflects_changes(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Patch Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        question = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 4, "rubric_json": {"key_page_number": 1, "criteria": []}},
        )
        assert question.status_code == 201
        question_id = question.json()["id"]

        patch = client.patch(
            f"/api/exams/{exam_id}/questions/{question_id}",
            json={"max_marks": 6, "rubric_json": {"key_page_number": 2, "criteria": [{"desc": "correct", "marks": 6}]}},
        )
        assert patch.status_code == 200
        assert patch.json()["max_marks"] == 6
        assert patch.json()["rubric_json"]["key_page_number"] == 2

        listed = client.get(f"/api/exams/{exam_id}/questions")
        assert listed.status_code == 200
        assert listed.json()[0]["max_marks"] == 6
        assert listed.json()[0]["rubric_json"]["key_page_number"] == 2


def test_key_page_image_endpoint_returns_image_content_type_without_auth(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BACKEND_API_KEY", "test-api-key")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Image Endpoint Exam"}, headers={"X-API-Key": "test-api-key"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
            headers={"X-API-Key": "test-api-key"},
        )
        assert upload.status_code == 200

        build = client.post(f"/api/exams/{exam_id}/key/build-pages", headers={"X-API-Key": "test-api-key"})
        assert build.status_code == 200

        image_response = client.get(f"/api/exams/{exam_id}/key/page/1")
        assert image_response.status_code == 200
        assert image_response.headers["content-type"].startswith("image/")


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
        payload = response.json()
        assert payload["detail"] == f"No key files uploaded. Call /api/exams/{exam_id}/key/upload first."
        assert payload["request_id"]
        assert payload["stage"] == "build_key_pages"


def test_parse_answer_key_escalates_nano_to_mini_when_mock_nano_is_low_confidence(tmp_path, monkeypatch, caplog) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Math Final"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )
        assert upload.status_code == 200

        caplog.set_level(logging.INFO)
        caplog.clear()
        response = client.post(f"/api/exams/{exam_id}/key/parse")
        assert response.status_code == 200
        payload = response.json()

        assert payload["model_used"] == "gpt-5-mini"
        assert payload["questions_count"] >= 1
        assert payload["attempts"][0]["model"] == "gpt-5-nano"
        assert payload["attempts"][0]["confidence_score"] == 0.4
        assert payload["attempts"][1]["model"] == "gpt-5-mini"
        assert any(
            "nano questions=0 or low confidence -> escalating to mini" in record.getMessage()
            for record in caplog.records
        )


def test_parse_answer_key_retries_timeout_and_returns_200(tmp_path, monkeypatch, caplog) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    parser = OpenAIAnswerKeyParser.__new__(OpenAIAnswerKeyParser)
    parser._max_images_per_request = 1
    parser._payload_limit_bytes = 2_500_000
    parser._retry_backoffs_seconds = (0.0, 0.0)
    parser._mini_retry_backoffs_seconds = ()

    class _FakeResponse:
        def __init__(self, output_text: str) -> None:
            self.output_text = output_text

    class _FakeResponses:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs):
            _ = kwargs
            self.calls += 1
            if self.calls == 1:
                raise httpx.TimeoutException("Request timed out.")
            return _FakeResponse(
                json.dumps({
                    "confidence_score": 0.81,
                    "warnings": [],
                    "questions": [{
                        "label": "Q1",
                        "max_marks": 4,
                        "marks_source": "explicit",
                        "marks_confidence": 0.9,
                        "marks_reason": "visible",
                        "question_text": "Find x",
                        "answer_key": "x=2",
                        "model_solution": "algebra",
                        "warnings": [],
                        "criteria": [{"desc": "correct", "marks": 4}],
                        "evidence": [{"page_number": 1, "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.2, "kind": "question_box", "confidence": 0.8}],
                    }],
                })
            )

    class _FakeClient:
        def __init__(self) -> None:
            self.responses = _FakeResponses()

    parser._client = _FakeClient()

    app.dependency_overrides = {}
    from app.ai.openai_vision import get_answer_key_parser

    app.dependency_overrides[get_answer_key_parser] = lambda: parser

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Timeout Retry Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )
        assert upload.status_code == 200

        caplog.set_level(logging.INFO)
        response = client.post(f"/api/exams/{exam_id}/key/parse")
        assert response.status_code == 200
        payload = response.json()
        assert payload["questions_count"] == 1
        assert payload["stage"] == "save_questions"
        assert payload["attempts"][0]["model"] == "gpt-5-nano"
        assert any("key/parse openai retry" in r.getMessage() for r in caplog.records)

    app.dependency_overrides = {}



def test_patch_question_updates_fields_and_list_reflects_changes(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Patch Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        create = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 2, "rubric_json": {"criteria": []}},
        )
        assert create.status_code == 201
        question_id = create.json()["id"]

        patch_marks = client.patch(f"/api/exams/{exam_id}/questions/{question_id}", json={"max_marks": 5})
        assert patch_marks.status_code == 200
        assert patch_marks.json()["max_marks"] == 5

        patch_rubric = client.patch(
            f"/api/exams/{exam_id}/questions/{question_id}",
            json={"rubric_json": {"criteria": [{"desc": "method", "marks": 5}], "marks_source": "explicit"}},
        )
        assert patch_rubric.status_code == 200
        assert patch_rubric.json()["rubric_json"]["marks_source"] == "explicit"

        listing = client.get(f"/api/exams/{exam_id}/questions")
        assert listing.status_code == 200
        assert listing.json()[0]["max_marks"] == 5
        assert listing.json()[0]["rubric_json"]["criteria"][0]["marks"] == 5


def test_get_key_page_and_key_visual_returns_image(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Visual Exam"})
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )
        assert upload.status_code == 200

        parse = client.post(f"/api/exams/{exam_id}/key/parse")
        assert parse.status_code == 200

        page = client.get(f"/api/exams/{exam_id}/key/page/1")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("image/")

        questions = client.get(f"/api/exams/{exam_id}/questions")
        question_id = questions.json()[0]["id"]
        visual = client.get(f"/api/exams/{exam_id}/questions/{question_id}/key-visual")
        assert visual.status_code == 200
        assert visual.headers["content-type"].startswith("image/")


def test_parse_answer_key_uses_pdf_renderer_for_pdf_uploads(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    called: dict[str, int] = {"count": 0}

    def _fake_render(input_path: Path, output_dir: Path, start_page_number: int, max_pages: int) -> list[Path]:
        called["count"] += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered = output_dir / f"page_{start_page_number:04d}.png"
        rendered.write_bytes(_tiny_png_bytes())
        return [rendered]

    monkeypatch.setattr("app.routers.exams._render_pdf_pages", _fake_render)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "PDF Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.pdf", _tiny_pdf_bytes(), "application/pdf"))],
        )
        assert upload.status_code == 200

        response = client.post(f"/api/exams/{exam_id}/key/parse")
        assert response.status_code == 200
        assert called["count"] == 1
        assert "PDF rendering not available on serverless" not in response.text


def test_parse_answer_key_returns_400_when_pdf_render_fails(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    def _fake_render_fail(input_path: Path, output_dir: Path, start_page_number: int, max_pages: int) -> list[Path]:
        raise HTTPException(status_code=400, detail="PDF render failed. Try uploading images.")

    monkeypatch.setattr("app.routers.exams._render_pdf_pages", _fake_render_fail)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "PDF Fail Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.pdf", _tiny_pdf_bytes(), "application/pdf"))],
        )
        assert upload.status_code == 200

        response = client.post(f"/api/exams/{exam_id}/key/parse")
        assert response.status_code == 400
        payload = response.json()
        assert payload["detail"] == "PDF render failed. Try uploading images."
        assert payload["stage"] == "build_key_pages"
        assert payload["request_id"]



def test_build_key_pages_returns_502_with_request_id_and_stage(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated render blowup")

    monkeypatch.setattr("app.routers.exams._render_pdf_pages", _boom)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Build Error Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.pdf", _tiny_pdf_bytes(), "application/pdf"))],
        )
        assert upload.status_code == 200

        response = client.post(f"/api/exams/{exam_id}/key/build-pages")
        assert response.status_code == 502
        payload = response.json()
        assert payload["detail"] == "Build key pages failed"
        assert payload["request_id"]
        assert payload["stage"] == "render_pdf"
        assert "simulated render blowup" in payload["message"]


def test_bulk_upload_preview_and_finalize_creates_submissions(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    def _fake_render(input_path: Path, output_dir: Path, start_page_number: int, max_pages: int) -> list[Path]:
        _ = (input_path, start_page_number, max_pages)
        from PIL import Image
        output_dir.mkdir(parents=True, exist_ok=True)
        created = []
        for idx in range(1, 5):
            out = output_dir / f"page_{idx:04d}.png"
            Image.new("RGB", (400, 600), (255, 255, 255)).save(out, format="PNG")
            created.append(out)
        return created

    monkeypatch.setattr("app.routers.exams._render_pdf_pages", _fake_render)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Bulk Upload Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        preview = client.post(
            f"/api/exams/{exam_id}/submissions/bulk",
            files={"file": ("all-tests.pdf", _tiny_pdf_bytes(), "application/pdf")},
        )
        assert preview.status_code == 201
        payload = preview.json()
        assert payload["page_count"] == 4
        assert len(payload["candidates"]) == 2
        assert payload["candidates"][0]["student_name"] == "Alice Johnson"
        assert payload["candidates"][0]["page_start"] == 1
        assert payload["candidates"][0]["page_end"] == 2

        bulk_upload_id = payload["bulk_upload_id"]
        finalize = client.post(
            f"/api/exams/{exam_id}/submissions/bulk/{bulk_upload_id}/finalize",
            json={
                "candidates": [
                    {"student_name": "Alice Johnson", "page_start": 1, "page_end": 2},
                    {"student_name": "Bob Smith", "page_start": 3, "page_end": 4},
                ]
            },
        )
        assert finalize.status_code == 200
        final_payload = finalize.json()
        assert len(final_payload["submissions"]) == 2

        detail = client.get(f"/api/exams/{exam_id}")
        assert detail.status_code == 200
        assert len(detail.json()["submissions"]) == 2


def test_parse_answer_key_incremental_flow(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Incremental Parse"})
        exam_id = exam.json()["id"]
        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key1.png", _tiny_png_bytes(), "image/png")), ("files", ("key2.png", _tiny_png_bytes(), "image/png"))],
        )
        assert upload.status_code == 200

        build_pages = client.post(f"/api/exams/{exam_id}/key/build-pages")
        assert build_pages.status_code == 200

        started = client.post(f"/api/exams/{exam_id}/key/parse/start")
        assert started.status_code == 200
        start_payload = started.json()
        assert start_payload["page_count"] == 2
        assert start_payload["pages_done"] == 0
        job_id = start_payload["job_id"]

        latest = client.get(f"/api/exams/{exam_id}/key/parse/latest")
        assert latest.status_code == 200
        assert latest.json()["exam_exists"] is True
        assert latest.json()["job"]["job_id"] == job_id

        next1 = client.post(f"/api/exams/{exam_id}/key/parse/next", params={"job_id": job_id, "batch_size": 3})
        assert next1.status_code == 200
        assert next1.json()["pages_done"] == 2
        assert next1.json()["pages_processed"] == [1, 2]

        status = client.get(f"/api/exams/{exam_id}/key/parse/status", params={"job_id": job_id})
        assert status.status_code == 200
        assert status.json()["pages_done"] == 2
        assert status.json()["exam_exists"] is True
        assert status.json()["job_exists"] is True

        next2 = client.post(f"/api/exams/{exam_id}/key/parse/next", params={"job_id": job_id})
        assert next2.status_code == 200
        assert next2.json()["pages_done"] == 2

        done = client.post(f"/api/exams/{exam_id}/key/parse/next", params={"job_id": job_id})
        assert done.status_code == 200
        assert done.json()["status"] == "done"

        finished = client.post(f"/api/exams/{exam_id}/key/parse/finish", params={"job_id": job_id})
        assert finished.status_code == 200
        finished_payload = finished.json()
        assert isinstance(finished_payload["questions"], list)
        assert len(finished_payload["questions"]) >= 1

    with Session(db.engine) as session:
        questions = session.exec(select(Question).where(Question.exam_id == exam_id)).all()
        assert len(questions) >= 1


def test_parse_status_job_exam_mismatch(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_a = client.post("/api/exams", json={"name": "Exam A"}).json()["id"]
        exam_b = client.post("/api/exams", json={"name": "Exam B"}).json()["id"]
        client.post(
            f"/api/exams/{exam_a}/key/upload",
            files=[("files", ("key1.png", _tiny_png_bytes(), "image/png"))],
        )
        client.post(f"/api/exams/{exam_a}/key/build-pages")
        started = client.post(f"/api/exams/{exam_a}/key/parse/start")
        job_id = started.json()["job_id"]

        mismatch = client.get(f"/api/exams/{exam_b}/key/parse/status", params={"job_id": job_id})
        assert mismatch.status_code == 409
        assert mismatch.json()["detail"]["detail"] == "Parse job does not belong to this exam"
        assert mismatch.json()["detail"]["exam_exists"] is True
        assert mismatch.json()["detail"]["job_exists"] is True
def test_upload_exam_key_file_over_4mb_returns_413(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BLOB_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Big File Exam"})
        exam_id = exam.json()["id"]

        oversized = b"a" * (4 * 1024 * 1024 + 1)
        response = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("big.pdf", oversized, "application/pdf"))],
        )

        assert response.status_code == 413
        assert response.json()["detail"] == "File too large for server upload on Vercel. Use client upload mode."


def test_parse_start_reuses_unfinished_job(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Reuse Parse Job"}).json()["id"]
        client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key1.png", _tiny_png_bytes(), "image/png")), ("files", ("key2.png", _tiny_png_bytes(), "image/png"))],
        )
        client.post(f"/api/exams/{exam_id}/key/build-pages")

        first = client.post(f"/api/exams/{exam_id}/key/parse/start")
        second = client.post(f"/api/exams/{exam_id}/key/parse/start")

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["job_id"] == second.json()["job_id"]
        assert second.json()["reused"] is True


def test_parse_latest_has_remaining_work_for_pending_or_failed_pages(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Latest Parse Metadata"}).json()["id"]
        client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key1.png", _tiny_png_bytes(), "image/png")), ("files", ("key2.png", _tiny_png_bytes(), "image/png"))],
        )
        client.post(f"/api/exams/{exam_id}/key/build-pages")
        started = client.post(f"/api/exams/{exam_id}/key/parse/start")
        job_id = started.json()["job_id"]

        with Session(db.engine) as session:
            page_two = session.exec(
                select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job_id, ExamKeyParsePage.page_number == 2)
            ).first()
            assert page_two is not None
            page_two.status = "failed"
            session.add(page_two)
            session.commit()

        latest = client.get(f"/api/exams/{exam_id}/key/parse/latest")
        assert latest.status_code == 200
        payload = latest.json()["job"]
        assert payload["has_remaining_work"] is True
        assert payload["failed_pages"] == [2]
        assert 1 in payload["pending_pages"]


def test_retry_parse_page_does_not_create_new_job(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Retry Same Job"}).json()["id"]
        client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key1.png", _tiny_png_bytes(), "image/png")), ("files", ("key2.png", _tiny_png_bytes(), "image/png"))],
        )
        client.post(f"/api/exams/{exam_id}/key/build-pages")
        started = client.post(f"/api/exams/{exam_id}/key/parse/start")
        job_id = started.json()["job_id"]

        with Session(db.engine) as session:
            parse_page = session.exec(
                select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job_id, ExamKeyParsePage.page_number == 1)
            ).first()
            assert parse_page is not None
            parse_page.status = "failed"
            session.add(parse_page)
            session.commit()

        retry = client.post(f"/api/exams/{exam_id}/key/parse/retry", params={"job_id": job_id, "page_number": 1})
        assert retry.status_code == 200
        latest = client.get(f"/api/exams/{exam_id}/key/parse/latest")
        assert latest.status_code == 200
        assert latest.json()["job"]["job_id"] == job_id




def test_parse_next_concurrent_isolated_and_recomputes_totals(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Concurrent Parse"}).json()["id"]
        client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[
                ("files", ("key1.png", _tiny_png_bytes(), "image/png")),
                ("files", ("key2.png", _tiny_png_bytes(), "image/png")),
                ("files", ("key3.png", _tiny_png_bytes(), "image/png")),
            ],
        )
        client.post(f"/api/exams/{exam_id}/key/build-pages")
        started = client.post(f"/api/exams/{exam_id}/key/parse/start")
        job_id = started.json()["job_id"]

    with Session(db.engine) as session:
        page_two = session.exec(
            select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id, ExamKeyPage.page_number == 2)
        ).first()
        assert page_two is not None
        Path(page_two.image_path).unlink()
        page_two.blob_pathname = None
        page_two.blob_url = None
        session.add(page_two)
        session.commit()

    with TestClient(app) as client:
        next_result = client.post(f"/api/exams/{exam_id}/key/parse/next", params={"job_id": job_id, "batch_size": 3})
        assert next_result.status_code == 200
        payload = next_result.json()
        assert payload["pages_processed"] == [1, 2, 3]
        result_by_page = {item["page_number"]: item["status"] for item in payload["page_results"]}
        assert result_by_page[1] == "done"
        assert result_by_page[2] == "failed"
        assert result_by_page[3] == "done"
        assert payload["pages_done"] == 2
        assert payload["status"] == "failed"

        retry = client.post(f"/api/exams/{exam_id}/key/parse/retry", params={"job_id": job_id, "page_number": 2})
        assert retry.status_code == 200
        assert retry.json()["job_id"] == job_id

        start_reuse = client.post(f"/api/exams/{exam_id}/key/parse/start")
        assert start_reuse.status_code == 200
        assert start_reuse.json()["job_id"] == job_id
        assert start_reuse.json()["reused"] is True

    with Session(db.engine) as session:
        pages = session.exec(
            select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job_id).order_by(ExamKeyParsePage.page_number)
        ).all()
        assert [page.status for page in pages] == ["done", "pending", "done"]
        job = session.get(ExamKeyParseJob, job_id)
        assert job is not None
        assert job.pages_done == 2
        assert job.cost_total == pytest.approx(sum(page.cost for page in pages))
        assert job.input_tokens_total == sum(page.input_tokens for page in pages)
        assert job.output_tokens_total == sum(page.output_tokens for page in pages)

        questions = session.exec(select(Question).where(Question.exam_id == exam_id)).all()
        labels = [question.label for question in questions]
        assert len(labels) == len(set(labels))

def test_get_exam_detail_returns_key_files_submissions_and_parse_jobs(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BLOB_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_resp = client.post("/api/exams", json={"name": "History Exam"})
        assert exam_resp.status_code == 201
        exam_id = exam_resp.json()["id"]

        key_upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )
        assert key_upload.status_code == 200

        submission_resp = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Ada"})
        assert submission_resp.status_code == 201

    with Session(db.engine) as session:
        session.add(
            ExamKeyParseJob(
                exam_id=exam_id,
                status="running",
                page_count=2,
                pages_done=1,
                cost_total=0.2,
                input_tokens_total=100,
                output_tokens_total=50,
            )
        )
        session.commit()

    with TestClient(app) as client:
        detail = client.get(f"/api/exams/{exam_id}")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["exam"]["id"] == exam_id
        assert len(payload["key_files"]) == 1
        assert payload["key_files"][0]["original_filename"] == "key.png"
        assert len(payload["submissions"]) == 1
        assert payload["submissions"][0]["student_name"] == "Ada"
        assert len(payload["parse_jobs"]) == 1
        assert payload["parse_jobs"][0]["status"] == "running"


def test_list_exam_submissions_returns_submission_rows(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_resp = client.post("/api/exams", json={"name": "Math Exam"})
        assert exam_resp.status_code == 201
        exam_id = exam_resp.json()["id"]

    with Session(db.engine) as session:
        session.add(Submission(exam_id=exam_id, student_name="Alice", status=SubmissionStatus.UPLOADED))
        session.add(Submission(exam_id=exam_id, student_name="Bob", status=SubmissionStatus.PAGES_READY))
        session.commit()

    with TestClient(app) as client:
        response = client.get(f"/api/exams/{exam_id}/submissions")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload) == 2
        names = [item["student_name"] for item in payload]
        assert "Alice" in names
        assert "Bob" in names


def test_extract_usage_supports_nested_fields() -> None:
    result = ParseResult(
        payload={
            "usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
                "cost_usd": 0.001,
            },
            "response": {
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 3,
                    "total_tokens": 8,
                    "cost": 0.002,
                },
            },
            "meta": {
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 1,
                },
            },
        },
        model="gpt-5-mini",
    )

    input_tokens, output_tokens, cost = exams_router._extract_usage(result)
    assert input_tokens == 18
    assert output_tokens == 11
    assert cost == pytest.approx(0.003)


def test_parse_finish_returns_recomputed_totals(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Finish Totals"}).json()["id"]
        client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key1.png", _tiny_png_bytes(), "image/png")), ("files", ("key2.png", _tiny_png_bytes(), "image/png"))],
        )
        client.post(f"/api/exams/{exam_id}/key/build-pages")
        started = client.post(f"/api/exams/{exam_id}/key/parse/start").json()
        job_id = started["job_id"]

    with Session(db.engine) as session:
        pages = session.exec(select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job_id).order_by(ExamKeyParsePage.page_number)).all()
        assert len(pages) == 2
        pages[0].status = "done"
        pages[0].cost = 0.004
        pages[0].input_tokens = 120
        pages[0].output_tokens = 50
        pages[1].status = "done"
        pages[1].cost = 0.002
        pages[1].input_tokens = 80
        pages[1].output_tokens = 30
        for page in pages:
            session.add(page)
        session.commit()

    with TestClient(app) as client:
        finished = client.post(f"/api/exams/{exam_id}/key/parse/finish", params={"job_id": job_id})
        assert finished.status_code == 200
        payload = finished.json()
        assert payload["status"] == "done"
        assert payload["pages_done"] == 2
        assert payload["page_count"] == 2
        assert payload["totals"]["cost_total"] == pytest.approx(0.006)
        assert payload["totals"]["input_tokens_total"] == 200
        assert payload["totals"]["output_tokens_total"] == 80


def test_list_questions_orders_by_parse_order_then_source_page(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Order Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        q1 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={
                "label": "Q-late",
                "max_marks": 1,
                "rubric_json": {"source_page_number": 3, "parse_order": 3002, "criteria": []},
            },
        )
        q2 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={
                "label": "Q-early",
                "max_marks": 1,
                "rubric_json": {"source_page_number": 1, "parse_order": 1001, "criteria": []},
            },
        )
        q3 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={
                "label": "Q-no-parse-order",
                "max_marks": 1,
                "rubric_json": {"source_page_number": 2, "criteria": []},
            },
        )

        assert q1.status_code == 201
        assert q2.status_code == 201
        assert q3.status_code == 201

        listed = client.get(f"/api/exams/{exam_id}/questions")
        assert listed.status_code == 200

        labels = [item["label"] for item in listed.json()]
        assert labels == ["Q-early", "Q-late", "Q-no-parse-order"]


def test_list_key_pages_reports_exists_on_disk(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Metadata Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )
        assert upload.status_code == 200

        build = client.post(f"/api/exams/{exam_id}/key/build-pages")
        assert build.status_code == 200

        pages = client.get(f"/api/exams/{exam_id}/key/pages")
        assert pages.status_code == 200
        payload = pages.json()
        assert len(payload) == 1
        assert payload[0]["page_number"] == 1
        assert payload[0]["exists_on_disk"] is True
        assert payload[0]["exists_on_storage"] is True
        assert payload[0]["blob_pathname"].startswith(f"exams/{exam_id}/key-pages/")
        assert payload[0]["image_path"]


def test_key_page_image_route_returns_debug_detail_when_file_missing(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Missing Image Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )
        assert upload.status_code == 200

        build = client.post(f"/api/exams/{exam_id}/key/build-pages")
        assert build.status_code == 200

        rows = build.json()
        image_path = Path(settings.data_dir) / rows[0]["image_path"]
        image_path.unlink()

        with Session(db.engine) as session:
            row = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id, ExamKeyPage.page_number == 1)).first()
            assert row is not None
            row.blob_pathname = None
            row.blob_url = None
            session.add(row)
            session.commit()

        missing_image = client.get(f"/api/exams/{exam_id}/key/page/1")
        assert missing_image.status_code == 404
        detail = missing_image.json()["detail"]
        assert detail["message"] == "Key page image missing"
        assert detail["exam_id"] == exam_id
        assert detail["page_number"] == 1
        assert detail["image_path"].endswith("page_0001.png")
        assert detail["blob_pathname"] is None
        assert detail["local_file_exists"] is False

        missing_row = client.get(f"/api/exams/{exam_id}/key/page/2")
        assert missing_row.status_code == 404
        assert missing_row.json()["detail"] == "Key page not found"

def test_build_key_pages_reuses_existing_durable_rows_without_rerender(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Reuse durable key pages"})
        exam_id = exam.json()["id"]
        client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )

        first_build = client.post(f"/api/exams/{exam_id}/key/build-pages")
        assert first_build.status_code == 200

        image_path = Path(settings.data_dir) / first_build.json()[0]["image_path"]
        image_path.unlink()

        second_build = client.post(f"/api/exams/{exam_id}/key/build-pages")
        assert second_build.status_code == 200

    with Session(db.engine) as session:
        rows = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id)).all()
        assert len(rows) == 1
        assert rows[0].blob_pathname is not None


def test_key_page_image_route_serves_blob_when_local_file_deleted(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Blob-backed page"})
        exam_id = exam.json()["id"]
        client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )

        build = client.post(f"/api/exams/{exam_id}/key/build-pages")
        assert build.status_code == 200

        image_path = Path(settings.data_dir) / build.json()[0]["image_path"]
        image_path.unlink()

        image_response = client.get(f"/api/exams/{exam_id}/key/page/1")
        assert image_response.status_code == 200
        assert image_response.headers["content-type"].startswith("image/")
        assert image_response.content.startswith(b"\x89PNG")
