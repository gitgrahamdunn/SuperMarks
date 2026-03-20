from __future__ import annotations

from pathlib import Path
import time

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from app import db
from app.ai.openai_vision import ParseResult
from app.main import app
from app.routers.exams import get_answer_key_parser
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
                files=[("files", ("key.png", _tiny_png_bytes(), "image/png"))],
            )
            assert upload_response.status_code == 200
            assert upload_response.json()["uploaded"] == 1


            parse_response = client.post(f"/api/exams/{exam_id}/key/parse")
            assert parse_response.status_code == 200
            parse_json = parse_response.json()
            assert parse_json["ok"] is True
            assert parse_json["stage"] == "job_started"
            for _ in range(10):
                finished = client.post(f"/api/exams/{exam_id}/key/parse/finish", params={"job_id": parse_json["job_id"]})
                assert finished.status_code == 200
                if finished.json()["status"] != "running":
                    break
                time.sleep(0.05)
            questions = client.get(f"/api/exams/{exam_id}/questions")
            assert questions.status_code == 200
            assert len(questions.json()) == 1
    finally:
        app.dependency_overrides.pop(get_answer_key_parser, None)
