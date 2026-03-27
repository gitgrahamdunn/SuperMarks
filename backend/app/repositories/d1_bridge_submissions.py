"""D1 bridge-backed submission repository functions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Any

from app.d1_bridge import D1Statement, get_d1_bridge_client
from app.models import (
    AnswerCrop,
    GradeResult,
    Question,
    QuestionRegion,
    Submission,
    SubmissionFile,
    SubmissionPage,
    Transcription,
    utcnow,
)
from app.persistence import DbSession


def _bridge():
    return get_d1_bridge_client()


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _hydrate(model_cls, row: dict[str, Any] | None):
    if not isinstance(row, dict):
        return None
    return model_cls.model_validate(row)


def _hydrate_many(model_cls, rows: list[dict[str, Any]]) -> list[Any]:
    result: list[Any] = []
    for row in rows:
        hydrated = _hydrate(model_cls, row)
        if hydrated is not None:
            result.append(hydrated)
    return result


def _update_row(table: str, row_id: int, fields: dict[str, Any], returning: str) -> dict[str, Any] | None:
    assignments = ", ".join(f"{column} = ?" for column in fields)
    values = [_normalize_value(value) for value in fields.values()]
    values.append(row_id)
    return _bridge().query_first(
        f"""
        UPDATE {table}
        SET {assignments}
        WHERE id = ?
        RETURNING {returning}
        """,
        values,
    )


def get_submission(session: DbSession, submission_id: int) -> Submission | None:
    _ = session
    return _hydrate(
        Submission,
        _bridge().query_first(
            """
            SELECT id, exam_id, student_name, first_name, last_name, status, capture_mode,
                   front_page_totals_json, front_page_candidates_json, front_page_usage_json,
                   front_page_reviewed_at, created_at
            FROM submission
            WHERE id = ?
            """,
            [submission_id],
        ),
    )


def create_submission(
    session: DbSession,
    *,
    exam_id: int,
    student_name: str,
    first_name: str,
    last_name: str,
    status,
    capture_mode,
) -> Submission:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO submission
            (exam_id, student_name, first_name, last_name, status, capture_mode,
             front_page_totals_json, front_page_candidates_json, front_page_usage_json,
             front_page_reviewed_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)
        RETURNING id, exam_id, student_name, first_name, last_name, status, capture_mode,
                  front_page_totals_json, front_page_candidates_json, front_page_usage_json,
                  front_page_reviewed_at, created_at
        """,
        [
            exam_id,
            student_name,
            first_name,
            last_name,
            _normalize_value(status),
            _normalize_value(capture_mode),
            _normalize_value(utcnow()),
        ],
    )
    created = _hydrate(Submission, row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created submission row")
    return created


def update_submission(session: DbSession, submission: Submission, **fields) -> Submission:
    _ = session
    if not fields:
        return submission
    row = _update_row(
        "submission",
        int(submission.id or 0),
        fields,
        "id, exam_id, student_name, first_name, last_name, status, capture_mode, "
        "front_page_totals_json, front_page_candidates_json, front_page_usage_json, "
        "front_page_reviewed_at, created_at",
    )
    updated = _hydrate(Submission, row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated submission row")
    return updated


def update_submission_front_page_data(
    session: DbSession,
    submission: Submission,
    *,
    front_page_candidates_json: str | None = None,
    front_page_usage_json: str | None = None,
) -> Submission:
    _ = session
    row = _bridge().query_first(
        """
        UPDATE submission
        SET front_page_candidates_json = ?, front_page_usage_json = ?
        WHERE id = ?
        RETURNING id, exam_id, student_name, first_name, last_name, status, capture_mode,
                  front_page_totals_json, front_page_candidates_json, front_page_usage_json,
                  front_page_reviewed_at, created_at
        """,
        [front_page_candidates_json, front_page_usage_json, int(submission.id or 0)],
    )
    updated = _hydrate(Submission, row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated submission row")
    return updated


def list_submission_files(session: DbSession, submission_id: int) -> list[SubmissionFile]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, submission_id, file_kind, original_filename, stored_path, blob_url,
               blob_pathname, content_type, size_bytes, created_at
        FROM submissionfile
        WHERE submission_id = ?
        ORDER BY id
        """,
        [submission_id],
    )
    return _hydrate_many(SubmissionFile, rows)


def list_submission_files_for_submission_ids(session: DbSession, submission_ids) -> list[SubmissionFile]:
    _ = session
    ids = list(submission_ids)
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = _bridge().query_all(
        f"""
        SELECT id, submission_id, file_kind, original_filename, stored_path, blob_url,
               blob_pathname, content_type, size_bytes, created_at
        FROM submissionfile
        WHERE submission_id IN ({placeholders})
        ORDER BY submission_id ASC, id ASC
        """,
        ids,
    )
    return _hydrate_many(SubmissionFile, rows)


def create_submission_file(
    session: DbSession,
    *,
    submission_id: int,
    file_kind: str,
    original_filename: str,
    stored_path: str,
    content_type: str,
    size_bytes: int,
    blob_url: str | None = None,
    blob_pathname: str | None = None,
) -> SubmissionFile:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO submissionfile
            (submission_id, file_kind, original_filename, stored_path, blob_url, blob_pathname, content_type, size_bytes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id, submission_id, file_kind, original_filename, stored_path, blob_url,
                  blob_pathname, content_type, size_bytes, created_at
        """,
        [
            submission_id,
            file_kind,
            original_filename,
            stored_path,
            blob_url,
            blob_pathname,
            content_type,
            size_bytes,
            _normalize_value(utcnow()),
        ],
    )
    created = _hydrate(SubmissionFile, row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created submission file row")
    return created


def register_submission_files(
    session: DbSession,
    *,
    submission_id: int,
    files: Sequence[dict[str, str | int | None]],
) -> int:
    statements: list[D1Statement] = []
    for file in files:
        statements.append(
            D1Statement(
                """
                INSERT INTO submissionfile
                    (submission_id, file_kind, original_filename, stored_path, blob_url, blob_pathname, content_type, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    submission_id,
                    str(file["file_kind"]),
                    str(file["original_filename"]),
                    str(file["stored_path"]),
                    str(file["blob_url"]) if file.get("blob_url") is not None else None,
                    str(file["blob_pathname"]) if file.get("blob_pathname") is not None else None,
                    str(file["content_type"]),
                    int(file["size_bytes"]),
                    _normalize_value(utcnow()),
                ],
            )
        )
    if not statements:
        return 0
    _bridge().batch(statements)
    return len(statements)


def list_submission_pages(session: DbSession, submission_id: int) -> list[SubmissionPage]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, submission_id, page_number, image_path, width, height, created_at
        FROM submissionpage
        WHERE submission_id = ?
        ORDER BY page_number
        """,
        [submission_id],
    )
    return _hydrate_many(SubmissionPage, rows)


def list_submission_pages_for_submission_ids(session: DbSession, submission_ids) -> list[SubmissionPage]:
    _ = session
    ids = list(submission_ids)
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = _bridge().query_all(
        f"""
        SELECT id, submission_id, page_number, image_path, width, height, created_at
        FROM submissionpage
        WHERE submission_id IN ({placeholders})
        ORDER BY submission_id ASC, page_number ASC
        """,
        ids,
    )
    return _hydrate_many(SubmissionPage, rows)


def get_submission_page(session: DbSession, submission_id: int, page_number: int) -> SubmissionPage | None:
    _ = session
    return _hydrate(
        SubmissionPage,
        _bridge().query_first(
            """
            SELECT id, submission_id, page_number, image_path, width, height, created_at
            FROM submissionpage
            WHERE submission_id = ? AND page_number = ?
            """,
            [submission_id, page_number],
        ),
    )


def clear_submission_pages(session: DbSession, submission_id: int) -> None:
    _ = session
    _bridge().run("DELETE FROM submissionpage WHERE submission_id = ?", [submission_id])


def create_submission_page(
    session: DbSession,
    *,
    submission_id: int,
    page_number: int,
    image_path: str,
    width: int,
    height: int,
) -> SubmissionPage:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO submissionpage (submission_id, page_number, image_path, width, height, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id, submission_id, page_number, image_path, width, height, created_at
        """,
        [submission_id, page_number, image_path, width, height, _normalize_value(utcnow())],
    )
    created = _hydrate(SubmissionPage, row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created submission page row")
    return created


def list_submission_crops(session: DbSession, submission_id: int) -> list[AnswerCrop]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, submission_id, question_id, image_path, created_at
        FROM answercrop
        WHERE submission_id = ?
        ORDER BY id
        """,
        [submission_id],
    )
    return _hydrate_many(AnswerCrop, rows)


def get_submission_crop(session: DbSession, submission_id: int, question_id: int) -> AnswerCrop | None:
    _ = session
    return _hydrate(
        AnswerCrop,
        _bridge().query_first(
            """
            SELECT id, submission_id, question_id, image_path, created_at
            FROM answercrop
            WHERE submission_id = ? AND question_id = ?
            """,
            [submission_id, question_id],
        ),
    )


def clear_submission_crops(session: DbSession, submission_id: int) -> None:
    _ = session
    _bridge().run("DELETE FROM answercrop WHERE submission_id = ?", [submission_id])


def create_submission_crop(
    session: DbSession,
    *,
    submission_id: int,
    question_id: int,
    image_path: str,
) -> AnswerCrop:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO answercrop (submission_id, question_id, image_path, created_at)
        VALUES (?, ?, ?, ?)
        RETURNING id, submission_id, question_id, image_path, created_at
        """,
        [submission_id, question_id, image_path, _normalize_value(utcnow())],
    )
    created = _hydrate(AnswerCrop, row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created crop row")
    return created


def list_submission_transcriptions(session: DbSession, submission_id: int) -> list[Transcription]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, submission_id, question_id, provider, text, confidence, raw_json, created_at
        FROM transcription
        WHERE submission_id = ?
        ORDER BY id
        """,
        [submission_id],
    )
    return _hydrate_many(Transcription, rows)


def clear_submission_transcriptions(session: DbSession, submission_id: int) -> None:
    _ = session
    _bridge().run("DELETE FROM transcription WHERE submission_id = ?", [submission_id])


def create_submission_transcription(
    session: DbSession,
    *,
    submission_id: int,
    question_id: int,
    provider: str,
    text: str,
    confidence: float,
    raw_json: str,
) -> Transcription:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO transcription (submission_id, question_id, provider, text, confidence, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        RETURNING id, submission_id, question_id, provider, text, confidence, raw_json, created_at
        """,
        [submission_id, question_id, provider, text, confidence, raw_json, _normalize_value(utcnow())],
    )
    created = _hydrate(Transcription, row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created transcription row")
    return created


def list_submission_grades(session: DbSession, submission_id: int) -> list[GradeResult]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, submission_id, question_id, marks_awarded, breakdown_json, feedback_json, model_name, created_at
        FROM graderesult
        WHERE submission_id = ?
        ORDER BY id
        """,
        [submission_id],
    )
    return _hydrate_many(GradeResult, rows)


def get_submission_grade(session: DbSession, submission_id: int, question_id: int) -> GradeResult | None:
    _ = session
    return _hydrate(
        GradeResult,
        _bridge().query_first(
            """
            SELECT id, submission_id, question_id, marks_awarded, breakdown_json, feedback_json, model_name, created_at
            FROM graderesult
            WHERE submission_id = ? AND question_id = ?
            """,
            [submission_id, question_id],
        ),
    )


def clear_submission_grades(session: DbSession, submission_id: int) -> None:
    _ = session
    _bridge().run("DELETE FROM graderesult WHERE submission_id = ?", [submission_id])


def upsert_submission_grade(
    session: DbSession,
    *,
    submission_id: int,
    question_id: int,
    marks_awarded: float,
    breakdown_json: str,
    feedback_json: str,
    model_name: str,
) -> GradeResult:
    _ = session
    existing = get_submission_grade(session, submission_id, question_id)
    if existing:
        row = _bridge().query_first(
            """
            UPDATE graderesult
            SET marks_awarded = ?, breakdown_json = ?, feedback_json = ?, model_name = ?
            WHERE id = ?
            RETURNING id, submission_id, question_id, marks_awarded, breakdown_json, feedback_json, model_name, created_at
            """,
            [marks_awarded, breakdown_json, feedback_json, model_name, int(existing.id or 0)],
        )
    else:
        row = _bridge().query_first(
            """
            INSERT INTO graderesult (submission_id, question_id, marks_awarded, breakdown_json, feedback_json, model_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING id, submission_id, question_id, marks_awarded, breakdown_json, feedback_json, model_name, created_at
            """,
            [submission_id, question_id, marks_awarded, breakdown_json, feedback_json, model_name, _normalize_value(utcnow())],
        )
    grade = _hydrate(GradeResult, row)
    if grade is None:
        raise RuntimeError("D1 bridge did not return the upserted grade row")
    return grade


def list_exam_questions(session: DbSession, exam_id: int) -> list[Question]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, exam_id, label, max_marks, rubric_json, created_at
        FROM question
        WHERE exam_id = ?
        ORDER BY id
        """,
        [exam_id],
    )
    return _hydrate_many(Question, rows)


def list_exam_front_page_total_submissions(session: DbSession, exam_id: int) -> list[Submission]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, exam_id, student_name, first_name, last_name, status, capture_mode,
               front_page_totals_json, front_page_candidates_json, front_page_usage_json,
               front_page_reviewed_at, created_at
        FROM submission
        WHERE exam_id = ? AND capture_mode = 'front_page_totals'
        ORDER BY id
        """,
        [exam_id],
    )
    return _hydrate_many(Submission, rows)


def list_submissions_by_ids(session: DbSession, submission_ids) -> list[Submission]:
    _ = session
    ids = list(submission_ids)
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = _bridge().query_all(
        f"""
        SELECT id, exam_id, student_name, first_name, last_name, status, capture_mode,
               front_page_totals_json, front_page_candidates_json, front_page_usage_json,
               front_page_reviewed_at, created_at
        FROM submission
        WHERE id IN ({placeholders})
        ORDER BY id ASC
        """,
        ids,
    )
    return _hydrate_many(Submission, rows)


def list_question_regions(session: DbSession, question_id: int) -> list[QuestionRegion]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, question_id, page_number, x, y, w, h, created_at
        FROM questionregion
        WHERE question_id = ?
        ORDER BY id
        """,
        [question_id],
    )
    return _hydrate_many(QuestionRegion, rows)


def update_submission_status(session: DbSession, submission: Submission, status) -> Submission:
    _ = session
    row = _bridge().query_first(
        """
        UPDATE submission
        SET status = ?
        WHERE id = ?
        RETURNING id, exam_id, student_name, first_name, last_name, status, capture_mode,
                  front_page_totals_json, front_page_candidates_json, front_page_usage_json,
                  front_page_reviewed_at, created_at
        """,
        [_normalize_value(status), int(submission.id or 0)],
    )
    updated = _hydrate(Submission, row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated submission status row")
    return updated


def update_submission_capture_mode(session: DbSession, submission: Submission, capture_mode) -> Submission:
    _ = session
    row = _bridge().query_first(
        """
        UPDATE submission
        SET capture_mode = ?
        WHERE id = ?
        RETURNING id, exam_id, student_name, first_name, last_name, status, capture_mode,
                  front_page_totals_json, front_page_candidates_json, front_page_usage_json,
                  front_page_reviewed_at, created_at
        """,
        [_normalize_value(capture_mode), int(submission.id or 0)],
    )
    updated = _hydrate(Submission, row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated submission capture mode row")
    return updated
