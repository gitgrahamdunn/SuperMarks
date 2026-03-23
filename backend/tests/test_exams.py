from __future__ import annotations

import csv
import json
import logging
import time
import zipfile
from datetime import timedelta

from io import BytesIO, StringIO
from pathlib import Path

import httpx
import pytest

pytest.importorskip("httpx")

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

from app import db
from app.ai.openai_vision import BulkNameDetectionResult, FrontPageTotalsExtractResult, OpenAIAnswerKeyParser, ParseResult
from app.main import app
from app.routers import exams as exams_router
from app.models import AnswerCrop, BulkUploadPage, Exam, ExamBulkUploadFile, ExamIntakeJob, ExamKeyFile, ExamKeyPage, ExamKeyParseJob, ExamKeyParsePage, ExamStatus, GradeResult, Question, QuestionParseEvidence, QuestionRegion, Submission, SubmissionCaptureMode, SubmissionFile, SubmissionPage, SubmissionStatus, Transcription, utcnow
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


def test_list_exams_includes_latest_intake_job_state(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with Session(db.engine) as session:
        exam = Exam(name="Queued Intake Exam")
        session.add(exam)
        session.flush()
        session.add(ExamIntakeJob(exam_id=exam.id, status="failed", stage="failed", page_count=3, pages_processed=1, submissions_created=0, error_message="old"))
        session.add(ExamIntakeJob(
            exam_id=exam.id,
            status="running",
            stage="detecting_names",
            page_count=3,
            pages_built=3,
            pages_processed=2,
            submissions_created=0,
            candidates_ready=0,
            review_open_threshold=3,
            initial_review_ready=False,
            fully_warmed=False,
            review_ready=False,
            lease_expires_at=utcnow() + timedelta(minutes=5),
            metrics_json=json.dumps({"render_upload_ms": 11.2}),
        ))
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/exams")

    assert response.status_code == 200
    payload = response.json()
    exam_row = next(item for item in payload if item["name"] == "Queued Intake Exam")
    assert exam_row["intake_job"]["status"] == "running"
    assert exam_row["intake_job"]["stage"] == "detecting_names"
    assert exam_row["intake_job"]["pages_built"] == 3
    assert exam_row["intake_job"]["pages_processed"] == 2
    assert exam_row["intake_job"]["candidates_ready"] == 0
    assert exam_row["intake_job"]["review_open_threshold"] == 3
    assert exam_row["intake_job"]["initial_review_ready"] is False
    assert exam_row["intake_job"]["fully_warmed"] is False
    assert exam_row["intake_job"]["review_ready"] is False
    assert exam_row["intake_job"]["metrics"]["render_upload_ms"] == 11.2


def test_list_exams_auto_resumes_stalled_intake_job(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    resumed_job_ids: list[int] = []
    monkeypatch.setattr(exams_router, "_spawn_exam_intake_job_thread", lambda job_id: resumed_job_ids.append(job_id))

    with Session(db.engine) as session:
        exam = Exam(name="Stalled Intake Exam", status=ExamStatus.DRAFT)
        session.add(exam)
        session.flush()
        job = ExamIntakeJob(
            exam_id=exam.id,
            status="running",
            stage="warming_initial_review",
            page_count=3,
            pages_built=3,
            pages_processed=3,
            submissions_created=3,
            candidates_ready=1,
            review_open_threshold=3,
            initial_review_ready=True,
            fully_warmed=False,
            review_ready=False,
            lease_expires_at=utcnow() - timedelta(minutes=3),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    with TestClient(app) as client:
        response = client.get("/api/exams")

    assert response.status_code == 200
    payload = response.json()
    exam_row = next(item for item in payload if item["name"] == "Stalled Intake Exam")
    assert exam_row["status"] == "REVIEWING"
    assert exam_row["intake_job"]["status"] == "queued"
    assert exam_row["intake_job"]["stage"] == "resuming"
    assert exam_row["intake_job"]["initial_review_ready"] is True
    assert exam_row["intake_job"]["fully_warmed"] is False
    assert exam_row["intake_job"]["review_ready"] is True
    assert exam_row["intake_job"]["error_message"] is None
    assert resumed_job_ids == [job_id]


def test_split_initial_and_remaining_review_ids_uses_first_ten_threshold(monkeypatch) -> None:
    monkeypatch.setenv("SUPERMARKS_FRONT_PAGE_REVIEW_OPEN_THRESHOLD", "10")

    initial_ids, remaining_ids, threshold = exams_router._split_initial_and_remaining_review_ids(list(range(1, 31)))

    assert threshold == 10
    assert initial_ids == list(range(1, 11))
    assert remaining_ids == list(range(11, 31))


def test_mark_exam_review_ready_requires_pages_and_candidate_payloads(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with Session(db.engine) as session:
        exam = Exam(name="Review Ready Exam")
        session.add(exam)
        session.flush()

        ready_submission = Submission(
            exam_id=exam.id,
            student_name="Jordan",
            capture_mode=SubmissionCaptureMode.FRONT_PAGE_TOTALS,
            front_page_candidates_json=json.dumps({"source": "cached", "objective_scores": [], "warnings": []}),
        )
        missing_candidate_submission = Submission(
            exam_id=exam.id,
            student_name="Avery",
            capture_mode=SubmissionCaptureMode.FRONT_PAGE_TOTALS,
        )
        session.add(ready_submission)
        session.add(missing_candidate_submission)
        session.flush()

        session.add(SubmissionPage(submission_id=ready_submission.id, page_number=1, image_path=str(tmp_path / "ready.png"), width=100, height=100))
        session.add(SubmissionPage(submission_id=missing_candidate_submission.id, page_number=1, image_path=str(tmp_path / "missing.png"), width=100, height=100))
        session.commit()

        failures = exams_router._front_page_review_readiness_failures([ready_submission.id, missing_candidate_submission.id], session)

    assert failures == [f"Submission {missing_candidate_submission.id} has no front-page candidate payload."]


def test_claim_exam_intake_job_respects_active_lease_and_allows_stale_reclaim(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with Session(db.engine) as session:
        exam = Exam(name="Lease Exam")
        session.add(exam)
        session.flush()
        job = ExamIntakeJob(exam_id=exam.id, status="queued", stage="queued", page_count=3)
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    with Session(db.engine) as session:
        assert exams_router._claim_exam_intake_job(job_id, "runner-a", session) is True
        claimed = session.get(ExamIntakeJob, job_id)
        assert claimed.runner_id == "runner-a"
        assert claimed.attempt_count == 1
        assert claimed.lease_expires_at is not None

    with Session(db.engine) as session:
        assert exams_router._claim_exam_intake_job(job_id, "runner-b", session) is False

    with Session(db.engine) as session:
        stale = session.get(ExamIntakeJob, job_id)
        stale.lease_expires_at = utcnow() - timedelta(minutes=1)
        session.add(stale)
        session.commit()

    with Session(db.engine) as session:
        assert exams_router._claim_exam_intake_job(job_id, "runner-b", session) is True
        reclaimed = session.get(ExamIntakeJob, job_id)
        assert reclaimed.runner_id == "runner-b"
        assert reclaimed.attempt_count == 2


def test_segment_bulk_candidates_keeps_weak_later_page_name_reads_in_same_submission() -> None:
    detections = [
        BulkNameDetectionResult(
            page_number=1,
            student_name="Jordan Lee",
            exam_name="Math 20",
            confidence=0.96,
            evidence={"x": 0.08, "y": 0.05, "w": 0.28, "h": 0.07},
        ),
        BulkNameDetectionResult(
            page_number=2,
            student_name="Jardan",
            exam_name="Math 20",
            confidence=0.58,
            evidence={"x": 0.42, "y": 0.63, "w": 0.18, "h": 0.05},
        ),
        BulkNameDetectionResult(
            page_number=3,
            student_name=None,
            exam_name="Math 20",
            confidence=0.0,
            evidence=None,
        ),
    ]

    candidates, warnings = exams_router._segment_bulk_candidates(detections, roster=[], min_pages_per_student=1)

    assert warnings == []
    assert len(candidates) == 1
    assert candidates[0].student_name == "Jordan Lee"
    assert candidates[0].page_start == 1
    assert candidates[0].page_end == 3


def test_image_upload_one_shot_extracts_name_and_prefills_candidate_payloads(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    calls: list[str] = []

    class StubExtractor:
        def extract(self, image_path, request_id, *, model_override=None, template=None, thinking_level_override=None):
            _ = (request_id, model_override, template)
            calls.append(str(image_path))
            student_label = Path(image_path).stem.replace("page_", "Student ")
            return FrontPageTotalsExtractResult(
                payload={
                    "exam_name": {"value_text": "Math 20-1 Unit Test", "confidence": 0.9, "evidence": []},
                    "student_name": {
                        "value_text": student_label,
                        "confidence": 0.94,
                        "evidence": [{"page_number": 1, "quote": student_label, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.05}],
                    },
                    "overall_marks_awarded": {"value_text": "42", "confidence": 0.9, "evidence": []},
                    "overall_max_marks": {"value_text": "50", "confidence": 0.9, "evidence": []},
                    "objective_scores": [],
                    "warnings": [],
                },
                model="gemini-2.5-flash",
                usage={
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                    "thinking_level": thinking_level_override or "low",
                    "thinking_budget": 128,
                    "prompt_tokens": 1000,
                    "candidate_tokens": 120,
                    "thought_tokens": 80,
                    "total_tokens": 1200,
                    "normalized_image_width": 800,
                    "normalized_image_height": 1000,
                    "normalized_image_bytes": 24000,
                    "estimated_cost_usd": 0.0008,
                },
            )

    monkeypatch.setattr(exams_router, "get_front_page_totals_extractor", lambda: StubExtractor())

    with Session(db.engine) as session:
        exam = Exam(name="Untitled Test")
        session.add(exam)
        session.flush()
        bulk = ExamBulkUploadFile(exam_id=exam.id, original_filename="2 uploaded images", stored_path="")
        session.add(bulk)
        session.flush()
        job = ExamIntakeJob(
            exam_id=exam.id,
            bulk_upload_id=bulk.id,
            status="running",
            stage="extracting_front_pages",
            page_count=2,
            pages_built=2,
            pages_processed=0,
            thinking_level="med",
            metrics_json=json.dumps({"front_page_thinking_level": "med"}),
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        rendered_paths: list[Path] = []
        for idx in range(1, 3):
            path = tmp_path / f"page_{idx:04d}.png"
            path.write_bytes(_tiny_png_bytes())
            rendered_paths.append(path)

        detections, prefilled = exams_router._extract_image_upload_front_page_pages(
            exam=exam,
            bulk=bulk,
            rendered_paths=rendered_paths,
            session=session,
            job=job,
        )

        candidates, warnings = exams_router._build_bulk_preview_from_detections(
            upload_files=None,
            bulk=bulk,
            detections=detections,
            roster=None,
        )
        created = exams_router._finalize_bulk_candidates(
            exam=exam,
            bulk=bulk,
            candidates=candidates,
            session=session,
            prefilled_candidate_payloads=prefilled,
        )
        session.commit()
        session.refresh(exam)

        assert warnings == []
        assert exam.name == "Math 20-1 Unit Test"
        assert len(calls) == 2
        assert len(created) == 2
        session.refresh(job)
        metrics = json.loads(job.metrics_json or "{}")
        assert metrics["front_page_calls"] == 2
        assert metrics["front_page_thinking_level"] == "med"
        assert metrics["front_page_prompt_tokens"] == 2000
        assert metrics["front_page_output_tokens"] == 240
        assert metrics["front_page_thought_tokens"] == 160
        assert metrics["front_page_avg_cost_per_page_usd"] == pytest.approx(0.0008)

        submissions = session.exec(select(Submission).where(Submission.exam_id == exam.id).order_by(Submission.id)).all()
        assert len(submissions) == 2
        assert all((submission.front_page_candidates_json or "").strip() for submission in submissions)


def test_start_exam_intake_job_persists_selected_thinking_level(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    spawned_job_ids: list[int] = []
    monkeypatch.setattr(exams_router, "_spawn_exam_intake_job_thread", lambda job_id: spawned_job_ids.append(job_id))

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Thinking Level Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        response = client.post(
            f"/api/exams/{exam_id}/intake-jobs/start",
            files=[("files", ("paper-1.png", _tiny_png_bytes(), "image/png"))],
            data={"front_page_thinking_level": "high"},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["thinking_level"] == "high"
    assert spawned_job_ids

    with Session(db.engine) as session:
        job = session.exec(select(ExamIntakeJob).where(ExamIntakeJob.exam_id == exam_id)).first()
        assert job is not None
        assert job.thinking_level == "high"
        assert json.loads(job.metrics_json or "{}")["front_page_thinking_level"] == "high"


def test_retry_exam_intake_job_preserves_thinking_level(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    spawned_job_ids: list[int] = []
    monkeypatch.setattr(exams_router, "_spawn_exam_intake_job_thread", lambda job_id: spawned_job_ids.append(job_id))

    with Session(db.engine) as session:
        exam = Exam(name="Retry Thinking Exam", status=ExamStatus.FAILED)
        session.add(exam)
        session.flush()
        bulk = ExamBulkUploadFile(exam_id=exam.id, original_filename="papers.pdf", stored_path="")
        session.add(bulk)
        session.flush()
        job = ExamIntakeJob(
            exam_id=exam.id,
            bulk_upload_id=bulk.id,
            status="failed",
            stage="review_not_ready",
            page_count=2,
            pages_built=2,
            pages_processed=2,
            thinking_level="off",
        )
        session.add(job)
        session.commit()
        exam_id = exam.id

    with TestClient(app) as client:
        response = client.post(f"/api/exams/{exam_id}/intake-jobs/retry")

    assert response.status_code == 202
    payload = response.json()
    assert payload["thinking_level"] == "off"
    assert spawned_job_ids

    with Session(db.engine) as session:
        jobs = session.exec(
            select(ExamIntakeJob).where(ExamIntakeJob.exam_id == exam_id).order_by(ExamIntakeJob.created_at.asc(), ExamIntakeJob.id.asc())
        ).all()
        assert len(jobs) == 2
        assert jobs[-1].thinking_level == "off"
        assert json.loads(jobs[-1].metrics_json or "{}")["front_page_thinking_level"] == "off"


def test_delete_exam_removes_related_rows_and_local_files(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("BLOB_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_response = client.post("/api/exams", json={"name": "Delete Me"})
        assert exam_response.status_code == 201
        exam_id = exam_response.json()["id"]

    exam_dir = Path(settings.data_dir) / "exams" / str(exam_id)
    pages_dir = Path(settings.data_dir) / "pages" / str(exam_id)
    crops_dir = Path(settings.data_dir) / "crops" / str(exam_id)
    uploads_dir = Path(settings.data_dir) / "uploads" / str(exam_id)
    objects_dir = Path(settings.data_dir) / "objects" / "exams" / str(exam_id)

    for path in (exam_dir, pages_dir, crops_dir, uploads_dir, objects_dir):
        path.mkdir(parents=True, exist_ok=True)
        (path / "marker.txt").write_text("x", encoding="utf-8")

    with Session(db.engine) as session:
        exam = session.get(Exam, exam_id)
        assert exam is not None

        submission = Submission(exam_id=exam_id, student_name="Student One", status=SubmissionStatus.GRADED)
        session.add(submission)
        session.flush()

        question = Question(exam_id=exam_id, label="Q1", max_marks=4, rubric_json=json.dumps({"key_page_number": 1}))
        session.add(question)
        session.flush()

        parse_job = ExamKeyParseJob(exam_id=exam_id, status="done", page_count=1, pages_done=1)
        session.add(parse_job)
        session.flush()
        session.add(ExamIntakeJob(exam_id=exam_id, status="running", stage="detecting_names", page_count=1, pages_processed=0, submissions_created=0))

        bulk_upload = ExamBulkUploadFile(exam_id=exam_id, original_filename="bulk.pdf", stored_path=f"exams/{exam_id}/bulk/input.pdf")
        session.add(bulk_upload)
        session.flush()

        session.add(SubmissionFile(submission_id=submission.id, file_kind="pdf", original_filename="submission.pdf", stored_path=f"exams/{exam_id}/submissions/{submission.id}/submission.pdf"))
        session.add(SubmissionPage(submission_id=submission.id, page_number=1, image_path=str(pages_dir / "page1.png"), width=100, height=200))
        session.add(AnswerCrop(submission_id=submission.id, question_id=question.id, image_path=str(crops_dir / "crop1.png")))
        session.add(Transcription(submission_id=submission.id, question_id=question.id, provider="stub", text="answer", confidence=1.0, raw_json="{}"))
        session.add(GradeResult(submission_id=submission.id, question_id=question.id, marks_awarded=4, breakdown_json="{}", feedback_json="{}", model_name="manual"))
        session.add(QuestionRegion(question_id=question.id, page_number=1, x=0, y=0, w=1, h=1))
        session.add(QuestionParseEvidence(question_id=question.id, exam_id=exam_id, page_number=1, x=0, y=0, w=1, h=1, evidence_kind="question_box", confidence=1.0))
        session.add(ExamKeyFile(exam_id=exam_id, original_filename="key.pdf", stored_path=f"exams/{exam_id}/key/key.pdf"))
        session.add(ExamKeyPage(exam_id=exam_id, page_number=1, image_path=str(exam_dir / "key_pages" / "page_0001.png"), width=100, height=200))
        session.add(ExamKeyParsePage(job_id=parse_job.id, page_number=1, status="done"))
        session.add(BulkUploadPage(bulk_upload_id=bulk_upload.id, page_number=1, image_path=str(exam_dir / "bulk" / "page_0001.png"), width=100, height=200))
        session.commit()

    with TestClient(app) as client:
        response = client.delete(f"/api/exams/{exam_id}")
        assert response.status_code == 204

        list_response = client.get("/api/exams")
        assert list_response.status_code == 200
        assert all(item["id"] != exam_id for item in list_response.json())

    with Session(db.engine) as session:
        assert session.get(Exam, exam_id) is None
        assert session.exec(select(Submission).where(Submission.exam_id == exam_id)).all() == []
        assert session.exec(select(Question).where(Question.exam_id == exam_id)).all() == []
        assert session.exec(select(ExamKeyParseJob).where(ExamKeyParseJob.exam_id == exam_id)).all() == []
        assert session.exec(select(ExamIntakeJob).where(ExamIntakeJob.exam_id == exam_id)).all() == []
        assert session.exec(select(ExamKeyFile).where(ExamKeyFile.exam_id == exam_id)).all() == []
        assert session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id)).all() == []
        assert session.exec(select(ExamBulkUploadFile).where(ExamBulkUploadFile.exam_id == exam_id)).all() == []

    for path in (exam_dir, pages_dir, crops_dir, uploads_dir, objects_dir):
        assert not path.exists()


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
        assert payload["request_id"]
        assert payload["stage"] == "job_started"
        for _ in range(10):
            finish = client.post(f"/api/exams/{exam_id}/key/parse/finish", params={"job_id": payload["job_id"]})
            assert finish.status_code == 200
            if finish.json()["status"] != "running":
                break
            time.sleep(0.05)
        questions_resp = client.get(f"/api/exams/{exam_id}/questions")
        assert questions_resp.status_code == 200
        assert len(questions_resp.json()) == 2
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
        assert payload["stage"] == "job_started"
        for _ in range(10):
            finish = client.post(f"/api/exams/{exam_id}/key/parse/finish", params={"job_id": payload["job_id"]})
            assert finish.status_code == 200
            if finish.json()["status"] != "running":
                break
            time.sleep(0.05)
        questions_resp = client.get(f"/api/exams/{exam_id}/questions")
        assert questions_resp.status_code == 200
        assert len(questions_resp.json()) >= 1
        assert any(
            "fast parse escalated to stronger model" in record.getMessage()
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
                        "objective_codes": ["OB1"],
                        "warnings": [],
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
        assert payload["stage"] == "job_started"
        assert payload["status"] in {"running", "done"}
        for _ in range(10):
            finish = client.post(f"/api/exams/{exam_id}/key/parse/finish", params={"job_id": payload["job_id"]})
            assert finish.status_code == 200
            if finish.json()["status"] != "running":
                break
            time.sleep(0.05)
        assert finish.json()["page_count"] == 1
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
        assert all(item["capture_mode"] == "front_page_totals" for item in final_payload["submissions"])
        assert all(item["front_page_totals"] is None for item in final_payload["submissions"])

        detail = client.get(f"/api/exams/{exam_id}")
        assert detail.status_code == 200
        assert len(detail.json()["submissions"]) == 2

        bootstrap = client.get(f"/api/exams/{exam_id}/workspace-bootstrap")
        assert bootstrap.status_code == 200
        bootstrap_payload = bootstrap.json()
        assert bootstrap_payload["exam"]["id"] == exam_id
        assert len(bootstrap_payload["submissions"]) == 2
        assert bootstrap_payload["marking_dashboard"]["exam_id"] == exam_id
        assert "latest_parse" in bootstrap_payload

    with Session(db.engine) as session:
        submissions = session.exec(select(Submission).where(Submission.exam_id == exam_id).order_by(Submission.id)).all()
        assert len(submissions) == 2
        assert all((submission.front_page_candidates_json or "").strip() for submission in submissions)


def test_bulk_upload_preview_accepts_single_image(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Bulk Image Upload Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        preview = client.post(
            f"/api/exams/{exam_id}/submissions/bulk",
            files={"file": ("singlepage.jpg", _tiny_png_bytes(), "image/jpeg")},
        )
        assert preview.status_code == 201
        payload = preview.json()
        assert payload["page_count"] == 1
        assert len(payload["candidates"]) == 1
        assert payload["candidates"][0]["page_start"] == 1
        assert payload["candidates"][0]["page_end"] == 1


def test_bulk_upload_preview_splits_multiple_images_into_individual_candidates(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("OPENAI_MOCK", "1")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam = client.post("/api/exams", json={"name": "Bulk Image Split Exam"})
        assert exam.status_code == 201
        exam_id = exam.json()["id"]

        preview = client.post(
            f"/api/exams/{exam_id}/submissions/bulk",
            files=[
                ("files", ("student-1.jpg", _tiny_png_bytes(), "image/jpeg")),
                ("files", ("student-2.jpg", _tiny_png_bytes(), "image/jpeg")),
                ("files", ("student-3.jpg", _tiny_png_bytes(), "image/jpeg")),
            ],
        )
        assert preview.status_code == 201
        payload = preview.json()
        assert payload["page_count"] == 3
        assert len(payload["candidates"]) == 3
        assert [candidate["page_start"] for candidate in payload["candidates"]] == [1, 2, 3]
        assert [candidate["page_end"] for candidate in payload["candidates"]] == [1, 2, 3]


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


class _SequenceParser:
    def __init__(self) -> None:
        self.calls_by_page: dict[int, int] = {}

    def parse(self, image_paths: list[Path], model: str, request_id: str) -> ParseResult:
        page_name = image_paths[0].stem
        page_number = int(page_name.split("_")[-1])
        call_count = self.calls_by_page.get(page_number, 0) + 1
        self.calls_by_page[page_number] = call_count

        label = "Q2" if page_number == 2 else ("Q1" if call_count == 1 else "Q1R")
        marks = 2 if page_number == 1 and call_count == 1 else (5 if page_number == 1 else 3)
        payload = {
            "confidence_score": 0.93,
            "warnings": [],
            "questions": [{
                "label": label,
                "max_marks": marks,
                "marks_source": "explicit",
                "marks_confidence": 0.95,
                "marks_reason": "visible",
                "question_text": f"Question text {label}",
                "answer_key": f"Answer {label}",
                "objective_codes": [],
                "warnings": [],
                "evidence": [{"page_number": page_number, "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.2, "kind": "question_box", "confidence": 0.9}],
            }],
            "usage": {"input_tokens": 10, "output_tokens": 5, "cost": 0.01},
        }
        return ParseResult(payload=payload, model=model)


def test_retry_parse_page_reruns_only_requested_page_and_refreshes_questions(tmp_path, monkeypatch) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")
    monkeypatch.setenv("SUPERMARKS_LLM_MODEL", "gpt-5-nano")
    monkeypatch.setenv("SUPERMARKS_LLM_ESCALATE_MODEL", "gpt-5-mini")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    parser = _SequenceParser()
    from app.ai.openai_vision import get_answer_key_parser
    app.dependency_overrides[get_answer_key_parser] = lambda: parser

    try:
        with TestClient(app) as client:
            exam_id = client.post("/api/exams", json={"name": "Retry One Page"}).json()["id"]
            client.post(
                f"/api/exams/{exam_id}/key/upload",
                files=[("files", ("key1.png", _tiny_png_bytes(), "image/png")), ("files", ("key2.png", _tiny_png_bytes(), "image/png"))],
            )
            client.post(f"/api/exams/{exam_id}/key/build-pages")
            job_id = client.post(f"/api/exams/{exam_id}/key/parse/start").json()["job_id"]

            parsed = client.post(f"/api/exams/{exam_id}/key/parse/next", params={"job_id": job_id, "batch_size": 2})
            assert parsed.status_code == 200
            before_questions = client.get(f"/api/exams/{exam_id}/questions").json()
            assert [question["label"] for question in before_questions] == ["Q1", "Q2"]

            retried = client.post(f"/api/exams/{exam_id}/key/parse/retry", params={"page_number": 1})
            assert retried.status_code == 200
            payload = retried.json()
            assert payload["job_id"] == job_id
            assert payload["page_number"] == 1
            assert payload["status"] == "done"
            assert payload["job_status"] == "done"
            assert payload["page"]["page_number"] == 1
            assert payload["page"]["status"] == "done"

            labels = [question["label"] for question in payload["questions"]]
            assert labels == ["Q1R", "Q2"]

        with Session(db.engine) as session:
            questions = session.exec(select(Question).where(Question.exam_id == exam_id)).all()
            assert [question.label for question in questions] == ["Q2", "Q1R"] or [question.label for question in questions] == ["Q1R", "Q2"]
            parse_pages = session.exec(select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job_id).order_by(ExamKeyParsePage.page_number)).all()
            assert [page.status for page in parse_pages] == ["done", "done"]
            assert parse_pages[0].model_used == "gpt-5-nano"
    finally:
        app.dependency_overrides = {}



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
        assert [page.status for page in pages] == ["done", "failed", "done"]
        assert pages[1].cost == 0.0
        assert pages[1].input_tokens == 0
        assert pages[1].output_tokens == 0
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

def test_exam_marking_dashboard_summarizes_submission_progress_and_objectives(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Dashboard Exam"}).json()["id"]
        q1 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 4, "rubric_json": {"objective_codes": ["OB1"], "criteria": []}},
        ).json()["id"]
        q2 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q2", "max_marks": 6, "rubric_json": {"objective_codes": ["OB2"], "criteria": []}},
        ).json()["id"]

        ready_submission = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Ready Student"}).json()["id"]
        complete_submission = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Complete Student"}).json()["id"]
        in_progress_submission = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "In Progress Student"}).json()["id"]
        blocked_submission = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Blocked Student"}).json()["id"]

    with Session(db.engine) as session:
        for submission_id in [ready_submission, complete_submission, in_progress_submission]:
            session.add(SubmissionPage(submission_id=submission_id, page_number=1, image_path=str(tmp_path / f"page-{submission_id}.png"), width=100, height=100))
            Path(tmp_path / f"page-{submission_id}.png").write_bytes(_tiny_png_bytes())

        for question_id in [q1, q2]:
            session.add(QuestionRegion(question_id=question_id, page_number=1, x=0, y=0, w=1, h=1))

        session.add(AnswerCrop(submission_id=ready_submission, question_id=q1, image_path=str(tmp_path / "ready-q1.png")))
        session.add(AnswerCrop(submission_id=ready_submission, question_id=q2, image_path=str(tmp_path / "ready-q2.png")))
        session.add(Transcription(submission_id=ready_submission, question_id=q1, provider="stub", text="A", confidence=0.9, raw_json="{}"))
        session.add(Transcription(submission_id=ready_submission, question_id=q2, provider="stub", text="B", confidence=0.9, raw_json="{}"))
        Path(tmp_path / "ready-q1.png").write_bytes(_tiny_png_bytes())
        Path(tmp_path / "ready-q2.png").write_bytes(_tiny_png_bytes())

        for question_id, marks in [(q1, 4), (q2, 5)]:
            path = tmp_path / f"complete-{question_id}.png"
            path.write_bytes(_tiny_png_bytes())
            session.add(AnswerCrop(submission_id=complete_submission, question_id=question_id, image_path=str(path)))
            session.add(Transcription(submission_id=complete_submission, question_id=question_id, provider="stub", text="done", confidence=0.9, raw_json="{}"))
            session.add(GradeResult(submission_id=complete_submission, question_id=question_id, marks_awarded=marks, breakdown_json="{}", feedback_json="{}", model_name="teacher_manual"))

        partial_path = tmp_path / "partial-q1.png"
        partial_path.write_bytes(_tiny_png_bytes())
        session.add(AnswerCrop(submission_id=in_progress_submission, question_id=q1, image_path=str(partial_path)))
        session.add(AnswerCrop(submission_id=in_progress_submission, question_id=q2, image_path=str(tmp_path / "partial-q2.png")))
        Path(tmp_path / "partial-q2.png").write_bytes(_tiny_png_bytes())
        session.add(Transcription(submission_id=in_progress_submission, question_id=q1, provider="stub", text="partial", confidence=0.9, raw_json="{}"))
        session.add(Transcription(submission_id=in_progress_submission, question_id=q2, provider="stub", text="partial", confidence=0.9, raw_json="{}"))
        session.add(GradeResult(submission_id=in_progress_submission, question_id=q1, marks_awarded=2, breakdown_json="{}", feedback_json="{}", model_name="teacher_manual"))
        session.commit()

    with TestClient(app) as client:
        response = client.get(f"/api/exams/{exam_id}/marking-dashboard")
        assert response.status_code == 200
        payload = response.json()
        assert payload["completion"] == {
            "total_submissions": 4,
            "ready_count": 1,
            "blocked_count": 1,
            "in_progress_count": 1,
            "complete_count": 1,
            "completion_percent": 25.0,
        }
        assert payload["objectives"] == [
            {
                "objective_code": "OB1",
                "marks_awarded": 6.0,
                "max_marks": 16.0,
                "questions_count": 4,
                "submissions_with_objective": 4,
                "complete_submissions_with_objective": 1,
                "incomplete_submissions_with_objective": 3,
                "total_awarded_complete": 4.0,
                "total_max_complete": 4.0,
                "average_awarded_complete": 4.0,
                "average_percent_complete": 100.0,
                "total_awarded_all_current": 6.0,
                "total_max_all_current": 16.0,
                "average_percent_all_current": 37.5,
                "strongest_complete_student": "Complete Student",
                "strongest_complete_percent": 100.0,
                "weakest_complete_student": "Complete Student",
                "weakest_complete_percent": 100.0,
                "weakest_complete_submission": {
                    "submission_id": complete_submission,
                    "student_name": "Complete Student",
                    "capture_mode": "question_level",
                    "objective_percent": 100.0,
                },
                "teacher_summary": "1/4 results export-ready; complete average 100.0%; strongest Complete Student (100.0%), weakest Complete Student (100.0%)",
                "attention_submissions": [
                    {
                        "submission_id": blocked_submission,
                        "student_name": "Blocked Student",
                        "capture_mode": "question_level",
                        "workflow_status": "blocked",
                        "objective_percent": 0.0,
                        "next_return_point": "Q1",
                        "next_action": "Open Q1 to clear the blocker.",
                    },
                    {
                        "submission_id": in_progress_submission,
                        "student_name": "In Progress Student",
                        "capture_mode": "question_level",
                        "workflow_status": "in_progress",
                        "objective_percent": 50.0,
                        "next_return_point": "Q2",
                        "next_action": "Resume marking at Q2.",
                    },
                    {
                        "submission_id": ready_submission,
                        "student_name": "Ready Student",
                        "capture_mode": "question_level",
                        "workflow_status": "ready",
                        "objective_percent": 0.0,
                        "next_return_point": "Q1",
                        "next_action": "Start marking at Q1.",
                    },
                ],
            },
            {
                "objective_code": "OB2",
                "marks_awarded": 5.0,
                "max_marks": 24.0,
                "questions_count": 4,
                "submissions_with_objective": 4,
                "complete_submissions_with_objective": 1,
                "incomplete_submissions_with_objective": 3,
                "total_awarded_complete": 5.0,
                "total_max_complete": 6.0,
                "average_awarded_complete": 5.0,
                "average_percent_complete": 83.3,
                "total_awarded_all_current": 5.0,
                "total_max_all_current": 24.0,
                "average_percent_all_current": 20.8,
                "strongest_complete_student": "Complete Student",
                "strongest_complete_percent": 83.3,
                "weakest_complete_student": "Complete Student",
                "weakest_complete_percent": 83.3,
                "weakest_complete_submission": {
                    "submission_id": complete_submission,
                    "student_name": "Complete Student",
                    "capture_mode": "question_level",
                    "objective_percent": 83.3,
                },
                "teacher_summary": "1/4 results export-ready; complete average 83.3%; strongest Complete Student (83.3%), weakest Complete Student (83.3%)",
                "attention_submissions": [
                    {
                        "submission_id": blocked_submission,
                        "student_name": "Blocked Student",
                        "capture_mode": "question_level",
                        "workflow_status": "blocked",
                        "objective_percent": 0.0,
                        "next_return_point": "Q1",
                        "next_action": "Open Q1 to clear the blocker.",
                    },
                    {
                        "submission_id": in_progress_submission,
                        "student_name": "In Progress Student",
                        "capture_mode": "question_level",
                        "workflow_status": "in_progress",
                        "objective_percent": 0.0,
                        "next_return_point": "Q2",
                        "next_action": "Resume marking at Q2.",
                    },
                    {
                        "submission_id": ready_submission,
                        "student_name": "Ready Student",
                        "capture_mode": "question_level",
                        "workflow_status": "ready",
                        "objective_percent": 0.0,
                        "next_return_point": "Q1",
                        "next_action": "Start marking at Q1.",
                    },
                ],
            },
        ]
        rows = {row["student_name"]: row for row in payload["submissions"]}
        assert rows["Ready Student"]["workflow_status"] == "ready"
        assert rows["Ready Student"]["flagged_count"] == 0
        assert rows["Ready Student"]["marking_progress"] == "0/2 marked"
        assert rows["Ready Student"]["next_question_label"] == "Q1"
        assert rows["Ready Student"]["next_action_text"] == "Start marking at Q1."
        assert rows["Ready Student"]["export_ready"] is False
        assert rows["Ready Student"]["reporting_attention"] == "Result needs teacher attention before it is ready for export."
        assert rows["Ready Student"]["next_return_point"] == "Q1"
        assert rows["Ready Student"]["next_action"] == "Start marking at Q1."
        assert rows["Complete Student"]["workflow_status"] == "complete"
        assert rows["Complete Student"]["marking_progress"] == "2/2 marked"
        assert rows["Complete Student"]["running_total"] == 9
        assert rows["Complete Student"]["export_ready"] is True
        assert rows["Complete Student"]["reporting_attention"] == "Every submission currently has a complete result."
        assert rows["Complete Student"]["next_return_point"] == ""
        assert rows["Complete Student"]["next_action"] == "Review results or return to the class queue."
        assert rows["Complete Student"]["objective_totals"] == [
            {"objective_code": "OB1", "marks_awarded": 4.0, "max_marks": 4.0, "questions_count": 1},
            {"objective_code": "OB2", "marks_awarded": 5.0, "max_marks": 6.0, "questions_count": 1},
        ]
        assert rows["In Progress Student"]["workflow_status"] == "in_progress"
        assert rows["In Progress Student"]["teacher_marked_questions"] == 1
        assert rows["In Progress Student"]["marking_progress"] == "1/2 marked"
        assert rows["In Progress Student"]["next_question_label"] == "Q2"
        assert rows["In Progress Student"]["next_action_text"] == "Resume marking at Q2."
        assert rows["In Progress Student"]["export_ready"] is False
        assert rows["In Progress Student"]["reporting_attention"] == "Result needs teacher attention before it is ready for export."
        assert rows["In Progress Student"]["next_return_point"] == "Q2"
        assert rows["In Progress Student"]["next_action"] == "Resume marking at Q2."
        assert rows["Blocked Student"]["workflow_status"] == "blocked"
        assert rows["Blocked Student"]["flagged_count"] == 2
        assert rows["Blocked Student"]["marking_progress"] == "0/2 marked"
        assert rows["Blocked Student"]["next_question_label"] == "Q1"
        assert rows["Blocked Student"]["next_action_text"] == "Open Q1 to clear the blocker."
        assert rows["Blocked Student"]["export_ready"] is False
        assert rows["Blocked Student"]["reporting_attention"] == "No submission pages have been built yet."
        assert rows["Blocked Student"]["next_return_point"] == "Q1"
        assert rows["Blocked Student"]["next_action"] == "Open Q1 to clear the blocker."


def test_exam_export_summary_csv_matches_class_reporting_view(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Summary CSV Exam"}).json()["id"]
        q1 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 4, "rubric_json": {"objective_codes": ["OB1"], "criteria": []}},
        ).json()["id"]
        q2 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q2", "max_marks": 6, "rubric_json": {"objective_codes": ["OB2"], "criteria": []}},
        ).json()["id"]
        complete_submission_id = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Ada"}).json()["id"]
        in_progress_submission_id = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Byron"}).json()["id"]
        client.put(f"/api/submissions/{complete_submission_id}/questions/{q1}/manual-grade", json={"marks_awarded": 4, "teacher_note": "great"})
        client.put(f"/api/submissions/{complete_submission_id}/questions/{q2}/manual-grade", json={"marks_awarded": 5, "teacher_note": "strong"})
        client.put(f"/api/submissions/{in_progress_submission_id}/questions/{q1}/manual-grade", json={"marks_awarded": 2, "teacher_note": "partial"})

        export = client.get(f"/api/exams/{exam_id}/export-summary.csv")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        rows = list(csv.DictReader(StringIO(export.text)))
        assert rows[0].keys() == {
            "student",
            "capture_mode",
            "workflow_status",
            "export_ready",
            "marking_progress",
            "running_total",
            "total_possible",
            "total_percent",
            "teacher_marked_questions",
            "questions_total",
            "objective_count",
            "objective_summary",
            "next_return_point",
            "next_action",
            "reporting_attention",
        }
        by_student = {row["student"]: row for row in rows}
        assert by_student["Ada"] == {
            "student": "Ada",
            "capture_mode": "question_level",
            "workflow_status": "complete",
            "export_ready": "yes",
            "marking_progress": "2/2 marked",
            "running_total": "9.0",
            "total_possible": "10.0",
            "total_percent": "90.0",
            "teacher_marked_questions": "2",
            "questions_total": "2",
            "objective_count": "2",
            "objective_summary": "OB1 4.0/4.0 | OB2 5.0/6.0",
            "next_return_point": "Q1",
            "next_action": "Review results or return to the class queue.",
            "reporting_attention": "Every submission currently has a complete result.",
        }
        assert by_student["Byron"]["capture_mode"] == "question_level"
        assert by_student["Byron"]["workflow_status"] == "in_progress"
        assert by_student["Byron"]["export_ready"] == "no"
        assert by_student["Byron"]["marking_progress"] == "1/2 marked"
        assert by_student["Byron"]["running_total"] == "2.0"
        assert by_student["Byron"]["total_possible"] == "10.0"
        assert by_student["Byron"]["total_percent"] == "20.0"
        assert by_student["Byron"]["teacher_marked_questions"] == "1"
        assert by_student["Byron"]["questions_total"] == "2"
        assert by_student["Byron"]["objective_count"] == "2"
        assert by_student["Byron"]["objective_summary"] == "OB1 2.0/4.0 | OB2 0.0/6.0"
        assert by_student["Byron"]["next_return_point"] == "Q1"
        assert by_student["Byron"]["next_action"] == "Resume marking at Q1."
        assert by_student["Byron"]["reporting_attention"] == "No submission pages have been built yet."


def test_exam_export_csv_includes_question_rows_and_total_score(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "CSV Exam"}).json()["id"]
        q1 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 4, "rubric_json": {"objective_codes": ["OB1"], "criteria": []}},
        ).json()["id"]
        client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q2", "max_marks": 6, "rubric_json": {"objective_codes": ["OB2", "OB3"], "criteria": []}},
        )
        submission_id = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Ada"}).json()["id"]
        client.put(f"/api/submissions/{submission_id}/questions/{q1}/manual-grade", json={"marks_awarded": 3, "teacher_note": "ok"})

        export = client.get(f"/api/exams/{exam_id}/export.csv")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        body = export.text
        assert "student,capture_mode,workflow_status,export_ready,flagged_questions,teacher_marked_questions,questions_total,marking_progress,total_awarded,total_possible,total_percent,objective_summary,objective_count,reporting_attention,next_return_point,next_action,objective_OB1_awarded,objective_OB1_max,objective_OB2_awarded,objective_OB2_max,objective_OB3_awarded,objective_OB3_max,Q1_awarded,Q1_max,Q1_objectives,Q2_awarded,Q2_max,Q2_objectives" in body
        assert "Ada,question_level,in_progress,no,2,1,2,1/2 marked,3.0,10.0,30.0,OB1 3.0/4.0 | OB2 0.0/6.0 | OB3 0.0/6.0,3,No submission pages have been built yet.,Q1,Resume marking at Q1.,3.0,4.0,0.0,6.0,0.0,6.0,3.0,4.0,OB1,,6.0,OB2; OB3" in body


def test_exam_export_xlsx_includes_dynamic_outcome_columns(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Midterm 1"}).json()["id"]
        q1 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 20, "rubric_json": {"objective_codes": ["OB1"], "criteria": []}},
        ).json()["id"]
        submission_id = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Ada"}).json()["id"]
        client.put(f"/api/submissions/{submission_id}/questions/{q1}/manual-grade", json={"marks_awarded": 15, "teacher_note": "solid"})

        export = client.get(f"/api/exams/{exam_id}/export.xlsx")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        archive = zipfile.ZipFile(BytesIO(export.content))
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

        assert "test_name" in sheet_xml
        assert "name" in sheet_xml
        assert "grade" in sheet_xml
        assert "outcome_OB1" in sheet_xml
        assert "Midterm 1" in sheet_xml
        assert "Ada" in sheet_xml
        assert "15/20" in sheet_xml
        assert "percent" not in sheet_xml
        assert "capture_mode" not in sheet_xml
        assert "workflow_status" not in sheet_xml
        assert "objective_summary" not in sheet_xml


def test_exam_export_xlsx_infers_outcome_columns_from_parsed_front_page_candidates(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Midterm 2"}).json()["id"]
        submission_id = client.post(
            f"/api/exams/{exam_id}/submissions",
            json={"student_name": "Ada", "capture_mode": "front_page_totals"},
        ).json()["id"]

        with Session(db.engine) as session:
            submission = session.get(Submission, submission_id)
            assert submission is not None
            submission.front_page_candidates_json = json.dumps({
                "student_name": {"value_text": "Ada", "confidence": 0.95, "evidence": []},
                "overall_marks_awarded": {"value_text": "18", "confidence": 0.95, "evidence": []},
                "overall_max_marks": {"value_text": "20", "confidence": 0.95, "evidence": []},
                "objective_scores": [
                    {
                        "objective_code": {"value_text": "OB1", "confidence": 0.9, "evidence": []},
                        "marks_awarded": {"value_text": "8", "confidence": 0.9, "evidence": []},
                        "max_marks": {"value_text": "10", "confidence": 0.9, "evidence": []},
                    },
                    {
                        "objective_code": {"value_text": "OB2", "confidence": 0.9, "evidence": []},
                        "marks_awarded": {"value_text": "10", "confidence": 0.9, "evidence": []},
                        "max_marks": {"value_text": "10", "confidence": 0.9, "evidence": []},
                    },
                ],
                "warnings": [],
                "source": "stub-front-page",
            })
            session.add(submission)
            session.commit()

        export = client.get(f"/api/exams/{exam_id}/export.xlsx")
        assert export.status_code == 200

        archive = zipfile.ZipFile(BytesIO(export.content))
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

        assert "outcome_OB1" in sheet_xml
        assert "outcome_OB2" in sheet_xml
        assert "8/10" in sheet_xml
        assert "10/10" in sheet_xml


def test_exam_export_objectives_summary_csv_rolls_up_objective_posture(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Objective Summary CSV Exam"}).json()["id"]
        q1 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 4, "rubric_json": {"objective_codes": ["OB1"], "criteria": []}},
        ).json()["id"]
        q2 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q2", "max_marks": 6, "rubric_json": {"objective_codes": ["OB2"], "criteria": []}},
        ).json()["id"]
        complete_submission_id = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Ada"}).json()["id"]
        in_progress_submission_id = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Byron"}).json()["id"]

        client.put(f"/api/submissions/{complete_submission_id}/questions/{q1}/manual-grade", json={"marks_awarded": 4, "teacher_note": "great"})
        client.put(f"/api/submissions/{complete_submission_id}/questions/{q2}/manual-grade", json={"marks_awarded": 5, "teacher_note": "strong"})
        client.put(f"/api/submissions/{in_progress_submission_id}/questions/{q1}/manual-grade", json={"marks_awarded": 2, "teacher_note": "partial"})

        dashboard = client.get(f"/api/exams/{exam_id}/marking-dashboard")
        assert dashboard.status_code == 200

        export = client.get(f"/api/exams/{exam_id}/export-objectives-summary.csv")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        rows = list(csv.DictReader(StringIO(export.text)))
        assert rows[0].keys() == {
            "objective_code",
            "submissions_with_objective",
            "complete_submissions_with_objective",
            "incomplete_submissions_with_objective",
            "total_awarded_complete",
            "total_max_complete",
            "average_awarded_complete",
            "average_percent_complete",
            "total_awarded_all_current",
            "total_max_all_current",
            "average_percent_all_current",
            "strongest_complete_student",
            "strongest_complete_percent",
            "weakest_complete_student",
            "weakest_complete_percent",
            "teacher_summary",
        }
        by_objective = {row["objective_code"]: row for row in rows}
        dashboard_by_objective = {row["objective_code"]: row for row in dashboard.json()["objectives"]}
        assert dashboard_by_objective["OB1"]["teacher_summary"] == "1/2 results export-ready; complete average 100.0%; strongest Ada (100.0%), weakest Ada (100.0%)"
        assert dashboard_by_objective["OB1"]["total_awarded_all_current"] == 6.0
        assert dashboard_by_objective["OB1"]["weakest_complete_submission"] == {
            "submission_id": complete_submission_id,
            "student_name": "Ada",
            "capture_mode": "question_level",
            "objective_percent": 100.0,
        }

        assert by_objective["OB1"] == {
            "objective_code": "OB1",
            "submissions_with_objective": "2",
            "complete_submissions_with_objective": "1",
            "incomplete_submissions_with_objective": "1",
            "total_awarded_complete": "4.0",
            "total_max_complete": "4.0",
            "average_awarded_complete": "4.0",
            "average_percent_complete": "100.0",
            "total_awarded_all_current": "6.0",
            "total_max_all_current": "8.0",
            "average_percent_all_current": "75.0",
            "strongest_complete_student": "Ada",
            "strongest_complete_percent": "100.0",
            "weakest_complete_student": "Ada",
            "weakest_complete_percent": "100.0",
            "teacher_summary": "1/2 results export-ready; complete average 100.0%; strongest Ada (100.0%), weakest Ada (100.0%)",
        }
        assert by_objective["OB2"] == {
            "objective_code": "OB2",
            "submissions_with_objective": "2",
            "complete_submissions_with_objective": "1",
            "incomplete_submissions_with_objective": "1",
            "total_awarded_complete": "5.0",
            "total_max_complete": "6.0",
            "average_awarded_complete": "5.0",
            "average_percent_complete": "83.3",
            "total_awarded_all_current": "5.0",
            "total_max_all_current": "12.0",
            "average_percent_all_current": "41.7",
            "strongest_complete_student": "Ada",
            "strongest_complete_percent": "83.3",
            "weakest_complete_student": "Ada",
            "weakest_complete_percent": "83.3",
            "teacher_summary": "1/2 results export-ready; complete average 83.3%; strongest Ada (83.3%), weakest Ada (83.3%)",
        }


def test_exam_export_student_summaries_zip_packages_teacher_readable_files(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Student Summary Package Exam"}).json()["id"]
        q1 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 4, "rubric_json": {"objective_codes": ["OB1"], "criteria": []}},
        ).json()["id"]
        q2 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q2", "max_marks": 6, "rubric_json": {"objective_codes": ["OB2"], "criteria": []}},
        ).json()["id"]

        ada_submission_id = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Ada Lovelace"}).json()["id"]
        byron_submission_id = client.post(
            f"/api/exams/{exam_id}/submissions",
            json={"student_name": "Byron", "capture_mode": "front_page_totals"},
        ).json()["id"]

        crop_dir = Path(settings.data_dir) / "crops" / str(exam_id) / str(ada_submission_id)
        crop_dir.mkdir(parents=True, exist_ok=True)
        q1_crop_path = crop_dir / "Q1.png"
        q1_crop_path.write_bytes(_tiny_png_bytes())
        q2_crop_path = crop_dir / "Q2.png"
        q2_crop_path.write_bytes(_tiny_png_bytes())
        with Session(db.engine) as session:
            session.add(AnswerCrop(submission_id=ada_submission_id, question_id=q1, image_path=str(q1_crop_path)))
            session.add(AnswerCrop(submission_id=ada_submission_id, question_id=q2, image_path=str(q2_crop_path)))
            session.add(Transcription(submission_id=ada_submission_id, question_id=q1, provider="stub-ocr", text="Ada answer for Q1", confidence=0.98, raw_json=json.dumps({"text": "Ada answer for Q1", "confidence": 0.98})))
            session.add(Transcription(submission_id=ada_submission_id, question_id=q2, provider="stub-ocr", text="Ada answer for Q2", confidence=0.87, raw_json=json.dumps({"text": "Ada answer for Q2", "confidence": 0.87})))
            session.commit()

        client.put(f"/api/submissions/{ada_submission_id}/questions/{q1}/manual-grade", json={"marks_awarded": 4, "teacher_note": "Strong setup"})
        client.put(f"/api/submissions/{ada_submission_id}/questions/{q2}/manual-grade", json={"marks_awarded": 5, "teacher_note": "Clear reasoning"})
        client.put(
            f"/api/submissions/{byron_submission_id}/front-page-totals",
            json={
                "student_name": "Byron",
                "overall_marks_awarded": 18,
                "overall_max_marks": 20,
                "objective_scores": [
                    {"objective_code": "OB1", "marks_awarded": 8, "max_marks": 10},
                    {"objective_code": "OB2", "marks_awarded": 10, "max_marks": 10},
                ],
                "teacher_note": "Checked against the cover page.",
                "confirmed": True,
            },
        )

        export = client.get(f"/api/exams/{exam_id}/export-student-summaries.zip")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("application/zip")

        archive = zipfile.ZipFile(BytesIO(export.content))
        names = sorted(archive.namelist())
        assert names == [
            "student-summaries/01-ada-lovelace/evidence/Q1-crop.png",
            "student-summaries/01-ada-lovelace/evidence/Q1-transcription.json",
            "student-summaries/01-ada-lovelace/evidence/Q1-transcription.txt",
            "student-summaries/01-ada-lovelace/evidence/Q2-crop.png",
            "student-summaries/01-ada-lovelace/evidence/Q2-transcription.json",
            "student-summaries/01-ada-lovelace/evidence/Q2-transcription.txt",
            "student-summaries/01-ada-lovelace/evidence/README.txt",
            "student-summaries/01-ada-lovelace/evidence/manifest.csv",
            "student-summaries/01-ada-lovelace/summary.html",
            "student-summaries/01-ada-lovelace/summary.txt",
            "student-summaries/02-byron/summary.html",
            "student-summaries/02-byron/summary.txt",
            "student-summaries/README.txt",
            "student-summaries/manifest.csv",
        ]

        manifest_rows = list(csv.DictReader(StringIO(archive.read("student-summaries/manifest.csv").decode("utf-8"))))
        by_student = {row["student"]: row for row in manifest_rows}
        assert by_student["Ada Lovelace"] == {
            "student": "Ada Lovelace",
            "capture_mode": "question_level",
            "workflow_status": "complete",
            "export_ready": "yes",
            "flagged_questions": "2",
            "teacher_marked_questions": "2",
            "questions_total": "2",
            "marking_progress": "2/2 marked",
            "total_awarded": "9.0",
            "total_possible": "10.0",
            "total_percent": "90.0",
            "objective_summary": "OB1 4.0/4.0 | OB2 5.0/6.0",
            "reporting_attention": "Every submission currently has a complete result.",
            "next_return_point": "Q1",
            "next_action": "Review results or return to the class queue.",
            "summary_text_file": "student-summaries/01-ada-lovelace/summary.txt",
            "summary_html_file": "student-summaries/01-ada-lovelace/summary.html",
            "evidence_manifest_file": "student-summaries/01-ada-lovelace/evidence/manifest.csv",
            "evidence_file_count": "8",
        }
        assert by_student["Byron"]["summary_text_file"] == "student-summaries/02-byron/summary.txt"
        assert by_student["Byron"]["summary_html_file"] == "student-summaries/02-byron/summary.html"
        assert by_student["Byron"]["evidence_manifest_file"] == ""
        assert by_student["Byron"]["evidence_file_count"] == "0"
        assert by_student["Byron"]["capture_mode"] == "front_page_totals"
        assert by_student["Byron"]["workflow_status"] == "complete"
        assert by_student["Byron"]["flagged_questions"] == "0"
        assert by_student["Byron"]["teacher_marked_questions"] == "1"
        assert by_student["Byron"]["questions_total"] == "0"
        assert by_student["Byron"]["marking_progress"] == "confirmed totals"
        assert by_student["Byron"]["objective_summary"] == "OB1 8.0/10.0 | OB2 10.0/10.0"
        assert by_student["Byron"]["next_return_point"] == ""
        assert by_student["Byron"]["next_action"] == "Review saved front-page totals."

        package_readme = archive.read("student-summaries/README.txt").decode("utf-8")
        assert "manifest.csv — class index" in package_readme
        assert "export_ready, workflow_status, flagged_questions, teacher_marked_questions, questions_total, marking_progress, next_return_point, and next_action columns" in package_readme
        assert "saved result; use manifest.csv to see whether that result is export-ready or still awaiting teacher confirmation" in package_readme
        assert "open that student's evidence/manifest.csv first" in package_readme
        assert "2 students · 1 question-level package · 1 front-page totals package" in package_readme

        evidence_readme = archive.read("student-summaries/01-ada-lovelace/evidence/README.txt").decode("utf-8")
        assert "Evidence guide — Ada Lovelace" in evidence_readme
        assert "Open evidence/manifest.csv first" in evidence_readme

        evidence_manifest_rows = list(csv.DictReader(StringIO(archive.read("student-summaries/01-ada-lovelace/evidence/manifest.csv").decode("utf-8"))))
        assert evidence_manifest_rows == [
            {
                "question_label": "Q1",
                "grade_status": "Teacher-marked",
                "teacher_note": "Strong setup",
                "transcription_provider": "stub-ocr",
                "transcription_confidence": "0.98",
                "crop_image_file": "student-summaries/01-ada-lovelace/evidence/Q1-crop.png",
                "transcription_text_file": "student-summaries/01-ada-lovelace/evidence/Q1-transcription.txt",
                "transcription_json_file": "student-summaries/01-ada-lovelace/evidence/Q1-transcription.json",
            },
            {
                "question_label": "Q2",
                "grade_status": "Teacher-marked",
                "teacher_note": "Clear reasoning",
                "transcription_provider": "stub-ocr",
                "transcription_confidence": "0.87",
                "crop_image_file": "student-summaries/01-ada-lovelace/evidence/Q2-crop.png",
                "transcription_text_file": "student-summaries/01-ada-lovelace/evidence/Q2-transcription.txt",
                "transcription_json_file": "student-summaries/01-ada-lovelace/evidence/Q2-transcription.json",
            },
        ]
        assert archive.read("student-summaries/01-ada-lovelace/evidence/Q1-transcription.txt").decode("utf-8") == "Ada answer for Q1"
        assert json.loads(archive.read("student-summaries/01-ada-lovelace/evidence/Q2-transcription.json").decode("utf-8")) == {
            "confidence": 0.87,
            "text": "Ada answer for Q2",
        }

        ada_summary = archive.read("student-summaries/01-ada-lovelace/summary.txt").decode("utf-8")
        assert "Student: Ada Lovelace" in ada_summary
        assert "Workflow status: Complete" in ada_summary
        assert "Objective breakdown:" in ada_summary
        assert "- OB1: 4.0/4.0 (100.0%) · 1 question" in ada_summary
        assert "- Q1: 4.0/4.0 · OB1 · Teacher-marked" in ada_summary
        assert "Teacher note: Strong setup" in ada_summary

        ada_html = archive.read("student-summaries/01-ada-lovelace/summary.html").decode("utf-8")
        assert "<!doctype html>" in ada_html.lower()
        assert "<h1>Ada Lovelace</h1>" in ada_html
        assert "<th>Teacher note</th>" in ada_html
        assert "Strong setup" in ada_html

        byron_summary = archive.read("student-summaries/02-byron/summary.txt").decode("utf-8")
        assert "Student: Byron" in byron_summary
        assert "Capture mode: front_page_totals" in byron_summary
        assert "Teacher-marked questions: front-page totals workflow" in byron_summary
        assert "- OB2: 10.0/10.0 (100.0%) · front-page category total" in byron_summary
        assert "question-level marks are not stored in this export package" in byron_summary

        byron_html = archive.read("student-summaries/02-byron/summary.html").decode("utf-8")
        assert "Byron" in byron_html
        assert "front-page totals workflow" in byron_html
        assert "question-level marks are not stored in this export package" in byron_html


def test_reporting_outputs_stay_consistent_across_dashboard_summary_csv_and_student_package(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Reporting Consistency Exam"}).json()["id"]
        q1 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 4, "rubric_json": {"objective_codes": ["OB1"], "criteria": []}},
        ).json()["id"]
        client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q2", "max_marks": 6, "rubric_json": {"objective_codes": ["OB2"], "criteria": []}},
        )

        ada_submission_id = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Ada"}).json()["id"]
        byron_submission_id = client.post(
            f"/api/exams/{exam_id}/submissions",
            json={"student_name": "Byron", "capture_mode": "front_page_totals"},
        ).json()["id"]

        client.put(f"/api/submissions/{ada_submission_id}/questions/{q1}/manual-grade", json={"marks_awarded": 4, "teacher_note": "done"})
        client.put(
            f"/api/submissions/{byron_submission_id}/front-page-totals",
            json={
                "overall_marks_awarded": 18,
                "overall_max_marks": 20,
                "objective_scores": [
                    {"objective_code": "OB1", "marks_awarded": 8, "max_marks": 10},
                    {"objective_code": "OB2", "marks_awarded": 10, "max_marks": 10},
                ],
                "teacher_note": "Needs one more check.",
                "confirmed": False,
            },
        )

        dashboard = client.get(f"/api/exams/{exam_id}/marking-dashboard")
        assert dashboard.status_code == 200
        dashboard_rows = {row["student_name"]: row for row in dashboard.json()["submissions"]}

        summary_export = client.get(f"/api/exams/{exam_id}/export-summary.csv")
        assert summary_export.status_code == 200
        summary_rows = {row["student"]: row for row in csv.DictReader(StringIO(summary_export.text))}

        package_export = client.get(f"/api/exams/{exam_id}/export-student-summaries.zip")
        assert package_export.status_code == 200
        archive = zipfile.ZipFile(BytesIO(package_export.content))
        manifest_rows = {row["student"]: row for row in csv.DictReader(StringIO(archive.read("student-summaries/manifest.csv").decode("utf-8")))}

        for student_name in ["Ada", "Byron"]:
            dashboard_row = dashboard_rows[student_name]
            summary_row = summary_rows[student_name]
            manifest_row = manifest_rows[student_name]

            assert summary_row["capture_mode"] == dashboard_row["capture_mode"] == manifest_row["capture_mode"]
            assert summary_row["workflow_status"] == dashboard_row["workflow_status"] == manifest_row["workflow_status"]
            assert summary_row["export_ready"] == manifest_row["export_ready"]
            assert summary_row["marking_progress"] == dashboard_row["marking_progress"] == manifest_row["marking_progress"]
            assert summary_row["teacher_marked_questions"] == str(dashboard_row["teacher_marked_questions"]) == manifest_row["teacher_marked_questions"]
            assert summary_row["questions_total"] == str(dashboard_row["questions_total"]) == manifest_row["questions_total"]
            expected_marking_progress = (
                "confirmed totals"
                if dashboard_row["capture_mode"] == "front_page_totals" and dashboard_row["workflow_status"] == "complete"
                else "pending front-page confirmation"
                if dashboard_row["capture_mode"] == "front_page_totals"
                else f"{dashboard_row['teacher_marked_questions']}/{dashboard_row['questions_total']} marked"
            )
            assert manifest_row["marking_progress"] == expected_marking_progress
            assert manifest_row["flagged_questions"] == str(dashboard_row["flagged_count"])
            assert summary_row["running_total"] == str(dashboard_row["running_total"]) == manifest_row["total_awarded"]
            assert summary_row["total_possible"] == str(dashboard_row["total_possible"]) == manifest_row["total_possible"]
            assert summary_row["objective_summary"] == manifest_row["objective_summary"]
            assert summary_row["reporting_attention"] == manifest_row["reporting_attention"]
            assert summary_row["next_return_point"] == (dashboard_row["next_question_label"] or "") == manifest_row["next_return_point"]
            assert summary_row["next_action"] == (dashboard_row["next_action_text"] or "") == manifest_row["next_action"]

        package_readme = archive.read("student-summaries/README.txt").decode("utf-8")
        assert "export_ready, workflow_status, flagged_questions, teacher_marked_questions, questions_total, marking_progress, next_return_point, and next_action columns" in package_readme
        assert "use each student's summary and manifest row for confirmation status, export readiness, totals, and objective summaries" in package_readme

        byron_summary = archive.read("student-summaries/02-byron/summary.txt").decode("utf-8")
        assert "Student: Byron" in byron_summary
        assert "Workflow status: Ready" in byron_summary
        assert "Export-ready: no" in byron_summary
        assert "Total: 18.0/20.0 (90.0%)" in byron_summary
        assert "Objective summary: OB1 8.0/10.0 | OB2 10.0/10.0" in byron_summary
        assert "Reporting attention: Front-page totals still need teacher confirmation." in byron_summary
        assert "Next action: Capture and confirm the front-page totals." in byron_summary
        assert "saved front-page totals that still need teacher confirmation" in byron_summary
        assert "Export readiness: not final until the front-page totals are confirmed." in byron_summary

        byron_html = archive.read("student-summaries/02-byron/summary.html").decode("utf-8")
        assert "saved front-page totals that still need teacher confirmation" in byron_html
        assert "Export readiness:</strong> not final until the front-page totals are confirmed." in byron_html
        assert "confirm front-page totals before treating this export as final" in byron_html



def test_submission_results_include_objective_totals(tmp_path) -> None:
    settings.data_dir = str(tmp_path / "data")
    settings.sqlite_path = str(tmp_path / "test.db")

    db.engine = create_engine(settings.sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(db.engine)

    with TestClient(app) as client:
        exam_id = client.post("/api/exams", json={"name": "Results Exam"}).json()["id"]
        q1 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q1", "max_marks": 4, "rubric_json": {"objective_codes": ["OB1"], "criteria": []}},
        ).json()["id"]
        q2 = client.post(
            f"/api/exams/{exam_id}/questions",
            json={"label": "Q2", "max_marks": 6, "rubric_json": {"objective_codes": ["OB1", "OB2"], "criteria": []}},
        ).json()["id"]
        submission_id = client.post(f"/api/exams/{exam_id}/submissions", json={"student_name": "Ada"}).json()["id"]
        client.put(f"/api/submissions/{submission_id}/questions/{q1}/manual-grade", json={"marks_awarded": 3, "teacher_note": "ok"})
        client.put(f"/api/submissions/{submission_id}/questions/{q2}/manual-grade", json={"marks_awarded": 5, "teacher_note": "strong"})

        response = client.get(f"/api/submissions/{submission_id}/results")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_score"] == 8.0
        assert payload["total_possible"] == 10.0
        assert payload["objective_totals"] == [
            {"objective_code": "OB1", "marks_awarded": 8.0, "max_marks": 10.0, "questions_count": 2},
            {"objective_code": "OB2", "marks_awarded": 5.0, "max_marks": 6.0, "questions_count": 1},
        ]

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
