"""D1 bridge-backed exam repository functions."""

from __future__ import annotations

from collections.abc import Sequence
import json
from datetime import datetime
from enum import Enum
from typing import Any

from app.d1_bridge import D1Statement, get_d1_bridge_client
from app.models import (
    BulkUploadPage,
    ClassList,
    Exam,
    ExamBulkUploadFile,
    ExamIntakeJob,
    ExamKeyFile,
    ExamKeyPage,
    ExamKeyParseJob,
    ExamKeyParsePage,
    Question,
    Submission,
    SubmissionCaptureMode,
    utcnow,
)
from app.persistence import DbSession
from app.repositories.d1_bridge_submissions import _hydrate, _hydrate_many


def _bridge():
    return get_d1_bridge_client()


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return json.dumps(value)
    return value


def _normalize_json_field(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _exam_from_row(row: dict[str, Any] | None) -> Exam | None:
    return _hydrate(Exam, row)


def _class_list_from_row(row: dict[str, Any] | None) -> ClassList | None:
    return _hydrate(ClassList, row)


def _exam_key_file_from_row(row: dict[str, Any] | None) -> ExamKeyFile | None:
    return _hydrate(ExamKeyFile, row)


def _exam_key_page_from_row(row: dict[str, Any] | None) -> ExamKeyPage | None:
    return _hydrate(ExamKeyPage, row)


def _exam_intake_job_from_row(row: dict[str, Any] | None) -> ExamIntakeJob | None:
    return _hydrate(ExamIntakeJob, row)


def _exam_bulk_upload_from_row(row: dict[str, Any] | None) -> ExamBulkUploadFile | None:
    return _hydrate(ExamBulkUploadFile, row)


def _bulk_upload_page_from_row(row: dict[str, Any] | None) -> BulkUploadPage | None:
    return _hydrate(BulkUploadPage, row)


def _exam_parse_job_from_row(row: dict[str, Any] | None) -> ExamKeyParseJob | None:
    return _hydrate(ExamKeyParseJob, row)


def _exam_parse_page_from_row(row: dict[str, Any] | None) -> ExamKeyParsePage | None:
    if not isinstance(row, dict):
        return None
    normalized = dict(row)
    normalized["result_json"] = _normalize_json_field(normalized.get("result_json"))
    normalized["error_json"] = _normalize_json_field(normalized.get("error_json"))
    return ExamKeyParsePage.model_validate(normalized)


def _update_row(table: str, row_id: int, fields: dict[str, Any], returning_sql: str) -> dict[str, Any] | None:
    assignments = ", ".join(f"{column} = ?" for column in fields)
    params = [_normalize_value(value) for value in fields.values()]
    params.append(row_id)
    return _bridge().query_first(
        f"UPDATE {table} SET {assignments} WHERE id = ? RETURNING {returning_sql}",
        params,
    )


def get_exam(session: DbSession, exam_id: int) -> Exam | None:
    _ = session
    return _exam_from_row(
        _bridge().query_first(
            """
            SELECT id, owner_user_id, name, created_at, teacher_style_profile_json, front_page_template_json,
                   class_list_json, class_list_source_json, status
            FROM exam
            WHERE id = ?
            """,
            [exam_id],
        )
    )


def create_exam(session: DbSession, *, name: str, owner_user_id: int | None = None) -> Exam:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO exam
            (owner_user_id, name, created_at, teacher_style_profile_json, front_page_template_json, class_list_json, class_list_source_json, status)
        VALUES (?, ?, ?, NULL, NULL, NULL, NULL, ?)
        RETURNING id, owner_user_id, name, created_at, teacher_style_profile_json, front_page_template_json, class_list_json, class_list_source_json, status
        """,
        [owner_user_id, name, _normalize_value(utcnow()), "DRAFT"],
    )
    created = _exam_from_row(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created exam row")
    return created


def update_exam(session: DbSession, exam: Exam, **fields) -> Exam:
    _ = session
    if not fields:
        return exam
    row = _update_row(
        "exam",
        int(exam.id or 0),
        fields,
        "id, owner_user_id, name, created_at, teacher_style_profile_json, front_page_template_json, class_list_json, class_list_source_json, status",
    )
    updated = _exam_from_row(row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated exam row")
    return updated


def list_exams(session: DbSession, owner_user_id: int | None = None) -> list[Exam]:
    _ = session
    params: list[Any] = []
    where_clause = ""
    if owner_user_id is not None:
        where_clause = "WHERE owner_user_id = ?"
        params.append(owner_user_id)
    rows = _bridge().query_all(
        """
        SELECT id, owner_user_id, name, created_at, teacher_style_profile_json, front_page_template_json,
               class_list_json, class_list_source_json, status
        FROM exam
        """
        + where_clause
        + """
        ORDER BY created_at DESC, id DESC
        """,
        params,
    )
    return _hydrate_many(Exam, rows)


def list_class_lists(session: DbSession, owner_user_id: int | None = None) -> list[ClassList]:
    _ = session
    params: list[Any] = []
    where_clause = ""
    if owner_user_id is not None:
        where_clause = "WHERE owner_user_id = ?"
        params.append(owner_user_id)
    rows = _bridge().query_all(
        """
        SELECT id, owner_user_id, name, names_json, source_json, created_at
        FROM classlist
        """
        + where_clause
        + """
        ORDER BY created_at DESC, id DESC
        """,
        params,
    )
    return _hydrate_many(ClassList, rows)


def get_class_list(session: DbSession, class_list_id: int) -> ClassList | None:
    _ = session
    return _class_list_from_row(
        _bridge().query_first(
            """
            SELECT id, owner_user_id, name, names_json, source_json, created_at
            FROM classlist
            WHERE id = ?
            """,
            [class_list_id],
        )
    )


def create_class_list(session: DbSession, *, name: str, owner_user_id: int | None = None) -> ClassList:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO classlist (owner_user_id, name, names_json, source_json, created_at)
        VALUES (?, ?, '[]', NULL, ?)
        RETURNING id, owner_user_id, name, names_json, source_json, created_at
        """,
        [owner_user_id, name, _normalize_value(utcnow())],
    )
    created = _class_list_from_row(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created class list row")
    return created


def update_class_list_payload(session: DbSession, *, class_list: ClassList, names_json: str, source_json: str, name: str | None = None) -> ClassList:
    _ = session
    row = _update_row(
        "classlist",
        int(class_list.id or 0),
        {"name": name if name is not None else class_list.name, "names_json": names_json, "source_json": source_json},
        "id, owner_user_id, name, names_json, source_json, created_at",
    )
    updated = _class_list_from_row(row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated class list row")
    return updated


def delete_class_list(session: DbSession, *, class_list: ClassList) -> None:
    _ = session
    _bridge().run("DELETE FROM classlist WHERE id = ?", [int(class_list.id or 0)])


def update_exam_class_list_payload(session: DbSession, *, exam: Exam, class_list_json: str, class_list_source_json: str) -> Exam:
    return update_exam(
        session,
        exam,
        class_list_json=class_list_json,
        class_list_source_json=class_list_source_json,
    )


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


def list_exam_submissions(session: DbSession, exam_id: int) -> list[Submission]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, exam_id, student_name, first_name, last_name, status, capture_mode,
               front_page_totals_json, front_page_candidates_json, front_page_usage_json,
               front_page_reviewed_at, created_at
        FROM submission
        WHERE exam_id = ?
        ORDER BY id
        """,
        [exam_id],
    )
    return _hydrate_many(Submission, rows)


def list_front_page_unreviewed_submissions(session: DbSession, exam_id: int) -> list[Submission]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, exam_id, student_name, first_name, last_name, status, capture_mode,
               front_page_totals_json, front_page_candidates_json, front_page_usage_json,
               front_page_reviewed_at, created_at
        FROM submission
        WHERE exam_id = ? AND capture_mode = ? AND front_page_reviewed_at IS NULL
        ORDER BY id
        """,
        [exam_id, SubmissionCaptureMode.FRONT_PAGE_TOTALS.value],
    )
    return _hydrate_many(Submission, rows)


def list_exam_key_files(session: DbSession, exam_id: int) -> list[ExamKeyFile]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, exam_id, original_filename, stored_path, blob_url, blob_pathname, content_type, size_bytes, created_at
        FROM examkeyfile
        WHERE exam_id = ?
        ORDER BY id
        """,
        [exam_id],
    )
    return _hydrate_many(ExamKeyFile, rows)


def list_exam_key_pages(session: DbSession, exam_id: int) -> list[ExamKeyPage]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, exam_id, page_number, image_path, blob_pathname, blob_url, width, height, created_at
        FROM examkeypage
        WHERE exam_id = ?
        ORDER BY page_number
        """,
        [exam_id],
    )
    return _hydrate_many(ExamKeyPage, rows)


def get_exam_key_page(session: DbSession, *, exam_id: int, page_number: int) -> ExamKeyPage | None:
    _ = session
    return _exam_key_page_from_row(
        _bridge().query_first(
            """
            SELECT id, exam_id, page_number, image_path, blob_pathname, blob_url, width, height, created_at
            FROM examkeypage
            WHERE exam_id = ? AND page_number = ?
            """,
            [exam_id, page_number],
        )
    )


def clear_exam_key_pages(session: DbSession, exam_id: int) -> None:
    _ = session
    _bridge().run("DELETE FROM examkeypage WHERE exam_id = ?", [exam_id])


def create_exam_key_page(
    session: DbSession,
    *,
    exam_id: int,
    page_number: int,
    image_path: str,
    width: int,
    height: int,
    blob_pathname: str | None = None,
    blob_url: str | None = None,
) -> ExamKeyPage:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO examkeypage
            (exam_id, page_number, image_path, blob_pathname, blob_url, width, height, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id, exam_id, page_number, image_path, blob_pathname, blob_url, width, height, created_at
        """,
        [exam_id, page_number, image_path, blob_pathname, blob_url, width, height, _normalize_value(utcnow())],
    )
    created = _exam_key_page_from_row(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created key page row")
    return created


def update_exam_key_page(session: DbSession, page: ExamKeyPage, **fields) -> ExamKeyPage:
    _ = session
    if not fields:
        return page
    row = _update_row(
        "examkeypage",
        int(page.id or 0),
        fields,
        "id, exam_id, page_number, image_path, blob_pathname, blob_url, width, height, created_at",
    )
    updated = _exam_key_page_from_row(row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated key page row")
    return updated


def list_exam_parse_jobs(session: DbSession, exam_id: int) -> list[ExamKeyParseJob]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, exam_id, status, page_count, pages_done, created_at, updated_at,
               cost_total, input_tokens_total, output_tokens_total
        FROM examkeyparsejob
        WHERE exam_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        [exam_id],
    )
    result: list[ExamKeyParseJob] = []
    for row in rows:
        hydrated = _exam_parse_job_from_row(row)
        if hydrated is not None:
            result.append(hydrated)
    return result


def get_exam_parse_job(session: DbSession, job_id: int) -> ExamKeyParseJob | None:
    _ = session
    return _exam_parse_job_from_row(
        _bridge().query_first(
            """
            SELECT id, exam_id, status, page_count, pages_done, created_at, updated_at,
                   cost_total, input_tokens_total, output_tokens_total
            FROM examkeyparsejob
            WHERE id = ?
            """,
            [job_id],
        )
    )


def update_exam_parse_job(session: DbSession, job: ExamKeyParseJob, **fields) -> ExamKeyParseJob:
    _ = session
    if not fields:
        return job
    row = _update_row(
        "examkeyparsejob",
        int(job.id or 0),
        fields,
        "id, exam_id, status, page_count, pages_done, created_at, updated_at, cost_total, input_tokens_total, output_tokens_total",
    )
    updated = _exam_parse_job_from_row(row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated parse job row")
    return updated


def get_latest_exam_parse_job(session: DbSession, exam_id: int) -> ExamKeyParseJob | None:
    _ = session
    return _exam_parse_job_from_row(
        _bridge().query_first(
            """
            SELECT id, exam_id, status, page_count, pages_done, created_at, updated_at,
                   cost_total, input_tokens_total, output_tokens_total
            FROM examkeyparsejob
            WHERE exam_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            [exam_id],
        )
    )


def create_exam_parse_job(
    session: DbSession,
    *,
    exam_id: int,
    status: str,
    page_count: int,
    pages_done: int,
    created_at,
    updated_at,
) -> ExamKeyParseJob:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO examkeyparsejob
            (exam_id, status, page_count, pages_done, created_at, updated_at, cost_total, input_tokens_total, output_tokens_total)
        VALUES (?, ?, ?, ?, ?, ?, 0.0, 0, 0)
        RETURNING id, exam_id, status, page_count, pages_done, created_at, updated_at, cost_total, input_tokens_total, output_tokens_total
        """,
        [
            exam_id,
            status,
            page_count,
            pages_done,
            _normalize_value(created_at),
            _normalize_value(updated_at),
        ],
    )
    created = _exam_parse_job_from_row(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created parse job row")
    return created


def list_exam_parse_pages(session: DbSession, job_id: int) -> list[ExamKeyParsePage]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, job_id, page_number, status, confidence, model_used, result_json, error_json,
               cost, input_tokens, output_tokens, created_at, updated_at
        FROM examkeyparsepage
        WHERE job_id = ?
        ORDER BY page_number
        """,
        [job_id],
    )
    result: list[ExamKeyParsePage] = []
    for row in rows:
        hydrated = _exam_parse_page_from_row(row)
        if hydrated is not None:
            result.append(hydrated)
    return result


def update_exam_parse_page(session: DbSession, page: ExamKeyParsePage, **fields) -> ExamKeyParsePage:
    _ = session
    if not fields:
        return page
    row = _update_row(
        "examkeyparsepage",
        int(page.id or 0),
        fields,
        "id, job_id, page_number, status, confidence, model_used, result_json, error_json, cost, input_tokens, output_tokens, created_at, updated_at",
    )
    updated = _exam_parse_page_from_row(row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated parse page row")
    return updated


def list_pending_exam_parse_pages(session: DbSession, job_id: int, *, limit: int) -> list[ExamKeyParsePage]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, job_id, page_number, status, confidence, model_used, result_json, error_json,
               cost, input_tokens, output_tokens, created_at, updated_at
        FROM examkeyparsepage
        WHERE job_id = ? AND status = 'pending'
        ORDER BY page_number
        LIMIT ?
        """,
        [job_id, limit],
    )
    result: list[ExamKeyParsePage] = []
    for row in rows:
        hydrated = _exam_parse_page_from_row(row)
        if hydrated is not None:
            result.append(hydrated)
    return result


def get_exam_parse_page(session: DbSession, *, job_id: int, page_number: int) -> ExamKeyParsePage | None:
    _ = session
    return _exam_parse_page_from_row(
        _bridge().query_first(
            """
            SELECT id, job_id, page_number, status, confidence, model_used, result_json, error_json,
                   cost, input_tokens, output_tokens, created_at, updated_at
            FROM examkeyparsepage
            WHERE job_id = ? AND page_number = ?
            """,
            [job_id, page_number],
        )
    )


def exam_parse_job_has_remaining_work(session: DbSession, job_id: int) -> bool:
    _ = session
    row = _bridge().query_first(
        """
        SELECT id
        FROM examkeyparsepage
        WHERE job_id = ? AND status IN ('pending', 'running', 'failed')
        LIMIT 1
        """,
        [job_id],
    )
    return row is not None


def create_exam_parse_page(
    session: DbSession,
    *,
    job_id: int,
    page_number: int,
    status: str,
    updated_at,
) -> ExamKeyParsePage:
    _ = session
    created_at = updated_at or utcnow()
    row = _bridge().query_first(
        """
        INSERT INTO examkeyparsepage
            (job_id, page_number, status, confidence, model_used, result_json, error_json, cost, input_tokens, output_tokens, created_at, updated_at)
        VALUES (?, ?, ?, 0.0, NULL, NULL, NULL, 0.0, 0, 0, ?, ?)
        RETURNING id, job_id, page_number, status, confidence, model_used, result_json, error_json,
                  cost, input_tokens, output_tokens, created_at, updated_at
        """,
        [job_id, page_number, status, _normalize_value(created_at), _normalize_value(updated_at)],
    )
    created = _exam_parse_page_from_row(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created parse page row")
    return created


def get_latest_exam_intake_job(session: DbSession, exam_id: int) -> ExamIntakeJob | None:
    _ = session
    return _exam_intake_job_from_row(
        _bridge().query_first(
            """
            SELECT id, exam_id, bulk_upload_id, status, stage, page_count, pages_built, pages_processed,
                   submissions_created, candidates_ready, review_open_threshold, initial_review_ready,
                   fully_warmed, review_ready, thinking_level, attempt_count, runner_id,
                   lease_expires_at, started_at, finished_at, last_progress_at, metrics_json,
                   error_message, created_at, updated_at
            FROM examintakejob
            WHERE exam_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            [exam_id],
        )
    )


def list_latest_exam_intake_jobs_by_exam_id(session: DbSession, exam_ids: list[int]) -> dict[int, ExamIntakeJob]:
    _ = session
    if not exam_ids:
        return {}
    placeholders = ", ".join("?" for _ in exam_ids)
    rows = _bridge().query_all(
        f"""
        SELECT id, exam_id, bulk_upload_id, status, stage, page_count, pages_built, pages_processed,
               submissions_created, candidates_ready, review_open_threshold, initial_review_ready,
               fully_warmed, review_ready, thinking_level, attempt_count, runner_id,
               lease_expires_at, started_at, finished_at, last_progress_at, metrics_json,
               error_message, created_at, updated_at
        FROM examintakejob
        WHERE exam_id IN ({placeholders})
        ORDER BY exam_id ASC, created_at DESC, id DESC
        """,
        exam_ids,
    )
    latest_by_exam_id: dict[int, ExamIntakeJob] = {}
    for row in rows:
        hydrated = _exam_intake_job_from_row(row)
        if hydrated is not None:
            latest_by_exam_id.setdefault(hydrated.exam_id, hydrated)
    return latest_by_exam_id


def get_exam_intake_job(session: DbSession, job_id: int) -> ExamIntakeJob | None:
    _ = session
    return _exam_intake_job_from_row(
        _bridge().query_first(
            """
            SELECT id, exam_id, bulk_upload_id, status, stage, page_count, pages_built, pages_processed,
                   submissions_created, candidates_ready, review_open_threshold, initial_review_ready,
                   fully_warmed, review_ready, thinking_level, attempt_count, runner_id,
                   lease_expires_at, started_at, finished_at, last_progress_at, metrics_json,
                   error_message, created_at, updated_at
            FROM examintakejob
            WHERE id = ?
            """,
            [job_id],
        )
    )


def update_exam_intake_job(session: DbSession, job: ExamIntakeJob, **fields) -> ExamIntakeJob:
    _ = session
    if not fields:
        return job
    row = _update_row(
        "examintakejob",
        int(job.id or 0),
        fields,
        "id, exam_id, bulk_upload_id, status, stage, page_count, pages_built, pages_processed, submissions_created, "
        "candidates_ready, review_open_threshold, initial_review_ready, fully_warmed, review_ready, thinking_level, "
        "attempt_count, runner_id, lease_expires_at, started_at, finished_at, last_progress_at, metrics_json, "
        "error_message, created_at, updated_at",
    )
    updated = _exam_intake_job_from_row(row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated intake job row")
    return updated


def list_queued_or_running_exam_intake_jobs(session: DbSession) -> list[ExamIntakeJob]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, exam_id, bulk_upload_id, status, stage, page_count, pages_built, pages_processed,
               submissions_created, candidates_ready, review_open_threshold, initial_review_ready,
               fully_warmed, review_ready, thinking_level, attempt_count, runner_id,
               lease_expires_at, started_at, finished_at, last_progress_at, metrics_json,
               error_message, created_at, updated_at
        FROM examintakejob
        WHERE status IN ('queued', 'running')
        ORDER BY created_at ASC, id ASC
        """
    )
    result: list[ExamIntakeJob] = []
    for row in rows:
        hydrated = _exam_intake_job_from_row(row)
        if hydrated is not None:
            result.append(hydrated)
    return result


def create_exam_intake_job(
    session: DbSession,
    *,
    exam_id: int,
    bulk_upload_id: int | None,
    status: str,
    stage: str,
    page_count: int,
    pages_built: int,
    pages_processed: int,
    submissions_created: int,
    candidates_ready: int,
    review_open_threshold: int,
    initial_review_ready: bool,
    fully_warmed: bool,
    review_ready: bool,
    thinking_level: str | None,
    last_progress_at,
    metrics_json: str,
) -> ExamIntakeJob:
    _ = session
    now = utcnow()
    row = _bridge().query_first(
        """
        INSERT INTO examintakejob
            (exam_id, bulk_upload_id, status, stage, page_count, pages_built, pages_processed,
             submissions_created, candidates_ready, review_open_threshold, initial_review_ready,
             fully_warmed, review_ready, thinking_level, attempt_count, runner_id, lease_expires_at,
             started_at, finished_at, last_progress_at, metrics_json, error_message, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL, NULL, NULL, ?, ?, NULL, ?, ?)
        RETURNING id, exam_id, bulk_upload_id, status, stage, page_count, pages_built, pages_processed,
                  submissions_created, candidates_ready, review_open_threshold, initial_review_ready,
                  fully_warmed, review_ready, thinking_level, attempt_count, runner_id, lease_expires_at,
                  started_at, finished_at, last_progress_at, metrics_json, error_message, created_at, updated_at
        """,
        [
            exam_id,
            bulk_upload_id,
            status,
            stage,
            page_count,
            pages_built,
            pages_processed,
            submissions_created,
            candidates_ready,
            review_open_threshold,
            initial_review_ready,
            fully_warmed,
            review_ready,
            thinking_level or "low",
            _normalize_value(last_progress_at),
            metrics_json,
            _normalize_value(now),
            _normalize_value(now),
        ],
    )
    created = _exam_intake_job_from_row(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created intake job row")
    return created


def list_bulk_upload_pages(session: DbSession, bulk_upload_id: int) -> list[BulkUploadPage]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, bulk_upload_id, page_number, image_path, width, height, detected_student_name,
               detection_confidence, detection_evidence_json, front_page_usage_json, created_at
        FROM bulkuploadpage
        WHERE bulk_upload_id = ?
        ORDER BY page_number
        """,
        [bulk_upload_id],
    )
    return _hydrate_many(BulkUploadPage, rows)


def get_exam_bulk_upload(session: DbSession, bulk_upload_id: int) -> ExamBulkUploadFile | None:
    _ = session
    return _exam_bulk_upload_from_row(
        _bridge().query_first(
            """
            SELECT id, exam_id, original_filename, stored_path, source_manifest_json, created_at
            FROM exambulkuploadfile
            WHERE id = ?
            """,
            [bulk_upload_id],
        )
    )


def create_exam_bulk_upload(session: DbSession, *, exam_id: int, original_filename: str, stored_path: str) -> ExamBulkUploadFile:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO exambulkuploadfile (exam_id, original_filename, stored_path, source_manifest_json, created_at)
        VALUES (?, ?, ?, NULL, ?)
        RETURNING id, exam_id, original_filename, stored_path, source_manifest_json, created_at
        """,
        [exam_id, original_filename, stored_path, _normalize_value(utcnow())],
    )
    created = _exam_bulk_upload_from_row(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created bulk upload row")
    return created


def update_exam_bulk_upload(
    session: DbSession,
    *,
    bulk: ExamBulkUploadFile,
    original_filename: str,
    stored_path: str,
    source_manifest_json: str | None = None,
) -> ExamBulkUploadFile:
    _ = session
    fields: dict[str, Any] = {
        "original_filename": original_filename,
        "stored_path": stored_path,
    }
    if source_manifest_json is not None:
        fields["source_manifest_json"] = source_manifest_json
    row = _update_row(
        "exambulkuploadfile",
        int(bulk.id or 0),
        fields,
        "id, exam_id, original_filename, stored_path, source_manifest_json, created_at",
    )
    updated = _exam_bulk_upload_from_row(row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated bulk upload row")
    return updated


def clear_bulk_upload_pages(session: DbSession, *, bulk_upload_id: int) -> None:
    _ = session
    _bridge().run("DELETE FROM bulkuploadpage WHERE bulk_upload_id = ?", [bulk_upload_id])


def create_bulk_upload_page(
    session: DbSession,
    *,
    bulk_upload_id: int,
    page_number: int,
    image_path: str,
    width: int,
    height: int,
    detected_student_name: str | None,
    detection_confidence: float,
    detection_evidence_json: str,
) -> BulkUploadPage:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO bulkuploadpage
            (bulk_upload_id, page_number, image_path, width, height, detected_student_name,
             detection_confidence, detection_evidence_json, front_page_usage_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        RETURNING id, bulk_upload_id, page_number, image_path, width, height, detected_student_name,
                  detection_confidence, detection_evidence_json, front_page_usage_json, created_at
        """,
        [
            bulk_upload_id,
            page_number,
            image_path,
            width,
            height,
            detected_student_name,
            detection_confidence,
            detection_evidence_json,
            _normalize_value(utcnow()),
        ],
    )
    created = _bulk_upload_page_from_row(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created bulk upload page row")
    return created


def update_bulk_upload_page(session: DbSession, row: BulkUploadPage, **fields) -> BulkUploadPage:
    _ = session
    if not fields:
        return row
    updated_row = _update_row(
        "bulkuploadpage",
        int(row.id or 0),
        fields,
        "id, bulk_upload_id, page_number, image_path, width, height, detected_student_name, detection_confidence, detection_evidence_json, front_page_usage_json, created_at",
    )
    updated = _bulk_upload_page_from_row(updated_row)
    if updated is None:
        raise RuntimeError("D1 bridge did not return the updated bulk upload page row")
    return updated


def delete_exam_data(session: DbSession, *, exam: Exam) -> list[int]:
    _ = session
    exam_id = int(exam.id or 0)
    parse_job_rows = _bridge().query_all(
        """
        SELECT id
        FROM examkeyparsejob
        WHERE exam_id = ?
        ORDER BY id
        """,
        [exam_id],
    )
    parse_job_ids = [int(row["id"]) for row in parse_job_rows if row.get("id") is not None]
    _bridge().batch(
        [
            D1Statement("DELETE FROM submissionpage WHERE submission_id IN (SELECT id FROM submission WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM submissionfile WHERE submission_id IN (SELECT id FROM submission WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM answercrop WHERE submission_id IN (SELECT id FROM submission WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM transcription WHERE submission_id IN (SELECT id FROM submission WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM graderesult WHERE submission_id IN (SELECT id FROM submission WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM questionregion WHERE question_id IN (SELECT id FROM question WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM questionparseevidence WHERE exam_id = ?", [exam_id]),
            D1Statement("DELETE FROM answercrop WHERE question_id IN (SELECT id FROM question WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM transcription WHERE question_id IN (SELECT id FROM question WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM graderesult WHERE question_id IN (SELECT id FROM question WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM examkeyparsepage WHERE job_id IN (SELECT id FROM examkeyparsejob WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM submission WHERE exam_id = ?", [exam_id]),
            D1Statement("DELETE FROM question WHERE exam_id = ?", [exam_id]),
            D1Statement("DELETE FROM examkeyparsejob WHERE exam_id = ?", [exam_id]),
            D1Statement("DELETE FROM examkeypage WHERE exam_id = ?", [exam_id]),
            D1Statement("DELETE FROM examkeyfile WHERE exam_id = ?", [exam_id]),
            D1Statement("DELETE FROM examintakejob WHERE exam_id = ?", [exam_id]),
            D1Statement("DELETE FROM bulkuploadpage WHERE bulk_upload_id IN (SELECT id FROM exambulkuploadfile WHERE exam_id = ?)", [exam_id]),
            D1Statement("DELETE FROM exambulkuploadfile WHERE exam_id = ?", [exam_id]),
            D1Statement("DELETE FROM exam WHERE id = ?", [exam_id]),
        ]
    )
    return parse_job_ids


def create_exam_key_file(
    session: DbSession,
    *,
    exam_id: int,
    original_filename: str,
    stored_path: str,
    content_type: str,
    size_bytes: int,
    blob_url: str | None = None,
    blob_pathname: str | None = None,
) -> ExamKeyFile:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO examkeyfile
            (exam_id, original_filename, stored_path, blob_url, blob_pathname, content_type, size_bytes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id, exam_id, original_filename, stored_path, blob_url, blob_pathname, content_type, size_bytes, created_at
        """,
        [
            exam_id,
            original_filename,
            stored_path,
            blob_url,
            blob_pathname,
            content_type,
            size_bytes,
            _normalize_value(utcnow()),
        ],
    )
    created = _exam_key_file_from_row(row)
    if created is None:
        raise RuntimeError("D1 bridge did not return the created key file row")
    return created


def register_exam_key_files(
    session: DbSession,
    *,
    exam_id: int,
    files: Sequence[dict[str, str | int | None]],
) -> int:
    registered = 0
    for file in files:
        create_exam_key_file(
            session,
            exam_id=exam_id,
            original_filename=str(file["original_filename"]),
            stored_path=str(file["stored_path"]),
            content_type=str(file["content_type"]),
            size_bytes=int(file["size_bytes"]),
            blob_url=str(file["blob_url"]) if file.get("blob_url") is not None else None,
            blob_pathname=str(file["blob_pathname"]) if file.get("blob_pathname") is not None else None,
        )
        registered += 1
    return registered
