"""D1 bridge-backed reporting repository functions."""

from __future__ import annotations

from app.models import AnswerCrop, GradeResult, Question, QuestionRegion, Submission, SubmissionPage, Transcription
from app.persistence import DbSession
from app.repositories.d1_bridge_submissions import _bridge, _hydrate_many
from app.repositories.reporting import ExamReportingCollections, SubmissionReportingCollections


def load_submission_reporting_collections(session: DbSession, submission_id: int, exam_id: int) -> SubmissionReportingCollections:
    del exam_id
    _ = session
    bridge = _bridge()
    return SubmissionReportingCollections(
        pages=_hydrate_many(
            SubmissionPage,
            bridge.query_all(
                """
                SELECT id, submission_id, page_number, image_path, width, height, created_at
                FROM submissionpage
                WHERE submission_id = ?
                ORDER BY page_number
                """,
                [submission_id],
            ),
        ),
        crops=_hydrate_many(
            AnswerCrop,
            bridge.query_all(
                """
                SELECT id, submission_id, question_id, image_path, created_at
                FROM answercrop
                WHERE submission_id = ?
                ORDER BY id
                """,
                [submission_id],
            ),
        ),
        transcriptions=_hydrate_many(
            Transcription,
            bridge.query_all(
                """
                SELECT id, submission_id, question_id, provider, text, confidence, raw_json, created_at
                FROM transcription
                WHERE submission_id = ?
                ORDER BY id
                """,
                [submission_id],
            ),
        ),
        grades=_hydrate_many(
            GradeResult,
            bridge.query_all(
                """
                SELECT id, submission_id, question_id, marks_awarded, breakdown_json, feedback_json, model_name, created_at
                FROM graderesult
                WHERE submission_id = ?
                ORDER BY id
                """,
                [submission_id],
            ),
        ),
    )


def load_exam_reporting_collections(session: DbSession, exam_id: int) -> ExamReportingCollections:
    _ = session
    bridge = _bridge()
    questions = _hydrate_many(
        Question,
        bridge.query_all(
            """
            SELECT id, exam_id, label, max_marks, rubric_json, created_at
            FROM question
            WHERE exam_id = ?
            ORDER BY id
            """,
            [exam_id],
        ),
    )
    submissions = _hydrate_many(
        Submission,
        bridge.query_all(
            """
            SELECT id, exam_id, student_name, first_name, last_name, status, capture_mode,
                   front_page_totals_json, front_page_candidates_json, front_page_usage_json,
                   front_page_reviewed_at, created_at
            FROM submission
            WHERE exam_id = ?
            ORDER BY id
            """,
            [exam_id],
        ),
    )
    submission_ids = [submission.id for submission in submissions if submission.id is not None]
    question_ids = [question.id for question in questions if question.id is not None]

    if submission_ids:
        submission_placeholders = ", ".join("?" for _ in submission_ids)
        pages = _hydrate_many(
            SubmissionPage,
            bridge.query_all(
                f"""
                SELECT id, submission_id, page_number, image_path, width, height, created_at
                FROM submissionpage
                WHERE submission_id IN ({submission_placeholders})
                ORDER BY submission_id ASC, page_number ASC
                """,
                submission_ids,
            ),
        )
        crops = _hydrate_many(
            AnswerCrop,
            bridge.query_all(
                f"""
                SELECT id, submission_id, question_id, image_path, created_at
                FROM answercrop
                WHERE submission_id IN ({submission_placeholders})
                ORDER BY submission_id ASC, id ASC
                """,
                submission_ids,
            ),
        )
        transcriptions = _hydrate_many(
            Transcription,
            bridge.query_all(
                f"""
                SELECT id, submission_id, question_id, provider, text, confidence, raw_json, created_at
                FROM transcription
                WHERE submission_id IN ({submission_placeholders})
                ORDER BY submission_id ASC, id ASC
                """,
                submission_ids,
            ),
        )
        grades = _hydrate_many(
            GradeResult,
            bridge.query_all(
                f"""
                SELECT id, submission_id, question_id, marks_awarded, breakdown_json, feedback_json, model_name, created_at
                FROM graderesult
                WHERE submission_id IN ({submission_placeholders})
                ORDER BY submission_id ASC, id ASC
                """,
                submission_ids,
            ),
        )
    else:
        pages = []
        crops = []
        transcriptions = []
        grades = []

    if question_ids:
        question_placeholders = ", ".join("?" for _ in question_ids)
        question_regions = _hydrate_many(
            QuestionRegion,
            bridge.query_all(
                f"""
                SELECT id, question_id, page_number, x, y, w, h, created_at
                FROM questionregion
                WHERE question_id IN ({question_placeholders})
                ORDER BY question_id ASC, id ASC
                """,
                question_ids,
            ),
        )
    else:
        question_regions = []

    return ExamReportingCollections(
        questions=questions,
        submissions=submissions,
        question_regions=question_regions,
        pages=pages,
        crops=crops,
        transcriptions=transcriptions,
        grades=grades,
    )
