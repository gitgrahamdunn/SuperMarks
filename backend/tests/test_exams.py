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
from app.ai.openai_vision import OpenAIAnswerKeyParser
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
        assert payload["usage"]["total_tokens"] >= 0
        assert payload["cost"]["total_cost"] >= 0
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


def test_merge_next_question_merges_payload_and_deletes_next(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Merge Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        first = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 3, "rubric_json": {"question_text": "Part A", "criteria": [{"desc": "A", "marks": 3}], "answer_key": "ans a"}},
        )
        second = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q2", "max_marks": 2, "rubric_json": {"question_text": "Part B", "criteria": [{"desc": "B", "marks": 2}], "answer_key": "ans b"}},
        )
        assert first.status_code == 201
        assert second.status_code == 201

        merged = client.post(f"/api/exams/{exam_id}/questions/{first.json()['id']}/merge-next")
        assert merged.status_code == 200
        payload = merged.json()
        assert payload["questions_count"] == 1
        assert payload["question"]["max_marks"] == 5
        assert len(payload["question"]["rubric_json"]["criteria"]) == 2
        assert payload["question"]["rubric_json"]["merged_from"] == [first.json()["id"], second.json()["id"]]

        listed = client.get(f"/api/exams/{exam_id}/questions")
        assert listed.status_code == 200
        assert len(listed.json()) == 1


def test_split_question_by_criteria_creates_new_question(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Split Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        question = client.post(
            f"/api/exams/{exam_id}/questions",
            json={
                "label": "Q4",
                "max_marks": 10,
                "rubric_json": {
                    "criteria": [
                        {"desc": "method", "marks": 4},
                        {"desc": "answer", "marks": 6},
                    ],
                    "answer_key": "method + answer",
                },
            },
        )
        assert question.status_code == 201
        question_id = question.json()["id"]

        split = client.post(
            f"/api/exams/{exam_id}/questions/{question_id}/split",
            json={"mode": "criteria_index", "criteria_split_index": 1},
        )
        assert split.status_code == 200
        payload = split.json()
        assert payload["questions_count"] == 2
        assert payload["original"]["max_marks"] == 4
        assert payload["created"]["max_marks"] == 6
        assert payload["created"]["label"] == "Q4b"

        listed = client.get(f"/api/exams/{exam_id}/questions")
        assert listed.status_code == 200
        assert len(listed.json()) == 2


def test_exam_cost_endpoint_returns_aggregated_cost(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Cost Endpoint"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        upload = client.post(
            f"/api/exams/{exam_id}/key/upload",
            files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
        )
        assert upload.status_code == 200

        parse = client.post(f"/api/exams/{exam_id}/key/parse")
        assert parse.status_code == 200

        response = client.get(f"/api/exams/{exam_id}/cost")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_cost"] >= 0
        assert payload["total_tokens"] >= 0
        assert "gpt-5-nano" in payload["model_breakdown"]
        assert "gpt-5-mini" in payload["model_breakdown"]
