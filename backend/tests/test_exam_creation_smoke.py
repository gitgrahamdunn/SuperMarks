from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from app import db
from app.ai.openai_vision import ParseResult
from app.main import app
from app.routers.exams import get_answer_key_parser
from app.settings import settings


class _StubAnswerKeyParser:
    def parse(self, image_paths: list[Path], model: str) -> ParseResult:
        assert image_paths
        return ParseResult(
            payload={
                "confidence_score": 0.95,
                "questions": [
                    {
                        "label": "Q1",
                        "max_marks": 2,
                        "criteria": [{"desc": "Correct", "marks": 2}],
                        "answer_key": "42",
                    }
                ],
            },
            model=model,
        )


def test_exam_creation_workflow_smoke(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    app.dependency_overrides[get_answer_key_parser] = lambda: _StubAnswerKeyParser()

    try:
        with TestClient(app) as client:
            create_exam_response = client.post("/api/exams", json={"name": "Smoke Exam"})
            assert create_exam_response.status_code == 201
            create_exam_json = create_exam_response.json()
            assert isinstance(create_exam_json["id"], int)
            assert create_exam_json["name"] == "Smoke Exam"

            exam_id = create_exam_json["id"]
            upload_response = client.post(
                f"/api/exams/{exam_id}/key/upload",
                files=[("files", ("key.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
            )
            assert upload_response.status_code == 200
            assert upload_response.json() == {"uploaded": 1}

            key_pages_dir = Path(settings.data_dir) / "key_pages" / str(exam_id)
            key_pages_dir.mkdir(parents=True, exist_ok=True)
            (key_pages_dir / "page-1.png").write_bytes(b"fake-image")

            parse_response = client.post(f"/api/exams/{exam_id}/key/parse")
            assert parse_response.status_code == 200
            parse_json = parse_response.json()
            assert parse_json["ok"] is True
            assert isinstance(parse_json["model_used"], str)
            assert isinstance(parse_json["confidence_score"], float)
            assert parse_json["questions_count"] == 1
    finally:
        app.dependency_overrides.pop(get_answer_key_parser, None)
