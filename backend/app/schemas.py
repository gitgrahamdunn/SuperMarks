"""Request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models import ExamStatus, SubmissionStatus


class ExamCreate(BaseModel):
    name: str


class ExamRead(BaseModel):
    id: int
    name: str
    created_at: datetime
    teacher_style_profile_json: str | None
    status: ExamStatus


class SubmissionFileRead(BaseModel):
    id: int
    file_kind: str
    original_filename: str
    stored_path: str


class SubmissionPageRead(BaseModel):
    id: int
    page_number: int
    image_path: str
    width: int
    height: int


class SubmissionRead(BaseModel):
    id: int
    exam_id: int
    student_name: str
    status: SubmissionStatus
    created_at: datetime
    files: list[SubmissionFileRead] = Field(default_factory=list)
    pages: list[SubmissionPageRead] = Field(default_factory=list)


class RegionIn(BaseModel):
    page_number: int
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class RegionRead(RegionIn):
    id: int


class QuestionCreate(BaseModel):
    label: str
    max_marks: int = Field(ge=0)
    rubric_json: dict[str, Any] | None = None


class QuestionRead(BaseModel):
    id: int
    exam_id: int
    label: str
    max_marks: int
    rubric_json: dict[str, Any]
    regions: list[RegionRead] = Field(default_factory=list)


class QuestionUpdate(BaseModel):
    label: str | None = None
    max_marks: int | None = Field(default=None, ge=0)
    rubric_json: dict[str, Any] | None = None


class QuestionMergeResponse(BaseModel):
    question: QuestionRead
    questions_count: int


class QuestionSplitRequest(BaseModel):
    mode: str = Field(default="criteria_index")
    criteria_split_index: int = Field(ge=1)


class QuestionSplitResponse(BaseModel):
    original: QuestionRead
    created: QuestionRead
    questions_count: int


class TranscriptionRead(BaseModel):
    id: int
    submission_id: int
    question_id: int
    provider: str
    text: str
    confidence: float
    raw_json: dict[str, Any]


class GradeResultRead(BaseModel):
    id: int
    submission_id: int
    question_id: int
    marks_awarded: float
    breakdown_json: dict[str, Any]
    feedback_json: dict[str, Any]
    model_name: str


class SubmissionResults(BaseModel):
    submission_id: int
    transcriptions: list[TranscriptionRead]
    grades: list[GradeResultRead]


class ExamDetail(BaseModel):
    exam: ExamRead
    submissions: list[SubmissionRead]
    questions: list[QuestionRead]




class ExamKeyPageRead(BaseModel):
    id: int
    exam_id: int
    page_number: int
    image_path: str
    width: int
    height: int


class ExamKeyUploadResponse(BaseModel):
    uploaded: int


class ExamCostModelBreakdown(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0


class ExamCostResponse(BaseModel):
    total_cost: float = 0.0
    total_tokens: int = 0
    model_breakdown: dict[str, ExamCostModelBreakdown] = Field(default_factory=dict)
