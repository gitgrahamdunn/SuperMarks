from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import SQLModel, create_engine

from app import db
from app.main import app
from app.settings import settings


def make_image_bytes(text: str) -> bytes:
    image = Image.new("RGB", (400, 200), color="white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 80), text, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_end_to_end_pipeline(tmp_path: Path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_resp = client.post("/api/exams", json={"name": "Algebra Test"})
        assert exam_resp.status_code == 201
        exam_id = exam_resp.json()["id"]

        files = [
            ("files", ("page1.png", make_image_bytes("Q1 x=4"), "image/png")),
            ("files", ("page2.png", make_image_bytes("Q2 y=7"), "image/png")),
        ]
        upload_resp = client.post(
            f"/api/exams/{exam_id}/submissions",
            data={"student_name": "Alice"},
            files=files,
        )
        assert upload_resp.status_code == 201
        submission_id = upload_resp.json()["id"]

        pages_resp = client.post(f"/api/submissions/{submission_id}/build-pages")
        assert pages_resp.status_code == 200
        pages = pages_resp.json()
        assert len(pages) == 2

        q1_resp = client.post(
            f"/api/exams/{exam_id}/questions",
            json={
                "label": "Q1",
                "max_marks": 5,
                "rubric_json": {
                    "total_marks": 5,
                    "criteria": [
                        {"id": "setup", "desc": "Correct equation setup", "marks": 2},
                        {"id": "steps", "desc": "Valid algebra steps", "marks": 2},
                        {"id": "final", "desc": "Correct final answer", "marks": 1},
                    ],
                    "answer_key": "x=4",
                },
            },
        )
        assert q1_resp.status_code == 201
        q1_id = q1_resp.json()["id"]

        q2_resp = client.post(
            f"/api/exams/{exam_id}/questions",
            json={
                "label": "Q2",
                "max_marks": 3,
                "rubric_json": {
                    "total_marks": 3,
                    "criteria": [{"id": "final", "desc": "Correct final answer", "marks": 3}],
                    "answer_key": "y=7",
                },
            },
        )
        assert q2_resp.status_code == 201
        q2_id = q2_resp.json()["id"]

        for qid, page in [(q1_id, 1), (q2_id, 2)]:
            regions_resp = client.post(
                f"/api/questions/{qid}/regions",
                json=[{"page_number": page, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}],
            )
            assert regions_resp.status_code == 200

        crops_resp = client.post(f"/api/submissions/{submission_id}/build-crops")
        assert crops_resp.status_code == 200
        assert crops_resp.json()["count"] == 2

        transcribe_resp = client.post(f"/api/submissions/{submission_id}/transcribe", params={"provider": "stub"})
        assert transcribe_resp.status_code == 200

        grade_resp = client.post(f"/api/submissions/{submission_id}/grade", params={"grader": "rule_based"})
        assert grade_resp.status_code == 200

        results_resp = client.get(f"/api/submissions/{submission_id}/results")
        assert results_resp.status_code == 200
        payload = results_resp.json()
        assert len(payload["transcriptions"]) == 2
        assert len(payload["grades"]) == 2

        # verify artifacts exist
        for page in pages:
            assert (Path(settings.data_dir) / page["image_path"]).exists()

        assert (Path(settings.data_dir) / "crops" / str(exam_id) / str(submission_id) / "Q1.png").exists()
        assert (Path(settings.data_dir) / "crops" / str(exam_id) / str(submission_id) / "Q2.png").exists()
