"""SQLModel ORM models for SuperMarks."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

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
    status: ExamStatus = Field(default=ExamStatus.DRAFT)


class Submission(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    student_name: str
    status: SubmissionStatus = Field(default=SubmissionStatus.UPLOADED)
    created_at: datetime = Field(default_factory=utcnow)


class SubmissionFile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    submission_id: int = Field(foreign_key="submission.id", index=True)
    file_kind: str
    original_filename: str
    stored_path: str
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
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
    content_type: str = "application/octet-stream"
    size_bytes: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class ExamKeyPage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    page_number: int
    image_path: str
    width: int
    height: int
    created_at: datetime = Field(default_factory=utcnow)


class ExamKeyParseRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    exam_id: int = Field(foreign_key="exam.id", index=True)
    request_id: str = Field(index=True)
    model_used: str
    status: str
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None
    error_json: Optional[str] = None
    timings_json: Optional[str] = None


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
