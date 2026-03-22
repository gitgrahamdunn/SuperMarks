from __future__ import annotations

import csv
from collections import defaultdict
import json
import re
import zipfile
from dataclasses import dataclass
from html import escape
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Generic, TypeVar
from xml.sax.saxutils import escape as xml_escape

from sqlmodel import Session, select

from app.models import AnswerCrop, Exam, GradeResult, Question, QuestionRegion, Submission, SubmissionCaptureMode, SubmissionPage, Transcription
from app.reporting import accumulate_objective_totals, build_exam_objective_report, front_page_objective_totals, front_page_totals_read, objective_summary_text, objective_totals_read, question_objective_codes
from app.schemas import ExamCompletionSummary, ExamMarkingDashboardResponse, SubmissionDashboardRow


@dataclass
class ExamSubmissionReportingData:
    pages: list[SubmissionPage]
    crops: list[AnswerCrop]
    transcriptions: list[Transcription]
    grades: list[GradeResult]


@dataclass
class ExamReportingSnapshot:
    question_regions_by_question_id: dict[int, list[QuestionRegion]]
    submission_data_by_submission_id: dict[int, ExamSubmissionReportingData]


@dataclass
class MarksExportObjectiveValue:
    objective_code: str
    marks_awarded: float
    max_marks: float


@dataclass
class MarksExportQuestionValue:
    question_id: int
    marks_awarded: float | None
    max_marks: float
    objective_codes: list[str]


@dataclass
class SubmissionMarksExportPayload:
    prefix_values: list[Any]
    objective_values_by_code: dict[str, MarksExportObjectiveValue]
    question_values_by_question_id: dict[int, MarksExportQuestionValue]


@dataclass
class SubmissionReportingProjection:
    submission: Submission
    dashboard_row: SubmissionDashboardRow
    export_row: StudentReportingExportRow
    marks_export_payload: SubmissionMarksExportPayload
    grade_map: dict[int, GradeResult]
    question_rows: list[StudentSummaryQuestionRow]


@dataclass
class ExamReportingContext:
    exam: Exam
    questions: list[Question]
    submissions: list[Submission]
    dashboard_rows: list[SubmissionDashboardRow]
    submission_projections: list[SubmissionReportingProjection]
    snapshot: ExamReportingSnapshot


class CsvExportRow:
    def as_csv_row(self) -> list[Any]:
        raise NotImplementedError


CsvExportRowT = TypeVar("CsvExportRowT", bound=CsvExportRow)


@dataclass
class CsvExportSpec(Generic[CsvExportRowT]):
    headers: list[str]
    rows: list[CsvExportRowT]


def write_csv_export(buffer: StringIO, export_spec: CsvExportSpec[CsvExportRow]) -> None:
    writer = csv.writer(buffer)
    writer.writerow(export_spec.headers)
    for row in export_spec.rows:
        writer.writerow(row.as_csv_row())


@dataclass
class ExamMarksExportRow(CsvExportRow):
    submission: Submission
    dashboard_row: SubmissionDashboardRow
    prefix_values: list[Any]
    objective_values: list[Any]
    question_values: list[Any]

    def as_csv_row(self) -> list[Any]:
        return [*self.prefix_values, *self.objective_values, *self.question_values]


@dataclass
class ExamSummaryExportRow(CsvExportRow):
    dashboard_row: SubmissionDashboardRow
    export_row: StudentReportingExportRow

    def as_csv_row(self) -> list[Any]:
        return self.export_row.as_summary_csv_row()


@dataclass
class ExamObjectiveSummaryExportRow(CsvExportRow):
    objective_code: str
    submissions_with_objective: int
    complete_submissions_with_objective: int
    incomplete_submissions_with_objective: int
    total_awarded_complete: float
    total_max_complete: float
    average_awarded_complete: float | None
    average_percent_complete: float | None
    total_awarded_all_current: float
    total_max_all_current: float
    average_percent_all_current: float | None
    strongest_complete_student: str
    strongest_complete_percent: float | None
    weakest_complete_student: str
    weakest_complete_percent: float | None
    teacher_summary: str

    def as_csv_row(self) -> list[Any]:
        return [
            self.objective_code,
            self.submissions_with_objective,
            self.complete_submissions_with_objective,
            self.incomplete_submissions_with_objective,
            self.total_awarded_complete,
            self.total_max_complete,
            self.average_awarded_complete,
            self.average_percent_complete,
            self.total_awarded_all_current,
            self.total_max_all_current,
            self.average_percent_all_current,
            self.strongest_complete_student,
            self.strongest_complete_percent,
            self.weakest_complete_student,
            self.weakest_complete_percent,
            self.teacher_summary,
        ]


@dataclass
class MarksExportObjectiveColumn:
    objective_code: str
    awarded_header: str
    max_header: str


@dataclass
class MarksExportQuestionColumn:
    question_id: int
    awarded_header: str
    max_header: str
    objectives_header: str


@dataclass
class ExamMarksExportPlan:
    headers: list[str]
    objective_columns: list[MarksExportObjectiveColumn]
    question_columns: list[MarksExportQuestionColumn]


@dataclass
class ExamExportLayout:
    marks: ExamMarksExportPlan
    summary_headers: list[str]
    objectives_summary_headers: list[str]
    student_summary_manifest_headers: list[str]
    student_summary_evidence_headers: list[str]


MARKS_EXPORT_PREFIX_HEADERS = [
    "student",
    "capture_mode",
    "workflow_status",
    "export_ready",
    "flagged_questions",
    "teacher_marked_questions",
    "questions_total",
    "marking_progress",
    "total_awarded",
    "total_possible",
    "total_percent",
    "objective_summary",
    "objective_count",
    "reporting_attention",
    "next_return_point",
    "next_action",
]


@dataclass
class CsvExportArtifact(Generic[CsvExportRowT]):
    filename: str
    export_spec: CsvExportSpec[CsvExportRowT]


@dataclass
class ZipExportArtifact:
    filename: str
    artifact_specs: list[ZipArtifactSpec]


@dataclass
class ExamStudentSummaryZipArtifact:
    filename: str
    content: bytes


@dataclass
class TextZipArtifactSpec:
    relpath: str
    text: str


@dataclass
class FileZipArtifactSpec:
    relpath: str
    source_path: str


@dataclass
class CsvZipArtifactSpec(Generic[CsvExportRowT]):
    relpath: str
    export_spec: CsvExportSpec[CsvExportRowT]


ZipArtifactSpec = TextZipArtifactSpec | FileZipArtifactSpec | CsvZipArtifactSpec[CsvExportRow]


@dataclass
class StudentSummaryEvidenceArtifactContent:
    readme_text: str
    readme_relpath: str
    manifest_relpath: str
    rows: list[StudentSummaryEvidenceRow]


@dataclass
class StudentSummaryEvidencePackagePlan:
    readme_relpath: str
    manifest_relpath: str
    file_count: int
    artifact_specs: list[ZipArtifactSpec]


@dataclass
class StudentSummaryPackageArtifacts:
    package_dirname: str
    summary_text_relpath: str
    summary_html_relpath: str


@dataclass
class StudentSummaryPackagePlan:
    submission: Submission
    dashboard_row: SubmissionDashboardRow
    export_row: StudentReportingExportRow
    package_artifacts: StudentSummaryPackageArtifacts
    evidence_package: StudentSummaryEvidencePackagePlan | None
    artifact_specs: list[ZipArtifactSpec]

    @property
    def package_dirname(self) -> str:
        return self.package_artifacts.package_dirname

    @property
    def summary_text_relpath(self) -> str:
        return self.package_artifacts.summary_text_relpath

    @property
    def summary_html_relpath(self) -> str:
        return self.package_artifacts.summary_html_relpath


@dataclass
class StudentSummariesZipArtifacts:
    root_dirname: str
    readme_relpath: str
    manifest_relpath: str


@dataclass
class ExamStudentSummariesZipPlan:
    archive_artifacts: StudentSummariesZipArtifacts
    readme_text: str
    manifest_rows: list[StudentSummaryManifestRow]
    manifest_export_spec: CsvExportSpec[StudentSummaryManifestRow]
    submission_packages: list[StudentSummaryPackagePlan]


@dataclass
class StudentSummaryQuestionRow:
    label: str
    marks_awarded: str | float
    max_marks: float
    objective_codes: list[str]
    status: str
    teacher_note: str

    @property
    def objective_text(self) -> str:
        return ", ".join(self.objective_codes) if self.objective_codes else "No objective code"


@dataclass
class StudentSummaryObjectiveRow:
    objective_code: str
    marks_awarded: float
    max_marks: float
    percent: float | None
    coverage: str


@dataclass
class StudentReportingSnapshot:
    student_name: str
    capture_mode: str
    workflow_status: str
    export_ready: bool
    total_awarded: float
    total_possible: float
    total_percent: float | None
    objective_summary: str
    reporting_attention: str
    next_return_point: str
    next_action: str


@dataclass
class StudentReportingExportRow:
    student: str
    capture_mode: str
    workflow_status: str
    export_ready: str
    flagged_questions: int
    teacher_marked_questions: int
    questions_total: int
    marking_progress: str
    total_awarded: float
    total_possible: float
    total_percent: str | float
    objective_summary: str
    objective_count: int
    reporting_attention: str
    next_return_point: str
    next_action: str

    def as_marks_prefix_row(self) -> list[Any]:
        return [
            self.student,
            self.capture_mode,
            self.workflow_status,
            self.export_ready,
            self.flagged_questions,
            self.teacher_marked_questions,
            self.questions_total,
            self.marking_progress,
            self.total_awarded,
            self.total_possible,
            self.total_percent,
            self.objective_summary,
            self.objective_count,
            self.reporting_attention,
            self.next_return_point,
            self.next_action,
        ]

    def as_summary_csv_row(self) -> list[Any]:
        return [
            self.student,
            self.capture_mode,
            self.workflow_status,
            self.export_ready,
            self.marking_progress,
            self.total_awarded,
            self.total_possible,
            self.total_percent,
            self.teacher_marked_questions,
            self.questions_total,
            self.objective_count,
            self.objective_summary,
            self.next_return_point,
            self.next_action,
            self.reporting_attention,
        ]


@dataclass
class StudentSummaryPayload:
    exam_name: str
    student_name: str
    capture_mode: str
    workflow_status: str
    workflow_status_label: str
    export_ready: bool
    total_awarded: float
    total_possible: float
    total_percent: float | None
    teacher_marked_questions: int
    questions_total: int
    teacher_marked_summary: str
    reporting_attention: str
    next_return_point: str
    next_action: str
    objective_summary: str
    objectives: list[StudentSummaryObjectiveRow]
    question_rows: list[StudentSummaryQuestionRow]
    uses_front_page_totals: bool


@dataclass
class StudentSummaryEvidenceRow(CsvExportRow):
    question_id: int
    question_label: str
    crop_path: str
    crop_relpath: str
    transcription_text: str
    transcription_raw_json: str
    transcription_provider: str
    transcription_confidence: str | float
    transcription_text_relpath: str
    transcription_json_relpath: str
    grade_status: str
    teacher_note: str

    def as_csv_row(self) -> list[Any]:
        return [
            self.question_label,
            self.grade_status,
            self.teacher_note,
            self.transcription_provider,
            self.transcription_confidence,
            self.crop_relpath,
            self.transcription_text_relpath,
            self.transcription_json_relpath,
        ]


@dataclass
class StudentSummaryManifestRow(CsvExportRow):
    student: str
    capture_mode: str
    workflow_status: str
    export_ready: str
    flagged_questions: int
    teacher_marked_questions: int
    questions_total: int
    marking_progress: str
    total_awarded: float
    total_possible: float
    total_percent: str | float
    objective_summary: str
    reporting_attention: str
    next_return_point: str
    next_action: str
    summary_text_file: str
    summary_html_file: str
    evidence_manifest_file: str
    evidence_file_count: int

    def as_csv_row(self) -> list[Any]:
        return [
            self.student,
            self.capture_mode,
            self.workflow_status,
            self.export_ready,
            self.flagged_questions,
            self.teacher_marked_questions,
            self.questions_total,
            self.marking_progress,
            self.total_awarded,
            self.total_possible,
            self.total_percent,
            self.objective_summary,
            self.reporting_attention,
            self.next_return_point,
            self.next_action,
            self.summary_text_file,
            self.summary_html_file,
            self.evidence_manifest_file,
            self.evidence_file_count,
        ]


def load_exam_reporting_snapshot(
    exam_id: int,
    *,
    questions: list[Question],
    submissions: list[Submission],
    session: Session,
) -> ExamReportingSnapshot:
    _ = exam_id
    question_ids = [question.id for question in questions]
    submission_ids = [submission.id for submission in submissions]

    question_regions_by_question_id: dict[int, list[QuestionRegion]] = defaultdict(list)
    if question_ids:
        regions = session.exec(select(QuestionRegion).where(QuestionRegion.question_id.in_(question_ids))).all()
        for region in regions:
            question_regions_by_question_id[region.question_id].append(region)

    pages_by_submission_id: dict[int, list[SubmissionPage]] = defaultdict(list)
    crops_by_submission_id: dict[int, list[AnswerCrop]] = defaultdict(list)
    transcriptions_by_submission_id: dict[int, list[Transcription]] = defaultdict(list)
    grades_by_submission_id: dict[int, list[GradeResult]] = defaultdict(list)

    if submission_ids:
        for page in session.exec(select(SubmissionPage).where(SubmissionPage.submission_id.in_(submission_ids))).all():
            pages_by_submission_id[page.submission_id].append(page)
        for crop in session.exec(select(AnswerCrop).where(AnswerCrop.submission_id.in_(submission_ids))).all():
            crops_by_submission_id[crop.submission_id].append(crop)
        for transcription in session.exec(select(Transcription).where(Transcription.submission_id.in_(submission_ids))).all():
            transcriptions_by_submission_id[transcription.submission_id].append(transcription)
        for grade in session.exec(select(GradeResult).where(GradeResult.submission_id.in_(submission_ids))).all():
            grades_by_submission_id[grade.submission_id].append(grade)

    submission_data_by_submission_id = {
        submission.id: ExamSubmissionReportingData(
            pages=pages_by_submission_id.get(submission.id, []),
            crops=crops_by_submission_id.get(submission.id, []),
            transcriptions=transcriptions_by_submission_id.get(submission.id, []),
            grades=grades_by_submission_id.get(submission.id, []),
        )
        for submission in submissions
    }

    return ExamReportingSnapshot(
        question_regions_by_question_id=dict(question_regions_by_question_id),
        submission_data_by_submission_id=submission_data_by_submission_id,
    )



def build_submission_reporting_projection(
    submission: Submission,
    row: SubmissionDashboardRow,
    *,
    questions: list[Question],
    snapshot: ExamReportingSnapshot,
) -> SubmissionReportingProjection:
    question_rows: list[StudentSummaryQuestionRow] = []
    grade_map: dict[int, GradeResult] = {}
    export_row = build_student_reporting_export_row(row)
    objective_values_by_code = {
        objective.objective_code: MarksExportObjectiveValue(
            objective_code=objective.objective_code,
            marks_awarded=round(float(objective.marks_awarded), 2),
            max_marks=round(float(objective.max_marks), 2),
        )
        for objective in row.objective_totals
    }
    question_values_by_question_id: dict[int, MarksExportQuestionValue] = {}

    if submission.capture_mode != SubmissionCaptureMode.FRONT_PAGE_TOTALS:
        submission_data = snapshot.submission_data_by_submission_id.get(
            submission.id,
            ExamSubmissionReportingData(pages=[], crops=[], transcriptions=[], grades=[]),
        )
        grade_map = {grade.question_id: grade for grade in submission_data.grades}

    for question in questions:
        grade = grade_map.get(question.id)
        objective_codes = question_objective_codes(question)
        question_values_by_question_id[question.id] = MarksExportQuestionValue(
            question_id=question.id,
            marks_awarded=None if grade is None else round(float(grade.marks_awarded), 2),
            max_marks=round(float(question.max_marks), 2),
            objective_codes=objective_codes,
        )

        if submission.capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS:
            continue

        teacher_note = ""
        if grade:
            feedback = json.loads(grade.feedback_json) if grade.feedback_json else {}
            teacher_note = str(feedback.get("teacher_note") or "").strip() if isinstance(feedback, dict) else ""
        question_rows.append(StudentSummaryQuestionRow(
            label=question.label,
            marks_awarded="" if grade is None else round(float(grade.marks_awarded), 2),
            max_marks=round(float(question.max_marks), 2),
            objective_codes=objective_codes,
            status="Teacher-marked" if grade is not None else "Not yet marked",
            teacher_note=teacher_note,
        ))

    return SubmissionReportingProjection(
        submission=submission,
        dashboard_row=row,
        export_row=export_row,
        marks_export_payload=SubmissionMarksExportPayload(
            prefix_values=export_row.as_marks_prefix_row(),
            objective_values_by_code=objective_values_by_code,
            question_values_by_question_id=question_values_by_question_id,
        ),
        grade_map=grade_map,
        question_rows=question_rows,
    )



def load_exam_reporting_context(exam_id: int, session: Session) -> ExamReportingContext | None:
    exam = session.get(Exam, exam_id)
    if not exam:
        return None

    questions = session.exec(select(Question).where(Question.exam_id == exam_id).order_by(Question.id)).all()
    submissions = session.exec(
        select(Submission)
        .where(Submission.exam_id == exam_id)
        .order_by(Submission.created_at.asc(), Submission.id.asc())
    ).all()
    snapshot = load_exam_reporting_snapshot(exam_id, questions=questions, submissions=submissions, session=session)
    dashboard_rows = [build_submission_dashboard_row(submission, session, questions=questions, snapshot=snapshot) for submission in submissions]
    submission_projections = [
        build_submission_reporting_projection(submission, row, questions=questions, snapshot=snapshot)
        for submission, row in zip(submissions, dashboard_rows, strict=False)
    ]
    return ExamReportingContext(
        exam=exam,
        questions=questions,
        submissions=submissions,
        dashboard_rows=dashboard_rows,
        submission_projections=submission_projections,
        snapshot=snapshot,
    )


def build_submission_dashboard_row(
    submission: Submission,
    session: Session,
    *,
    questions: list[Question] | None = None,
    snapshot: ExamReportingSnapshot | None = None,
) -> SubmissionDashboardRow:
    questions = questions or session.exec(select(Question).where(Question.exam_id == submission.exam_id).order_by(Question.id)).all()
    total_possible = float(sum(question.max_marks for question in questions))
    if submission.capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS:
        front_page_totals = front_page_totals_read(submission)
        objective_totals = front_page_objective_totals(front_page_totals)
        confirmed = bool(front_page_totals and front_page_totals.confirmed)
        running_total = float(front_page_totals.overall_marks_awarded) if front_page_totals else 0.0
        if front_page_totals and front_page_totals.overall_max_marks is not None:
            total_possible = float(front_page_totals.overall_max_marks)
        workflow_status = "complete" if confirmed else "ready"
        return attach_student_reporting_state(SubmissionDashboardRow(
            submission_id=submission.id,
            student_name=submission.student_name,
            capture_mode=submission.capture_mode,
            workflow_status=workflow_status,
            flagged_count=0,
            questions_total=0,
            teacher_marked_questions=1 if confirmed else 0,
            marking_progress=student_marking_progress_text_from_values(
                capture_mode=submission.capture_mode,
                workflow_status=workflow_status,
                teacher_marked_questions=1 if confirmed else 0,
                questions_total=0,
            ),
            running_total=running_total,
            total_possible=total_possible,
            objective_totals=objective_totals_read(objective_totals),
            ready_for_marking=confirmed,
            can_prepare_now=True,
            summary_reasons=[] if confirmed else ["Front-page totals still need teacher confirmation."],
            next_question_id=None,
            next_question_label=None,
            next_action_text="Review saved front-page totals." if confirmed else "Capture and confirm the front-page totals.",
        ))

    submission_data = snapshot.submission_data_by_submission_id.get(submission.id) if snapshot else None
    pages = submission_data.pages if submission_data is not None else session.exec(select(SubmissionPage).where(SubmissionPage.submission_id == submission.id)).all()
    crops = submission_data.crops if submission_data is not None else session.exec(select(AnswerCrop).where(AnswerCrop.submission_id == submission.id)).all()
    transcriptions = submission_data.transcriptions if submission_data is not None else session.exec(select(Transcription).where(Transcription.submission_id == submission.id)).all()
    grades = submission_data.grades if submission_data is not None else session.exec(select(GradeResult).where(GradeResult.submission_id == submission.id)).all()

    page_numbers = {page.page_number for page in pages if page.image_path}
    crop_map = {crop.question_id: crop for crop in crops if crop.image_path}
    transcription_map = {item.question_id: item for item in transcriptions}
    grade_map = {grade.question_id: grade for grade in grades}

    summary_reasons: list[str] = []
    can_prepare_now = True
    flagged_count = 0
    teacher_marked_questions = 0
    objective_totals: dict[str, dict[str, float]] = {}
    running_total = 0.0
    next_question_id: int | None = None
    next_question_label: str | None = None
    next_action_text: str | None = None

    if not pages and questions:
        summary_reasons.append("No submission pages have been built yet.")

    for question in questions:
        regions = snapshot.question_regions_by_question_id.get(question.id, []) if snapshot else session.exec(select(QuestionRegion).where(QuestionRegion.question_id == question.id)).all()
        flagged_reasons: list[str] = []
        if not regions:
            flagged_reasons.append("No template regions saved for this question.")
            can_prepare_now = False

        region_page_numbers = sorted({region.page_number for region in regions})
        missing_for_question = [page_number for page_number in region_page_numbers if page_number not in page_numbers]
        if missing_for_question:
            flagged_reasons.append(f"Missing submission page(s): {', '.join(str(n) for n in missing_for_question)}.")
            can_prepare_now = False

        crop = crop_map.get(question.id)
        if regions and not missing_for_question and not crop:
            flagged_reasons.append("Answer crop has not been built yet.")

        transcription = transcription_map.get(question.id)
        if crop and not transcription:
            flagged_reasons.append("Transcription has not been generated yet.")
        elif transcription and not transcription.text.strip():
            flagged_reasons.append("Transcription is empty.")
        elif transcription and transcription.confidence < 0.5:
            flagged_reasons.append("Transcription confidence is low.")

        if flagged_reasons:
            flagged_count += 1
            if next_question_id is None:
                next_question_id = question.id
                next_question_label = question.label
                next_action_text = f"Open {question.label} to clear the blocker."

        grade = grade_map.get(question.id)
        awarded = float(grade.marks_awarded) if grade else 0.0
        running_total += awarded
        if grade and grade.model_name == "teacher_manual":
            teacher_marked_questions += 1
        elif next_question_id is None and not flagged_reasons:
            next_question_id = question.id
            next_question_label = question.label
            next_action_text = f"Open {question.label} to keep marking moving."

        accumulate_objective_totals(objective_totals, question, awarded)

    questions_total = len(questions)
    ready_for_marking = questions_total > 0 and flagged_count == 0

    if questions_total == 0:
        workflow_status = "blocked"
        summary_reasons.append("No questions are configured for this exam yet.")
        can_prepare_now = False
    elif teacher_marked_questions == questions_total:
        workflow_status = "complete"
    elif teacher_marked_questions > 0:
        workflow_status = "in_progress"
    elif ready_for_marking:
        workflow_status = "ready"
    else:
        workflow_status = "blocked"

    if not summary_reasons and workflow_status == "blocked":
        summary_reasons.append("Preparation is incomplete for at least one question.")

    if workflow_status == "complete":
        next_action_text = "Review results or return to the class queue."
    elif workflow_status == "ready" and next_question_label:
        next_action_text = f"Start marking at {next_question_label}."
    elif workflow_status == "in_progress" and next_question_label:
        next_action_text = f"Resume marking at {next_question_label}."
    elif workflow_status == "blocked" and next_question_label and not next_action_text:
        next_action_text = f"Open {next_question_label} to clear the blocker."

    return attach_student_reporting_state(SubmissionDashboardRow(
        submission_id=submission.id,
        student_name=submission.student_name,
        capture_mode=submission.capture_mode,
        workflow_status=workflow_status,
        flagged_count=flagged_count,
        questions_total=questions_total,
        teacher_marked_questions=teacher_marked_questions,
        marking_progress=student_marking_progress_text_from_values(
            capture_mode=submission.capture_mode,
            workflow_status=workflow_status,
            teacher_marked_questions=teacher_marked_questions,
            questions_total=questions_total,
        ),
        running_total=running_total,
        total_possible=total_possible,
        objective_totals=objective_totals_read(objective_totals),
        ready_for_marking=ready_for_marking,
        can_prepare_now=can_prepare_now,
        summary_reasons=summary_reasons,
        next_question_id=next_question_id,
        next_question_label=next_question_label,
        next_action_text=next_action_text,
    ))


def build_exam_marking_dashboard(context: ExamReportingContext) -> ExamMarkingDashboardResponse:
    objective_report = build_exam_objective_report(context.dashboard_rows)

    ready_count = sum(1 for row in context.dashboard_rows if row.workflow_status == "ready")
    blocked_count = sum(1 for row in context.dashboard_rows if row.workflow_status == "blocked")
    in_progress_count = sum(1 for row in context.dashboard_rows if row.workflow_status == "in_progress")
    complete_count = sum(1 for row in context.dashboard_rows if row.workflow_status == "complete")
    total_submissions = len(context.dashboard_rows)
    completion_percent = round((complete_count / total_submissions) * 100, 1) if total_submissions else 0.0

    return ExamMarkingDashboardResponse(
        exam_id=context.exam.id,
        exam_name=context.exam.name,
        total_possible=float(sum(question.max_marks for question in context.questions)),
        objectives=objective_report.objectives,
        submissions=context.dashboard_rows,
        completion=ExamCompletionSummary(
            total_submissions=total_submissions,
            ready_count=ready_count,
            blocked_count=blocked_count,
            in_progress_count=in_progress_count,
            complete_count=complete_count,
            completion_percent=completion_percent,
        ),
    )


def marks_export_question_safe_label(question: Question) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", question.label.strip()).strip("_") or f"question_{question.id}"


def build_exam_marks_export_layout(questions: list[Question]) -> ExamMarksExportPlan:
    objective_columns = [
        MarksExportObjectiveColumn(
            objective_code=code,
            awarded_header=f"objective_{code}_awarded",
            max_header=f"objective_{code}_max",
        )
        for code in sorted({code for question in questions for code in question_objective_codes(question)})
    ]
    question_columns = [
        MarksExportQuestionColumn(
            question_id=question.id,
            awarded_header=f"{safe_label}_awarded",
            max_header=f"{safe_label}_max",
            objectives_header=f"{safe_label}_objectives",
        )
        for question in questions
        for safe_label in [marks_export_question_safe_label(question)]
    ]

    headers = list(MARKS_EXPORT_PREFIX_HEADERS)
    for column in objective_columns:
        headers.extend([column.awarded_header, column.max_header])
    for column in question_columns:
        headers.extend([
            column.awarded_header,
            column.max_header,
            column.objectives_header,
        ])

    return ExamMarksExportPlan(
        headers=headers,
        objective_columns=objective_columns,
        question_columns=question_columns,
    )


SUMMARY_EXPORT_HEADERS = [
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
]

OBJECTIVES_SUMMARY_EXPORT_HEADERS = [
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
]

STUDENT_SUMMARY_MANIFEST_HEADERS = [
    "student",
    "capture_mode",
    "workflow_status",
    "export_ready",
    "flagged_questions",
    "teacher_marked_questions",
    "questions_total",
    "marking_progress",
    "total_awarded",
    "total_possible",
    "total_percent",
    "objective_summary",
    "reporting_attention",
    "next_return_point",
    "next_action",
    "summary_text_file",
    "summary_html_file",
    "evidence_manifest_file",
    "evidence_file_count",
]

STUDENT_SUMMARY_EVIDENCE_HEADERS = [
    "question_label",
    "grade_status",
    "teacher_note",
    "transcription_provider",
    "transcription_confidence",
    "crop_image_file",
    "transcription_text_file",
    "transcription_json_file",
]


def build_exam_export_layout(questions: list[Question]) -> ExamExportLayout:
    return ExamExportLayout(
        marks=build_exam_marks_export_layout(questions),
        summary_headers=list(SUMMARY_EXPORT_HEADERS),
        objectives_summary_headers=list(OBJECTIVES_SUMMARY_EXPORT_HEADERS),
        student_summary_manifest_headers=list(STUDENT_SUMMARY_MANIFEST_HEADERS),
        student_summary_evidence_headers=list(STUDENT_SUMMARY_EVIDENCE_HEADERS),
    )


def _build_exam_marks_export_plan(context: ExamReportingContext) -> ExamMarksExportPlan:
    return build_exam_export_layout(context.questions).marks


def _build_exam_marks_export_row(
    projection: SubmissionReportingProjection,
    *,
    export_plan: ExamMarksExportPlan,
) -> ExamMarksExportRow:
    payload = projection.marks_export_payload

    objective_values: list[Any] = []
    for column in export_plan.objective_columns:
        objective = payload.objective_values_by_code.get(column.objective_code)
        objective_values.extend([
            "" if objective is None else objective.marks_awarded,
            "" if objective is None else objective.max_marks,
        ])

    question_values: list[Any] = []
    for column in export_plan.question_columns:
        question_value = payload.question_values_by_question_id.get(column.question_id)
        question_values.extend([
            "" if question_value is None or question_value.marks_awarded is None else question_value.marks_awarded,
            "" if question_value is None else question_value.max_marks,
            "" if question_value is None else "; ".join(question_value.objective_codes),
        ])

    return ExamMarksExportRow(
        submission=projection.submission,
        dashboard_row=projection.dashboard_row,
        prefix_values=list(payload.prefix_values),
        objective_values=objective_values,
        question_values=question_values,
    )


def build_exam_marks_export_spec(context: ExamReportingContext, session: Session) -> CsvExportSpec[ExamMarksExportRow]:
    _ = session
    export_plan = _build_exam_marks_export_plan(context)
    return CsvExportSpec(
        headers=export_plan.headers,
        rows=[
            _build_exam_marks_export_row(
                projection,
                export_plan=export_plan,
            )
            for projection in context.submission_projections
        ],
    )


def build_exam_marks_export_artifact(exam_id: int, session: Session) -> CsvExportArtifact[ExamMarksExportRow] | None:
    context = load_exam_reporting_context(exam_id, session)
    if context is None:
        return None
    return CsvExportArtifact(
        filename=f"exam-{exam_id}-marks.csv",
        export_spec=build_exam_marks_export_spec(context, session),
    )


def build_exam_summary_export_spec(context: ExamReportingContext) -> CsvExportSpec[ExamSummaryExportRow]:
    headers = build_exam_export_layout(context.questions).summary_headers
    return CsvExportSpec(
        headers=headers,
        rows=[
            ExamSummaryExportRow(
                dashboard_row=projection.dashboard_row,
                export_row=projection.export_row,
            )
            for projection in context.submission_projections
        ],
    )


def build_exam_summary_export_artifact(exam_id: int, session: Session) -> CsvExportArtifact[ExamSummaryExportRow] | None:
    context = load_exam_reporting_context(exam_id, session)
    if context is None:
        return None
    return CsvExportArtifact(
        filename=f"exam-{exam_id}-summary.csv",
        export_spec=build_exam_summary_export_spec(context),
    )


def _xlsx_column_name(index: int) -> str:
    name = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xlsx_inline_string_cell(cell_ref: str, value: Any) -> str:
    text = xml_escape("" if value is None else str(value))
    return f'<c r="{cell_ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def build_exam_gradebook_xlsx_bytes(context: ExamReportingContext) -> bytes:
    headers = ["test_name", "name", "grade"]
    rows: list[list[str]] = [headers]
    for projection in context.submission_projections:
        export_row = projection.export_row
        total_awarded = round(float(export_row.total_awarded), 2)
        total_possible = round(float(export_row.total_possible), 2)
        rows.append([
            context.exam.name,
            export_row.student,
            f"{total_awarded:g}/{total_possible:g}",
        ])

    sheet_rows: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells = [
          _xlsx_inline_string_cell(f"{_xlsx_column_name(column_index)}{row_index}", value)
          for column_index, value in enumerate(row, start=1)
        ]
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        f'{"".join(sheet_rows)}'
        '</sheetData>'
        '</worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Grades" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", root_rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
    return buffer.getvalue()


def build_exam_gradebook_xlsx_artifact(exam_id: int, session: Session) -> tuple[str, bytes] | None:
    context = load_exam_reporting_context(exam_id, session)
    if context is None:
        return None
    return (f"exam-{exam_id}-grades.xlsx", build_exam_gradebook_xlsx_bytes(context))


def build_student_summary_manifest_export_spec(
    questions: list[Question],
    rows: list[StudentSummaryManifestRow],
) -> CsvExportSpec[StudentSummaryManifestRow]:
    headers = build_exam_export_layout(questions).student_summary_manifest_headers
    return CsvExportSpec(headers=headers, rows=rows)


def build_student_summary_evidence_export_spec(
    questions: list[Question],
    rows: list[StudentSummaryEvidenceRow],
) -> CsvExportSpec[StudentSummaryEvidenceRow]:
    headers = build_exam_export_layout(questions).student_summary_evidence_headers
    return CsvExportSpec(headers=headers, rows=rows)


def build_exam_objectives_summary_export_spec(context: ExamReportingContext) -> CsvExportSpec[ExamObjectiveSummaryExportRow]:
    headers = build_exam_export_layout(context.questions).objectives_summary_headers

    return CsvExportSpec(
        headers=headers,
        rows=[
            ExamObjectiveSummaryExportRow(
                objective_code=summary.objective_code,
                submissions_with_objective=summary.submissions_with_objective,
                complete_submissions_with_objective=summary.complete_submissions_with_objective,
                incomplete_submissions_with_objective=summary.incomplete_submissions_with_objective,
                total_awarded_complete=summary.total_awarded_complete,
                total_max_complete=summary.total_max_complete,
                average_awarded_complete=summary.average_awarded_complete,
                average_percent_complete=summary.average_percent_complete,
                total_awarded_all_current=summary.total_awarded_all_current,
                total_max_all_current=summary.total_max_all_current,
                average_percent_all_current=summary.average_percent_all_current,
                strongest_complete_student=summary.strongest_complete_student,
                strongest_complete_percent=summary.strongest_complete_percent,
                weakest_complete_student=summary.weakest_complete_student,
                weakest_complete_percent=summary.weakest_complete_percent,
                teacher_summary=summary.teacher_summary,
            )
            for summary in build_exam_objective_report(context.dashboard_rows).objectives
        ],
    )


def build_exam_objectives_summary_export_artifact(exam_id: int, session: Session) -> CsvExportArtifact[ExamObjectiveSummaryExportRow] | None:
    context = load_exam_reporting_context(exam_id, session)
    if context is None:
        return None
    return CsvExportArtifact(
        filename=f"exam-{exam_id}-objectives-summary.csv",
        export_spec=build_exam_objectives_summary_export_spec(context),
    )


def build_exam_marking_dashboard_response(exam_id: int, session: Session) -> ExamMarkingDashboardResponse | None:
    context = load_exam_reporting_context(exam_id, session)
    if context is None:
        return None
    return build_exam_marking_dashboard(context)


def student_summary_reporting_attention(row: SubmissionDashboardRow) -> str:
    if row.workflow_status == "complete":
        return "Every submission currently has a complete result."
    if row.summary_reasons:
        return row.summary_reasons[0]
    return "Result needs teacher attention before it is ready for export."


def workflow_status_label(value: str) -> str:
    return {
        "ready": "Ready",
        "blocked": "Blocked",
        "in_progress": "In progress",
        "complete": "Complete",
    }.get(value, value.replace("_", " ").title())


def safe_export_stem(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or fallback


def student_package_dirname(index: int, stem: str) -> str:
    return f"student-summaries/{index:02d}-{stem}"


def student_package_relpath(dirname: str, filename: str) -> str:
    return f"{dirname}/{filename}"


def student_marking_progress_text_from_values(
    *,
    capture_mode: SubmissionCaptureMode,
    workflow_status: str,
    teacher_marked_questions: int,
    questions_total: int,
) -> str:
    if capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS:
        return "confirmed totals" if workflow_status == "complete" else "pending front-page confirmation"
    if questions_total <= 0:
        return "0/0 marked"
    return f"{teacher_marked_questions}/{questions_total} marked"


def student_marking_progress_text(row: SubmissionDashboardRow) -> str:
    return row.marking_progress or student_marking_progress_text_from_values(
        capture_mode=row.capture_mode,
        workflow_status=row.workflow_status,
        teacher_marked_questions=row.teacher_marked_questions,
        questions_total=row.questions_total,
    )


def attach_student_reporting_state(row: SubmissionDashboardRow) -> SubmissionDashboardRow:
    row.export_ready = row.workflow_status == "complete"
    row.reporting_attention = student_summary_reporting_attention(row)
    row.next_return_point = row.next_question_label or ""
    row.next_action = row.next_action_text or ""
    return row


def build_student_reporting_snapshot(row: SubmissionDashboardRow) -> StudentReportingSnapshot:
    total_possible_value = round(float(row.total_possible), 2)
    total_awarded_value = round(float(row.running_total), 2)
    total_percent = round((total_awarded_value / total_possible_value) * 100, 1) if total_possible_value > 0 else None
    objective_summary = objective_summary_text(row.objective_totals)
    return StudentReportingSnapshot(
        student_name=row.student_name,
        capture_mode=row.capture_mode.value if hasattr(row.capture_mode, "value") else str(row.capture_mode),
        workflow_status=row.workflow_status,
        export_ready=row.export_ready,
        total_awarded=total_awarded_value,
        total_possible=total_possible_value,
        total_percent=total_percent,
        objective_summary=objective_summary,
        reporting_attention=row.reporting_attention or student_summary_reporting_attention(row),
        next_return_point=row.next_return_point,
        next_action=row.next_action,
    )


def build_student_reporting_export_row(row: SubmissionDashboardRow) -> StudentReportingExportRow:
    snapshot = build_student_reporting_snapshot(row)
    return StudentReportingExportRow(
        student=snapshot.student_name,
        capture_mode=snapshot.capture_mode,
        workflow_status=snapshot.workflow_status,
        export_ready="yes" if snapshot.export_ready else "no",
        flagged_questions=row.flagged_count,
        teacher_marked_questions=row.teacher_marked_questions,
        questions_total=row.questions_total,
        marking_progress=student_marking_progress_text(row),
        total_awarded=snapshot.total_awarded,
        total_possible=snapshot.total_possible,
        total_percent="" if snapshot.total_percent is None else snapshot.total_percent,
        objective_summary=snapshot.objective_summary,
        objective_count=len(row.objective_totals),
        reporting_attention=snapshot.reporting_attention,
        next_return_point=snapshot.next_return_point,
        next_action=snapshot.next_action,
    )


def build_student_summary_payload(
    exam: Exam,
    submission: Submission,
    row: SubmissionDashboardRow,
    question_rows: list[StudentSummaryQuestionRow],
) -> StudentSummaryPayload:
    snapshot = build_student_reporting_snapshot(row)
    objective_summary = snapshot.objective_summary or "No objective totals available yet."
    objectives: list[StudentSummaryObjectiveRow] = []
    for objective in row.objective_totals:
        max_marks = float(objective.max_marks)
        marks_awarded = float(objective.marks_awarded)
        percent = round((marks_awarded / max_marks) * 100, 1) if max_marks > 0 else None
        coverage = (
            "front-page category total"
            if submission.capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS
            else f"{objective.questions_count} question{'s' if objective.questions_count != 1 else ''}"
        )
        objectives.append(StudentSummaryObjectiveRow(
            objective_code=objective.objective_code,
            marks_awarded=round(marks_awarded, 2),
            max_marks=round(max_marks, 2),
            percent=percent,
            coverage=coverage,
        ))

    return StudentSummaryPayload(
        exam_name=exam.name,
        student_name=submission.student_name,
        capture_mode=snapshot.capture_mode,
        workflow_status=snapshot.workflow_status,
        workflow_status_label=workflow_status_label(snapshot.workflow_status),
        export_ready=snapshot.export_ready,
        total_awarded=snapshot.total_awarded,
        total_possible=snapshot.total_possible,
        total_percent=snapshot.total_percent,
        teacher_marked_questions=row.teacher_marked_questions,
        questions_total=row.questions_total,
        teacher_marked_summary=(
            f"{row.teacher_marked_questions}/{row.questions_total}"
            if submission.capture_mode != SubmissionCaptureMode.FRONT_PAGE_TOTALS
            else "front-page totals workflow"
        ),
        reporting_attention=snapshot.reporting_attention,
        next_return_point=snapshot.next_return_point or "—",
        next_action=snapshot.next_action or "—",
        objective_summary=objective_summary,
        objectives=objectives,
        question_rows=question_rows,
        uses_front_page_totals=submission.capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS,
    )


def build_student_package_readme_text(exam: Exam, submissions: list[Submission]) -> str:
    question_level_count = sum(1 for submission in submissions if submission.capture_mode != SubmissionCaptureMode.FRONT_PAGE_TOTALS)
    front_page_count = len(submissions) - question_level_count
    lines = [
        f"SuperMarks student summary package — {exam.name}",
        "",
        "What this zip contains:",
        "- manifest.csv — class index with one row per student package and direct file paths.",
        "- One folder per student, each with summary.txt and summary.html.",
        "- Question-level submissions also include an evidence/ folder with crops, transcriptions, and evidence/manifest.csv.",
        "",
        "How to use it:",
        "1. Open manifest.csv if you want the whole-class index first.",
        "2. Check each row's export_ready, workflow_status, flagged_questions, teacher_marked_questions, questions_total, marking_progress, next_return_point, and next_action columns before treating that student result as final.",
        "3. Open a student's summary.txt or summary.html for the teacher-readable overview.",
        "4. If you need source evidence for a question-level submission, open that student's evidence/manifest.csv first, then the linked files.",
        "",
        "Evidence framing:",
        "- summary.* = teacher-facing summary of the current saved result; use manifest.csv to see whether that result is export-ready or still awaiting teacher confirmation.",
        "- evidence/manifest.csv = per-question map of what source artifacts exist.",
        "- *-crop.png = answer image used during review.",
        "- *-transcription.txt/json = OCR text and raw structured OCR output when available.",
        "- Front-page totals submissions do not include question-level evidence; use each student's summary and manifest row for confirmation status, export readiness, totals, and objective summaries.",
        "",
        f"Package totals: {len(submissions)} students · {question_level_count} question-level package{'s' if question_level_count != 1 else ''} · {front_page_count} front-page totals package{'s' if front_page_count != 1 else ''}.",
        "",
        "Generated by SuperMarks.",
    ]
    return "\n".join(lines).strip() + "\n"


def build_submission_evidence_readme_text(submission: Submission) -> str:
    if submission.capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS:
        return ""
    lines = [
        f"Evidence guide — {submission.student_name}",
        "",
        "Open evidence/manifest.csv first. It tells you, question by question:",
        "- whether a saved teacher mark exists",
        "- whether a teacher note was saved",
        "- which crop image belongs to the question",
        "- which transcription text/json files exist",
        "",
        "Use this folder as supporting source evidence for the exported summary, not as a replacement for teacher judgment.",
        "",
        "Generated by SuperMarks.",
    ]
    return "\n".join(lines).strip() + "\n"


def front_page_totals_question_breakdown_text(export_ready: bool) -> str:
    if export_ready:
        return "This submission uses confirmed front-page totals, so question-level marks are not stored in this export package."
    return "This submission uses saved front-page totals that still need teacher confirmation, so question-level marks are not stored in this export package."


def student_summary_footer_note(export_ready: bool) -> str:
    if export_ready:
        return "Generated from the SuperMarks export-ready student summary package."
    return "Generated from the SuperMarks student summary package; confirm front-page totals before treating this export as final."


def build_student_summary_text(
    exam: Exam,
    submission: Submission,
    row: SubmissionDashboardRow,
    question_rows: list[StudentSummaryQuestionRow],
) -> str:
    payload = build_student_summary_payload(exam, submission, row, question_rows)
    lines = [
        f"Exam: {payload.exam_name}",
        f"Student: {payload.student_name}",
        f"Capture mode: {payload.capture_mode}",
        f"Workflow status: {payload.workflow_status_label}",
        f"Export-ready: {'yes' if payload.export_ready else 'no'}",
        f"Total: {payload.total_awarded}/{payload.total_possible}" + (f" ({payload.total_percent}%)" if payload.total_percent is not None else ""),
        f"Teacher-marked questions: {payload.teacher_marked_summary}",
        f"Reporting attention: {payload.reporting_attention}",
        f"Next return point: {payload.next_return_point}",
        f"Next action: {payload.next_action}",
        "",
        "Objective breakdown:",
    ]
    if payload.objectives:
        for objective in payload.objectives:
            lines.append(
                f"- {objective.objective_code}: {objective.marks_awarded}/{objective.max_marks}"
                + (f" ({objective.percent}%)" if objective.percent is not None else "")
                + f" · {objective.coverage}"
            )
    else:
        lines.append("- No objective totals available yet.")

    lines.extend(["", f"Objective summary: {payload.objective_summary}", ""])

    if payload.uses_front_page_totals:
        lines.extend([
            "Question breakdown:",
            f"- {front_page_totals_question_breakdown_text(payload.export_ready)}",
            f"- Export readiness: {'ready to return/export' if payload.export_ready else 'not final until the front-page totals are confirmed.'}",
        ])
        return "\n".join(lines).strip() + "\n"

    lines.append("Question breakdown:")
    if payload.question_rows:
        for item in payload.question_rows:
            lines.append(
                f"- {item.label}: {item.marks_awarded}/{item.max_marks} · {item.objective_text} · {item.status}"
            )
            if item.teacher_note:
                lines.append(f"  Teacher note: {item.teacher_note}")
    else:
        lines.append("- No question-level marks saved yet.")

    return "\n".join(lines).strip() + "\n"


def build_student_summary_html(
    exam: Exam,
    submission: Submission,
    row: SubmissionDashboardRow,
    question_rows: list[StudentSummaryQuestionRow],
) -> str:
    payload = build_student_summary_payload(exam, submission, row, question_rows)
    total_suffix = f" ({payload.total_percent}%)" if payload.total_percent is not None else ""
    objective_items = "".join(
        "<li>"
        f"<strong>{escape(str(objective.objective_code))}</strong>: "
        f"{escape(str(objective.marks_awarded))}/{escape(str(objective.max_marks))}"
        + (f" ({escape(str(objective.percent))}%)" if objective.percent is not None else "")
        + f" <span class=\"muted\">· {escape(str(objective.coverage))}</span>"
        + "</li>"
        for objective in payload.objectives
    ) or "<li>No objective totals available yet.</li>"

    if payload.uses_front_page_totals:
        question_section = (
            f"<p>{escape(front_page_totals_question_breakdown_text(payload.export_ready))}</p>"
            + (
                "<p><strong>Export readiness:</strong> ready to return/export.</p>"
                if payload.export_ready
                else "<p><strong>Export readiness:</strong> not final until the front-page totals are confirmed.</p>"
            )
        )
    else:
        question_rows_html = "".join(
            "<tr>"
            f"<td>{escape(str(item.label))}</td>"
            f"<td>{escape(str(item.marks_awarded))}/{escape(str(item.max_marks))}</td>"
            f"<td>{escape(str(item.objective_text))}</td>"
            f"<td>{escape(str(item.status))}</td>"
            f"<td>{escape(str(item.teacher_note or '—'))}</td>"
            "</tr>"
            for item in payload.question_rows
        ) or "<tr><td colspan='5'>No question-level marks saved yet.</td></tr>"
        question_section = (
            "<table><thead><tr><th>Question</th><th>Marks</th><th>Objectives</th><th>Status</th><th>Teacher note</th></tr></thead>"
            f"<tbody>{question_rows_html}</tbody></table>"
        )

    return f"""<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <title>{escape(payload.student_name)} — {escape(payload.exam_name)}</title>
    <style>
      :root {{ color-scheme: light; }}
      body {{ font-family: Inter, Arial, sans-serif; margin: 2rem; color: #172033; line-height: 1.5; }}
      h1, h2 {{ margin-bottom: 0.5rem; }}
      .meta {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.75rem 1.25rem; margin: 1.5rem 0; }}
      .meta-item {{ border: 1px solid #d7deea; border-radius: 0.75rem; padding: 0.75rem 0.9rem; background: #f8faff; }}
      .label {{ display: block; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; color: #5f6b84; margin-bottom: 0.25rem; }}
      .value {{ font-size: 1rem; font-weight: 600; }}
      .muted {{ color: #5f6b84; }}
      ul {{ padding-left: 1.25rem; }}
      table {{ width: 100%; border-collapse: collapse; margin-top: 0.75rem; }}
      th, td {{ border: 1px solid #d7deea; padding: 0.6rem; text-align: left; vertical-align: top; }}
      th {{ background: #eef3fb; }}
      .footer-note {{ margin-top: 1.5rem; font-size: 0.95rem; color: #4b5770; }}
      @media print {{ body {{ margin: 1rem; }} .meta-item {{ break-inside: avoid; }} table, tr, td, th {{ break-inside: avoid; }} }}
    </style>
  </head>
  <body>
    <header>
      <p class=\"muted\">SuperMarks student summary</p>
      <h1>{escape(payload.student_name)}</h1>
      <p><strong>Exam:</strong> {escape(payload.exam_name)}</p>
      <p><strong>Objective summary:</strong> {escape(payload.objective_summary)}</p>
    </header>

    <section class=\"meta\">
      <div class=\"meta-item\"><span class=\"label\">Capture mode</span><span class=\"value\">{escape(payload.capture_mode)}</span></div>
      <div class=\"meta-item\"><span class=\"label\">Workflow status</span><span class=\"value\">{escape(payload.workflow_status_label)}</span></div>
      <div class=\"meta-item\"><span class=\"label\">Export-ready</span><span class=\"value\">{'yes' if payload.export_ready else 'no'}</span></div>
      <div class=\"meta-item\"><span class=\"label\">Total</span><span class=\"value\">{escape(str(payload.total_awarded))}/{escape(str(payload.total_possible))}{escape(total_suffix)}</span></div>
      <div class=\"meta-item\"><span class=\"label\">Teacher-marked questions</span><span class=\"value\">{escape(payload.teacher_marked_summary)}</span></div>
      <div class=\"meta-item\"><span class=\"label\">Reporting attention</span><span class=\"value\">{escape(payload.reporting_attention)}</span></div>
      <div class=\"meta-item\"><span class=\"label\">Next return point</span><span class=\"value\">{escape(payload.next_return_point)}</span></div>
      <div class=\"meta-item\"><span class=\"label\">Next action</span><span class=\"value\">{escape(payload.next_action)}</span></div>
    </section>

    <section>
      <h2>Objective breakdown</h2>
      <ul>{objective_items}</ul>
    </section>

    <section>
      <h2>Question breakdown</h2>
      {question_section}
    </section>

    <p class=\"footer-note\">{escape(student_summary_footer_note(payload.export_ready))}</p>
  </body>
</html>
"""


def collect_submission_evidence_rows(
    submission: Submission,
    questions: list[Question],
    submission_data: ExamSubmissionReportingData,
    package_dirname: str,
) -> list[StudentSummaryEvidenceRow]:
    crops = submission_data.crops
    transcriptions = submission_data.transcriptions
    grades = submission_data.grades

    crop_map = {crop.question_id: crop for crop in crops if Path(crop.image_path).exists()}
    transcription_map = {item.question_id: item for item in transcriptions}
    grade_map = {grade.question_id: grade for grade in grades}

    evidence_rows: list[StudentSummaryEvidenceRow] = []
    for question in questions:
        crop = crop_map.get(question.id)
        transcription = transcription_map.get(question.id)
        grade = grade_map.get(question.id)
        safe_label = re.sub(r"[^A-Za-z0-9]+", "-", question.label.strip()).strip("-") or f"question-{question.id}"
        crop_relpath = ""
        transcription_text_relpath = ""
        transcription_json_relpath = ""
        if crop:
            crop_relpath = student_package_relpath(package_dirname, f"evidence/{safe_label}-crop{Path(crop.image_path).suffix or '.png'}")
        if transcription:
            transcription_text_relpath = student_package_relpath(package_dirname, f"evidence/{safe_label}-transcription.txt")
            transcription_json_relpath = student_package_relpath(package_dirname, f"evidence/{safe_label}-transcription.json")
        teacher_note = ""
        if grade and grade.feedback_json:
            feedback = json.loads(grade.feedback_json)
            if isinstance(feedback, dict):
                teacher_note = str(feedback.get("teacher_note") or "").strip()
        evidence_rows.append(StudentSummaryEvidenceRow(
            question_id=question.id,
            question_label=question.label,
            crop_path=crop.image_path if crop else "",
            crop_relpath=crop_relpath,
            transcription_text=transcription.text if transcription else "",
            transcription_raw_json=transcription.raw_json if transcription else "",
            transcription_provider=transcription.provider if transcription else "",
            transcription_confidence=round(float(transcription.confidence), 4) if transcription else "",
            transcription_text_relpath=transcription_text_relpath,
            transcription_json_relpath=transcription_json_relpath,
            grade_status="Teacher-marked" if grade is not None else "Not yet marked",
            teacher_note=teacher_note,
        ))
    return evidence_rows


def count_submission_evidence_files(rows: list[StudentSummaryEvidenceRow]) -> int:
    file_count = 0
    for row in rows:
        if row.crop_relpath:
            file_count += 1
        if row.transcription_text_relpath:
            file_count += 1
        if row.transcription_json_relpath:
            file_count += 1
    return file_count


def build_submission_evidence_artifact_content(
    submission: Submission,
    questions: list[Question],
    submission_data: ExamSubmissionReportingData,
    package_dirname: str,
) -> StudentSummaryEvidenceArtifactContent:
    rows = collect_submission_evidence_rows(submission, questions, submission_data, package_dirname)
    return StudentSummaryEvidenceArtifactContent(
        readme_text=build_submission_evidence_readme_text(submission),
        readme_relpath=student_package_relpath(package_dirname, "evidence/README.txt"),
        manifest_relpath=student_package_relpath(package_dirname, "evidence/manifest.csv"),
        rows=rows,
    )


def build_submission_evidence_package_artifact_specs(
    questions: list[Question],
    artifact_content: StudentSummaryEvidenceArtifactContent,
) -> list[ZipArtifactSpec]:
    artifact_specs: list[ZipArtifactSpec] = [
        TextZipArtifactSpec(relpath=artifact_content.readme_relpath, text=artifact_content.readme_text),
    ]
    for row in artifact_content.rows:
        if row.crop_relpath and row.crop_path:
            artifact_specs.append(FileZipArtifactSpec(relpath=row.crop_relpath, source_path=row.crop_path))
        if row.transcription_text_relpath:
            artifact_specs.append(TextZipArtifactSpec(relpath=row.transcription_text_relpath, text=row.transcription_text))
        if row.transcription_json_relpath:
            raw_json = row.transcription_raw_json
            artifact_specs.append(TextZipArtifactSpec(
                relpath=row.transcription_json_relpath,
                text=raw_json if raw_json.strip() else "{}",
            ))

    artifact_specs.append(CsvZipArtifactSpec(
        relpath=artifact_content.manifest_relpath,
        export_spec=build_student_summary_evidence_export_spec(questions, artifact_content.rows),
    ))
    return artifact_specs


def build_submission_evidence_package_plan(
    submission: Submission,
    questions: list[Question],
    submission_data: ExamSubmissionReportingData,
    package_dirname: str,
) -> StudentSummaryEvidencePackagePlan | None:
    if submission.capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS:
        return None

    artifact_content = build_submission_evidence_artifact_content(
        submission,
        questions,
        submission_data,
        package_dirname,
    )
    return StudentSummaryEvidencePackagePlan(
        readme_relpath=artifact_content.readme_relpath,
        manifest_relpath=artifact_content.manifest_relpath,
        file_count=2 + count_submission_evidence_files(artifact_content.rows),
        artifact_specs=build_submission_evidence_package_artifact_specs(
            questions,
            artifact_content,
        ),
    )


def build_student_summary_artifact_specs(
    exam: Exam,
    submission: Submission,
    row: SubmissionDashboardRow,
    question_rows: list[StudentSummaryQuestionRow],
    *,
    summary_text_relpath: str,
    summary_html_relpath: str,
) -> list[TextZipArtifactSpec]:
    return [
        TextZipArtifactSpec(
            relpath=summary_text_relpath,
            text=build_student_summary_text(exam, submission, row, question_rows),
        ),
        TextZipArtifactSpec(
            relpath=summary_html_relpath,
            text=build_student_summary_html(exam, submission, row, question_rows),
        ),
    ]


def build_exam_student_summary_package_artifact_specs(
    summary_artifact_specs: list[TextZipArtifactSpec],
    evidence_package: StudentSummaryEvidencePackagePlan | None,
) -> list[ZipArtifactSpec]:
    artifact_specs: list[ZipArtifactSpec] = list(summary_artifact_specs)
    if evidence_package is not None:
        artifact_specs.extend(evidence_package.artifact_specs)
    return artifact_specs


def build_student_summary_package_artifacts(submission: Submission, index: int) -> StudentSummaryPackageArtifacts:
    stem = safe_export_stem(submission.student_name, f"student-{submission.id}")
    package_dirname = student_package_dirname(index, stem)
    return StudentSummaryPackageArtifacts(
        package_dirname=package_dirname,
        summary_text_relpath=student_package_relpath(package_dirname, "summary.txt"),
        summary_html_relpath=student_package_relpath(package_dirname, "summary.html"),
    )


def build_student_summaries_zip_artifacts() -> StudentSummariesZipArtifacts:
    root_dirname = "student-summaries"
    return StudentSummariesZipArtifacts(
        root_dirname=root_dirname,
        readme_relpath=student_package_relpath(root_dirname, "README.txt"),
        manifest_relpath=student_package_relpath(root_dirname, "manifest.csv"),
    )


def build_student_summary_manifest_row(
    export_row: StudentReportingExportRow,
    package_artifacts: StudentSummaryPackageArtifacts,
    evidence_package: StudentSummaryEvidencePackagePlan | None,
) -> StudentSummaryManifestRow:
    return StudentSummaryManifestRow(
        student=export_row.student,
        capture_mode=export_row.capture_mode,
        workflow_status=export_row.workflow_status,
        export_ready=export_row.export_ready,
        flagged_questions=export_row.flagged_questions,
        teacher_marked_questions=export_row.teacher_marked_questions,
        questions_total=export_row.questions_total,
        marking_progress=export_row.marking_progress,
        total_awarded=export_row.total_awarded,
        total_possible=export_row.total_possible,
        total_percent=export_row.total_percent,
        objective_summary=export_row.objective_summary,
        reporting_attention=export_row.reporting_attention,
        next_return_point=export_row.next_return_point,
        next_action=export_row.next_action,
        summary_text_file=package_artifacts.summary_text_relpath,
        summary_html_file=package_artifacts.summary_html_relpath,
        evidence_manifest_file="" if evidence_package is None else evidence_package.manifest_relpath,
        evidence_file_count=0 if evidence_package is None else evidence_package.file_count,
    )


def build_exam_student_summary_package_plan(
    context: ExamReportingContext,
    projection: SubmissionReportingProjection,
    index: int,
) -> tuple[StudentSummaryPackagePlan, StudentSummaryManifestRow]:
    submission = projection.submission
    row = projection.dashboard_row
    export_row = projection.export_row
    question_rows = projection.question_rows
    package_artifacts = build_student_summary_package_artifacts(submission, index)
    submission_data = context.snapshot.submission_data_by_submission_id.get(
        submission.id,
        ExamSubmissionReportingData(pages=[], crops=[], transcriptions=[], grades=[]),
    )
    evidence_package = build_submission_evidence_package_plan(
        submission,
        context.questions,
        submission_data,
        package_artifacts.package_dirname,
    )
    summary_artifact_specs = build_student_summary_artifact_specs(
        context.exam,
        submission,
        row,
        question_rows,
        summary_text_relpath=package_artifacts.summary_text_relpath,
        summary_html_relpath=package_artifacts.summary_html_relpath,
    )
    package_plan = StudentSummaryPackagePlan(
        submission=submission,
        dashboard_row=row,
        export_row=export_row,
        package_artifacts=package_artifacts,
        evidence_package=evidence_package,
        artifact_specs=build_exam_student_summary_package_artifact_specs(
            summary_artifact_specs,
            evidence_package,
        ),
    )
    manifest_row = build_student_summary_manifest_row(
        export_row,
        package_artifacts,
        evidence_package,
    )
    return package_plan, manifest_row


def build_exam_student_summaries_zip_artifact_specs(
    export_plan: ExamStudentSummariesZipPlan,
) -> list[ZipArtifactSpec]:
    artifact_specs: list[ZipArtifactSpec] = [
        TextZipArtifactSpec(
            relpath=export_plan.archive_artifacts.readme_relpath,
            text=export_plan.readme_text,
        ),
    ]
    for package in export_plan.submission_packages:
        artifact_specs.extend(package.artifact_specs)
    artifact_specs.append(
        CsvZipArtifactSpec(
            relpath=export_plan.archive_artifacts.manifest_relpath,
            export_spec=export_plan.manifest_export_spec,
        )
    )
    return artifact_specs


def build_exam_student_summaries_zip_plan(context: ExamReportingContext) -> ExamStudentSummariesZipPlan:
    archive_artifacts = build_student_summaries_zip_artifacts()
    manifest_rows: list[StudentSummaryManifestRow] = []
    submission_packages: list[StudentSummaryPackagePlan] = []

    for index, projection in enumerate(context.submission_projections, start=1):
        package_plan, manifest_row = build_exam_student_summary_package_plan(
            context,
            projection,
            index,
        )
        submission_packages.append(package_plan)
        manifest_rows.append(manifest_row)

    readme_text = build_student_package_readme_text(context.exam, context.submissions)
    manifest_export_spec = build_student_summary_manifest_export_spec(context.questions, manifest_rows)

    return ExamStudentSummariesZipPlan(
        archive_artifacts=archive_artifacts,
        readme_text=readme_text,
        manifest_rows=manifest_rows,
        manifest_export_spec=manifest_export_spec,
        submission_packages=submission_packages,
    )


def write_zip_artifact_spec(archive: zipfile.ZipFile, artifact_spec: ZipArtifactSpec) -> None:
    if isinstance(artifact_spec, TextZipArtifactSpec):
        archive.writestr(artifact_spec.relpath, artifact_spec.text)
        return
    if isinstance(artifact_spec, FileZipArtifactSpec):
        archive.write(artifact_spec.source_path, artifact_spec.relpath)
        return
    manifest_buffer = StringIO()
    write_csv_export(manifest_buffer, artifact_spec.export_spec)
    archive.writestr(artifact_spec.relpath, manifest_buffer.getvalue())


def build_zip_export_content(artifact_specs: list[ZipArtifactSpec]) -> bytes:
    archive_buffer = BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for artifact_spec in artifact_specs:
            write_zip_artifact_spec(archive, artifact_spec)
    return archive_buffer.getvalue()


def build_exam_student_summaries_zip_export_artifact(exam_id: int, session: Session) -> ZipExportArtifact | None:
    context = load_exam_reporting_context(exam_id, session)
    if context is None:
        return None

    export_plan = build_exam_student_summaries_zip_plan(context)
    return ZipExportArtifact(
        filename=f"exam-{exam_id}-student-summaries.zip",
        artifact_specs=build_exam_student_summaries_zip_artifact_specs(export_plan),
    )


def build_exam_student_summaries_zip(exam_id: int, session: Session) -> ExamStudentSummaryZipArtifact | None:
    artifact = build_exam_student_summaries_zip_export_artifact(exam_id, session)
    if artifact is None:
        return None

    return ExamStudentSummaryZipArtifact(
        filename=artifact.filename,
        content=build_zip_export_content(artifact.artifact_specs),
    )
