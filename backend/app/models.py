"""SQLModel ORM models for SuperMarks."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Return timezone-aware UTC now timestamp."""
    return datetime.now(timezone.utc)


class SubmissionStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PAGES_READY = "PAGES_READY"
    CROPS_READY = "CROPS_READY"
    TRANSCRIBED = "TRANSCRIBED"
    GRADED = "GRADED"


class ExamStatus(str, Enum):
    DRAFT = "DRAFT"
    KEY_UPLOADED = "KEY_UPLOADED"
    KEY_PAGES_READY = "KEY_PAGES_READY"
    PARSED = "PARSED"
    REVIEWING = "REVIEWING"
    READY = "READY"
    FAILED = "FAILED"


class Exam(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=utcnow)
    teacher_style_profile_json: Optional[str] = None
    front_page_template_json: Optional[str] = None
    status: ExamStatus = Field(default=ExamStatus.DRAFT)


class ExamIntakeJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    bulk_upload_id: Optional[int] = Field(default=None, foreign_key="exambulkuploadfile.id", index=True)
    status: str = "queued"
    stage: str = "queued"
    page_count: int = 0
    pages_processed: int = 0
    submissions_created: int = 0
    attempt_count: int = 0
    runner_id: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    metrics_json: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SubmissionCaptureMode(str, Enum):
    QUESTION_LEVEL = "question_level"
    FRONT_PAGE_TOTALS = "front_page_totals"


class Submission(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    student_name: str
    first_name: str = ""
    last_name: str = ""
    status: SubmissionStatus = Field(default=SubmissionStatus.UPLOADED)
    capture_mode: SubmissionCaptureMode = Field(default=SubmissionCaptureMode.QUESTION_LEVEL)
    front_page_totals_json: Optional[str] = None
    front_page_candidates_json: Optional[str] = None
    front_page_reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class SubmissionFile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    submission_id: int = Field(foreign_key="submission.id", index=True)
    file_kind: str
    original_filename: str
    stored_path: str
    blob_url: Optional[str] = None
    blob_pathname: Optional[str] = None
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class ExamBulkUploadFile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    original_filename: str
    stored_path: str
    created_at: datetime = Field(default_factory=utcnow)


class BulkUploadPage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bulk_upload_id: int = Field(foreign_key="exambulkuploadfile.id", index=True)
    page_number: int
    image_path: str
    width: int
    height: int
    detected_student_name: Optional[str] = None
    detection_confidence: float = 0.0
    detection_evidence_json: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class SubmissionPage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    submission_id: int = Field(foreign_key="submission.id", index=True)
    page_number: int
    image_path: str
    width: int
    height: int
    created_at: datetime = Field(default_factory=utcnow)


class Question(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    label: str
    max_marks: int
    rubric_json: str
    created_at: datetime = Field(default_factory=utcnow)


class ExamKeyFile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    original_filename: str
    stored_path: str
    blob_url: Optional[str] = None
    blob_pathname: Optional[str] = None
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class ExamKeyPage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    page_number: int
    image_path: str
    blob_pathname: Optional[str] = None
    blob_url: Optional[str] = None
    width: int
    height: int
    created_at: datetime = Field(default_factory=utcnow)


class ExamKeyParseJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    status: str = "running"
    page_count: int = 0
    pages_done: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    cost_total: float = 0.0
    input_tokens_total: int = 0
    output_tokens_total: int = 0


class ExamKeyParsePage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="examkeyparsejob.id", index=True)
    page_number: int
    status: str = "pending"
    confidence: float = 0.0
    model_used: Optional[str] = None
    result_json: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    error_json: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class QuestionParseEvidence(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    page_number: int
    x: float
    y: float
    w: float
    h: float
    evidence_kind: str
    confidence: float
    created_at: datetime = Field(default_factory=utcnow)


class QuestionRegion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True)
    page_number: int
    x: float
    y: float
    w: float
    h: float
    created_at: datetime = Field(default_factory=utcnow)


class AnswerCrop(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    submission_id: int = Field(foreign_key="submission.id", index=True)
    question_id: int = Field(foreign_key="question.id", index=True)
    image_path: str
    created_at: datetime = Field(default_factory=utcnow)


class Transcription(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    submission_id: int = Field(foreign_key="submission.id", index=True)
    question_id: int = Field(foreign_key="question.id", index=True)
    provider: str
    text: str
    confidence: float
    raw_json: str
    created_at: datetime = Field(default_factory=utcnow)


class GradeResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    submission_id: int = Field(foreign_key="submission.id", index=True)
    question_id: int = Field(foreign_key="question.id", index=True)
    marks_awarded: float
    breakdown_json: str
    feedback_json: str
    model_name: str
    created_at: datetime = Field(default_factory=utcnow)
