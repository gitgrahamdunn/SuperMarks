from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from app.models import AnswerCrop, Transcription

from app import db
from app.main import app
from app.settings import settings
from tests.test_pipeline import make_image_bytes


def setup_test_db(tmp_path):
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)


def test_prepare_status_flags_missing_assets_and_auto_recovers(tmp_path):
    setup_test_db(tmp_path)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Algebra Test"}).json()["id"]
        submission_id = client.post(
            f"/api/exams/{exam_id}/submissions",
            data={"student_name": "Alice"},
            files=[("files", ("page1.png", make_image_bytes("Q1 x=4"), "image/png"))],
        ).json()["id"]

        q1_id = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 5, "rubric_json": {"answer_key": "x=4"}},
        ).json()["id"]
        q2_id = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q2", "max_marks": 3, "rubric_json": {"answer_key": "y=7"}},
        ).json()["id"]

        assert client.post(
            f"/api/questions/{q1_id}/regions",
            json=[{"page_number": 1, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}],
        ).status_code == 200
        assert client.post(
            f"/api/questions/{q2_id}/regions",
            json=[{"page_number": 1, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}],
        ).status_code == 200

        status_resp = client.get(f"/api/submissions/{submission_id}/prepare-status")
        assert status_resp.status_code == 200
        status_payload = status_resp.json()
        assert status_payload["ready_for_marking"] is False
        assert status_payload["can_prepare_now"] is True
        assert status_payload["suggested_actions"] == ["build_pages"]
        assert any("No submission pages have been built yet." in reason for reason in status_payload["summary_reasons"])

        prepare_resp = client.post(f"/api/submissions/{submission_id}/prepare")
        assert prepare_resp.status_code == 200
        prepare_payload = prepare_resp.json()
        assert prepare_payload["ready_for_marking"] is True
        assert prepare_payload["actions_run"] == ["build_pages", "build_crops", "transcribe"]
        assert prepare_payload["questions_ready"] == 2
        assert prepare_payload["manual_marked_questions"] == 0
        assert prepare_payload["unsafe_to_retry_reasons"] == []
        assert all(question["ready"] for question in prepare_payload["questions"])

        results_resp = client.get(f"/api/submissions/{submission_id}/results")
        assert results_resp.status_code == 200
        assert len(results_resp.json()["transcriptions"]) == 2


def test_prepare_status_surfaces_page_mismatch_blocker(tmp_path):
    setup_test_db(tmp_path)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Geometry Test"}).json()["id"]
        submission_id = client.post(
            f"/api/exams/{exam_id}/submissions",
            data={"student_name": "Bob"},
            files=[("files", ("page1.png", make_image_bytes("only one page"), "image/png"))],
        ).json()["id"]

        question_id = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 2, "rubric_json": {"answer_key": "42"}},
        ).json()["id"]
        assert client.post(
            f"/api/questions/{question_id}/regions",
            json=[{"page_number": 2, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}],
        ).status_code == 200

        assert client.post(f"/api/submissions/{submission_id}/build-pages").status_code == 200

        status_resp = client.get(f"/api/submissions/{submission_id}/prepare-status")
        payload = status_resp.json()
        assert payload["ready_for_marking"] is False
        assert payload["can_prepare_now"] is False
        assert payload["missing_page_numbers"] == [2]
        assert any("do not exist: 2" in reason for reason in payload["summary_reasons"])

        question = payload["questions"][0]
        assert question["ready"] is False
        assert any("Missing submission page(s): 2." == reason for reason in question["flagged_reasons"])

        prepare_resp = client.post(f"/api/submissions/{submission_id}/prepare")
        assert prepare_resp.status_code == 200
        prepare_payload = prepare_resp.json()
        assert prepare_payload["actions_run"] == []
        assert prepare_payload["can_prepare_now"] is False
        assert prepare_payload["blocked_actions"] == []


def test_prepare_status_blocks_auto_recovery_after_manual_marking_when_template_changed(tmp_path):
    setup_test_db(tmp_path)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Calculus Test"}).json()["id"]
        submission_id = client.post(
            f"/api/exams/{exam_id}/submissions",
            data={"student_name": "Carol"},
            files=[("files", ("page1.png", make_image_bytes("Q1 work"), "image/png"))],
        ).json()["id"]

        question_id = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 4, "rubric_json": {"answer_key": "x=2"}},
        ).json()["id"]
        assert client.post(
            f"/api/questions/{question_id}/regions",
            json=[{"page_number": 1, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}],
        ).status_code == 200

        assert client.post(f"/api/submissions/{submission_id}/prepare").status_code == 200
        assert client.put(
            f"/api/submissions/{submission_id}/questions/{question_id}/manual-grade",
            json={"marks_awarded": 3, "teacher_note": "Counted method marks manually."},
        ).status_code == 200

        assert client.post(
            f"/api/questions/{question_id}/regions",
            json=[{"page_number": 1, "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}],
        ).status_code == 200

        status_resp = client.get(f"/api/submissions/{submission_id}/prepare-status")
        assert status_resp.status_code == 200
        payload = status_resp.json()
        assert payload["ready_for_marking"] is False
        assert payload["can_prepare_now"] is False
        assert payload["manual_marked_questions"] == 1
        assert payload["blocked_actions"] == ["build_crops", "transcribe"]
        assert payload["suggested_actions"] == ["build_crops"]
        assert any("manual marking started" in reason.lower() or "avoid overwriting work" in reason.lower() for reason in payload["summary_reasons"])
        assert payload["unsafe_to_retry_reasons"] == [
            "Teacher manual marks already exist on questions that would need assets rebuilt or re-transcribed."
        ]

        question = payload["questions"][0]
        assert question["asset_state"] == "stale_crop"
        assert question["has_manual_grade"] is True
        assert question["stale_crop"] is True
        assert question["stale_transcription"] is True
        assert any("template regions changed" in reason for reason in question["flagged_reasons"])
        assert any("manual marking has already started" in reason for reason in question["blocking_reasons"])

        prepare_resp = client.post(f"/api/submissions/{submission_id}/prepare")
        assert prepare_resp.status_code == 200
        prepare_payload = prepare_resp.json()
        assert prepare_payload["actions_run"] == []
        assert prepare_payload["can_prepare_now"] is False

        with Session(db.engine) as session:
            crop = session.exec(select(AnswerCrop).where(AnswerCrop.submission_id == submission_id)).one()
            transcription = session.exec(select(Transcription).where(Transcription.submission_id == submission_id)).one()
            assert crop is not None
            assert transcription is not None
