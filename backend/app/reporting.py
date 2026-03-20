from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.models import Question, Submission
from app.schemas import ExamObjectiveRead, FrontPageObjectiveScore, FrontPageTotalsRead, ObjectiveAttentionSubmissionRead, ObjectiveCompleteSubmissionRead, ObjectiveTotalRead, SubmissionDashboardRow


def front_page_totals_read(submission: Submission) -> FrontPageTotalsRead | None:
    if not (submission.front_page_totals_json or "").strip():
        return None
    payload = json.loads(submission.front_page_totals_json)
    return FrontPageTotalsRead(
        overall_marks_awarded=float(payload.get("overall_marks_awarded") or 0),
        overall_max_marks=float(payload["overall_max_marks"]) if payload.get("overall_max_marks") is not None else None,
        objective_scores=[
            FrontPageObjectiveScore(
                objective_code=str(item.get("objective_code") or "").strip(),
                marks_awarded=float(item.get("marks_awarded") or 0),
                max_marks=float(item["max_marks"]) if item.get("max_marks") is not None else None,
            )
            for item in payload.get("objective_scores", [])
            if str(item.get("objective_code") or "").strip()
        ],
        teacher_note=str(payload.get("teacher_note") or ""),
        confirmed=bool(payload.get("confirmed", True)),
        reviewed_at=submission.front_page_reviewed_at,
    )


def question_objective_codes(question: Question) -> list[str]:
    try:
        rubric = json.loads(question.rubric_json)
    except Exception:
        return []
    objective_codes = rubric.get("objective_codes") if isinstance(rubric, dict) else []
    if not isinstance(objective_codes, list):
        return []
    return [str(code).strip() for code in objective_codes if str(code).strip()]


def objective_totals_read(objective_totals: dict[str, dict[str, float | int]]) -> list[ObjectiveTotalRead]:
    return [
        ObjectiveTotalRead(
            objective_code=code,
            marks_awarded=round(float(values["marks_awarded"]), 2),
            max_marks=round(float(values["max_marks"]), 2),
            questions_count=int(values.get("questions_count", 0)),
        )
        for code, values in sorted(objective_totals.items())
    ]


def accumulate_objective_totals(objective_totals: dict[str, dict[str, float | int]], question: Question, awarded: float) -> None:
    for code in question_objective_codes(question):
        bucket = objective_totals.setdefault(code, {"marks_awarded": 0.0, "max_marks": 0.0, "questions_count": 0})
        bucket["marks_awarded"] += awarded
        bucket["max_marks"] += float(question.max_marks)
        bucket["questions_count"] += 1


def front_page_objective_totals(front_page_totals: FrontPageTotalsRead | None) -> dict[str, dict[str, float | int]]:
    objective_totals: dict[str, dict[str, float | int]] = {}
    if not front_page_totals:
        return objective_totals
    for score in front_page_totals.objective_scores:
        objective_totals[score.objective_code] = {
            "marks_awarded": float(score.marks_awarded),
            "max_marks": float(score.max_marks) if score.max_marks is not None else 0.0,
            "questions_count": 0,
        }
    return objective_totals


@dataclass
class ObjectiveSummaryProjection:
    objective_code: str
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
    weakest_complete_submission: ObjectiveCompleteSubmissionRead | None
    teacher_summary: str
    attention_submissions: list[ObjectiveAttentionSubmissionRead]

    def as_read_model(self, questions_count: int) -> ExamObjectiveRead:
        return ExamObjectiveRead(
            objective_code=self.objective_code,
            marks_awarded=self.total_awarded_all_current,
            max_marks=self.total_max_all_current,
            questions_count=questions_count,
            submissions_with_objective=self.submissions_with_objective,
            complete_submissions_with_objective=self.complete_submissions_with_objective,
            incomplete_submissions_with_objective=self.incomplete_submissions_with_objective,
            total_awarded_complete=self.total_awarded_complete,
            total_max_complete=self.total_max_complete,
            average_awarded_complete=self.average_awarded_complete,
            average_percent_complete=self.average_percent_complete,
            total_awarded_all_current=self.total_awarded_all_current,
            total_max_all_current=self.total_max_all_current,
            average_percent_all_current=self.average_percent_all_current,
            strongest_complete_student=self.strongest_complete_student,
            strongest_complete_percent=self.strongest_complete_percent,
            weakest_complete_student=self.weakest_complete_student,
            weakest_complete_percent=self.weakest_complete_percent,
            weakest_complete_submission=self.weakest_complete_submission,
            teacher_summary=self.teacher_summary,
            attention_submissions=self.attention_submissions,
        )

    def as_export_row(self) -> list[Any]:
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
class ExamObjectiveReport:
    objectives: list[ExamObjectiveRead]


def objective_summary_text(objective_totals: list[ObjectiveTotalRead]) -> str:
    if not objective_totals:
        return ""
    return " | ".join(
        f"{objective.objective_code} {round(float(objective.marks_awarded), 2):.1f}/{round(float(objective.max_marks), 2):.1f}"
        for objective in objective_totals
    )


def build_objective_summary_projections(rows: list[SubmissionDashboardRow]) -> list[ObjectiveSummaryProjection]:
    objective_buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        for objective in row.objective_totals:
            bucket = objective_buckets.setdefault(
                objective.objective_code,
                {
                    "marks_awarded": 0.0,
                    "max_marks": 0.0,
                    "questions_count": 0,
                    "submissions_with_objective": 0,
                    "complete_submissions_with_objective": 0,
                    "total_awarded_all_current": 0.0,
                    "total_max_all_current": 0.0,
                    "total_awarded_complete": 0.0,
                    "total_max_complete": 0.0,
                    "strongest_complete_student": "",
                    "strongest_complete_percent": None,
                    "weakest_complete_student": "",
                    "weakest_complete_percent": None,
                    "weakest_complete_submission": None,
                    "attention_submissions": [],
                },
            )
            bucket["marks_awarded"] += float(objective.marks_awarded)
            bucket["max_marks"] += float(objective.max_marks)
            bucket["questions_count"] += int(objective.questions_count)
            bucket["submissions_with_objective"] += 1
            awarded = float(objective.marks_awarded)
            max_marks = float(objective.max_marks)
            bucket["total_awarded_all_current"] += awarded
            bucket["total_max_all_current"] += max_marks
            percent = round((awarded / max_marks) * 100, 1) if max_marks > 0 else None

            if row.workflow_status == "complete":
                bucket["complete_submissions_with_objective"] += 1
                bucket["total_awarded_complete"] += awarded
                bucket["total_max_complete"] += max_marks
                if percent is not None and (
                    bucket["strongest_complete_percent"] is None or percent > bucket["strongest_complete_percent"]
                ):
                    bucket["strongest_complete_percent"] = percent
                    bucket["strongest_complete_student"] = row.student_name
                if percent is not None and (
                    bucket["weakest_complete_percent"] is None or percent < bucket["weakest_complete_percent"]
                ):
                    bucket["weakest_complete_percent"] = percent
                    bucket["weakest_complete_student"] = row.student_name
                    bucket["weakest_complete_submission"] = ObjectiveCompleteSubmissionRead(
                        submission_id=row.submission_id,
                        student_name=row.student_name,
                        capture_mode=row.capture_mode,
                        objective_percent=percent,
                    )
            else:
                bucket["attention_submissions"].append(ObjectiveAttentionSubmissionRead(
                    submission_id=row.submission_id,
                    student_name=row.student_name,
                    capture_mode=row.capture_mode,
                    workflow_status=row.workflow_status,
                    objective_percent="" if percent is None else percent,
                    next_return_point=row.next_return_point,
                    next_action=row.next_action,
                ))

    projections: list[ObjectiveSummaryProjection] = []
    for objective_code in sorted(objective_buckets):
        bucket = objective_buckets[objective_code]
        submissions_with_objective = int(bucket["submissions_with_objective"])
        complete_submissions_with_objective = int(bucket["complete_submissions_with_objective"])
        incomplete_submissions_with_objective = submissions_with_objective - complete_submissions_with_objective
        total_awarded_complete = round(float(bucket["total_awarded_complete"]), 2)
        total_max_complete = round(float(bucket["total_max_complete"]), 2)
        total_awarded_all_current = round(float(bucket["total_awarded_all_current"]), 2)
        total_max_all_current = round(float(bucket["total_max_all_current"]), 2)
        average_awarded_complete = (
            round(total_awarded_complete / complete_submissions_with_objective, 2)
            if complete_submissions_with_objective > 0
            else ""
        )
        average_percent_complete = (
            round((total_awarded_complete / total_max_complete) * 100, 1)
            if total_max_complete > 0
            else ""
        )
        average_percent_all_current = (
            round((total_awarded_all_current / total_max_all_current) * 100, 1)
            if total_max_all_current > 0
            else ""
        )
        strongest_complete_student = str(bucket["strongest_complete_student"] or "")
        strongest_complete_percent = bucket["strongest_complete_percent"]
        weakest_complete_student = str(bucket["weakest_complete_student"] or "")
        weakest_complete_percent = bucket["weakest_complete_percent"]
        weakest_complete_submission = bucket["weakest_complete_submission"]
        teacher_summary = (
            f"{complete_submissions_with_objective}/{submissions_with_objective} results export-ready; "
            f"complete average {average_percent_complete if average_percent_complete != '' else '—'}%"
        )
        if strongest_complete_student and weakest_complete_student:
            teacher_summary += (
                f"; strongest {strongest_complete_student} ({strongest_complete_percent}%), "
                f"weakest {weakest_complete_student} ({weakest_complete_percent}%)"
            )
        elif incomplete_submissions_with_objective > 0:
            teacher_summary += f"; {incomplete_submissions_with_objective} result(s) still in progress"

        attention_submissions = sorted(
            bucket["attention_submissions"],
            key=lambda item: (
                0 if item.workflow_status == "blocked" else 1,
                0 if item.workflow_status == "in_progress" else 1,
                item.student_name.lower(),
            ),
        )[:3]

        projections.append(ObjectiveSummaryProjection(
            objective_code=objective_code,
            submissions_with_objective=submissions_with_objective,
            complete_submissions_with_objective=complete_submissions_with_objective,
            incomplete_submissions_with_objective=incomplete_submissions_with_objective,
            total_awarded_complete=total_awarded_complete,
            total_max_complete=total_max_complete,
            average_awarded_complete=average_awarded_complete,
            average_percent_complete=average_percent_complete,
            total_awarded_all_current=total_awarded_all_current,
            total_max_all_current=total_max_all_current,
            average_percent_all_current=average_percent_all_current,
            strongest_complete_student=strongest_complete_student,
            strongest_complete_percent="" if strongest_complete_percent is None else strongest_complete_percent,
            weakest_complete_student=weakest_complete_student,
            weakest_complete_percent="" if weakest_complete_percent is None else weakest_complete_percent,
            weakest_complete_submission=weakest_complete_submission,
            teacher_summary=teacher_summary,
            attention_submissions=attention_submissions,
        ))

    return projections


def build_exam_objective_report(rows: list[SubmissionDashboardRow]) -> ExamObjectiveReport:
    projections = build_objective_summary_projections(rows)
    questions_count_by_code: dict[str, int] = {}
    for row in rows:
        for objective in row.objective_totals:
            questions_count_by_code[objective.objective_code] = (
                questions_count_by_code.get(objective.objective_code, 0) + int(objective.questions_count)
            )
    return ExamObjectiveReport(
        objectives=[
            projection.as_read_model(questions_count=questions_count_by_code.get(projection.objective_code, 0))
            for projection in projections
        ],
    )
