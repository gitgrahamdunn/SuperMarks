"""Request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models import ExamStatus, SubmissionCaptureMode, SubmissionStatus


class ExamCreate(BaseModel):
    name: str = ""


class ClassListRead(BaseModel):
    id: int | None = None
    name: str = ""
    created_at: datetime | None = None
    names: list[str] = Field(default_factory=list)
    source: str = ""
    entry_count: int = 0
    filenames: list[str] = Field(default_factory=list)


class ExamRead(BaseModel):
    id: int
    name: str
    created_at: datetime
    teacher_style_profile_json: str | None
    status: ExamStatus
    class_list: ClassListRead | None = None
    intake_job: "ExamIntakeJobRead | None" = None


class SubmissionFileRead(BaseModel):
    id: int
    file_kind: str
    original_filename: str
    stored_path: str
    blob_url: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None


class SubmissionPageRead(BaseModel):
    id: int
    page_number: int
    image_path: str
    width: int
    height: int


class FrontPageObjectiveScore(BaseModel):
    objective_code: str
    marks_awarded: float = Field(ge=0)
    max_marks: float | None = Field(default=None, ge=0)


class FrontPageExtractionEvidence(BaseModel):
    page_number: int = 1
    quote: str = ""
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    w: float | None = Field(default=None, ge=0, le=1)
    h: float | None = Field(default=None, ge=0, le=1)


class FrontPageCandidateValue(BaseModel):
    value_text: str = ""
    confidence: float = Field(default=0, ge=0, le=1)
    evidence: list[FrontPageExtractionEvidence] = Field(default_factory=list)


class FrontPageObjectiveScoreCandidate(BaseModel):
    objective_code: FrontPageCandidateValue
    marks_awarded: FrontPageCandidateValue
    max_marks: FrontPageCandidateValue | None = None


class FrontPageTotalsCandidateRead(BaseModel):
    student_name: FrontPageCandidateValue | None = None
    overall_marks_awarded: FrontPageCandidateValue | None = None
    overall_max_marks: FrontPageCandidateValue | None = None
    objective_scores: list[FrontPageObjectiveScoreCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source: str = ""


class FrontPageTotalsRead(BaseModel):
    overall_marks_awarded: float = Field(ge=0)
    overall_max_marks: float | None = Field(default=None, ge=0)
    objective_scores: list[FrontPageObjectiveScore] = Field(default_factory=list)
    teacher_note: str = ""
    confirmed: bool = True
    reviewed_at: datetime | None = None


class FrontPageTotalsUpsert(BaseModel):
    student_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    overall_marks_awarded: float = Field(ge=0)
    overall_max_marks: float | None = Field(default=None, ge=0)
    objective_scores: list[FrontPageObjectiveScore] = Field(default_factory=list)
    teacher_note: str = ""
    confirmed: bool = True


class SubmissionRead(BaseModel):
    id: int
    exam_id: int
    student_name: str
    first_name: str = ""
    last_name: str = ""
    status: SubmissionStatus
    capture_mode: SubmissionCaptureMode
    front_page_totals: FrontPageTotalsRead | None = None
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


class SubmissionPrepareQuestionStatus(BaseModel):
    question_id: int
    question_label: str
    ready: bool
    flagged_reasons: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    asset_state: str = "ready"
    has_regions: bool
    has_crop: bool
    has_transcription: bool
    has_manual_grade: bool = False
    stale_crop: bool = False
    stale_transcription: bool = False
    transcription_confidence: float | None = None


class SubmissionPrepareStatus(BaseModel):
    submission_id: int
    ready_for_marking: bool
    can_prepare_now: bool
    summary_reasons: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    blocked_actions: list[str] = Field(default_factory=list)
    unsafe_to_retry_reasons: list[str] = Field(default_factory=list)
    questions_total: int
    questions_ready: int
    manual_marked_questions: int = 0
    pages_count: int
    missing_page_numbers: list[int] = Field(default_factory=list)
    actions_run: list[str] = Field(default_factory=list)
    questions: list[SubmissionPrepareQuestionStatus] = Field(default_factory=list)


class SubmissionResults(BaseModel):
    submission_id: int
    capture_mode: SubmissionCaptureMode = SubmissionCaptureMode.QUESTION_LEVEL
    total_score: float = 0
    total_possible: float = 0
    objective_totals: list[ObjectiveTotalRead] = Field(default_factory=list)
    front_page_totals: FrontPageTotalsRead | None = None
    transcriptions: list[TranscriptionRead]
    grades: list[GradeResultRead]


class ManualGradeUpsert(BaseModel):
    marks_awarded: float = Field(ge=0)
    teacher_note: str = ""


class ExamDetail(BaseModel):
    exam: ExamRead
    key_files: list[StoredFileRead] = Field(default_factory=list)
    submissions: list[SubmissionRead]
    parse_jobs: list["ExamParseJobRead"] = Field(default_factory=list)


class ExamParseJobRead(BaseModel):
    id: int
    exam_id: int
    status: str
    page_count: int
    pages_done: int
    created_at: datetime
    updated_at: datetime
    cost_total: float
    input_tokens_total: int
    output_tokens_total: int




class ExamKeyPageRead(BaseModel):
    id: int
    exam_id: int
    page_number: int
    image_path: str
    blob_pathname: str | None = None
    blob_url: str | None = None
    exists_on_disk: bool
    exists_on_storage: bool
    width: int
    height: int


class ExamKeyUploadResponse(BaseModel):
    uploaded: int
    urls: list[str] = Field(default_factory=list)


class StoredFileRead(BaseModel):
    id: int
    original_filename: str
    stored_path: str
    content_type: str
    size_bytes: int
    signed_url: str
    blob_url: str | None = None


class BlobFileMetadata(BaseModel):
    original_filename: str
    blob_pathname: str
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(default=0, ge=0)


class BlobRegisterRequest(BaseModel):
    files: list[BlobFileMetadata] = Field(default_factory=list)


class BlobRegisterResponse(BaseModel):
    registered: int


class NameEvidence(BaseModel):
    page_number: int
    x: float
    y: float
    w: float
    h: float


class BulkUploadCandidate(BaseModel):
    candidate_id: str
    student_name: str
    confidence: float
    page_start: int
    page_end: int
    needs_review: bool
    name_evidence: NameEvidence | None = None


class BulkUploadPreviewResponse(BaseModel):
    bulk_upload_id: int
    page_count: int
    candidates: list[BulkUploadCandidate]
    warnings: list[str] = Field(default_factory=list)


class BulkUploadFinalizeCandidate(BaseModel):
    student_name: str
    page_start: int
    page_end: int


class BulkUploadFinalizeRequest(BaseModel):
    candidates: list[BulkUploadFinalizeCandidate]


class BulkUploadFinalizeResponse(BaseModel):
    submissions: list[SubmissionRead]
    warnings: list[str] = Field(default_factory=list)


class ExamIntakeJobRead(BaseModel):
    id: int
    exam_id: int
    bulk_upload_id: int | None = None
    status: str
    stage: str
    page_count: int
    pages_built: int
    pages_processed: int
    submissions_created: int
    candidates_ready: int
    review_open_threshold: int
    initial_review_ready: bool
    fully_warmed: bool
    review_ready: bool
    thinking_level: str
    metrics: dict[str, float | int | str] | None = None
    error_message: str | None = None
    last_progress_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class FrontPageUsageEntryRead(BaseModel):
    submission_id: int
    student_name: str
    provider: str = ""
    model: str = ""
    thinking_level: str = ""
    thinking_budget: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    normalized_image_width: int = 0
    normalized_image_height: int = 0
    normalized_image_bytes: int = 0


class FrontPageUsageReportRead(BaseModel):
    exam_id: int
    exam_name: str
    entry_count: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    avg_tokens_per_image: float = 0.0
    avg_cost_per_image_usd: float = 0.0
    entries: list[FrontPageUsageEntryRead] = Field(default_factory=list)


class ObjectiveTotalRead(BaseModel):
    objective_code: str
    marks_awarded: float
    max_marks: float
    questions_count: int = 0


class ObjectiveAttentionSubmissionRead(BaseModel):
    submission_id: int
    student_name: str
    capture_mode: SubmissionCaptureMode = SubmissionCaptureMode.QUESTION_LEVEL
    workflow_status: str
    objective_percent: float | str
    next_return_point: str = ""
    next_action: str = ""


class ObjectiveCompleteSubmissionRead(BaseModel):
    submission_id: int
    student_name: str
    capture_mode: SubmissionCaptureMode = SubmissionCaptureMode.QUESTION_LEVEL
    objective_percent: float | str


class ExamObjectiveRead(BaseModel):
    objective_code: str
    marks_awarded: float
    max_marks: float
    questions_count: int = 0
    submissions_with_objective: int
    complete_submissions_with_objective: int
    incomplete_submissions_with_objective: int
    total_awarded_complete: float
    total_max_complete: float
    average_awarded_complete: float | str
    average_percent_complete: float | str
    total_awarded_all_current: float
    total_max_all_current: float
    average_percent_all_current: float | str
    strongest_complete_student: str
    strongest_complete_percent: float | str
    weakest_complete_student: str
    weakest_complete_percent: float | str
    weakest_complete_submission: ObjectiveCompleteSubmissionRead | None = None
    teacher_summary: str
    attention_submissions: list[ObjectiveAttentionSubmissionRead] = Field(default_factory=list)


class SubmissionDashboardRow(BaseModel):
    submission_id: int
    student_name: str
    capture_mode: SubmissionCaptureMode = SubmissionCaptureMode.QUESTION_LEVEL
    workflow_status: str
    flagged_count: int
    questions_total: int
    teacher_marked_questions: int
    marking_progress: str
    running_total: float
    total_possible: float
    objective_totals: list[ObjectiveTotalRead] = Field(default_factory=list)
    ready_for_marking: bool
    can_prepare_now: bool
    summary_reasons: list[str] = Field(default_factory=list)
    next_question_id: int | None = None
    next_question_label: str | None = None
    next_action_text: str | None = None
    export_ready: bool = False
    reporting_attention: str = ""
    next_return_point: str = ""
    next_action: str = ""


class ExamCompletionSummary(BaseModel):
    total_submissions: int
    ready_count: int
    blocked_count: int
    in_progress_count: int
    complete_count: int
    completion_percent: float


class ExamMarkingDashboardResponse(BaseModel):
    exam_id: int
    exam_name: str
    total_possible: float
    objectives: list[ExamObjectiveRead] = Field(default_factory=list)
    submissions: list[SubmissionDashboardRow] = Field(default_factory=list)
    completion: ExamCompletionSummary


class ExamWorkspaceBootstrapResponse(BaseModel):
    exam: ExamRead
    questions: list[QuestionRead] = Field(default_factory=list)
    key_files: list[StoredFileRead] = Field(default_factory=list)
    submissions: list[SubmissionRead] = Field(default_factory=list)
    marking_dashboard: ExamMarkingDashboardResponse
    latest_parse: dict[str, Any]
    latest_parse_status: dict[str, Any] | None = None
