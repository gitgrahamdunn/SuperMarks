"""Exam and question management endpoints."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import timedelta
from difflib import SequenceMatcher
from io import StringIO
from pathlib import Path
from typing import Any, Callable

import httpx

from PIL import Image, ImageOps

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlmodel import delete, select

from app import db
from app.auth import can_access_owned_resource, current_user_owner_id
from app.blob_service import BlobDownloadError, create_signed_blob_url, download_blob_bytes, normalize_blob_path
from app.ai.openai_vision import (
    _normalize_front_page_gemini_thinking_level,
    _front_page_model,
    AnswerKeyParser,
    BulkNameDetectionResult,
    OpenAIRequestError,
    ParseResult,
    SchemaBuildError,
    build_answer_key_response_schema,
    extract_class_list_names_from_image,
    get_answer_key_parser,
    get_bulk_name_detector,
    get_front_page_totals_extractor,
)
from app.persistence import (
    DbSession,
    commit_repository_session,
    flush_repository_session,
    get_repository_session,
    open_repository_session,
    repository_provider,
    rollback_repository_session,
)
from app.models import AnswerCrop, BulkUploadPage, ClassList, Exam, ExamBulkUploadFile, ExamIntakeJob, ExamKeyFile, ExamKeyPage, ExamKeyParseJob, ExamKeyParsePage, ExamStatus, GradeResult, Question, QuestionParseEvidence, QuestionRegion, Submission, SubmissionCaptureMode, SubmissionFile, SubmissionPage, SubmissionStatus, Transcription, utcnow
from app.class_lists import build_class_list_payload, nearest_known_student_name, normalize_class_list_names, parse_class_list_names_json, parse_class_list_tabular_bytes
from app.reporting import front_page_totals_read
from app.reporting_service import CsvExportSpec, CsvExportRow, build_exam_gradebook_xlsx_artifact, build_exam_marking_dashboard_response, build_exam_marks_export_artifact, build_exam_objectives_summary_export_artifact, build_exam_student_summaries_zip_export_artifact, build_exam_summary_export_artifact, build_zip_export_content, invalidate_exam_reporting_cache, write_csv_export
from app.name_utils import compose_student_name, normalize_student_name, split_student_name, submission_display_name, submission_name_parts
from app.schemas import BlobRegisterRequest, BlobRegisterResponse, BulkUploadCandidate, BulkUploadFinalizeRequest, BulkUploadFinalizeResponse, BulkUploadPreviewResponse, ClassListRead, ExamCreate, ExamDetail, ExamIntakeJobRead, ExamKeyPageRead, ExamKeyUploadResponse, ExamMarkingDashboardResponse, ExamParseJobRead, ExamRead, ExamWorkspaceBootstrapResponse, FrontPageCandidateValue, FrontPageExtractionEvidence, FrontPageObjectiveScoreCandidate, FrontPageTotalsCandidateRead, FrontPageUsageEntryRead, FrontPageUsageReportRead, NameEvidence, QuestionCreate, QuestionRead, QuestionUpdate, RegionRead, StoredFileRead, SubmissionFileRead, SubmissionPageRead, SubmissionRead
from app.settings import settings
from app.pipeline.pages import build_page_preview_image
from app.storage import ensure_dir, reset_dir, relative_to_data
from app.storage_provider import get_storage_provider, get_storage_signed_url, materialize_object_to_path
from app.blob_store import BlobUploadError, upload_bytes, upload_rendered_key_page
router = APIRouter(prefix="/exams", tags=["exams"])
public_router = APIRouter(prefix="/exams", tags=["exams-public"])
class_lists_router = APIRouter(prefix="/class-lists", tags=["class-lists"])
logger = logging.getLogger(__name__)
exam_repo = repository_provider().exams
question_repo = repository_provider().questions
submission_repo = repository_provider().submissions
_exam_question_locks: dict[int, threading.Lock] = {}
_exam_question_locks_guard = threading.Lock()
_parse_job_runner_locks: dict[int, threading.Lock] = {}
_parse_job_runner_guard = threading.Lock()
_exam_intake_runner_locks: dict[int, threading.Lock] = {}
_exam_intake_runner_guard = threading.Lock()


def _front_page_review_open_threshold(submission_count: int) -> int:
    configured = os.getenv("SUPERMARKS_FRONT_PAGE_REVIEW_OPEN_THRESHOLD", "10").strip()
    try:
        desired = int(configured or "10")
    except ValueError:
        desired = 10
    desired = max(1, desired)
    return min(desired, max(submission_count, 1))


def _name_detection_worker_count(page_count: int) -> int:
    configured = os.getenv("SUPERMARKS_NAME_DETECT_WORKERS", "8").strip()
    try:
        desired = int(configured or "8")
    except ValueError:
        desired = 8
    return max(1, min(desired, max(page_count, 1)))


def _front_page_candidate_worker_count(submission_count: int) -> int:
    configured = os.getenv("SUPERMARKS_FRONT_PAGE_WARM_WORKERS", "8").strip()
    try:
        desired = int(configured or "8")
    except ValueError:
        desired = 8
    return max(1, min(desired, max(submission_count, 1)))


_FRONT_PAGE_EXTRACT_ATTEMPTS = max(1, int(os.getenv("SUPERMARKS_FRONT_PAGE_EXTRACT_ATTEMPTS", "3") or "3"))


def _record_front_page_usage_metrics(metrics: dict[str, object], usage: dict[str, object] | None) -> None:
    if not usage:
        return
    call_count = max(int(usage.get("call_count") or 1), 1)
    metrics["front_page_provider"] = str(usage.get("provider") or metrics.get("front_page_provider") or "")
    metrics["front_page_model"] = str(usage.get("model") or metrics.get("front_page_model") or "")
    metrics["front_page_thinking_level"] = str(usage.get("thinking_level") or metrics.get("front_page_thinking_level") or "")
    metrics["front_page_thinking_budget"] = int(usage.get("thinking_budget") or metrics.get("front_page_thinking_budget") or 0)
    metrics["front_page_calls"] = int(metrics.get("front_page_calls") or 0) + call_count
    metrics["front_page_prompt_tokens"] = int(metrics.get("front_page_prompt_tokens") or 0) + int(usage.get("prompt_tokens") or 0)
    metrics["front_page_output_tokens"] = int(metrics.get("front_page_output_tokens") or 0) + int(usage.get("candidate_tokens") or 0)
    metrics["front_page_thought_tokens"] = int(metrics.get("front_page_thought_tokens") or 0) + int(usage.get("thought_tokens") or 0)
    metrics["front_page_total_tokens"] = int(metrics.get("front_page_total_tokens") or 0) + int(usage.get("total_tokens") or 0)
    metrics["front_page_estimated_cost_usd"] = round(
        float(metrics.get("front_page_estimated_cost_usd") or 0.0) + float(usage.get("estimated_cost_usd") or 0.0),
        6,
    )
    metrics["front_page_image_bytes_total"] = int(metrics.get("front_page_image_bytes_total") or 0) + int(
        usage.get("normalized_image_bytes_total") or usage.get("normalized_image_bytes") or 0
    )
    metrics["front_page_image_width_total"] = int(metrics.get("front_page_image_width_total") or 0) + int(
        usage.get("normalized_image_width_total") or usage.get("normalized_image_width") or 0
    )
    metrics["front_page_image_height_total"] = int(metrics.get("front_page_image_height_total") or 0) + int(
        usage.get("normalized_image_height_total") or usage.get("normalized_image_height") or 0
    )
    calls = max(int(metrics.get("front_page_calls") or 0), 1)
    metrics["front_page_avg_cost_per_page_usd"] = round(float(metrics["front_page_estimated_cost_usd"]) / calls, 6)
    metrics["front_page_avg_image_bytes"] = round(int(metrics["front_page_image_bytes_total"]) / calls, 1)


def _job_metrics_payload(job: ExamIntakeJob | None, intake_metrics: dict[str, object]) -> str:
    merged: dict[str, object] = {}
    raw_metrics = ((job.metrics_json if job else None) or "").strip()
    if raw_metrics:
        try:
            parsed = json.loads(raw_metrics)
            if isinstance(parsed, dict):
                merged.update(parsed)
        except json.JSONDecodeError:
            logger.warning("invalid intake metrics payload for job %s during metrics merge", getattr(job, "id", None))
    merged.update(intake_metrics)
    return json.dumps(merged)


def _front_page_usage_payload(raw_payload: str | None) -> dict[str, object] | None:
    if not raw_payload:
        return None
    payload = raw_payload.strip()
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _build_class_list_read(
    *,
    raw_names: str | None,
    raw_source: str | None,
    fallback_name: str = "",
    fallback_id: int | None = None,
    fallback_created_at=None,
) -> ClassListRead | None:
    names = parse_class_list_names_json(raw_names)
    if not names:
        return None
    source = ""
    filenames: list[str] = []
    class_list_id = fallback_id
    class_list_name = fallback_name
    created_at = fallback_created_at
    source_payload = (raw_source or "").strip()
    if source_payload:
        try:
            parsed_source = json.loads(source_payload)
        except json.JSONDecodeError:
            parsed_source = None
        if isinstance(parsed_source, dict):
            source = str(parsed_source.get("source") or "").strip()
            filenames = [str(item).strip() for item in parsed_source.get("filenames", []) if str(item).strip()] if isinstance(parsed_source.get("filenames"), list) else []
            parsed_class_list_id = parsed_source.get("class_list_id")
            if isinstance(parsed_class_list_id, int):
                class_list_id = parsed_class_list_id
            class_list_name = str(parsed_source.get("class_list_name") or class_list_name or "").strip()
            created_at_raw = str(parsed_source.get("created_at") or "").strip()
            if created_at_raw:
                try:
                    from datetime import datetime
                    created_at = datetime.fromisoformat(created_at_raw)
                except ValueError:
                    created_at = fallback_created_at
    return ClassListRead(
        id=class_list_id,
        name=class_list_name,
        created_at=created_at,
        names=names,
        source=source,
        entry_count=len(names),
        filenames=filenames,
    )


def _class_list_resource_read(class_list: ClassList) -> ClassListRead | None:
    return _build_class_list_read(
        raw_names=class_list.names_json,
        raw_source=class_list.source_json,
        fallback_name=class_list.name,
        fallback_id=class_list.id,
        fallback_created_at=class_list.created_at,
    )


def _class_list_read(exam: Exam) -> ClassListRead | None:
    return _build_class_list_read(
        raw_names=exam.class_list_json,
        raw_source=exam.class_list_source_json,
    )


def _exam_known_student_names(exam: Exam) -> list[str]:
    class_list = _class_list_read(exam)
    return class_list.names if class_list else []


def _normalized_class_list_name(value: str | None, *, filenames: list[str] | None = None, exam_name: str | None = None) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if normalized:
        return normalized
    if exam_name and exam_name.strip():
        return f"{exam_name.strip()} class list"
    if filenames:
        first = Path(filenames[0]).stem.replace("_", " ").replace("-", " ").strip()
        if first:
            return " ".join(first.split())
    return f"Class list {utcnow().strftime('%Y-%m-%d %H:%M')}"


def _extract_class_list_names_from_uploads(
    *,
    storage_dir: Path,
    upload_files: list[UploadFile],
) -> tuple[list[str], list[str]]:
    if not upload_files:
        return [], []

    source_names: list[str] = []
    filenames: list[str] = []
    class_list_dir = reset_dir(storage_dir)

    for index, upload in enumerate(upload_files, start=1):
        filename = _sanitize_filename(upload.filename or f"class-list-{index}")
        filenames.append(filename)
        payload = upload.file.read()
        suffix = Path(filename).suffix.lower()
        if suffix in {".csv", ".xlsx", ".xlsm"}:
            source_names.extend(parse_class_list_tabular_bytes(filename, payload))
            continue

        source_path = class_list_dir / filename
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(payload)
        rendered_paths = _render_bulk_pages(source_path, class_list_dir / f"rendered_{index:04d}")
        for rendered_path in rendered_paths:
            source_names.extend(extract_class_list_names_from_image(rendered_path))

    deduped_names = [nearest_known_student_name(name, source_names, minimum_ratio=1.0) for name in source_names]
    return normalize_class_list_names(deduped_names), filenames


def _persist_exam_class_list(
    *,
    exam: Exam,
    names: list[str],
    source: str,
    filenames: list[str] | None = None,
    class_list_id: int | None = None,
    class_list_name: str | None = None,
    created_at=None,
    session: DbSession,
) -> None:
    class_list_json, class_list_source_json = build_class_list_payload(
        names,
        source=source,
        filenames=filenames or [],
        class_list_id=class_list_id,
        class_list_name=class_list_name,
        created_at=created_at,
    )
    exam_repo.update_exam_class_list_payload(
        session,
        exam=exam,
        class_list_json=class_list_json,
        class_list_source_json=class_list_source_json,
    )


def _create_class_list_resource(
    *,
    name: str,
    names: list[str],
    source: str,
    filenames: list[str] | None,
    session: DbSession,
) -> ClassList:
    normalized_names = normalize_class_list_names(names)
    class_list = exam_repo.create_class_list(
        session,
        name=_normalized_class_list_name(name, filenames=filenames),
        owner_user_id=current_user_owner_id(),
    )
    names_json, source_json = build_class_list_payload(
        normalized_names,
        source=source,
        filenames=filenames or [],
        class_list_id=class_list.id,
        class_list_name=class_list.name,
        created_at=class_list.created_at,
    )
    return exam_repo.update_class_list_payload(session, class_list=class_list, names_json=names_json, source_json=source_json)


def _update_class_list_resource_names(
    *,
    class_list: ClassList,
    names: list[str],
    source: str | None = None,
    filenames: list[str] | None = None,
    session: DbSession,
) -> ClassList:
    payload = _class_list_resource_read(class_list)
    existing_names = payload.names if payload else []
    merged_names = normalize_class_list_names([*existing_names, *names])
    names_json, source_json = build_class_list_payload(
        merged_names,
        source=source or (payload.source if payload else "manual_update"),
        filenames=filenames if filenames is not None else (payload.filenames if payload else []),
        class_list_id=class_list.id,
        class_list_name=class_list.name,
        created_at=class_list.created_at,
    )
    return exam_repo.update_class_list_payload(session, class_list=class_list, names_json=names_json, source_json=source_json)


def _select_class_list_for_exam(*, exam: Exam, class_list: ClassList, session: DbSession) -> None:
    class_list_read = _class_list_resource_read(class_list)
    if not class_list_read:
        return
    _persist_exam_class_list(
        exam=exam,
        names=class_list_read.names,
        source="selected_class_list",
        filenames=class_list_read.filenames,
        class_list_id=class_list.id,
        class_list_name=class_list.name,
        created_at=class_list.created_at,
        session=session,
    )


def _invalidate_exam_front_page_candidate_cache_for_class_list(exam_id: int, session: DbSession) -> None:
    submissions = exam_repo.list_front_page_unreviewed_submissions(session, exam_id)
    for submission in submissions:
        submission_repo.update_submission_front_page_data(
            session,
            submission,
            front_page_candidates_json=None,
            front_page_usage_json=None,
        )


def _get_exam_question_lock(exam_id: int) -> threading.Lock:
    with _exam_question_locks_guard:
        lock = _exam_question_locks.get(exam_id)
        if lock is None:
            lock = threading.Lock()
            _exam_question_locks[exam_id] = lock
        return lock


def _get_parse_job_runner_lock(job_id: int) -> threading.Lock:
    with _parse_job_runner_guard:
        lock = _parse_job_runner_locks.get(job_id)
        if lock is None:
            lock = threading.Lock()
            _parse_job_runner_locks[job_id] = lock
        return lock


def _get_exam_intake_runner_lock(exam_id: int) -> threading.Lock:
    with _exam_intake_runner_guard:
        lock = _exam_intake_runner_locks.get(exam_id)
        if lock is None:
            lock = threading.Lock()
            _exam_intake_runner_locks[exam_id] = lock
        return lock


def _exam_intake_job_read(job: ExamIntakeJob | None) -> ExamIntakeJobRead | None:
    if not job or job.id is None:
        return None
    metrics: dict[str, object] | None = None
    raw_metrics = (job.metrics_json or "").strip()
    if raw_metrics:
        try:
            parsed_metrics = json.loads(raw_metrics)
            if isinstance(parsed_metrics, dict):
                metrics = parsed_metrics
        except json.JSONDecodeError:
            logger.warning("invalid intake metrics payload for job %s", job.id)
    return ExamIntakeJobRead(
        id=job.id,
        exam_id=job.exam_id,
        bulk_upload_id=job.bulk_upload_id,
        status=job.status,
        stage=job.stage,
        page_count=job.page_count,
        pages_built=job.pages_built,
        pages_processed=job.pages_processed,
        submissions_created=job.submissions_created,
        candidates_ready=job.candidates_ready,
        review_open_threshold=job.review_open_threshold,
        initial_review_ready=bool(job.initial_review_ready),
        fully_warmed=bool(job.fully_warmed),
        review_ready=bool(job.review_ready or job.initial_review_ready),
        thinking_level=_normalize_front_page_gemini_thinking_level(job.thinking_level),
        metrics=metrics,
        error_message=job.error_message,
        last_progress_at=job.last_progress_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _latest_exam_intake_job(exam_id: int, session: DbSession) -> ExamIntakeJob | None:
    job = exam_repo.get_latest_exam_intake_job(session, exam_id)
    return _auto_resume_exam_intake_job_if_needed(job, session)


def _latest_exam_intake_jobs_by_exam_id(exam_ids: list[int], session: DbSession) -> dict[int, ExamIntakeJob]:
    if not exam_ids:
        return {}
    latest_by_exam_id: dict[int, ExamIntakeJob] = {}
    for job in exam_repo.list_latest_exam_intake_jobs_by_exam_id(session, exam_ids).values():
        latest_by_exam_id.setdefault(job.exam_id, _auto_resume_exam_intake_job_if_needed(job, session))
    return latest_by_exam_id


def _exam_intake_lease_deadline():
    return utcnow() + timedelta(minutes=10)


def _coerce_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=utcnow().tzinfo)
    return value


def _claim_exam_intake_job(job_id: int, runner_id: str, session: DbSession) -> bool:
    job = exam_repo.get_exam_intake_job(session, job_id)
    if not job:
        return False
    if job.status == "complete":
        return False
    now = utcnow()
    lease_expires_at = _coerce_utc(job.lease_expires_at)
    if job.status == "running" and job.runner_id and job.runner_id != runner_id and lease_expires_at and lease_expires_at > now:
        return False
    exam_repo.update_exam_intake_job(
        session,
        job,
        status="running",
        runner_id=runner_id,
        attempt_count=int(job.attempt_count or 0) + 1,
        started_at=job.started_at or now,
        updated_at=now,
        last_progress_at=now,
        lease_expires_at=_exam_intake_lease_deadline(),
    )
    commit_repository_session(session)
    return True


def _spawn_exam_intake_job_thread(job_id: int) -> None:
    thread = threading.Thread(target=_run_exam_intake_job_background_by_job_id, args=(job_id,), daemon=True)
    thread.start()


def _auto_resume_exam_intake_job_if_needed(job: ExamIntakeJob | None, session: DbSession) -> ExamIntakeJob | None:
    if not job:
        return job
    is_stalled_running_job = job.status == "running" and bool(job.lease_expires_at) and _coerce_utc(job.lease_expires_at) <= utcnow()
    is_retryable_stalled_failure = job.status == "failed" and job.stage == "stalled"
    if not is_stalled_running_job and not is_retryable_stalled_failure:
        return job

    now = utcnow()
    job = exam_repo.update_exam_intake_job(
        session,
        job,
        status="queued",
        stage="resuming",
        fully_warmed=False,
        review_ready=bool(job.initial_review_ready),
        error_message=None,
        updated_at=now,
        finished_at=None,
        runner_id=None,
        lease_expires_at=None,
        last_progress_at=now,
    )

    exam = _get_exam_or_404(job.exam_id, session, check_access=False)
    if exam and exam.status != ExamStatus.READY:
        exam_repo.update_exam(
            session,
            exam,
            status=ExamStatus.REVIEWING if job.initial_review_ready else ExamStatus.DRAFT,
        )

    commit_repository_session(session)
    if job.id is not None:
        _spawn_exam_intake_job_thread(job.id)
    return job


def _resume_pending_exam_intake_jobs() -> None:
    with open_repository_session() as session:
        now = utcnow()
        jobs = exam_repo.list_queued_or_running_exam_intake_jobs(session)
        resumable_ids: list[int] = []
        for job in jobs:
            if job.id is None:
                continue
            lease_expires_at = _coerce_utc(job.lease_expires_at)
            if job.status == "queued":
                resumable_ids.append(job.id)
                continue
            if job.status == "running" and (lease_expires_at is None or lease_expires_at <= now):
                exam_repo.update_exam_intake_job(
                    session,
                    job,
                    status="queued",
                    stage="resuming",
                    fully_warmed=False,
                    review_ready=bool(job.initial_review_ready),
                    error_message=None,
                    runner_id=None,
                    lease_expires_at=None,
                    finished_at=None,
                    updated_at=now,
                    last_progress_at=now,
                )
                resumable_ids.append(job.id)
        commit_repository_session(session)
    for job_id in resumable_ids:
        _spawn_exam_intake_job_thread(job_id)


def _warm_front_page_candidates_background(exam_id: int, submission_ids: list[int]) -> list[int]:
    from app.routers.submissions import get_or_create_front_page_totals_candidates

    failed_submission_ids: list[int] = []
    progress_lock = threading.Lock()
    ready_count = 0

    def warm_one(submission_id: int) -> None:
        with open_repository_session() as session:
            get_or_create_front_page_totals_candidates(submission_id, session)

    worker_count = _front_page_candidate_worker_count(len(submission_ids))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(warm_one, submission_id): submission_id for submission_id in submission_ids}
        for future in concurrent.futures.as_completed(futures):
            submission_id = futures[future]
            try:
                future.result()
                with progress_lock:
                    ready_count += 1
            except Exception:
                logger.exception("background front-page candidate warm failed for exam %s submission %s", exam_id, submission_id)
                failed_submission_ids.append(submission_id)

    return failed_submission_ids


def _front_page_review_readiness_failures(submission_ids: list[int], session: DbSession) -> list[str]:
    if not submission_ids:
        return ["No submissions were created."]

    submissions = submission_repo.list_submissions_by_ids(session, submission_ids)
    if len(submissions) != len(submission_ids):
        return ["One or more submissions could not be loaded after intake."]

    pages = submission_repo.list_submission_pages_for_submission_ids(session, submission_ids)
    pages_by_submission_id: dict[int, list[SubmissionPage]] = {}
    for page in pages:
        pages_by_submission_id.setdefault(page.submission_id, []).append(page)

    failures: list[str] = []
    for submission in submissions:
        if not pages_by_submission_id.get(submission.id or 0):
            failures.append(f"Submission {submission.id} has no built pages.")
        if submission.capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS:
            raw_candidate_payload = (submission.front_page_candidates_json or "").strip()
            if not raw_candidate_payload:
                failures.append(f"Submission {submission.id} has no front-page candidate payload.")
                continue
            try:
                candidate_payload = FrontPageTotalsCandidateRead.model_validate_json(raw_candidate_payload)
            except Exception:
                failures.append(f"Submission {submission.id} has an invalid front-page candidate payload.")
                continue
            if _front_page_candidate_is_retryable_failure(candidate_payload):
                failures.append(f"Submission {submission.id} has a retryable front-page candidate failure.")
    return failures


def _split_initial_and_remaining_review_ids(submission_ids: list[int]) -> tuple[list[int], list[int], int]:
    threshold = _front_page_review_open_threshold(len(submission_ids))
    initial_ids = submission_ids[:threshold]
    remaining_ids = submission_ids[threshold:]
    return initial_ids, remaining_ids, threshold


def _set_exam_initial_review_ready(
    exam_id: int,
    job_id: int,
    *,
    threshold: int,
    candidates_ready: int,
    stage: str,
    metrics: dict[str, object],
) -> None:
    with open_repository_session() as session:
        job = exam_repo.get_exam_intake_job(session, job_id)
        exam = _get_exam_or_404(exam_id, session, check_access=False)
        if not job:
            return
        now = utcnow()
        exam_repo.update_exam_intake_job(
            session,
            job,
            review_open_threshold=threshold,
            candidates_ready=max(job.candidates_ready, candidates_ready),
            initial_review_ready=True,
            review_ready=True,
            stage=stage,
            updated_at=now,
            last_progress_at=now,
            lease_expires_at=_exam_intake_lease_deadline(),
            metrics_json=json.dumps(metrics),
        )
        if exam and exam.status != ExamStatus.READY:
            exam_repo.update_exam(session, exam, status=ExamStatus.REVIEWING)
        commit_repository_session(session)


def _mark_exam_review_ready_if_possible(exam_id: int, submission_ids: list[int], *, failed_submission_ids: list[int] | None = None) -> None:
    failed_submission_ids = failed_submission_ids or []
    with open_repository_session() as session:
        exam = _get_exam_or_404(exam_id, session, check_access=False)
        if not exam:
            return
        initial_ids, _remaining_ids, _threshold = _split_initial_and_remaining_review_ids(submission_ids)
        readiness_failures = _front_page_review_readiness_failures(initial_ids, session)
        if failed_submission_ids:
            readiness_failures.extend(
                f"Front-page warm failed for submission {submission_id}."
                for submission_id in sorted(set(failed_submission_ids).intersection(initial_ids))
            )
        if readiness_failures:
            if exam.status != ExamStatus.READY:
                exam_repo.update_exam(session, exam, status=ExamStatus.FAILED)
                commit_repository_session(session)
            raise RuntimeError("; ".join(readiness_failures))
        if exam.status != ExamStatus.READY:
            exam_repo.update_exam(session, exam, status=ExamStatus.REVIEWING)
            commit_repository_session(session)


def _warm_and_promote_front_page_review_background(exam_id: int, submission_ids: list[int]) -> None:
    try:
        failed_submission_ids = _warm_front_page_candidates_background(exam_id, submission_ids)
        _mark_exam_review_ready_if_possible(exam_id, submission_ids, failed_submission_ids=failed_submission_ids)
    except Exception:
        logger.exception("front-page warm/promote failed for exam %s", exam_id)


def _run_exam_intake_job_background_by_job_id(job_id: int) -> None:
    with open_repository_session() as session:
        job = exam_repo.get_exam_intake_job(session, job_id)
        if not job:
            return
        exam_id = job.exam_id
    _run_exam_intake_job_background(exam_id, job_id)


def _detect_bulk_pages(
    *,
    exam: Exam,
    bulk: ExamBulkUploadFile,
    rendered_paths: list[Path],
    session: DbSession,
    job: ExamIntakeJob | None = None,
) -> list[BulkNameDetectionResult]:
    existing_pages = exam_repo.list_bulk_upload_pages(session, bulk.id)
    page_rows_by_number = {row.page_number: row for row in existing_pages}
    detections: list[BulkNameDetectionResult | None] = [None] * len(rendered_paths)
    detector = get_bulk_name_detector()
    detected_exam_titles: list[str] = []

    def detect_one(idx: int, page_path: Path) -> tuple[int, int, int, BulkNameDetectionResult, str]:
        with Image.open(page_path) as image:
            w, h = image.width, image.height
        try:
            detection = detector.detect(page_path, idx, model=_front_page_model(), request_id=uuid.uuid4().hex)
            if detection.student_name is None or detection.confidence < 0.5:
                detection = detector.detect(page_path, idx, model=_front_page_model(), request_id=uuid.uuid4().hex)
        except OpenAIRequestError:
            detection = BulkNameDetectionResult(page_number=idx, student_name=None, exam_name=None, confidence=0.0, evidence=None)
        except Exception:
            logger.exception("bulk name detection failed for exam %s page %s", exam.id, idx)
            detection = BulkNameDetectionResult(page_number=idx, student_name=None, exam_name=None, confidence=0.0, evidence=None)
        normalized_detected_exam_title = _normalize_exam_title(detection.exam_name)
        if normalized_detected_exam_title and not _looks_like_same_name(normalized_detected_exam_title, detection.student_name):
            return idx, w, h, detection, normalized_detected_exam_title
        return idx, w, h, detection, ""

    processed_pages = 0
    worker_count = _name_detection_worker_count(len(rendered_paths))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(detect_one, idx, page_path): idx
            for idx, page_path in enumerate(rendered_paths, start=1)
        }
        for future in concurrent.futures.as_completed(futures):
            idx, width, height, detection, detected_exam_title = future.result()
            detections[idx - 1] = detection
            if detected_exam_title:
                detected_exam_titles.append(detected_exam_title)

            row = page_rows_by_number.get(idx)
            if row is None:
                row = exam_repo.create_bulk_upload_page(
                    session,
                    bulk_upload_id=bulk.id,
                    page_number=idx,
                    image_path=str(rendered_paths[idx - 1]),
                    width=width,
                    height=height,
                    detected_student_name=detection.student_name,
                    detection_confidence=detection.confidence,
                    detection_evidence_json=json.dumps(detection.evidence or {}),
                )
            else:
                exam_repo.update_bulk_upload_page(
                    session,
                    row,
                    image_path=str(rendered_paths[idx - 1]),
                    width=width,
                    height=height,
                    detected_student_name=detection.student_name,
                    detection_confidence=detection.confidence,
                    detection_evidence_json=json.dumps(detection.evidence or {}),
                )

            processed_pages += 1
            if job:
                now = utcnow()
                job = exam_repo.update_exam_intake_job(
                    session,
                    job,
                    pages_processed=processed_pages,
                    updated_at=now,
                    last_progress_at=now,
                    lease_expires_at=_exam_intake_lease_deadline(),
                )
                commit_repository_session(session)
            else:
                flush_repository_session(session)

    if detected_exam_titles:
        exam_repo.update_exam(session, exam, name=max(detected_exam_titles, key=len))
    if not job:
        commit_repository_session(session)
    return [item for item in detections if item is not None]


def _front_page_candidate_read_from_payload(
    payload: dict[str, object],
    *,
    source: str,
    page_width: float | None = None,
    page_height: float | None = None,
) -> FrontPageTotalsCandidateRead:
    def _normalize_front_page_coordinate(value: object, *, scale: float | None) -> float | None:
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if numeric < 0:
            return None
        if numeric <= 1:
            return numeric
        if numeric <= 1000:
            normalized = numeric / 1000
            if 0 <= normalized <= 1:
                return normalized
        if scale and scale > 1:
            normalized = numeric / scale
            if 0 <= normalized <= 1:
                return normalized
        return None

    def _candidate_value(raw_value: object) -> FrontPageCandidateValue | None:
        if not isinstance(raw_value, dict):
            return None
        evidence_rows: list[FrontPageExtractionEvidence] = []
        raw_evidence = raw_value.get("evidence")
        if isinstance(raw_evidence, list):
            for item in raw_evidence:
                if not isinstance(item, dict):
                    continue
                evidence_rows.append(
                    FrontPageExtractionEvidence(
                        page_number=int(item.get("page_number") or 1),
                        quote=str(item.get("quote") or ""),
                        x=_normalize_front_page_coordinate(item.get("x"), scale=page_width),
                        y=_normalize_front_page_coordinate(item.get("y"), scale=page_height),
                        w=_normalize_front_page_coordinate(item.get("w"), scale=page_width),
                        h=_normalize_front_page_coordinate(item.get("h"), scale=page_height),
                    )
                )
        return FrontPageCandidateValue(
            value_text=str(raw_value.get("value_text") or ""),
            confidence=float(raw_value.get("confidence") or 0),
            evidence=evidence_rows,
        )

    objective_scores: list[FrontPageObjectiveScoreCandidate] = []
    raw_objective_scores = payload.get("objective_scores")
    if isinstance(raw_objective_scores, list):
        for item in raw_objective_scores:
            if not isinstance(item, dict):
                continue
            objective_code = _candidate_value(item.get("objective_code"))
            marks_awarded = _candidate_value(item.get("marks_awarded"))
            if objective_code is None or marks_awarded is None:
                continue
            objective_scores.append(
                FrontPageObjectiveScoreCandidate(
                    objective_code=objective_code,
                    marks_awarded=marks_awarded,
                    max_marks=_candidate_value(item.get("max_marks")),
                )
            )

    return FrontPageTotalsCandidateRead(
        student_name=_candidate_value(payload.get("student_name")),
        overall_marks_awarded=_candidate_value(payload.get("overall_marks_awarded")),
        overall_max_marks=_candidate_value(payload.get("overall_max_marks")),
        objective_scores=objective_scores,
        warnings=[str(item) for item in payload.get("warnings", [])] if isinstance(payload.get("warnings"), list) else [],
        source=source,
    )


def _front_page_candidate_is_retryable_failure(candidate: FrontPageTotalsCandidateRead | None) -> bool:
    if not candidate:
        return False
    return (candidate.source or "").startswith("extractor_unavailable")


def _front_page_name_detection_from_candidate(
    *,
    page_number: int,
    candidate: FrontPageTotalsCandidateRead,
    exam_name: str | None,
) -> BulkNameDetectionResult:
    student_name_value = None
    if candidate.student_name:
        student_name_value = candidate.student_name.value_text.strip() or None
    evidence = candidate.student_name.evidence[0] if candidate.student_name and candidate.student_name.evidence else None
    evidence_box = None
    if evidence:
        evidence_box = {
            "x": evidence.x if evidence.x is not None else 0.0,
            "y": evidence.y if evidence.y is not None else 0.0,
            "w": evidence.w if evidence.w is not None else 0.0,
            "h": evidence.h if evidence.h is not None else 0.0,
        }
    return BulkNameDetectionResult(
        page_number=page_number,
        student_name=student_name_value,
        exam_name=exam_name,
        confidence=float(candidate.student_name.confidence if candidate.student_name else 0.0),
        evidence=evidence_box,
    )


def _extract_image_upload_front_page_pages(
    *,
    exam: Exam,
    bulk: ExamBulkUploadFile,
    rendered_paths: list[Path],
    session: DbSession,
    job: ExamIntakeJob | None = None,
) -> tuple[list[BulkNameDetectionResult], dict[int, FrontPageTotalsCandidateRead], dict[int, dict[str, object]]]:
    exam_id = exam.id or 0
    existing_pages = exam_repo.list_bulk_upload_pages(session, bulk.id)
    page_rows_by_number = {row.page_number: row for row in existing_pages}
    detections: list[BulkNameDetectionResult | None] = [None] * len(rendered_paths)
    candidate_payloads: dict[int, FrontPageTotalsCandidateRead] = {}
    usage_payloads: dict[int, dict[str, object]] = {}
    extractor = get_front_page_totals_extractor()
    detected_exam_titles: list[str] = []
    grouped_template: dict[str, object] | None = None
    known_student_names = _exam_known_student_names(exam)
    template_sample_paths = rendered_paths[: min(3, len(rendered_paths))]
    job_metrics: dict[str, object] | None = None
    job_thinking_level = _normalize_front_page_gemini_thinking_level(job.thinking_level) if job else None
    if job:
        raw_metrics = (job.metrics_json or "").strip()
        if raw_metrics:
            try:
                parsed_metrics = json.loads(raw_metrics)
                if isinstance(parsed_metrics, dict):
                    job_metrics = parsed_metrics
            except json.JSONDecodeError:
                logger.warning("invalid intake metrics payload for job %s during one-shot front-page extraction", job.id)
        if job_metrics is None:
            job_metrics = {}

    if template_sample_paths and hasattr(extractor, "extract_template_group"):
        try:
            grouped_template_candidate = extractor.extract_template_group(  # type: ignore[attr-defined]
                template_sample_paths,
                model_override=_front_page_model(),
                thinking_level_override=job_thinking_level,
            )
            if isinstance(grouped_template_candidate, dict) and grouped_template_candidate:
                grouped_template = grouped_template_candidate
                exam_name = str(grouped_template.get("exam_name") or "").strip()
                exam_repo.update_exam(
                    session,
                    exam,
                    name=exam_name or exam.name,
                    front_page_template_json=json.dumps(grouped_template),
                )
                commit_repository_session(session)
        except Exception:
            logger.exception("grouped front-page template extraction failed for exam %s", exam_id)

    def extract_one(idx: int, page_path: Path) -> tuple[int, int, int, BulkNameDetectionResult, FrontPageTotalsCandidateRead, str, dict[str, object] | None]:
        with Image.open(page_path) as image:
            w, h = image.width, image.height
        def _run_extract():
            try:
                return extractor.extract(
                    image_path=page_path,
                    request_id=f"exam-{exam_id}-bulk-page-{idx}",
                    model_override=_front_page_model(),
                    template=grouped_template,
                    thinking_level_override=job_thinking_level,
                    known_student_names=known_student_names,
                )
            except TypeError:
                try:
                    return extractor.extract(
                        image_path=page_path,
                        request_id=f"exam-{exam_id}-bulk-page-{idx}",
                        model_override=_front_page_model(),
                        template=grouped_template,
                        thinking_level_override=job_thinking_level,
                    )
                except TypeError:
                    return extractor.extract(
                        image_path=page_path,
                        request_id=f"exam-{exam_id}-bulk-page-{idx}",
                    )
        result = None
        for attempt in range(1, _FRONT_PAGE_EXTRACT_ATTEMPTS + 1):
            try:
                result = _run_extract()
                break
            except OpenAIRequestError as exc:
                logger.warning(
                    "front-page one-shot intake provider failure exam=%s page=%s attempt=%s/%s error=%s",
                    exam_id,
                    idx,
                    attempt,
                    _FRONT_PAGE_EXTRACT_ATTEMPTS,
                    exc,
                )
                if attempt < _FRONT_PAGE_EXTRACT_ATTEMPTS:
                    time.sleep(0.35 * attempt)
            except Exception:
                logger.exception(
                    "front-page one-shot intake failed for exam %s page %s attempt %s/%s",
                    exam_id,
                    idx,
                    attempt,
                    _FRONT_PAGE_EXTRACT_ATTEMPTS,
                )
                if attempt < _FRONT_PAGE_EXTRACT_ATTEMPTS:
                    time.sleep(0.35 * attempt)
        if result is None:
            candidate_payload = FrontPageTotalsCandidateRead(
                objective_scores=[],
                warnings=["Extractor unavailable for this paper right now. You can still confirm totals manually."],
                source="extractor_unavailable:oneshot",
            )
            detected_exam_title = ""
            usage = None
        else:
            candidate_payload = _front_page_candidate_read_from_payload(
                result.payload,
                source=f"{result.model}:oneshot" if result.model else "oneshot",
                page_width=w,
                page_height=h,
            )
            if known_student_names and candidate_payload.student_name and candidate_payload.student_name.value_text.strip():
                matched_name = nearest_known_student_name(candidate_payload.student_name.value_text, known_student_names)
                if matched_name and matched_name != candidate_payload.student_name.value_text:
                    candidate_payload.student_name.value_text = matched_name
            exam_name_payload = result.payload.get("exam_name")
            exam_name_value = None
            if isinstance(exam_name_payload, dict):
                exam_name_value = str(exam_name_payload.get("value_text") or "").strip() or None
            normalized_exam_title = _normalize_exam_title(exam_name_value)
            if normalized_exam_title and not _looks_like_same_name(normalized_exam_title, candidate_payload.student_name.value_text if candidate_payload.student_name else None):
                detected_exam_title = normalized_exam_title
            else:
                detected_exam_title = ""
            usage = result.usage
        detection = _front_page_name_detection_from_candidate(
            page_number=idx,
            candidate=candidate_payload,
            exam_name=detected_exam_title or None,
        )
        return idx, w, h, detection, candidate_payload, detected_exam_title, usage

    processed_pages = 0
    worker_count = _front_page_candidate_worker_count(len(rendered_paths))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(extract_one, idx, page_path): idx
            for idx, page_path in enumerate(rendered_paths, start=1)
        }
        for future in concurrent.futures.as_completed(futures):
            idx, width, height, detection, candidate_payload, detected_exam_title, usage = future.result()
            detections[idx - 1] = detection
            candidate_payloads[idx] = candidate_payload
            if usage:
                usage_payloads[idx] = dict(usage)
            if detected_exam_title:
                detected_exam_titles.append(detected_exam_title)

            row = page_rows_by_number.get(idx)
            if row is None:
                row = exam_repo.create_bulk_upload_page(
                    session,
                    bulk_upload_id=bulk.id,
                    page_number=idx,
                    image_path=str(rendered_paths[idx - 1]),
                    width=width,
                    height=height,
                    detected_student_name=detection.student_name,
                    detection_confidence=detection.confidence,
                    detection_evidence_json=json.dumps(detection.evidence or {}),
                )
            else:
                exam_repo.update_bulk_upload_page(
                    session,
                    row,
                    image_path=str(rendered_paths[idx - 1]),
                    width=width,
                    height=height,
                    detected_student_name=detection.student_name,
                    detection_confidence=detection.confidence,
                    detection_evidence_json=json.dumps(detection.evidence or {}),
                )

            processed_pages += 1
            if job:
                now = utcnow()
                current_metrics = job_metrics if job_metrics is not None else {}
                _record_front_page_usage_metrics(current_metrics, usage)
                job = exam_repo.update_exam_intake_job(
                    session,
                    job,
                    pages_processed=processed_pages,
                    updated_at=now,
                    last_progress_at=now,
                    lease_expires_at=_exam_intake_lease_deadline(),
                    metrics_json=json.dumps(current_metrics),
                )
                commit_repository_session(session)
            else:
                flush_repository_session(session)

    if candidate_payloads:
        from app.routers.submissions import apply_front_page_template_fill, build_front_page_consensus_template_from_candidates

        consensus_template = build_front_page_consensus_template_from_candidates(list(candidate_payloads.values()))
        if consensus_template:
            if grouped_template:
                merged_template = {**grouped_template, **consensus_template}
            else:
                merged_template = consensus_template
            exam_repo.update_exam(session, exam, front_page_template_json=json.dumps(merged_template))
            grouped_template = merged_template
            for page_number, candidate_payload in list(candidate_payloads.items()):
                candidate_payloads[page_number] = apply_front_page_template_fill(candidate_payload, grouped_template)

    if detected_exam_titles and not grouped_template:
        exam_repo.update_exam(session, exam, name=max(detected_exam_titles, key=len))
    if not job:
        commit_repository_session(session)
    return [item for item in detections if item is not None], candidate_payloads, usage_payloads


def _build_bulk_preview_from_detections(
    *,
    upload_files: list[UploadFile] | None = None,
    bulk: ExamBulkUploadFile,
    detections: list[BulkNameDetectionResult],
    roster: str | None,
) -> tuple[list[BulkUploadCandidate], list[str]]:
    roster_list: list[str] = []
    if roster:
        try:
            maybe_json = json.loads(roster)
            if isinstance(maybe_json, list):
                roster_list = [str(item).strip() for item in maybe_json if str(item).strip()]
        except json.JSONDecodeError:
            roster_list = [line.strip() for line in roster.splitlines() if line.strip()]

    if not bulk.stored_path and (
        (upload_files is not None and len(upload_files) > 1)
        or (upload_files is None and "uploaded images" in (bulk.original_filename or "").lower())
    ):
        return _segment_individual_image_candidates(detections)
    return _segment_bulk_candidates(detections, roster=roster_list, min_pages_per_student=1)


def _finalize_bulk_candidates(
    *,
    exam: Exam,
    bulk: ExamBulkUploadFile,
    candidates: list[BulkUploadCandidate],
    session: DbSession,
    prefilled_candidate_payloads: dict[int, FrontPageTotalsCandidateRead] | None = None,
    prefilled_usage_payloads: dict[int, dict[str, object]] | None = None,
) -> list[SubmissionRead]:
    pages = exam_repo.list_bulk_upload_pages(session, bulk.id)
    if not pages:
        raise HTTPException(status_code=400, detail="No rendered pages available")
    source_manifest_entries = _parse_bulk_source_manifest(bulk)
    page_map = {p.page_number: p for p in pages}
    max_page = pages[-1].page_number
    used_pages: set[int] = set()
    created: list[SubmissionRead] = []

    for candidate in candidates:
        if candidate.page_start < 1 or candidate.page_end > max_page or candidate.page_end < candidate.page_start:
            raise HTTPException(status_code=400, detail=f"Invalid page range for {candidate.student_name}")
        for page_num in range(candidate.page_start, candidate.page_end + 1):
            if page_num in used_pages:
                raise HTTPException(status_code=400, detail=f"Overlapping page range at page {page_num}")
            used_pages.add(page_num)

    prefilled_candidate_payloads = prefilled_candidate_payloads or {}
    prefilled_usage_payloads = prefilled_usage_payloads or {}

    for candidate in candidates:
        candidate_first_name, candidate_last_name = split_student_name(candidate.student_name)
        submission = submission_repo.create_submission(
            session,
            exam_id=exam.id or 0,
            student_name=compose_student_name(candidate_first_name, candidate_last_name),
            first_name=candidate_first_name,
            last_name=candidate_last_name,
            status=SubmissionStatus.UPLOADED,
            capture_mode=SubmissionCaptureMode.FRONT_PAGE_TOTALS,
        )
        if candidate.page_start == candidate.page_end:
            prefilled_candidate = prefilled_candidate_payloads.get(candidate.page_start)
            front_page_candidates_json: str | None = None
            front_page_usage_json: str | None = None
            if prefilled_candidate is not None and not _front_page_candidate_is_retryable_failure(prefilled_candidate):
                front_page_candidates_json = prefilled_candidate.model_dump_json()
            prefilled_usage = prefilled_usage_payloads.get(candidate.page_start)
            if prefilled_usage and prefilled_candidate is not None and not _front_page_candidate_is_retryable_failure(prefilled_candidate):
                front_page_usage_json = json.dumps(prefilled_usage)
            submission_repo.update_submission_front_page_data(
                session,
                submission,
                front_page_candidates_json=front_page_candidates_json,
                front_page_usage_json=front_page_usage_json,
            )

        page_reads = []
        submission_files: list[SubmissionFileRead] = []
        if bulk.stored_path:
            bulk_extension = Path(bulk.original_filename or "").suffix.lower()
            file_kind = "pdf" if bulk_extension == ".pdf" else "image"
            source_entry = source_manifest_entries[0] if source_manifest_entries else {}
            content_type = str(source_entry.get("content_type") or ("application/pdf" if file_kind == "pdf" else "image/jpeg"))
            file_row = submission_repo.create_submission_file(
                session,
                submission_id=submission.id,
                file_kind=file_kind,
                original_filename=bulk.original_filename,
                stored_path=str(source_entry.get("blob_pathname") or bulk.stored_path),
                blob_url=str(source_entry.get("blob_url") or ""),
                blob_pathname=str(source_entry.get("blob_pathname") or ""),
                content_type=content_type,
                size_bytes=int(source_entry.get("size_bytes") or 0),
            )
            submission_files.append(
                SubmissionFileRead(
                    id=file_row.id,
                    file_kind=file_row.file_kind,
                    original_filename=file_row.original_filename,
                    stored_path=file_row.stored_path,
                    blob_url=file_row.blob_url,
                    content_type=file_row.content_type,
                    size_bytes=file_row.size_bytes,
                )
            )

        for idx, page_num in enumerate(range(candidate.page_start, candidate.page_end + 1), start=1):
            src = page_map[page_num]
            if not bulk.stored_path:
                src_path = Path(src.image_path)
                source_entry = source_manifest_entries[page_num - 1] if page_num - 1 < len(source_manifest_entries) else {}
                file_row = submission_repo.create_submission_file(
                    session,
                    submission_id=submission.id,
                    file_kind="image",
                    original_filename=str(source_entry.get("original_filename") or src_path.name),
                    stored_path=str(source_entry.get("blob_pathname") or src_path),
                    blob_url=str(source_entry.get("blob_url") or ""),
                    blob_pathname=str(source_entry.get("blob_pathname") or ""),
                    content_type=str(source_entry.get("content_type") or "image/png"),
                    size_bytes=int(source_entry.get("size_bytes") or (src_path.stat().st_size if src_path.exists() else 0)),
                )
                submission_files.append(
                    SubmissionFileRead(
                        id=file_row.id,
                        file_kind=file_row.file_kind,
                        original_filename=file_row.original_filename,
                        stored_path=file_row.stored_path,
                        blob_url=file_row.blob_url,
                        content_type=file_row.content_type,
                        size_bytes=file_row.size_bytes,
                    )
                )
            src_path = Path(src.image_path)
            if src_path.exists():
                build_page_preview_image(src_path)
            sp = submission_repo.create_submission_page(
                session,
                submission_id=submission.id,
                page_number=idx,
                image_path=src.image_path,
                width=src.width,
                height=src.height,
            )
            page_reads.append(SubmissionPageRead(id=sp.id, page_number=idx, image_path=relative_to_data(Path(src.image_path)), width=src.width, height=src.height))
        created_first_name, created_last_name = submission_name_parts(submission.first_name, submission.last_name, submission.student_name)
        created.append(
            SubmissionRead(
                id=submission.id,
                exam_id=submission.exam_id,
                student_name=submission_display_name(submission.first_name, submission.last_name, submission.student_name),
                first_name=created_first_name,
                last_name=created_last_name,
                status=submission.status,
                capture_mode=submission.capture_mode,
                front_page_totals=front_page_totals_read(submission),
                created_at=submission.created_at,
                files=submission_files,
                pages=page_reads,
            )
        )
    return created


def _run_exam_intake_job_background(exam_id: int, job_id: int) -> None:
    lock = _get_exam_intake_runner_lock(exam_id)
    if not lock.acquire(blocking=False):
        return
    overall_started = time.perf_counter()
    stage_started = overall_started
    intake_metrics: dict[str, object] = {}
    runner_id = uuid.uuid4().hex
    try:
        with open_repository_session() as session:
            if not _claim_exam_intake_job(job_id, runner_id, session):
                return
            job = exam_repo.get_exam_intake_job(session, job_id)
            exam = _get_exam_or_404(exam_id, session, check_access=False)
            if not job or not exam:
                return
            bulk = exam_repo.get_exam_bulk_upload(session, job.bulk_upload_id) if job.bulk_upload_id else None
            if not bulk:
                failed_at = utcnow()
                intake_metrics["failed_stage"] = "missing_bulk"
                intake_metrics["total_ms"] = round((time.perf_counter() - overall_started) * 1000, 1)
                exam_repo.update_exam_intake_job(
                    session,
                    job,
                    status="failed",
                    stage="missing_bulk",
                    error_message="Bulk upload payload is missing.",
                    updated_at=failed_at,
                    last_progress_at=failed_at,
                    metrics_json=_job_metrics_payload(job, intake_metrics),
                    runner_id=runner_id,
                    lease_expires_at=None,
                    finished_at=failed_at,
                )
                commit_repository_session(session)
                return
            intake_metrics["front_page_thinking_level"] = _normalize_front_page_gemini_thinking_level(job.thinking_level)
            running_at = utcnow()
            exam_repo.update_exam_intake_job(
                session,
                job,
                status="running",
                stage="building_pages",
                updated_at=running_at,
                last_progress_at=running_at,
                runner_id=runner_id,
                lease_expires_at=_exam_intake_lease_deadline(),
                metrics_json=_job_metrics_payload(job, intake_metrics),
            )
            job = exam_repo.get_exam_intake_job(session, job_id) or job
            commit_repository_session(session)

            render_stage_started = time.perf_counter()
            rendered_paths, rendered_page_count = _render_stored_bulk_upload_files(bulk, _bulk_pages_dir(exam_id, bulk.id or 0))
            exam_repo.clear_bulk_upload_pages(session, bulk_upload_id=bulk.id)
            for idx, page_path in enumerate(rendered_paths, start=1):
                with Image.open(page_path) as image:
                    w, h = image.width, image.height
                exam_repo.create_bulk_upload_page(
                    session,
                    bulk_upload_id=bulk.id,
                    page_number=idx,
                    image_path=str(page_path),
                    width=w,
                    height=h,
                    detected_student_name=None,
                    detection_confidence=0.0,
                    detection_evidence_json="{}",
                )
            now = utcnow()
            intake_metrics["render_upload_ms"] = round((time.perf_counter() - render_stage_started) * 1000, 1)
            intake_metrics["page_count"] = rendered_page_count
            job = exam_repo.update_exam_intake_job(
                session,
                job,
                page_count=rendered_page_count,
                pages_built=rendered_page_count,
                updated_at=now,
                last_progress_at=now,
                lease_expires_at=_exam_intake_lease_deadline(),
                metrics_json=_job_metrics_payload(job, intake_metrics),
            )
            commit_repository_session(session)

            next_stage_at = utcnow()
            job = exam_repo.update_exam_intake_job(
                session,
                job,
                stage="detecting_names",
                updated_at=next_stage_at,
                last_progress_at=next_stage_at,
                lease_expires_at=_exam_intake_lease_deadline(),
                metrics_json=_job_metrics_payload(job, intake_metrics),
            )
            commit_repository_session(session)

            prefilled_candidate_payloads: dict[int, FrontPageTotalsCandidateRead] = {}
            prefilled_usage_payloads: dict[int, dict[str, object]] = {}
            if not bulk.stored_path:
                extracting_at = utcnow()
                job = exam_repo.update_exam_intake_job(
                    session,
                    job,
                    stage="extracting_front_pages",
                    updated_at=extracting_at,
                    last_progress_at=extracting_at,
                    lease_expires_at=_exam_intake_lease_deadline(),
                )
                commit_repository_session(session)
                detections, prefilled_candidate_payloads, prefilled_usage_payloads = _extract_image_upload_front_page_pages(
                    exam=exam,
                    bulk=bulk,
                    rendered_paths=rendered_paths,
                    session=session,
                    job=job,
                )
                intake_metrics["extracting_front_pages_ms"] = round((time.perf_counter() - stage_started) * 1000, 1)
            else:
                detections = _detect_bulk_pages(exam=exam, bulk=bulk, rendered_paths=rendered_paths, session=session, job=job)
                intake_metrics["detecting_names_ms"] = round((time.perf_counter() - stage_started) * 1000, 1)
            stage_started = time.perf_counter()
            creating_at = utcnow()
            job = exam_repo.update_exam_intake_job(
                session,
                job,
                stage="creating_submissions",
                updated_at=creating_at,
                lease_expires_at=_exam_intake_lease_deadline(),
                metrics_json=_job_metrics_payload(job, intake_metrics),
            )
            commit_repository_session(session)

            candidates, _warnings = _build_bulk_preview_from_detections(upload_files=None, bulk=bulk, detections=detections, roster=None)
            created = _finalize_bulk_candidates(
                exam=exam,
                bulk=bulk,
                candidates=candidates,
                session=session,
                prefilled_candidate_payloads=prefilled_candidate_payloads,
                prefilled_usage_payloads=prefilled_usage_payloads,
            )
            intake_metrics["creating_submissions_ms"] = round((time.perf_counter() - stage_started) * 1000, 1)
            intake_metrics["candidate_count"] = len(candidates)
            stage_started = time.perf_counter()
            warming_at = utcnow()
            job = exam_repo.update_exam_intake_job(
                session,
                job,
                submissions_created=len(created),
                candidates_ready=0,
                review_ready=False,
                stage="warming_initial_review",
                updated_at=warming_at,
                last_progress_at=warming_at,
                lease_expires_at=_exam_intake_lease_deadline(),
                metrics_json=_job_metrics_payload(job, intake_metrics),
            )
            exam = exam_repo.update_exam(session, exam, status=ExamStatus.DRAFT)
            commit_repository_session(session)

        created_submission_ids = [item.id for item in created if item.id is not None]
        initial_submission_ids, remaining_submission_ids, review_open_threshold = _split_initial_and_remaining_review_ids(created_submission_ids)
        failed_submission_ids: list[int] = []
        ready_submission_ids: set[int] = set()
        candidate_progress_lock = threading.Lock()

        with open_repository_session() as session:
            job = exam_repo.get_exam_intake_job(session, job_id)
            if job:
                now = utcnow()
                exam_repo.update_exam_intake_job(
                    session,
                    job,
                    review_open_threshold=review_open_threshold,
                    updated_at=now,
                    last_progress_at=now,
                    lease_expires_at=_exam_intake_lease_deadline(),
                    metrics_json=_job_metrics_payload(job, intake_metrics),
                )
                commit_repository_session(session)

        def record_candidate_progress(ready_submission_id: int) -> None:
            with candidate_progress_lock:
                ready_submission_ids.add(ready_submission_id)
                ready_count = len(ready_submission_ids)
            with open_repository_session() as progress_session:
                progress_job = exam_repo.get_exam_intake_job(progress_session, job_id)
                if not progress_job or progress_job.status != "running":
                    return
                now = utcnow()
                exam_repo.update_exam_intake_job(
                    progress_session,
                    progress_job,
                    candidates_ready=max(progress_job.candidates_ready, ready_count),
                    review_open_threshold=review_open_threshold,
                    updated_at=now,
                    last_progress_at=now,
                    lease_expires_at=_exam_intake_lease_deadline(),
                    metrics_json=_job_metrics_payload(progress_job, intake_metrics),
                )
                commit_repository_session(progress_session)

        def warm_submission_ids(submission_ids: list[int]) -> list[int]:
            if not submission_ids:
                return []

            def warm_one(submission_id: int) -> None:
                from app.routers.submissions import get_or_create_front_page_totals_candidates

                with open_repository_session() as submission_session:
                    get_or_create_front_page_totals_candidates(submission_id, submission_session)

            failed_ids: list[int] = []
            worker_count = _front_page_candidate_worker_count(len(submission_ids))
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {executor.submit(warm_one, submission_id): submission_id for submission_id in submission_ids}
                for future in concurrent.futures.as_completed(futures):
                    submission_id = futures[future]
                    try:
                        future.result()
                        record_candidate_progress(submission_id)
                    except Exception:
                        logger.exception("background front-page candidate warm failed for exam %s submission %s", exam_id, submission_id)
                        failed_ids.append(submission_id)
            return failed_ids

        initial_stage_started = stage_started
        initial_failed_submission_ids = warm_submission_ids(initial_submission_ids)
        intake_metrics["warming_initial_review_ms"] = round((time.perf_counter() - initial_stage_started) * 1000, 1)
        failed_submission_ids.extend(initial_failed_submission_ids)

        with open_repository_session() as session:
            initial_readiness_failures = _front_page_review_readiness_failures(initial_submission_ids, session)
        if initial_failed_submission_ids:
            initial_readiness_failures.extend(
                f"Front-page warm failed for submission {submission_id}."
                for submission_id in sorted(set(initial_failed_submission_ids))
            )

        if not initial_readiness_failures:
            _set_exam_initial_review_ready(
                exam_id,
                job_id,
                threshold=review_open_threshold,
                candidates_ready=len(ready_submission_ids),
                stage="warming_remaining_review" if remaining_submission_ids else "finalizing_review",
                metrics=intake_metrics,
            )

        remaining_stage_started = time.perf_counter()
        remaining_failed_submission_ids: list[int] = []
        if remaining_submission_ids and not initial_readiness_failures:
            remaining_failed_submission_ids = warm_submission_ids(remaining_submission_ids)
            failed_submission_ids.extend(remaining_failed_submission_ids)
        intake_metrics["warming_remaining_review_ms"] = round((time.perf_counter() - remaining_stage_started) * 1000, 1)
        intake_metrics["total_ms"] = round((time.perf_counter() - overall_started) * 1000, 1)

        with open_repository_session() as session:
            job = exam_repo.get_exam_intake_job(session, job_id)
            exam = _get_exam_or_404(exam_id, session, check_access=False)
            readiness_failures = list(initial_readiness_failures)
            ready_submission_count = len(ready_submission_ids)
            if not readiness_failures and remaining_submission_ids:
                readiness_failures = _front_page_review_readiness_failures(remaining_submission_ids, session)
            if not initial_readiness_failures and remaining_failed_submission_ids:
                readiness_failures.extend(
                    f"Front-page warm failed for submission {submission_id}."
                    for submission_id in sorted(set(remaining_failed_submission_ids))
                )
            if job:
                partial_ready = not initial_readiness_failures
                finished_at = utcnow()
                if readiness_failures:
                    intake_metrics["failed_stage"] = "warming_remaining_review" if partial_ready else "review_not_ready"
                exam_repo.update_exam_intake_job(
                    session,
                    job,
                    status="failed" if readiness_failures else "complete",
                    stage="partial_ready" if (partial_ready and readiness_failures) else ("review_not_ready" if readiness_failures else "complete"),
                    pages_processed=job.page_count,
                    pages_built=job.page_count,
                    candidates_ready=ready_submission_count,
                    review_open_threshold=review_open_threshold,
                    initial_review_ready=partial_ready,
                    fully_warmed=not readiness_failures,
                    review_ready=partial_ready,
                    updated_at=finished_at,
                    last_progress_at=finished_at,
                    runner_id=runner_id,
                    lease_expires_at=None,
                    finished_at=finished_at,
                    metrics_json=_job_metrics_payload(job, intake_metrics),
                    error_message="; ".join(readiness_failures) if readiness_failures else None,
                )
            if exam and exam.status != ExamStatus.READY:
                exam_repo.update_exam(
                    session,
                    exam,
                    status=ExamStatus.REVIEWING if partial_ready else (ExamStatus.FAILED if readiness_failures else ExamStatus.REVIEWING),
                )
            commit_repository_session(session)
            invalidate_exam_reporting_cache(exam_id)
        logger.info("exam intake finished exam=%s job=%s metrics=%s", exam_id, job_id, intake_metrics)
    except Exception as exc:
        logger.exception("exam intake job failed for exam %s job %s", exam_id, job_id)
        intake_metrics["failed_stage"] = intake_metrics.get("failed_stage") or "exception"
        intake_metrics["total_ms"] = round((time.perf_counter() - overall_started) * 1000, 1)
        with open_repository_session() as session:
            job = exam_repo.get_exam_intake_job(session, job_id)
            exam = _get_exam_or_404(exam_id, session, check_access=False)
            if job:
                partial_ready = bool(job.initial_review_ready or job.review_ready)
                failed_at = utcnow()
                exam_repo.update_exam_intake_job(
                    session,
                    job,
                    status="failed",
                    error_message=str(exc),
                    updated_at=failed_at,
                    last_progress_at=failed_at,
                    runner_id=runner_id,
                    lease_expires_at=None,
                    finished_at=failed_at,
                    fully_warmed=False,
                    review_ready=partial_ready,
                    metrics_json=_job_metrics_payload(job, intake_metrics),
                )
            if exam and exam.status != ExamStatus.READY:
                exam_repo.update_exam(
                    session,
                    exam,
                    status=ExamStatus.REVIEWING if (job and (job.initial_review_ready or job.review_ready)) else ExamStatus.FAILED,
                )
            commit_repository_session(session)
        logger.error("exam intake failed exam=%s job=%s metrics=%s", exam_id, job_id, intake_metrics)
    finally:
        lock.release()


class KeyPageBuildError(RuntimeError):
    """Raised when building key pages fails at a known stage."""

    def __init__(self, stage: str, cause: Exception):
        self.stage = stage
        super().__init__(str(cause))

_ALLOWED_TYPES = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
}

_ALLOWED_KEY_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
_ALLOWED_BULK_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
_MAX_RENDERED_KEY_PAGES = 10
_VERCEL_SERVER_UPLOAD_LIMIT_BYTES = 4 * 1024 * 1024


def _exam_key_pages_dir(exam_id: int) -> Path:
    return settings.data_path / "exams" / str(exam_id) / "key_pages"


def _load_key_page_images(exam_id: int, session: DbSession) -> list[Path]:
    rows = exam_repo.list_exam_key_pages(session, exam_id)
    if rows and all((row.blob_pathname or "").strip() for row in rows):
        return [Path(f"blob://{row.blob_pathname}") for row in rows]

    paths = [Path(row.image_path) for row in rows if Path(row.image_path).exists()]
    if paths:
        return paths

    legacy_dir = settings.data_path / "key_pages" / str(exam_id)
    if not legacy_dir.exists() or not legacy_dir.is_dir():
        return []
    return [path for path in sorted(legacy_dir.iterdir()) if path.suffix.lower() in {".png", ".jpg", ".jpeg"}]


def _upload_key_page_png(exam_id: int, page_number: int, png_path: Path) -> tuple[str, str]:
    upload = upload_rendered_key_page(exam_id=exam_id, page_number=page_number, local_png_path=png_path)
    fallback_pathname = f"exams/{exam_id}/key-pages/page_{page_number:04d}.png"
    blob_pathname = normalize_blob_path(str(upload.get("pathname") or fallback_pathname))
    blob_url = str(upload.get("url") or "") or blob_pathname
    return blob_pathname, blob_url



def _sanitize_filename(filename: str) -> str:
    cleaned = Path(filename or "upload.bin").name
    return cleaned.replace("/", "_").replace("\\", "_")


def _run_async(coro):
    return asyncio.run(coro)


def _resolve_signed_url(pathname: str) -> str:
    try:
        return _run_async(create_signed_blob_url(pathname))
    except Exception:
        return _run_async(get_storage_signed_url(pathname))




def _get_exam_question_or_404(exam_id: int, question_id: int, session: DbSession) -> Question:
    question = question_repo.get_exam_question(session, exam_id, question_id)
    if not question or question.exam_id != exam_id:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


def _normalize_to_png(input_path: Path, output_path: Path) -> tuple[int, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(input_path) as image:
        corrected = ImageOps.exif_transpose(image)
        rgb = corrected.convert("RGB")
        rgb.save(output_path, format="PNG")
        return rgb.width, rgb.height


def _render_pdf_pages(input_path: Path, output_dir: Path, start_page_number: int, max_pages: int) -> list[Path]:
    try:
        import fitz  # pymupdf
    except Exception as exc:
        raise HTTPException(status_code=400, detail="PDF render failed. Try uploading images.") from exc

    rendered_paths: list[Path] = []
    try:
        with fitz.open(input_path) as doc:
            page_count = doc.page_count
            if page_count > max_pages:
                raise HTTPException(status_code=400, detail=f"PDF has {page_count} pages; maximum supported is {max_pages}.")

            for index, page in enumerate(doc):
                output_path = output_dir / f"page_{start_page_number + index:04d}.png"
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                pixmap.save(str(output_path))
                rendered_paths.append(output_path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="PDF render failed. Try uploading images.") from exc

    return rendered_paths


def _render_bulk_pages(input_path: Path, output_dir: Path) -> list[Path]:
    extension = input_path.suffix.lower()
    if extension == ".pdf":
        return _render_pdf_pages(input_path, output_dir, start_page_number=1, max_pages=500)
    if extension in {".png", ".jpg", ".jpeg"}:
        output_path = output_dir / "page_0001.png"
        _normalize_to_png(input_path, output_path)
        return [output_path]
    raise HTTPException(status_code=400, detail="Bulk upload requires a PDF, PNG, or JPG file")


def _render_bulk_upload_files(files: list[UploadFile], output_dir: Path) -> tuple[list[Path], str, str]:
    if not files:
        raise HTTPException(status_code=400, detail="At least one bulk upload file is required")

    filenames = [_sanitize_filename(file.filename or f"bulk-upload-{index + 1}") for index, file in enumerate(files)]
    extensions = [Path(filename).suffix.lower() for filename in filenames]
    if any(extension not in _ALLOWED_BULK_EXTENSIONS for extension in extensions):
        raise HTTPException(status_code=400, detail="Bulk upload requires PDF, PNG, or JPG files")

    if any(extension == ".pdf" for extension in extensions):
        if len(files) != 1:
            raise HTTPException(status_code=400, detail="Upload one PDF or multiple images, not both")

        source_path = output_dir / filenames[0]
        payload = files[0].file.read()
        source_path.write_bytes(payload)
        return _render_bulk_pages(source_path, output_dir), filenames[0], source_path.name

    rendered_paths: list[Path] = []
    for index, (upload, filename) in enumerate(zip(files, filenames, strict=True), start=1):
        source_path = output_dir / f"source_{index:04d}{Path(filename).suffix.lower()}"
        source_path.write_bytes(upload.file.read())
        output_path = output_dir / f"page_{index:04d}.png"
        _normalize_to_png(source_path, output_path)
        rendered_paths.append(output_path)

    label = filenames[0] if len(filenames) == 1 else f"{len(filenames)} uploaded images"
    return rendered_paths, label, ""


def _bulk_upload_sources_dir(exam_id: int, bulk_upload_id: int) -> Path:
    return settings.data_path / "exams" / str(exam_id) / "bulk" / str(bulk_upload_id) / "source"


def _parse_bulk_source_manifest(bulk: ExamBulkUploadFile) -> list[dict[str, object]]:
    raw = (bulk.source_manifest_json or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("invalid bulk source manifest exam=%s bulk=%s", bulk.exam_id, bulk.id)
        return []
    if not isinstance(parsed, list):
        return []
    entries: list[dict[str, object]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        local_name = str(item.get("local_name") or "").strip()
        blob_pathname = str(item.get("blob_pathname") or "").strip()
        if not local_name or not blob_pathname:
            continue
        entries.append(
            {
                "local_name": local_name,
                "original_filename": str(item.get("original_filename") or local_name),
                "blob_pathname": blob_pathname,
                "blob_url": str(item.get("blob_url") or ""),
                "content_type": str(item.get("content_type") or "application/octet-stream"),
                "size_bytes": int(item.get("size_bytes") or 0),
            }
        )
    return entries


def _upload_bulk_source_file(*, exam_id: int, bulk_upload_id: int, local_name: str, payload: bytes, content_type: str) -> dict[str, str]:
    object_key = f"exams/{exam_id}/bulk/{bulk_upload_id}/source/{local_name}"
    return upload_bytes(object_key, payload, content_type)


def _persist_bulk_upload_sources(
    *,
    exam_id: int,
    bulk_upload_id: int,
    files: list[UploadFile],
    output_dir: Path,
) -> tuple[list[Path], str, str, int, list[dict[str, object]]]:
    if not files:
        raise HTTPException(status_code=400, detail="At least one bulk upload file is required")

    filenames = [_sanitize_filename(file.filename or f"bulk-upload-{index + 1}") for index, file in enumerate(files)]
    extensions = [Path(filename).suffix.lower() for filename in filenames]
    if any(extension not in _ALLOWED_BULK_EXTENSIONS for extension in extensions):
        raise HTTPException(status_code=400, detail="Bulk upload requires PDF, PNG, or JPG files")

    output_dir = reset_dir(output_dir)
    manifest: list[dict[str, object]] = []

    if any(extension == ".pdf" for extension in extensions):
        if len(files) != 1:
            raise HTTPException(status_code=400, detail="Upload one PDF or multiple images, not both")

        local_name = filenames[0]
        source_path = output_dir / local_name
        payload = files[0].file.read()
        source_path.write_bytes(payload)
        stored = _upload_bulk_source_file(
            exam_id=exam_id,
            bulk_upload_id=bulk_upload_id,
            local_name=local_name,
            payload=payload,
            content_type=files[0].content_type or "application/pdf",
        )
        manifest.append(
            {
                "local_name": local_name,
                "original_filename": filenames[0],
                "blob_pathname": str(stored.get("pathname") or ""),
                "blob_url": str(stored.get("url") or ""),
                "content_type": files[0].content_type or "application/pdf",
                "size_bytes": len(payload),
            }
        )
        return [source_path], filenames[0], source_path.name, 0, manifest

    stored_paths: list[Path] = []
    for index, (upload, filename) in enumerate(zip(files, filenames, strict=True), start=1):
        local_name = f"source_{index:04d}{Path(filename).suffix.lower()}"
        source_path = output_dir / local_name
        payload = upload.file.read()
        source_path.write_bytes(payload)
        stored = _upload_bulk_source_file(
            exam_id=exam_id,
            bulk_upload_id=bulk_upload_id,
            local_name=local_name,
            payload=payload,
            content_type=upload.content_type or "application/octet-stream",
        )
        manifest.append(
            {
                "local_name": local_name,
                "original_filename": filename,
                "blob_pathname": str(stored.get("pathname") or ""),
                "blob_url": str(stored.get("url") or ""),
                "content_type": upload.content_type or "application/octet-stream",
                "size_bytes": len(payload),
            }
        )
        stored_paths.append(source_path)

    label = filenames[0] if len(filenames) == 1 else f"{len(filenames)} uploaded images"
    return stored_paths, label, "", len(stored_paths), manifest


def _store_bulk_upload_files(files: list[UploadFile], output_dir: Path) -> tuple[list[Path], str, str, int]:
    if not files:
        raise HTTPException(status_code=400, detail="At least one bulk upload file is required")

    filenames = [_sanitize_filename(file.filename or f"bulk-upload-{index + 1}") for index, file in enumerate(files)]
    extensions = [Path(filename).suffix.lower() for filename in filenames]
    if any(extension not in _ALLOWED_BULK_EXTENSIONS for extension in extensions):
        raise HTTPException(status_code=400, detail="Bulk upload requires PDF, PNG, or JPG files")

    output_dir = reset_dir(output_dir)
    if any(extension == ".pdf" for extension in extensions):
        if len(files) != 1:
            raise HTTPException(status_code=400, detail="Upload one PDF or multiple images, not both")

        source_path = output_dir / filenames[0]
        payload = files[0].file.read()
        source_path.write_bytes(payload)
        return [source_path], filenames[0], source_path.name, 0

    stored_paths: list[Path] = []
    for index, (upload, filename) in enumerate(zip(files, filenames, strict=True), start=1):
        source_path = output_dir / f"source_{index:04d}{Path(filename).suffix.lower()}"
        source_path.write_bytes(upload.file.read())
        stored_paths.append(source_path)

    label = filenames[0] if len(filenames) == 1 else f"{len(filenames)} uploaded images"
    return stored_paths, label, "", len(stored_paths)


def _render_stored_bulk_upload_files(bulk: ExamBulkUploadFile, output_dir: Path) -> tuple[list[Path], int]:
    source_dir = _bulk_upload_sources_dir(bulk.exam_id, bulk.id or 0)
    if not source_dir.exists():
        manifest_entries = _parse_bulk_source_manifest(bulk)
        if manifest_entries:
            source_dir.mkdir(parents=True, exist_ok=True)
            for entry in manifest_entries:
                local_name = str(entry["local_name"])
                target = source_dir / local_name
                if target.exists():
                    continue
                content, _content_type = _run_async(download_blob_bytes(str(entry["blob_pathname"])))
                target.write_bytes(content)
    if not source_dir.exists():
        raise HTTPException(status_code=400, detail="Bulk upload source files are missing")

    if bulk.stored_path:
        source_path = source_dir / bulk.stored_path
        if not source_path.exists():
            raise HTTPException(status_code=400, detail="Bulk upload source file is missing")
        rendered = _render_bulk_pages(source_path, reset_dir(output_dir))
        return rendered, len(rendered)

    source_paths = sorted(path for path in source_dir.iterdir() if path.is_file())
    if not source_paths:
        raise HTTPException(status_code=400, detail="Bulk upload source files are missing")
    output_dir = reset_dir(output_dir)
    rendered_paths: list[Path] = []
    for index, source_path in enumerate(source_paths, start=1):
        output_path = output_dir / f"page_{index:04d}.png"
        _normalize_to_png(source_path, output_path)
        rendered_paths.append(output_path)
    return rendered_paths, len(rendered_paths)


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _delete_exam_resources(exam_id: int, session: DbSession) -> None:
    intake_lock = _get_exam_intake_runner_lock(exam_id)
    intake_lock.acquire()
    try:
        exam = _get_exam_or_404(exam_id, session)
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found")

        parse_job_ids = exam_repo.delete_exam_data(session, exam=exam)
        if db.engine is not None:
            commit_repository_session(session)

        _exam_question_locks.pop(exam_id, None)
        for job_id in parse_job_ids:
            if job_id is not None:
                _parse_job_runner_locks.pop(job_id, None)
        _exam_intake_runner_locks.pop(exam_id, None)

        _remove_tree(settings.data_path / "exams" / str(exam_id))
        _remove_tree(settings.data_path / "pages" / str(exam_id))
        _remove_tree(settings.data_path / "crops" / str(exam_id))
        _remove_tree(settings.data_path / "uploads" / str(exam_id))
        _remove_tree(settings.data_path / "cache" / "keys" / str(exam_id))
        _remove_tree(settings.data_path / "cache" / "key-pages" / str(exam_id))
        _remove_tree(settings.data_path / "objects" / "exams" / str(exam_id))
    finally:
        intake_lock.release()


def build_key_pages_for_exam(exam_id: int, session: DbSession) -> list[Path]:
    stage = "load_key_files"
    try:
        existing_rows = exam_repo.list_exam_key_pages(session, exam_id)
        if existing_rows:
            has_durable = all((row.blob_pathname or "").strip() for row in existing_rows)
            if has_durable:
                return [Path(f"blob://{row.blob_pathname}") for row in existing_rows]

            stage = "backfill_existing_rows"
            needs_commit = False
            for row in existing_rows:
                if (row.blob_pathname or "").strip():
                    continue
                local_path = Path(row.image_path)
                if not local_path.exists():
                    continue
                blob_pathname, blob_url = _upload_key_page_png(exam_id=exam_id, page_number=row.page_number, png_path=local_path)
                exam_repo.update_exam_key_page(
                    session,
                    row,
                    blob_pathname=blob_pathname,
                    blob_url=blob_url,
                )
                needs_commit = True

            if needs_commit:
                commit_repository_session(session)
                existing_rows = exam_repo.list_exam_key_pages(session, exam_id)
                if existing_rows and all((row.blob_pathname or "").strip() for row in existing_rows):
                    return [Path(f"blob://{row.blob_pathname}") for row in existing_rows]

        key_files = exam_repo.list_exam_key_files(session, exam_id)
        if not key_files:
            raise HTTPException(status_code=400, detail=f"No key files uploaded. Call /api/exams/{exam_id}/key/upload first.")

        output_dir = reset_dir(_exam_key_pages_dir(exam_id))
        exam_repo.clear_exam_key_pages(session, exam_id)

        created_paths: list[Path] = []
        page_num = 1

        for key_file in key_files:
            stage = "materialize_blob"
            source_path = _run_async(materialize_object_to_path(key_file.stored_path, settings.data_path / "cache" / "keys" / str(exam_id)))
            if not source_path.exists():
                continue

            extension = source_path.suffix.lower()
            if extension in {".png", ".jpg", ".jpeg"}:
                if page_num > _MAX_RENDERED_KEY_PAGES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Too many key pages; maximum supported is {_MAX_RENDERED_KEY_PAGES}.",
                    )
                out_path = output_dir / f"page_{page_num:04d}.png"
                stage = "write_pages"
                width, height = _normalize_to_png(source_path, out_path)
                stage = "upload_blob"
                blob_pathname, blob_url = _upload_key_page_png(exam_id=exam_id, page_number=page_num, png_path=out_path)
                exam_repo.create_exam_key_page(
                    session,
                    exam_id=exam_id,
                    page_number=page_num,
                    image_path=str(out_path),
                    blob_pathname=blob_pathname,
                    blob_url=blob_url,
                    width=width,
                    height=height,
                )
                created_paths.append(out_path)
                page_num += 1
                continue

            if extension == ".pdf":
                remaining_pages = _MAX_RENDERED_KEY_PAGES - (page_num - 1)
                if remaining_pages <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Too many key pages; maximum supported is {_MAX_RENDERED_KEY_PAGES}.",
                    )
                stage = "render_pdf"
                rendered_paths = _render_pdf_pages(
                    source_path,
                    output_dir,
                    start_page_number=page_num,
                    max_pages=remaining_pages,
                )
                for rendered in rendered_paths:
                    stage = "write_pages"
                    width, height = _normalize_to_png(rendered, rendered)
                    stage = "upload_blob"
                    blob_pathname, blob_url = _upload_key_page_png(exam_id=exam_id, page_number=page_num, png_path=rendered)
                    exam_repo.create_exam_key_page(
                        session,
                        exam_id=exam_id,
                        page_number=page_num,
                        image_path=str(rendered),
                        blob_pathname=blob_pathname,
                        blob_url=blob_url,
                        width=width,
                        height=height,
                    )
                    created_paths.append(rendered)
                    page_num += 1

        commit_repository_session(session)

        if not created_paths:
            raise HTTPException(
                status_code=400,
                detail="Key files exist, but key pages could not be produced. Upload png/jpg images or ensure PDF rendering support is available.",
            )

        return created_paths
    except HTTPException:
        raise
    except Exception as exc:
        raise KeyPageBuildError(stage=stage, cause=exc) from exc


def _validate_parse_payload(payload: dict[str, Any]) -> tuple[float, list[dict[str, Any]], list[str]]:
    warnings: list[str] = list(payload.get("warnings", [])) if isinstance(payload.get("warnings"), list) else []
    confidence = payload.get("confidence_score")
    questions = payload.get("questions")
    if not isinstance(confidence, (int, float)):
        raise ValueError("confidence_score missing or invalid")
    if confidence < 0 or confidence > 1:
        raise ValueError("confidence_score out of range")
    if not isinstance(questions, list):
        raise ValueError("questions missing or invalid")
    if not questions:
        warnings.append("No questions extracted; please review manually.")

    for question in questions:
        if not isinstance(question, dict):
            raise ValueError("question item must be object")
        if not str(question.get("label", "")).strip():
            raise ValueError("question label missing")
        if not isinstance(question.get("max_marks"), (int, float)):
            raise ValueError("question max_marks missing")
        if not isinstance(question.get("marks_confidence"), (int, float)):
            question["marks_confidence"] = 0.0
        if question.get("marks_source") not in {"explicit", "inferred", "unknown"}:
            question["marks_source"] = "unknown"

        objective_codes = question.get("objective_codes", [])
        if not isinstance(objective_codes, list):
            question["objective_codes"] = []
        else:
            question["objective_codes"] = [str(code).strip() for code in objective_codes if str(code).strip()]

        evidence = question.get("evidence", [])
        if not isinstance(evidence, list):
            question["evidence"] = []

    return float(confidence), questions, warnings


def _allowed_parse_models() -> list[str]:
    configured = os.getenv("SUPERMARKS_KEY_PARSE_MODELS", "gpt-5-nano,gpt-5-mini")
    models = [m.strip() for m in configured.split(",") if m.strip()]
    return models


def _resolve_models() -> tuple[str, str]:
    nano_override = os.getenv("SUPERMARKS_KEY_PARSE_NANO_MODEL", "").strip()
    mini_override = os.getenv("SUPERMARKS_KEY_PARSE_MINI_MODEL", "").strip()
    if nano_override and mini_override:
        return nano_override, mini_override

    allowed = _allowed_parse_models()
    expected = ["gpt-5-nano", "gpt-5-mini"]
    for model in expected:
        if model not in allowed:
            raise ValueError(f"Missing required model in allowlist: {model}")
    return expected[0], expected[1]




def _invoke_parser(parser: AnswerKeyParser, image_paths: list[Path], model: str, request_id: str) -> ParseResult:
    try:
        return parser.parse(image_paths, model=model, request_id=request_id)
    except TypeError:
        return parser.parse(image_paths, model=model)

@router.post("", response_model=ExamRead, status_code=status.HTTP_201_CREATED)
def create_exam(payload: ExamCreate, session: DbSession = Depends(get_repository_session)) -> Exam:
    exam_name = "Untitled Test"
    normalized_name = " ".join(str(payload.name or "").strip().split())
    exam: Exam | None = None
    if normalized_name:
        exam_name = normalized_name
    exam = exam_repo.create_exam(session, name=exam_name, owner_user_id=current_user_owner_id())
    commit_repository_session(session)
    return exam


def _normalized_exam_name(value: str | None) -> str:
    normalized_name = " ".join(str(value or "").strip().split())
    return normalized_name or "Untitled Test"


def _enqueue_intake_job_for_exam(
    *,
    exam: Exam,
    upload_files: list[UploadFile],
    selected_class_list_id: int | None,
    class_list_upload_files: list[UploadFile] | None,
    front_page_thinking_level: str | None,
    session: DbSession,
    cleanup_root: Path | None = None,
) -> ExamIntakeJob:
    if not upload_files:
        raise HTTPException(status_code=400, detail="At least one bulk upload file is required")

    normalized_thinking_level = _normalize_front_page_gemini_thinking_level(front_page_thinking_level)
    if exam.id is None:
        exam = exam_repo.create_exam(session, name=_normalized_exam_name(exam.name), owner_user_id=current_user_owner_id())
    if selected_class_list_id:
        class_list = _get_class_list_or_404(selected_class_list_id, session)
        _select_class_list_for_exam(exam=exam, class_list=class_list, session=session)
    elif class_list_upload_files:
        class_list_names, class_list_filenames = _extract_class_list_names_from_uploads(
            storage_dir=settings.data_path / "exams" / str(exam.id or 0) / "class-lists" / uuid.uuid4().hex,
            upload_files=class_list_upload_files,
        )
        if class_list_names:
            _persist_exam_class_list(
                exam=exam,
                names=class_list_names,
                source="uploaded_files",
                filenames=class_list_filenames,
                session=session,
            )

    bulk = exam_repo.create_exam_bulk_upload(session, exam_id=exam.id or 0, original_filename="bulk-upload", stored_path="")

    source_dir = _bulk_upload_sources_dir(exam.id or 0, bulk.id or 0)
    try:
        store_started = time.perf_counter()
        _stored_paths, filename, stored_path, page_count, source_manifest = _persist_bulk_upload_sources(
            exam_id=exam.id or 0,
            bulk_upload_id=bulk.id or 0,
            files=upload_files,
            output_dir=source_dir,
        )
        store_upload_ms = round((time.perf_counter() - store_started) * 1000, 1)

        exam_repo.update_exam_bulk_upload(
            session,
            bulk=bulk,
            original_filename=filename,
            stored_path=stored_path,
            source_manifest_json=json.dumps(source_manifest),
        )

        exam_repo.update_exam(session, exam, status=ExamStatus.DRAFT)

        job = exam_repo.create_exam_intake_job(
            session,
            exam_id=exam.id or 0,
            bulk_upload_id=bulk.id,
            status="queued",
            stage="queued",
            page_count=page_count,
            pages_built=0,
            pages_processed=0,
            submissions_created=0,
            candidates_ready=0,
            review_open_threshold=0,
            initial_review_ready=False,
            fully_warmed=False,
            review_ready=False,
            thinking_level=normalized_thinking_level,
            last_progress_at=utcnow(),
            metrics_json=json.dumps({
                "store_upload_ms": store_upload_ms,
                "page_count": page_count,
                "front_page_thinking_level": normalized_thinking_level,
                "class_list_count": len(_exam_known_student_names(exam)),
            }),
        )
        commit_repository_session(session)
        return job
    except Exception:
        rollback_repository_session(session)
        if cleanup_root is not None:
            _remove_tree(cleanup_root)
        raise


@router.post("/intake", response_model=ExamRead, status_code=status.HTTP_201_CREATED)
def create_exam_with_intake(
    name: str | None = Form(default=""),
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
    class_list_id: int | None = Form(default=None),
    class_list_files: list[UploadFile] | None = File(default=None),
    class_list_file: UploadFile | None = File(default=None),
    front_page_thinking_level: str | None = Form(default=None),
    session: DbSession = Depends(get_repository_session),
) -> ExamRead:
    upload_files = list(files or [])
    if file is not None:
        upload_files.append(file)
    class_list_upload_files = list(class_list_files or [])
    if class_list_file is not None:
        class_list_upload_files.append(class_list_file)

    exam = exam_repo.create_exam(session, name=_normalized_exam_name(name), owner_user_id=current_user_owner_id())
    job = _enqueue_intake_job_for_exam(
        exam=exam,
        upload_files=upload_files,
        selected_class_list_id=class_list_id,
        class_list_upload_files=class_list_upload_files,
        front_page_thinking_level=front_page_thinking_level,
        session=session,
        cleanup_root=settings.data_path / "exams" / str(exam.id or 0),
    )
    if job.id is not None:
        _spawn_exam_intake_job_thread(job.id)
    return _exam_read(exam, latest_intake_job=job)


@router.post("/{exam_id}/intake-jobs/start", response_model=ExamIntakeJobRead, status_code=status.HTTP_202_ACCEPTED)
def start_exam_intake_job(
    exam_id: int,
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
    class_list_id: int | None = Form(default=None),
    class_list_files: list[UploadFile] | None = File(default=None),
    class_list_file: UploadFile | None = File(default=None),
    front_page_thinking_level: str | None = Form(default=None),
    session: DbSession = Depends(get_repository_session),
) -> ExamIntakeJobRead:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    latest_job = _latest_exam_intake_job(exam_id, session)
    if latest_job and latest_job.status in {"queued", "running"}:
        return _exam_intake_job_read(latest_job)

    upload_files = list(files or [])
    if file is not None:
        upload_files.append(file)
    class_list_upload_files = list(class_list_files or [])
    if class_list_file is not None:
        class_list_upload_files.append(class_list_file)
    if not upload_files:
        raise HTTPException(status_code=400, detail="At least one bulk upload file is required")
    _remove_tree(settings.data_path / "exams" / str(exam_id) / "bulk")
    _remove_tree(settings.data_path / "uploads" / str(exam_id))
    job = _enqueue_intake_job_for_exam(
        exam=exam,
        upload_files=upload_files,
        selected_class_list_id=class_list_id,
        class_list_upload_files=class_list_upload_files,
        front_page_thinking_level=front_page_thinking_level,
        session=session,
        cleanup_root=settings.data_path / "exams" / str(exam_id) / "bulk",
    )

    if job.id is not None:
        _spawn_exam_intake_job_thread(job.id)
    return _exam_intake_job_read(job)


@router.get("/{exam_id}/intake-jobs/latest", response_model=ExamIntakeJobRead | None)
def get_latest_exam_intake_job(exam_id: int, session: DbSession = Depends(get_repository_session)) -> ExamIntakeJobRead | None:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    return _exam_intake_job_read(_latest_exam_intake_job(exam_id, session))


@router.post("/{exam_id}/class-list/upload", response_model=ExamRead)
def upload_exam_class_list(
    exam_id: int,
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
    session: DbSession = Depends(get_repository_session),
) -> ExamRead:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    upload_files = list(files or [])
    if file is not None:
        upload_files.append(file)
    if not upload_files:
        raise HTTPException(status_code=400, detail="At least one class list file is required")

    names, filenames = _extract_class_list_names_from_uploads(
        storage_dir=settings.data_path / "exams" / str(exam_id) / "class-lists" / uuid.uuid4().hex,
        upload_files=upload_files,
    )
    if not names:
        raise HTTPException(status_code=400, detail="No student names could be extracted from the class list files")
    _persist_exam_class_list(exam=exam, names=names, source="uploaded_files", filenames=filenames, session=session)
    _invalidate_exam_front_page_candidate_cache_for_class_list(exam_id, session)
    commit_repository_session(session)
    return _exam_read(exam)


@router.post("/{exam_id}/class-list/from-confirmed", response_model=ExamRead)
def create_exam_class_list_from_confirmed_names(exam_id: int, session: DbSession = Depends(get_repository_session)) -> ExamRead:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    submissions = exam_repo.list_exam_submissions(session, exam_id)
    names = [
        submission_display_name(submission.first_name, submission.last_name, submission.student_name)
        for submission in submissions
        if front_page_totals_read(submission)
        and front_page_totals_read(submission).confirmed
        and submission_display_name(submission.first_name, submission.last_name, submission.student_name).strip()
    ]
    if not names:
        raise HTTPException(status_code=400, detail="No confirmed student names are available yet")
    _persist_exam_class_list(exam=exam, names=names, source="confirmed_names", filenames=[], session=session)
    _invalidate_exam_front_page_candidate_cache_for_class_list(exam_id, session)
    commit_repository_session(session)
    return _exam_read(exam)


@class_lists_router.get("", response_model=list[ClassListRead])
def list_class_lists(session: DbSession = Depends(get_repository_session)) -> list[ClassListRead]:
    class_lists = exam_repo.list_class_lists(session, owner_user_id=current_user_owner_id())
    return [payload for item in class_lists if (payload := _class_list_resource_read(item)) is not None]


@class_lists_router.post("/upload", response_model=ClassListRead, status_code=status.HTTP_201_CREATED)
def create_class_list_from_uploads(
    name: str | None = Form(default=""),
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
    session: DbSession = Depends(get_repository_session),
) -> ClassListRead:
    upload_files = list(files or [])
    if file is not None:
        upload_files.append(file)
    if not upload_files:
        raise HTTPException(status_code=400, detail="At least one class list file is required")

    names, filenames = _extract_class_list_names_from_uploads(
        storage_dir=settings.data_path / "class-lists" / uuid.uuid4().hex,
        upload_files=upload_files,
    )
    if not names:
        raise HTTPException(status_code=400, detail="No student names could be extracted from the class list files")
    class_list = _create_class_list_resource(
        name=_normalized_class_list_name(name, filenames=filenames),
        names=names,
        source="uploaded_files",
        filenames=filenames,
        session=session,
    )
    commit_repository_session(session)
    payload = _class_list_resource_read(class_list)
    if not payload:
        raise HTTPException(status_code=500, detail="Class list could not be read after save")
    return payload


@class_lists_router.post("/from-exam/{exam_id}", response_model=ClassListRead, status_code=status.HTTP_201_CREATED)
def create_class_list_from_exam(
    exam_id: int,
    name: str | None = Form(default=""),
    names_json: str | None = Form(default=""),
    session: DbSession = Depends(get_repository_session),
) -> ClassListRead:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    submissions = exam_repo.list_exam_submissions(session, exam_id)
    names = [
        submission_display_name(submission.first_name, submission.last_name, submission.student_name)
        for submission in submissions
        if front_page_totals_read(submission)
        and front_page_totals_read(submission).confirmed
        and submission_display_name(submission.first_name, submission.last_name, submission.student_name).strip()
    ]
    normalized_names = normalize_class_list_names(names)
    provided_names_payload = (names_json or "").strip()
    if provided_names_payload:
        try:
            parsed_names = json.loads(provided_names_payload)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Edited class list names were invalid") from exc
        if not isinstance(parsed_names, list):
            raise HTTPException(status_code=400, detail="Edited class list names must be a list")
        normalized_names = normalize_class_list_names([str(item).strip() for item in parsed_names if str(item).strip()])
    if not normalized_names:
        raise HTTPException(status_code=400, detail="No checked student names are available yet")

    class_list = _create_class_list_resource(
        name=_normalized_class_list_name(name, exam_name=exam.name),
        names=normalized_names,
        source="confirmed_names_reviewed" if provided_names_payload else "confirmed_names",
        filenames=[],
        session=session,
    )
    _select_class_list_for_exam(exam=exam, class_list=class_list, session=session)
    commit_repository_session(session)
    payload = _class_list_resource_read(class_list)
    if not payload:
        raise HTTPException(status_code=500, detail="Class list could not be read after save")
    return payload


@class_lists_router.post("/{class_list_id}/append-names", response_model=ClassListRead)
def append_names_to_class_list(
    class_list_id: int,
    names_json: str | None = Form(default=""),
    exam_id: int | None = Form(default=None),
    session: DbSession = Depends(get_repository_session),
) -> ClassListRead:
    class_list = _get_class_list_or_404(class_list_id, session)
    if not class_list:
        raise HTTPException(status_code=404, detail="Class list not found")

    payload = (names_json or "").strip()
    if not payload:
        raise HTTPException(status_code=400, detail="Student names are required")
    try:
        parsed_names = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Student names were invalid") from exc
    if not isinstance(parsed_names, list):
        raise HTTPException(status_code=400, detail="Student names must be a list")

    normalized_names = normalize_class_list_names([str(item).strip() for item in parsed_names if str(item).strip()])
    if not normalized_names:
        raise HTTPException(status_code=400, detail="No student names were provided")

    _update_class_list_resource_names(
        class_list=class_list,
        names=normalized_names,
        source="manual_update",
        session=session,
    )

    if exam_id is not None:
        exam = _get_exam_or_404(exam_id, session)
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found")
        exam_class_list = _class_list_read(exam)
        if not exam_class_list or exam_class_list.id != class_list_id:
            raise HTTPException(status_code=400, detail="This exam is not using the selected class list")
        _select_class_list_for_exam(exam=exam, class_list=class_list, session=session)
        _invalidate_exam_front_page_candidate_cache_for_class_list(exam_id, session)

    commit_repository_session(session)
    updated_payload = _class_list_resource_read(class_list)
    if not updated_payload:
        raise HTTPException(status_code=500, detail="Class list could not be read after update")
    return updated_payload


@class_lists_router.delete("/{class_list_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_class_list(class_list_id: int, session: DbSession = Depends(get_repository_session)) -> Response:
    class_list = _get_class_list_or_404(class_list_id, session)
    if not class_list:
        raise HTTPException(status_code=404, detail="Class list not found")
    exam_repo.delete_class_list(session, class_list=class_list)
    commit_repository_session(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{exam_id}/intake-jobs/retry", response_model=ExamIntakeJobRead, status_code=status.HTTP_202_ACCEPTED)
def retry_exam_intake_job(exam_id: int, session: DbSession = Depends(get_repository_session)) -> ExamIntakeJobRead:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    latest_job = _latest_exam_intake_job(exam_id, session)
    if not latest_job or not latest_job.bulk_upload_id:
        raise HTTPException(status_code=400, detail="No intake job available to retry")
    if latest_job.status in {"queued", "running"}:
        return _exam_intake_job_read(latest_job)

    bulk = exam_repo.get_exam_bulk_upload(session, latest_job.bulk_upload_id)
    if not bulk:
        raise HTTPException(status_code=400, detail="Bulk upload payload is missing")

    retry_job = exam_repo.create_exam_intake_job(
        session,
        exam_id=exam_id,
        bulk_upload_id=bulk.id,
        status="queued",
        stage="queued",
        page_count=latest_job.page_count,
        pages_built=latest_job.pages_built,
        pages_processed=0,
        submissions_created=0,
        candidates_ready=0,
        review_open_threshold=latest_job.review_open_threshold,
        initial_review_ready=False,
        fully_warmed=False,
        review_ready=False,
        thinking_level=_normalize_front_page_gemini_thinking_level(latest_job.thinking_level),
        last_progress_at=utcnow(),
        metrics_json=json.dumps({
            "front_page_thinking_level": _normalize_front_page_gemini_thinking_level(latest_job.thinking_level),
        }),
    )
    exam_repo.update_exam(session, exam, status=ExamStatus.DRAFT)
    commit_repository_session(session)
    if retry_job.id is not None:
        _spawn_exam_intake_job_thread(retry_job.id)
    return _exam_intake_job_read(retry_job)


def _exam_read(exam: Exam, latest_intake_job: ExamIntakeJob | None = None) -> ExamRead:
    if latest_intake_job is None and exam.id:
        with open_repository_session() as session:
            latest_intake_job = _latest_exam_intake_job(exam.id or 0, session)
    effective_status = exam.status
    if latest_intake_job and latest_intake_job.initial_review_ready and effective_status != ExamStatus.READY:
        effective_status = ExamStatus.REVIEWING
    return ExamRead(
        id=exam.id,
        name=exam.name,
        created_at=exam.created_at,
        teacher_style_profile_json=exam.teacher_style_profile_json,
        status=effective_status,
        class_list=_class_list_read(exam),
        intake_job=_exam_intake_job_read(latest_intake_job),
    )


def _list_exam_key_files_read(exam_id: int, session: DbSession) -> list[StoredFileRead]:
    key_files = exam_repo.list_exam_key_files(session, exam_id)
    return [
        StoredFileRead(
            id=row.id,
            original_filename=row.original_filename,
            stored_path=row.stored_path,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            signed_url=_resolve_signed_url(row.stored_path),
            blob_url=row.blob_url,
        )
        for row in key_files
    ]


def _list_exam_submissions_read(exam_id: int, session: DbSession) -> list[SubmissionRead]:
    submissions = exam_repo.list_exam_submissions(session, exam_id)
    if not submissions:
        return []

    submission_ids = [submission.id for submission in submissions if submission.id is not None]
    files = submission_repo.list_submission_files_for_submission_ids(session, submission_ids)
    pages = submission_repo.list_submission_pages_for_submission_ids(session, submission_ids)

    files_by_submission_id: dict[int, list[SubmissionFile]] = {}
    for file_row in files:
        files_by_submission_id.setdefault(file_row.submission_id, []).append(file_row)

    pages_by_submission_id: dict[int, list[SubmissionPage]] = {}
    for page_row in pages:
        pages_by_submission_id.setdefault(page_row.submission_id, []).append(page_row)

    output: list[SubmissionRead] = []
    for sub in submissions:
        submission_files = files_by_submission_id.get(sub.id or 0, [])
        submission_pages = pages_by_submission_id.get(sub.id or 0, [])
        first_name, last_name = submission_name_parts(sub.first_name, sub.last_name, sub.student_name)
        output.append(
            SubmissionRead(
                id=sub.id,
                exam_id=sub.exam_id,
                student_name=submission_display_name(sub.first_name, sub.last_name, sub.student_name),
                first_name=first_name,
                last_name=last_name,
                status=sub.status,
                capture_mode=sub.capture_mode,
                front_page_totals=front_page_totals_read(sub),
                created_at=sub.created_at,
                files=[SubmissionFileRead(id=f.id, file_kind=f.file_kind, original_filename=f.original_filename, stored_path=f.stored_path, blob_url=f.blob_url, content_type=f.content_type, size_bytes=f.size_bytes) for f in submission_files],
                pages=[SubmissionPageRead(id=p.id, page_number=p.page_number, image_path=relative_to_data(Path(p.image_path)), width=p.width, height=p.height) for p in submission_pages],
            )
        )
    return output


@router.get("", response_model=list[ExamRead])
def list_exams(session: DbSession = Depends(get_repository_session)) -> list[Exam]:
    exams = exam_repo.list_exams(session, owner_user_id=current_user_owner_id())
    latest_jobs = _latest_exam_intake_jobs_by_exam_id([exam.id for exam in exams if exam.id is not None], session)
    return [_exam_read(exam, latest_jobs.get(exam.id or 0)) for exam in exams]


@router.get("/{exam_id}", response_model=ExamDetail)
def get_exam(exam_id: int, session: DbSession = Depends(get_repository_session)) -> ExamDetail:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    parse_jobs = exam_repo.list_exam_parse_jobs(session, exam_id)

    return ExamDetail(
        exam=_exam_read(exam),
        key_files=_list_exam_key_files_read(exam_id, session),
        submissions=_list_exam_submissions_read(exam_id, session),
        parse_jobs=[
            ExamParseJobRead(
                id=job.id,
                exam_id=job.exam_id,
                status=job.status,
                page_count=job.page_count,
                pages_done=job.pages_done,
                created_at=job.created_at,
                updated_at=job.updated_at,
                cost_total=job.cost_total,
                input_tokens_total=job.input_tokens_total,
                output_tokens_total=job.output_tokens_total,
            )
            for job in parse_jobs
        ],
    )


@router.delete("/{exam_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_exam(exam_id: int, session: DbSession = Depends(get_repository_session)) -> Response:
    _delete_exam_resources(exam_id, session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{exam_id}/submissions", response_model=list[SubmissionRead])
def list_exam_submissions(exam_id: int, session: DbSession = Depends(get_repository_session)) -> list[SubmissionRead]:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    return _list_exam_submissions_read(exam_id, session)


@router.get("/{exam_id}/front-page-usage", response_model=FrontPageUsageReportRead)
def get_exam_front_page_usage(exam_id: int, session: DbSession = Depends(get_repository_session)) -> FrontPageUsageReportRead:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    submissions = submission_repo.list_exam_front_page_total_submissions(session, exam_id)

    entries: list[FrontPageUsageEntryRead] = []
    prompt_tokens = 0
    output_tokens = 0
    thought_tokens = 0
    total_tokens = 0
    estimated_cost_usd = 0.0

    for submission in submissions:
        usage = _front_page_usage_payload(submission.front_page_usage_json)
        if not usage or submission.id is None:
            continue
        prompt_value = int(usage.get("prompt_tokens") or 0)
        output_value = int(usage.get("candidate_tokens") or 0)
        thought_value = int(usage.get("thought_tokens") or 0)
        total_value = int(usage.get("total_tokens") or 0)
        cost_value = float(usage.get("estimated_cost_usd") or 0.0)

        prompt_tokens += prompt_value
        output_tokens += output_value
        thought_tokens += thought_value
        total_tokens += total_value
        estimated_cost_usd += cost_value

        entries.append(
            FrontPageUsageEntryRead(
                submission_id=submission.id,
                student_name=submission_display_name(submission.first_name, submission.last_name, submission.student_name),
                provider=str(usage.get("provider") or ""),
                model=str(usage.get("model") or ""),
                thinking_level=str(usage.get("thinking_level") or ""),
                thinking_budget=int(usage.get("thinking_budget") or 0),
                prompt_tokens=prompt_value,
                output_tokens=output_value,
                thought_tokens=thought_value,
                total_tokens=total_value,
                estimated_cost_usd=round(cost_value, 6),
                normalized_image_width=int(usage.get("normalized_image_width") or 0),
                normalized_image_height=int(usage.get("normalized_image_height") or 0),
                normalized_image_bytes=int(usage.get("normalized_image_bytes") or 0),
            )
        )

    entry_count = len(entries)
    return FrontPageUsageReportRead(
        exam_id=exam_id,
        exam_name=exam.name,
        entry_count=entry_count,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        thought_tokens=thought_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=round(estimated_cost_usd, 6),
        avg_tokens_per_image=round(total_tokens / entry_count, 1) if entry_count else 0.0,
        avg_cost_per_image_usd=round(estimated_cost_usd / entry_count, 6) if entry_count else 0.0,
        entries=entries,
    )

@router.get("/{exam_id}/workspace-bootstrap", response_model=ExamWorkspaceBootstrapResponse)
def get_exam_workspace_bootstrap(exam_id: int, session: DbSession = Depends(get_repository_session)) -> ExamWorkspaceBootstrapResponse:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    latest_parse = get_latest_parse_job(exam_id, session)
    latest_parse_status = None
    latest_job = latest_parse.get("job") if isinstance(latest_parse, dict) else None
    if isinstance(latest_job, dict) and latest_job.get("job_id"):
        latest_parse_status = get_answer_key_parse_status(exam_id=exam_id, job_id=int(latest_job["job_id"]), session=session)

    return ExamWorkspaceBootstrapResponse(
        exam=_exam_read(exam),
        questions=list_questions(exam_id, session),
        key_files=_list_exam_key_files_read(exam_id, session),
        submissions=_list_exam_submissions_read(exam_id, session),
        marking_dashboard=build_exam_marking_dashboard_response(exam_id, session) or ExamMarkingDashboardResponse(
            exam_id=exam_id,
            exam_name=exam.name,
            total_possible=0,
            completion={"total_submissions": 0, "ready_count": 0, "blocked_count": 0, "in_progress_count": 0, "complete_count": 0, "completion_percent": 0},
        ),
        latest_parse=latest_parse,
        latest_parse_status=latest_parse_status,
    )


def _write_csv_export(buffer: StringIO, export_spec: CsvExportSpec[CsvExportRow]) -> None:
    write_csv_export(buffer, export_spec)


def _attachment_headers(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


def _export_attachment_response(*, content: str | bytes, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers=_attachment_headers(filename),
    )


def _csv_export_response(artifact) -> Response:
    buffer = StringIO()
    _write_csv_export(buffer, artifact.export_spec)
    return _export_attachment_response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        filename=artifact.filename,
    )


@router.get(
    "/{exam_id}/marking-dashboard",
    response_model=ExamMarkingDashboardResponse,
    response_model_exclude_unset=True,
)
def get_exam_marking_dashboard(exam_id: int, session: DbSession = Depends(get_repository_session)) -> ExamMarkingDashboardResponse:
    dashboard = build_exam_marking_dashboard_response(exam_id, session)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Exam not found")
    return dashboard


@router.get("/{exam_id}/export.csv")
def export_exam_marks_csv(exam_id: int, session: DbSession = Depends(get_repository_session)) -> Response:
    artifact = build_exam_marks_export_artifact(exam_id, session)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Exam not found")

    return _csv_export_response(artifact)


@router.get("/{exam_id}/export.xlsx")
def export_exam_gradebook_xlsx(exam_id: int, session: DbSession = Depends(get_repository_session)) -> Response:
    artifact = build_exam_gradebook_xlsx_artifact(exam_id, session)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Exam not found")

    filename, content = artifact
    return _export_attachment_response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


@router.get("/{exam_id}/export-summary.csv")
def export_exam_summary_csv(exam_id: int, session: DbSession = Depends(get_repository_session)) -> Response:
    artifact = build_exam_summary_export_artifact(exam_id, session)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Exam not found")

    return _csv_export_response(artifact)


@router.get("/{exam_id}/export-objectives-summary.csv")
def export_exam_objectives_summary_csv(exam_id: int, session: DbSession = Depends(get_repository_session)) -> Response:
    artifact = build_exam_objectives_summary_export_artifact(exam_id, session)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Exam not found")

    return _csv_export_response(artifact)

@router.get("/{exam_id}/export-student-summaries.zip")
def export_exam_student_summaries_zip(exam_id: int, session: DbSession = Depends(get_repository_session)) -> Response:
    artifact = build_exam_student_summaries_zip_export_artifact(exam_id, session)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Exam not found")

    return _export_attachment_response(
        content=build_zip_export_content(artifact.artifact_specs),
        media_type="application/zip",
        filename=artifact.filename,
    )



@router.post("/{exam_id}/submissions", response_model=SubmissionRead, status_code=status.HTTP_201_CREATED)
async def create_submission(
    exam_id: int,
    request: Request,
    session: DbSession = Depends(get_repository_session),
) -> SubmissionRead:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    student_name = ""
    files: list[UploadFile] = []
    content_type = request.headers.get("content-type", "")
    capture_mode = SubmissionCaptureMode.QUESTION_LEVEL
    if "application/json" in content_type:
        payload = await request.json()
        student_name = normalize_student_name(str(payload.get("student_name", ""))) if isinstance(payload, dict) else ""
        requested_mode = str(payload.get("capture_mode", SubmissionCaptureMode.QUESTION_LEVEL.value)).strip()
    else:
        form = await request.form()
        student_name = normalize_student_name(str(form.get("student_name", "")))
        requested_mode = str(form.get("capture_mode", SubmissionCaptureMode.QUESTION_LEVEL.value)).strip()
        files = [item for item in form.getlist("files") if hasattr(item, "filename") and hasattr(item, "file")]

    if requested_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS.value:
        capture_mode = SubmissionCaptureMode.FRONT_PAGE_TOTALS
    elif requested_mode != SubmissionCaptureMode.QUESTION_LEVEL.value:
        raise HTTPException(status_code=400, detail="Unsupported capture_mode")

    if not student_name:
        raise HTTPException(status_code=400, detail="student_name is required")

    first_name, last_name = split_student_name(student_name)
    submission = submission_repo.create_submission(
        session,
        exam_id=exam_id,
        student_name=compose_student_name(first_name, last_name),
        first_name=first_name,
        last_name=last_name,
        status=SubmissionStatus.UPLOADED,
        capture_mode=capture_mode,
    )
    commit_repository_session(session)
    invalidate_exam_reporting_cache(exam_id)

    created_files: list[SubmissionFileRead] = []
    if files:
        kinds = [_ALLOWED_TYPES.get(f.content_type or "") for f in files]
        if any(kind is None for kind in kinds):
            raise HTTPException(status_code=400, detail="Unsupported file type. Use pdf/png/jpg/jpeg")
        if "pdf" in kinds and len(files) > 1:
            raise HTTPException(status_code=400, detail="Upload one PDF OR multiple images, not mixed")

        storage = get_storage_provider()
        max_size = settings.max_upload_mb * 1024 * 1024
        for upload, kind in zip(files, kinds, strict=True):
            upload.file.seek(0, 2)
            size = upload.file.tell()
            upload.file.seek(0)
            if size > max_size:
                raise HTTPException(status_code=400, detail=f"File {upload.filename} exceeds {settings.max_upload_mb}MB")
            filename = _sanitize_filename(upload.filename or "upload.bin")
            upload_content_type = upload.content_type or "application/octet-stream"
            payload = upload.file.read()
            object_key = f"exams/{exam_id}/submissions/{submission.id}/{uuid.uuid4().hex}_{filename}"
            stored = await storage.put_bytes(object_key, payload, content_type=upload_content_type)
            row = submission_repo.create_submission_file(
                session,
                submission_id=submission.id,
                file_kind=kind,
                original_filename=filename,
                stored_path=stored["key"],
                content_type=upload_content_type,
                size_bytes=size,
            )
            created_files.append(SubmissionFileRead(id=row.id, file_kind=row.file_kind, original_filename=row.original_filename, stored_path=row.stored_path))
        commit_repository_session(session)

    submission_first_name, submission_last_name = submission_name_parts(submission.first_name, submission.last_name, submission.student_name)
    return SubmissionRead(
        id=submission.id,
        exam_id=submission.exam_id,
        student_name=submission_display_name(submission.first_name, submission.last_name, submission.student_name),
        first_name=submission_first_name,
        last_name=submission_last_name,
        status=submission.status,
        capture_mode=submission.capture_mode,
        front_page_totals=front_page_totals_read(submission),
        created_at=submission.created_at,
        files=created_files,
        pages=[],
    )



@router.post("/{exam_id}/key/register", response_model=BlobRegisterResponse)
def register_exam_key_files(exam_id: int, payload: BlobRegisterRequest, session: DbSession = Depends(get_repository_session)) -> BlobRegisterResponse:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    registered = exam_repo.register_exam_key_files(
        session,
        exam_id=exam_id,
        files=[
            {
                "original_filename": _sanitize_filename(file.original_filename),
                "stored_path": normalize_blob_path(file.blob_pathname),
                "content_type": file.content_type,
                "size_bytes": file.size_bytes,
            }
            for file in payload.files
        ],
    )

    if registered > 0:
        exam_repo.update_exam(session, exam, status=ExamStatus.KEY_UPLOADED)
    commit_repository_session(session)
    return BlobRegisterResponse(registered=registered)



def _bulk_pages_dir(exam_id: int, bulk_upload_id: int) -> Path:
    return settings.data_path / "exams" / str(exam_id) / "bulk" / str(bulk_upload_id) / "pages"


def _nearest_roster_name(name: str, roster: list[str]) -> str:
    if not roster:
        return name
    best = name
    best_score = 0.0
    for candidate in roster:
        score = SequenceMatcher(None, name.lower(), candidate.lower()).ratio()
        if score > best_score:
            best = candidate
            best_score = score
    return best if best_score >= 0.65 else name


def _normalize_exam_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _looks_like_same_name(left: str | None, right: str | None) -> bool:
    return _normalize_exam_title(left).casefold() == _normalize_exam_title(right).casefold()


def _segment_bulk_candidates(
    detections: list[BulkNameDetectionResult],
    roster: list[str],
    min_pages_per_student: int,
    max_carry_forward_pages: int = 2,
) -> tuple[list[BulkUploadCandidate], list[str]]:
    warnings: list[str] = []
    candidates: list[BulkUploadCandidate] = []
    if not detections:
        return candidates, warnings

    current_name = "Unknown Student"
    current_start = detections[0].page_number
    confidences: list[float] = []
    last_evidence: NameEvidence | None = None
    missing_run = 0

    def evidence_near_header(det: BulkNameDetectionResult) -> bool:
        evidence = det.evidence or {}
        try:
            y = float(evidence.get("y", 0.0))
            h = float(evidence.get("h", 0.0))
        except (TypeError, ValueError):
            return True
        return (y + h) <= 0.35

    def finalize(end_page: int, needs_review: bool = False) -> None:
        nonlocal candidates, current_start, confidences, last_evidence
        if end_page < current_start:
            return
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        candidate = BulkUploadCandidate(
            candidate_id=uuid.uuid4().hex,
            student_name=normalize_student_name(current_name),
            confidence=round(avg_conf, 3),
            page_start=current_start,
            page_end=end_page,
            needs_review=needs_review or current_name == "Unknown Student" or (end_page - current_start + 1) < min_pages_per_student,
            name_evidence=last_evidence,
        )
        if (end_page - current_start + 1) < min_pages_per_student:
            warnings.append(f"Candidate {candidate.student_name} has fewer than min_pages_per_student={min_pages_per_student}")
        candidates.append(candidate)

    for det in detections:
        proposed_name = (det.student_name or "").strip()
        if proposed_name:
            proposed_name = _nearest_roster_name(proposed_name, roster)
            evidence = det.evidence or {}
            last_evidence = NameEvidence(
                page_number=det.page_number,
                x=float(evidence.get("x", 0.0)),
                y=float(evidence.get("y", 0.0)),
                w=float(evidence.get("w", 0.0)),
                h=float(evidence.get("h", 0.0)),
            )
            if current_name == "Unknown Student":
                current_name = proposed_name
                confidences = [det.confidence]
                missing_run = 0
                continue
            if proposed_name != current_name:
                if det.confidence < 0.8 or not evidence_near_header(det):
                    confidences.append(max(confidences[-1] if confidences else det.confidence, det.confidence, 0.4))
                    missing_run = 0
                    continue
                finalize(det.page_number - 1)
                current_name = proposed_name
                current_start = det.page_number
                confidences = [det.confidence]
                missing_run = 0
                continue
            confidences.append(det.confidence)
            missing_run = 0
        else:
            missing_run += 1
            if missing_run > max_carry_forward_pages:
                warnings.append(f"Page {det.page_number} has ambiguous student name; please review.")
                confidences.append(0.0)
            else:
                confidences.append(max(confidences[-1] if confidences else 0.4, 0.4))

    finalize(detections[-1].page_number, needs_review=missing_run > max_carry_forward_pages)
    return candidates, warnings


def _segment_individual_image_candidates(detections: list[BulkNameDetectionResult]) -> tuple[list[BulkUploadCandidate], list[str]]:
    candidates: list[BulkUploadCandidate] = []
    warnings: list[str] = []
    for detection in detections:
        proposed_name = normalize_student_name((detection.student_name or "").strip() or "Unknown Student")
        evidence = detection.evidence or {}
        name_evidence = None
        if evidence:
            name_evidence = NameEvidence(
                page_number=detection.page_number,
                x=float(evidence.get("x", 0.0)),
                y=float(evidence.get("y", 0.0)),
                w=float(evidence.get("w", 0.0)),
                h=float(evidence.get("h", 0.0)),
            )
        candidates.append(
            BulkUploadCandidate(
                candidate_id=uuid.uuid4().hex,
                student_name=proposed_name,
                confidence=round(float(detection.confidence or 0.0), 3),
                page_start=detection.page_number,
                page_end=detection.page_number,
                needs_review=proposed_name == "Unknown Student",
                name_evidence=name_evidence,
            )
        )
    return candidates, warnings


@router.post("/{exam_id}/submissions/bulk", response_model=BulkUploadPreviewResponse, status_code=status.HTTP_201_CREATED)
def create_bulk_submission_preview(
    exam_id: int,
    files: list[UploadFile] | None = File(default=None),
    file: UploadFile | None = File(default=None),
    name_hint_regex: str | None = Form(default=None),
    roster: str | None = Form(default=None),
    min_pages_per_student: int = Form(default=1),
    session: DbSession = Depends(get_repository_session),
) -> BulkUploadPreviewResponse:
    _ = name_hint_regex
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    upload_files = list(files or [])
    if file is not None:
        upload_files.append(file)
    if not upload_files:
        raise HTTPException(status_code=400, detail="At least one bulk upload file is required")

    bulk = exam_repo.create_exam_bulk_upload(session, exam_id=exam_id, original_filename="bulk-upload", stored_path="")
    commit_repository_session(session)

    output_dir = reset_dir(_bulk_pages_dir(exam_id, bulk.id))
    rendered_paths, filename, stored_path = _render_bulk_upload_files(upload_files, output_dir)
    exam_repo.update_exam_bulk_upload(session, bulk=bulk, original_filename=filename, stored_path=stored_path)
    commit_repository_session(session)
    exam_repo.clear_bulk_upload_pages(session, bulk_upload_id=bulk.id)

    detections: list[BulkNameDetectionResult] = []
    detector = get_bulk_name_detector()
    detected_exam_title = ""
    for idx, page_path in enumerate(rendered_paths, start=1):
        with Image.open(page_path) as image:
            w, h = image.width, image.height
        detection: BulkNameDetectionResult | None = None
        try:
            detection = detector.detect(page_path, idx, model=_front_page_model(), request_id=uuid.uuid4().hex)
            if detection.student_name is None or detection.confidence < 0.5:
                detection = detector.detect(page_path, idx, model=_front_page_model(), request_id=uuid.uuid4().hex)
        except OpenAIRequestError:
            detection = BulkNameDetectionResult(page_number=idx, student_name=None, exam_name=None, confidence=0.0, evidence=None)
        normalized_detected_exam_title = _normalize_exam_title(detection.exam_name)
        if normalized_detected_exam_title and not _looks_like_same_name(normalized_detected_exam_title, detection.student_name):
            detected_exam_title = normalized_detected_exam_title
        exam_repo.create_bulk_upload_page(
            session,
            bulk_upload_id=bulk.id,
            page_number=idx,
            image_path=str(page_path),
            width=w,
            height=h,
            detected_student_name=detection.student_name,
            detection_confidence=detection.confidence,
            detection_evidence_json=json.dumps(detection.evidence or {}),
        )
        detections.append(detection)

    if detected_exam_title:
        exam_repo.update_exam(session, exam, name=detected_exam_title)
    exam_repo.update_exam(session, exam, status=ExamStatus.REVIEWING)

    commit_repository_session(session)

    roster_list: list[str] = []
    if roster:
        try:
            maybe_json = json.loads(roster)
            if isinstance(maybe_json, list):
                roster_list = [str(item).strip() for item in maybe_json if str(item).strip()]
        except json.JSONDecodeError:
            roster_list = [line.strip() for line in roster.splitlines() if line.strip()]

    if not stored_path and len(upload_files) > 1:
        candidates, warnings = _segment_individual_image_candidates(detections)
    else:
        candidates, warnings = _segment_bulk_candidates(detections, roster=roster_list, min_pages_per_student=max(min_pages_per_student, 1))
    return BulkUploadPreviewResponse(
        bulk_upload_id=bulk.id,
        page_count=len(rendered_paths),
        candidates=candidates,
        warnings=warnings,
    )


@router.get("/{exam_id}/submissions/bulk/{bulk_upload_id}", response_model=BulkUploadPreviewResponse)
def get_bulk_submission_preview(exam_id: int, bulk_upload_id: int, session: DbSession = Depends(get_repository_session)) -> BulkUploadPreviewResponse:
    bulk = exam_repo.get_exam_bulk_upload(session, bulk_upload_id)
    if not bulk or bulk.exam_id != exam_id:
        raise HTTPException(status_code=404, detail="Bulk upload not found")

    pages = exam_repo.list_bulk_upload_pages(session, bulk_upload_id)
    detections = [BulkNameDetectionResult(page_number=p.page_number, student_name=p.detected_student_name, exam_name=None, confidence=p.detection_confidence, evidence=json.loads(p.detection_evidence_json or "{}")) for p in pages]
    if not bulk.stored_path and len(pages) > 1:
        candidates, warnings = _segment_individual_image_candidates(detections)
    else:
        candidates, warnings = _segment_bulk_candidates(detections, roster=[], min_pages_per_student=1)
    return BulkUploadPreviewResponse(bulk_upload_id=bulk_upload_id, page_count=len(pages), candidates=candidates, warnings=warnings)


@router.get("/{exam_id}/submissions/bulk/{bulk_upload_id}/page/{page_number}")
def get_bulk_upload_page_image(exam_id: int, bulk_upload_id: int, page_number: int, session: DbSession = Depends(get_repository_session)) -> FileResponse:
    bulk = exam_repo.get_exam_bulk_upload(session, bulk_upload_id)
    if not bulk or bulk.exam_id != exam_id:
        raise HTTPException(status_code=404, detail="Bulk upload not found")

    row = next((item for item in exam_repo.list_bulk_upload_pages(session, bulk_upload_id) if item.page_number == page_number), None)
    if not row:
        raise HTTPException(status_code=404, detail="Page not found")

    image_path = Path(row.image_path)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Page image not found")
    return FileResponse(image_path)


@router.post("/{exam_id}/submissions/bulk/{bulk_upload_id}/finalize", response_model=BulkUploadFinalizeResponse)
def finalize_bulk_submission_preview(
    exam_id: int,
    bulk_upload_id: int,
    payload: BulkUploadFinalizeRequest,
    session: DbSession = Depends(get_repository_session),
) -> BulkUploadFinalizeResponse:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    bulk = exam_repo.get_exam_bulk_upload(session, bulk_upload_id)
    if not bulk or bulk.exam_id != exam_id:
        raise HTTPException(status_code=404, detail="Bulk upload not found")

    pages = exam_repo.list_bulk_upload_pages(session, bulk_upload_id)
    if not pages:
        raise HTTPException(status_code=400, detail="No rendered pages available")

    page_map = {p.page_number: p for p in pages}
    max_page = pages[-1].page_number
    used_pages: set[int] = set()
    warnings: list[str] = []
    created: list[SubmissionRead] = []
    created_submission_ids: list[int] = []

    for candidate in payload.candidates:
        if candidate.page_start < 1 or candidate.page_end > max_page or candidate.page_end < candidate.page_start:
            raise HTTPException(status_code=400, detail=f"Invalid page range for {candidate.student_name}")
        for page_num in range(candidate.page_start, candidate.page_end + 1):
            if page_num in used_pages:
                raise HTTPException(status_code=400, detail=f"Overlapping page range at page {page_num}")
            used_pages.add(page_num)

    all_pages = set(range(1, max_page + 1))
    if used_pages != all_pages:
        warnings.append("Candidate ranges do not cover all pages.")

    created = _finalize_bulk_candidates(
        exam=exam,
        bulk=bulk,
        candidates=payload.candidates,
        session=session,
    )
    created_submission_ids = [item.id for item in created if item.id is not None]

    exam_repo.update_exam(session, exam, status=ExamStatus.DRAFT)
    commit_repository_session(session)
    invalidate_exam_reporting_cache(exam_id)
    if created_submission_ids:
        thread = threading.Thread(
            target=_warm_and_promote_front_page_review_background,
            args=(exam_id, created_submission_ids),
            daemon=True,
        )
        thread.start()
    return BulkUploadFinalizeResponse(submissions=created, warnings=warnings)


@router.post("/{exam_id}/key/upload", response_model=ExamKeyUploadResponse)
def upload_exam_key_files(
    exam_id: int,
    files: list[UploadFile] = File(...),
    session: DbSession = Depends(get_repository_session),
) -> ExamKeyUploadResponse:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")

    uploaded = 0
    urls: list[str] = []

    for idx, upload in enumerate(files, start=1):
        filename = _sanitize_filename(upload.filename or f"key-{idx}")
        extension = Path(filename).suffix.lower()
        if extension not in _ALLOWED_KEY_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Unsupported file type. Use pdf/png/jpg/jpeg")

        content_type = upload.content_type or "application/octet-stream"
        payload = upload.file.read()
        if len(payload) > _VERCEL_SERVER_UPLOAD_LIMIT_BYTES:
            raise HTTPException(status_code=413, detail="File too large for direct server upload. Split the file or raise the backend upload limit.")
        object_key = f"exams/{exam_id}/key/{uuid.uuid4().hex}_{filename}"
        try:
            stored = upload_bytes(object_key, payload, content_type)
        except BlobUploadError as exc:
            raise HTTPException(status_code=500, detail=f"Blob upload failed: {exc}") from exc

        exam_repo.create_exam_key_file(
            session,
            exam_id=exam_id,
            original_filename=filename,
            stored_path=stored["pathname"],
            blob_url=stored["url"],
            blob_pathname=stored["pathname"],
            content_type=stored["contentType"],
            size_bytes=len(payload),
        )
        uploaded += 1
        urls.append(stored["url"])

    exam_repo.update_exam(session, exam, status=ExamStatus.KEY_UPLOADED)
    commit_repository_session(session)
    return ExamKeyUploadResponse(uploaded=uploaded, urls=urls)


@router.get("/{exam_id}/key/files", response_model=list[StoredFileRead])
def list_exam_key_files(exam_id: int, session: DbSession = Depends(get_repository_session)) -> list[StoredFileRead]:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    rows = exam_repo.list_exam_key_files(session, exam_id)
    result: list[StoredFileRead] = []
    for row in rows:
        result.append(
            StoredFileRead(
                id=row.id,
                original_filename=row.original_filename,
                stored_path=row.stored_path,
                content_type=row.content_type,
                size_bytes=row.size_bytes,
                signed_url=_resolve_signed_url(row.stored_path),
            )
        )
    return result


@router.post("/{exam_id}/key/build-pages", response_model=list[ExamKeyPageRead])
def build_exam_key_pages(exam_id: int, session: DbSession = Depends(get_repository_session)) -> list[ExamKeyPageRead]:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    stage = "build_key_pages"
    try:
        build_key_pages_for_exam(exam_id, session)
        exam_repo.update_exam(session, exam, status=ExamStatus.KEY_PAGES_READY)
        commit_repository_session(session)

        rows = exam_repo.list_exam_key_pages(session, exam_id)
        return [
            ExamKeyPageRead(
                id=r.id,
                exam_id=r.exam_id,
                page_number=r.page_number,
                image_path=relative_to_data(Path(r.image_path)),
                blob_pathname=r.blob_pathname,
                blob_url=r.blob_url,
                exists_on_disk=Path(r.image_path).exists(),
                exists_on_storage=bool((r.blob_pathname or "").strip()),
                width=r.width,
                height=r.height,
            )
            for r in rows
        ]
    except Exception as exc:
        request_id = str(uuid.uuid4())
        stage = getattr(exc, "stage", stage)
        logger.exception("build_exam_key_pages failed request_id=%s exam_id=%s stage=%s", request_id, exam_id, stage)
        return JSONResponse(
            status_code=502,
            content={
                "detail": "Build key pages failed",
                "request_id": request_id,
                "stage": stage,
                "message": str(exc)[:500],
            },
        )


@router.get("/{exam_id}/key/pages", response_model=list[ExamKeyPageRead])
def list_exam_key_pages(exam_id: int, session: DbSession = Depends(get_repository_session)) -> list[ExamKeyPageRead]:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    rows = exam_repo.list_exam_key_pages(session, exam_id)
    return [
            ExamKeyPageRead(
                id=r.id,
                exam_id=r.exam_id,
                page_number=r.page_number,
                image_path=relative_to_data(Path(r.image_path)),
                blob_pathname=r.blob_pathname,
                blob_url=r.blob_url,
                exists_on_disk=Path(r.image_path).exists(),
                exists_on_storage=bool((r.blob_pathname or "").strip()),
                width=r.width,
                height=r.height,
            )
            for r in rows
        ]


@router.post("/{exam_id}/questions", response_model=QuestionRead, status_code=status.HTTP_201_CREATED)
def create_question(exam_id: int, payload: QuestionCreate, session: DbSession = Depends(get_repository_session)) -> QuestionRead:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    rubric = payload.rubric_json or {
        "total_marks": payload.max_marks,
        "criteria": [],
        "model_solution": "",
        "answer_key": "",
    }
    question = question_repo.create_question(
        session,
        exam_id=exam_id,
        label=payload.label,
        max_marks=payload.max_marks,
        rubric_json=json.dumps(rubric),
    )
    commit_repository_session(session)
    invalidate_exam_reporting_cache(exam_id)

    return QuestionRead(
        id=question.id,
        exam_id=question.exam_id,
        label=question.label,
        max_marks=question.max_marks,
        rubric_json=rubric,
        regions=[],
    )


@router.get("/{exam_id}/questions", response_model=list[QuestionRead])
def list_questions(exam_id: int, session: DbSession = Depends(get_repository_session), job_id: int | None = None) -> list[QuestionRead]:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    if job_id is not None:
        _get_job_for_exam_or_error(exam_id, job_id, session)

    questions = sorted(question_repo.list_exam_questions(session, exam_id), key=question_repo.question_sort_key)
    result: list[QuestionRead] = []
    for q in questions:
        regions = submission_repo.list_question_regions(session, q.id)
        result.append(
            QuestionRead(
                id=q.id,
                exam_id=q.exam_id,
                label=q.label,
                max_marks=q.max_marks,
                rubric_json=json.loads(q.rubric_json),
                regions=[RegionRead(id=r.id, page_number=r.page_number, x=r.x, y=r.y, w=r.w, h=r.h) for r in regions],
            )
        )
    return result


@router.patch("/{exam_id}/questions/{question_id}", response_model=QuestionRead)
def update_question(
    exam_id: int,
    question_id: int,
    payload: QuestionUpdate,
    session: DbSession = Depends(get_repository_session),
) -> QuestionRead:
    question = _get_exam_question_or_404(exam_id, question_id, session)

    rubric = json.loads(question.rubric_json)
    if payload.rubric_json is not None:
        rubric = payload.rubric_json
    question = question_repo.update_question(
        session,
        question=question,
        label=payload.label,
        max_marks=payload.max_marks,
        rubric_json=json.dumps(rubric) if payload.rubric_json is not None else None,
    )
    commit_repository_session(session)
    invalidate_exam_reporting_cache(exam_id)

    regions = submission_repo.list_question_regions(session, question.id)
    return QuestionRead(
        id=question.id,
        exam_id=question.exam_id,
        label=question.label,
        max_marks=question.max_marks,
        rubric_json=rubric,
        regions=[RegionRead(id=r.id, page_number=r.page_number, x=r.x, y=r.y, w=r.w, h=r.h) for r in regions],
    )


def _key_page_missing_detail(exam_id: int, page: ExamKeyPage, requested_page_number: int) -> dict[str, Any]:
    image_path = Path(page.image_path)
    return {
        "message": "Key page image missing",
        "exam_id": exam_id,
        "page_number": requested_page_number,
        "blob_pathname": page.blob_pathname,
        "image_path": str(image_path),
        "local_file_exists": image_path.exists(),
    }


def _read_key_page_bytes_or_404(exam_id: int, page_number: int, session: DbSession) -> tuple[bytes, str]:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    page = next((item for item in exam_repo.list_exam_key_pages(session, exam_id) if item.page_number == page_number), None)
    if not page:
        raise HTTPException(status_code=404, detail="Key page not found")

    if (page.blob_pathname or "").strip():
        try:
            data, content_type = _run_async(download_blob_bytes(page.blob_pathname))
            return data, content_type or "image/png"
        except BlobDownloadError:
            pass

    image_path = Path(page.image_path)
    if image_path.exists():
        media_type = "image/png"
        if image_path.suffix.lower() in {".jpg", ".jpeg"}:
            media_type = "image/jpeg"
        return image_path.read_bytes(), media_type

    raise HTTPException(status_code=404, detail=_key_page_missing_detail(exam_id=exam_id, page=page, requested_page_number=page_number))


@public_router.get("/{exam_id}/key/page/{page_number}")
def get_key_page_image(
    exam_id: int,
    page_number: int,
    session: DbSession = Depends(get_repository_session),
) -> Response:
    content, media_type = _read_key_page_bytes_or_404(exam_id=exam_id, page_number=page_number, session=session)
    return Response(content=content, media_type=media_type)


@public_router.get("/{exam_id}/questions/{question_id}/key-visual")
def get_question_key_visual(
    exam_id: int,
    question_id: int,
    session: DbSession = Depends(get_repository_session),
) -> Response:
    question = _get_exam_question_or_404(exam_id, question_id, session)
    rubric = json.loads(question.rubric_json)
    page_number = int(rubric.get("key_page_number") or 1)

    page = next((item for item in exam_repo.list_exam_key_pages(session, exam_id) if item.page_number == page_number), None)
    if not page:
        exam_pages = exam_repo.list_exam_key_pages(session, exam_id)
        page = exam_pages[0] if exam_pages else None
    if not page:
        raise HTTPException(status_code=404, detail="Key page not found")

    content, media_type = _read_key_page_bytes_or_404(exam_id=exam_id, page_number=page.page_number, session=session)
    return Response(content=content, media_type=media_type)


def _extract_usage(result: ParseResult) -> tuple[int, int, float]:
    payload = result.payload if isinstance(result.payload, dict) else {}

    usage_candidates: list[dict[str, Any]] = []
    direct_usage = payload.get("usage")
    if isinstance(direct_usage, dict):
        usage_candidates.append(direct_usage)

    response_payload = payload.get("response")
    if isinstance(response_payload, dict):
        response_usage = response_payload.get("usage")
        if isinstance(response_usage, dict):
            usage_candidates.append(response_usage)

    nested_usage = payload.get("meta")
    if isinstance(nested_usage, dict):
        meta_usage = nested_usage.get("usage")
        if isinstance(meta_usage, dict):
            usage_candidates.append(meta_usage)

    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cost = 0.0

    for usage in usage_candidates:
        input_tokens += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
        cost += float(usage.get("cost") or usage.get("cost_usd") or 0.0)

    if input_tokens == 0 and output_tokens == 0 and total_tokens > 0:
        input_tokens = total_tokens

    return input_tokens, output_tokens, cost


def _ensure_unique_label(existing_labels: set[str], label: str, page_number: int) -> tuple[str, bool]:
    base = label.strip() or "Q?"
    if base not in existing_labels:
        return base, False
    candidate = f"{base} (page {page_number})"
    if candidate not in existing_labels:
        return candidate, True
    suffix = 2
    while f"{candidate} #{suffix}" in existing_labels:
        suffix += 1
    return f"{candidate} #{suffix}", True


def _looks_like_objective_code(value: str) -> bool:
    upper = value.strip().upper()
    return bool(re.match(r"^(OB|LO|SO|OUTCOME)\s*-?\s*\d+[A-Z]?$", upper))


def _should_escalate_parse_result(*, confidence: float, questions_payload: list[dict[str, Any]], warnings: list[str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not questions_payload:
        reasons.append("no_questions")
    if confidence < 0.60:
        reasons.append("low_confidence")

    labels: list[str] = []
    max_marks: list[int] = []
    objective_hits = 0
    fallback_count = 0
    empty_answer_key_count = 0

    for question in questions_payload:
        label = str(question.get("label") or "").strip()
        if not label:
            reasons.append("missing_label")
        else:
            labels.append(label)
            if label.upper() in {"Q?", "Q1"} and int(question.get("max_marks") or 0) == 0:
                fallback_count += 1

        marks = int(question.get("max_marks") or 0)
        max_marks.append(marks)
        if marks <= 0:
            reasons.append("non_positive_marks")

        answer_key = str(question.get("answer_key") or "").strip()
        if len(answer_key) < 2:
            empty_answer_key_count += 1

        objective_codes = question.get("objective_codes") if isinstance(question.get("objective_codes"), list) else []
        if any(_looks_like_objective_code(str(code)) for code in objective_codes):
            objective_hits += 1

    if len(set(labels)) != len(labels):
        reasons.append("duplicate_labels")
    if fallback_count == len(questions_payload) and fallback_count > 0:
        reasons.append("fallback_only")
    if empty_answer_key_count == len(questions_payload) and questions_payload:
        reasons.append("empty_answer_keys")
    if len(warnings) >= 3:
        reasons.append("warning_heavy")
    if len(set(max_marks)) > 1 and any(m == 0 for m in max_marks):
        reasons.append("inconsistent_marks")
    if objective_hits == 0 and any("OB" in str(q.get("question_text") or "").upper() for q in questions_payload):
        reasons.append("missed_objective_codes")

    deduped = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return (len(deduped) > 0, deduped)


def _questions_for_parse_page(exam_id: int, page_number: int, session: DbSession) -> list[Question]:
    page_questions: list[Question] = []
    for question in question_repo.list_exam_questions(session, exam_id):
        try:
            rubric = json.loads(question.rubric_json)
        except json.JSONDecodeError:
            continue
        source_page_number = int(rubric.get("source_page_number") or rubric.get("key_page_number") or 0)
        if source_page_number == page_number:
            page_questions.append(question)
    return page_questions


def _clear_parse_artifacts_for_page(exam_id: int, page_number: int, session: DbSession) -> None:
    for question in _questions_for_parse_page(exam_id, page_number, session):
        question_repo.delete_question_dependencies(session, question.id)
        question_repo.delete_question(session, question)
    flush_repository_session(session)


def _upsert_questions_for_page(exam_id: int, page_number: int, questions_payload: list[dict[str, Any]], session: DbSession) -> list[dict[str, Any]]:
    existing = {q.label: q for q in question_repo.list_exam_questions(session, exam_id)}
    existing_labels = set(existing.keys())
    stored: list[dict[str, Any]] = []

    for local_index, parsed in enumerate(questions_payload, start=1):
        raw_label = str(parsed.get("label") or "Q?")
        label, relabeled = _ensure_unique_label(existing_labels, raw_label, page_number)
        existing_labels.add(label)

        max_marks = int(parsed.get("max_marks") or 0)
        marks_source = str(parsed.get("marks_source") or "unknown")
        if marks_source not in {"explicit", "inferred", "unknown"}:
            marks_source = "unknown"
        marks_confidence = float(parsed.get("marks_confidence") or 0)
        parsed_warnings = parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []
        evidence_list = parsed.get("evidence") if isinstance(parsed.get("evidence"), list) else []

        rubric = {
            "total_marks": max_marks,
            "criteria": [],
            "answer_key": parsed.get("answer_key", ""),
            "model_solution": "",
            "question_text": parsed.get("question_text", ""),
            "objective_codes": parsed.get("objective_codes", []),
            "marks_source": marks_source,
            "marks_confidence": marks_confidence,
            "warnings": parsed_warnings,
            "marks_reason": parsed.get("marks_reason", ""),
            "evidence": evidence_list,
            "needs_review": relabeled or bool(parsed.get("needs_review", False)),
            "parse_order": (1000 * page_number) + local_index,
            "source_page_number": page_number,
            "key_page_number": page_number,
            "original_label": raw_label,
        }

        question = existing.get(label)
        if question:
            question = question_repo.update_question(
                session,
                question=question,
                max_marks=max_marks,
                rubric_json=json.dumps(rubric),
            )
        else:
            question = question_repo.create_question(
                session,
                exam_id=exam_id,
                label=label,
                max_marks=max_marks,
                rubric_json=json.dumps(rubric),
            )
            existing[label] = question

        valid_evidence = [e for e in evidence_list if isinstance(e, dict)]
        question_repo.replace_question_parse_evidence(
            session,
            question_id=question.id,
            exam_id=exam_id,
            page_number=page_number,
            evidence_list=valid_evidence,
        )

        stored.append({"id": question.id, "label": label, "max_marks": max_marks})

    return stored


def _raise_parse_validation_error(*, status_code: int, detail: str, exam_exists: bool, job_exists: bool, job_exam_id: int | None = None) -> None:
    payload: dict[str, Any] = {
        "detail": detail,
        "exam_exists": exam_exists,
        "job_exists": job_exists,
    }
    if job_exam_id is not None:
        payload["job_exam_id"] = job_exam_id
    raise HTTPException(status_code=status_code, detail=payload)


def _get_exam_or_404(exam_id: int, session: DbSession, *, check_access: bool = True) -> Exam:
    exam = exam_repo.get_exam(session, exam_id)
    if not exam or (check_access and not can_access_owned_resource(exam.owner_user_id)):
        _raise_parse_validation_error(status_code=404, detail="Exam not found", exam_exists=False, job_exists=False)
    return exam


def _get_class_list_or_404(class_list_id: int, session: DbSession) -> ClassList:
    class_list = exam_repo.get_class_list(session, class_list_id)
    if not class_list or not can_access_owned_resource(class_list.owner_user_id):
        raise HTTPException(status_code=404, detail="Class list not found")
    return class_list


def _get_job_for_exam_or_error(exam_id: int, job_id: int, session: DbSession) -> ExamKeyParseJob:
    job = exam_repo.get_exam_parse_job(session, job_id)
    if not job:
        _raise_parse_validation_error(status_code=404, detail="Parse job not found", exam_exists=True, job_exists=False)
    if job.exam_id != exam_id:
        _raise_parse_validation_error(
            status_code=409,
            detail="Parse job does not belong to this exam",
            exam_exists=True,
            job_exists=True,
            job_exam_id=job.exam_id,
        )
    return job


def _get_latest_job_for_exam(exam_id: int, session: DbSession) -> ExamKeyParseJob | None:
    return exam_repo.get_latest_exam_parse_job(session, exam_id)


def _job_has_remaining_work(job_id: int, session: DbSession) -> bool:
    return exam_repo.exam_parse_job_has_remaining_work(session, job_id)


def _process_single_parse_page_task(
    *,
    exam_id: int,
    job_id: int,
    page_number: int,
    parser: AnswerKeyParser,
) -> dict[str, Any]:
    with open_repository_session() as session:
        _get_exam_or_404(exam_id, session)
        job = _get_job_for_exam_or_error(exam_id, job_id, session)
        target_parse_page = exam_repo.get_exam_parse_page(session, job_id=job.id, page_number=page_number)
        if not target_parse_page:
            raise HTTPException(status_code=404, detail="Parse page not found")
        key_page = exam_repo.get_exam_key_page(session, exam_id=exam_id, page_number=page_number)

        queued_started_at = utcnow()
        exam_repo.update_exam_parse_page(
            session,
            target_parse_page,
            status="running",
            updated_at=queued_started_at,
        )
        commit_repository_session(session)

        logger.info("parse_page_start job_id=%s page=%s", job.id, page_number)
        parse_started_at = time.perf_counter()

        if key_page is None:
            exam_repo.update_exam_parse_page(
                session,
                target_parse_page,
                status="failed",
                error_json={"detail": "Key page not found"},
                updated_at=utcnow(),
            )
            commit_repository_session(session)
            elapsed_ms = int((time.perf_counter() - parse_started_at) * 1000)
            logger.info("parse_page_done job_id=%s page=%s status=%s model=%s ms=%s", job.id, page_number, "failed", "n/a", elapsed_ms)
            return {"page_number": page_number, "status": "failed", "cost": 0.0, "input_tokens": 0, "output_tokens": 0}

        nano_model, mini_model = _resolve_models()
        page_path = Path(key_page.image_path)
        if not page_path.exists() and (key_page.blob_pathname or "").strip():
            try:
                content, _content_type = _run_async(download_blob_bytes(key_page.blob_pathname))
                page_path = ensure_dir(settings.data_path / "cache" / "key-pages" / str(exam_id)) / f"page_{page_number:04d}.png"
                page_path.write_bytes(content)
            except BlobDownloadError:
                page_path = Path(key_page.image_path)

        if not page_path.exists():
            exam_repo.update_exam_parse_page(
                session,
                target_parse_page,
                status="failed",
                error_json={"detail": "Page image missing"},
                updated_at=utcnow(),
            )
            commit_repository_session(session)
            elapsed_ms = int((time.perf_counter() - parse_started_at) * 1000)
            logger.info("parse_page_done job_id=%s page=%s status=%s model=%s ms=%s", job.id, page_number, "failed", "n/a", elapsed_ms)
            return {"page_number": page_number, "status": "failed", "cost": 0.0, "input_tokens": 0, "output_tokens": 0}

        warnings: list[str] = []
        used_model = nano_model
        confidence = 0.0
        questions_payload: list[dict[str, Any]] = []
        input_tokens = 0
        output_tokens = 0
        cost = 0.0
        error_payload: dict[str, Any] | None = None
        tried_models: list[str] = []
        first_attempt_confidence = 0.0

        for model_name in [nano_model, mini_model]:
            tried_models.append(model_name)
            used_model = model_name
            try:
                result = _invoke_parser(parser, [page_path], model_name, str(job.id))
                in_t, out_t, cst = _extract_usage(result)
                input_tokens += in_t
                output_tokens += out_t
                cost += cst
                logger.info(
                    "parse_page_usage job_id=%s page=%s model=%s input_tokens=%s output_tokens=%s cost=%s",
                    job.id,
                    page_number,
                    model_name,
                    in_t,
                    out_t,
                    cst,
                )
                confidence, questions_payload, warnings = _validate_parse_payload(result.payload)
                if len(tried_models) == 1:
                    first_attempt_confidence = confidence
                should_escalate, escalate_reasons = _should_escalate_parse_result(
                    confidence=confidence,
                    questions_payload=questions_payload,
                    warnings=warnings,
                )
                if model_name == nano_model and should_escalate:
                    warnings.append("Escalated from fast pass: " + ", ".join(escalate_reasons))
                    logger.info("fast parse escalated to stronger model page=%s reasons=%s", page_number, ",".join(escalate_reasons))
                    continue
                break
            except (OpenAIRequestError, ValueError, SchemaBuildError) as exc:
                warnings.append(f"{model_name} failed: {type(exc).__name__}")
                error_payload = {"detail": str(exc)[:300], "model": model_name}
                continue
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{model_name} failed: {type(exc).__name__}")
                error_payload = {"detail": str(exc)[:300], "model": model_name}
                continue

        page_status = "done" if questions_payload else "failed"
        if questions_payload:
            with _get_exam_question_lock(exam_id):
                with open_repository_session() as question_session:
                    _upsert_questions_for_page(exam_id, page_number, questions_payload, question_session)
                    commit_repository_session(question_session)

        elapsed_ms = int((time.perf_counter() - parse_started_at) * 1000)
        finished_at = utcnow()
        final_should_escalate, final_escalation_reasons = _should_escalate_parse_result(
            confidence=confidence,
            questions_payload=questions_payload,
            warnings=warnings,
        )
        exam_repo.update_exam_parse_page(
            session,
            target_parse_page,
            model_used=used_model,
            confidence=confidence,
            status=page_status,
            cost=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            result_json={
                "questions": questions_payload,
                "warnings": warnings,
                "timing": {
                    "elapsed_ms": elapsed_ms,
                    "started_at": queued_started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                },
                "quality": {
                    "should_escalate": final_should_escalate,
                    "reasons": final_escalation_reasons,
                    "tried_models": tried_models,
                },
            },
            error_json=error_payload,
            updated_at=finished_at,
        )
        commit_repository_session(session)

        logger.info("parse_page_done job_id=%s page=%s status=%s model=%s ms=%s", job.id, page_number, page_status, used_model, elapsed_ms)
        return {
            "page_number": page_number,
            "status": page_status,
            "tried_models": tried_models,
            "first_attempt_confidence": first_attempt_confidence,
            "confidence": confidence,
            "model_used": used_model,
            "cost": cost,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }


async def _process_parse_pages_concurrently(
    *,
    exam_id: int,
    job_id: int,
    target_parse_pages: list[ExamKeyParsePage],
    parser_factory: Callable[[], AnswerKeyParser],
    max_concurrency: int,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _run_one(page_number: int) -> dict[str, Any]:
        async with semaphore:
            parser = parser_factory()
            return await asyncio.to_thread(
                _process_single_parse_page_task,
                exam_id=exam_id,
                job_id=job_id,
                page_number=page_number,
                parser=parser,
            )

    tasks = [asyncio.create_task(_run_one(parse_page.page_number)) for parse_page in target_parse_pages]
    return await asyncio.gather(*tasks, return_exceptions=False)


def _run_parse_job_background(exam_id: int, job_id: int, parser: AnswerKeyParser | None = None) -> None:
    runner_lock = _get_parse_job_runner_lock(job_id)
    if not runner_lock.acquire(blocking=False):
        logger.info("parse_background_skip_already_running job_id=%s", job_id)
        return
    try:
        resolved_parser = parser or get_answer_key_parser()
        while True:
            with open_repository_session() as session:
                job = _get_job_for_exam_or_error(exam_id, job_id, session)
                remaining = exam_repo.list_pending_exam_parse_pages(session, job.id, limit=3)
                if not remaining:
                    break
            with open_repository_session() as background_session:
                _run_async(parse_answer_key_next_page(exam_id=exam_id, job_id=job_id, session=background_session, parser=resolved_parser))
        _recompute_parse_job_state(exam_id, job_id)
    except Exception:
        logger.exception("parse_background_failed job_id=%s exam_id=%s", job_id, exam_id)
    finally:
        runner_lock.release()


def _recompute_parse_job_state(exam_id: int, job_id: int) -> dict[str, Any]:
    with open_repository_session() as recompute_session:
        exam = _get_exam_or_404(exam_id, recompute_session)
        job = _get_job_for_exam_or_error(exam_id, job_id, recompute_session)
        parse_pages = exam_repo.list_exam_parse_pages(recompute_session, job.id)

        pages_done = sum(1 for page in parse_pages if page.status == "done")
        has_running_or_pending = any(page.status in {"running", "pending"} for page in parse_pages)
        has_failed = any(page.status == "failed" for page in parse_pages)

        job.pages_done = pages_done
        job.cost_total = sum(float(page.cost or 0.0) for page in parse_pages)
        job.input_tokens_total = sum(int(page.input_tokens or 0) for page in parse_pages)
        job.output_tokens_total = sum(int(page.output_tokens or 0) for page in parse_pages)
        if has_running_or_pending:
            job.status = "running"
        elif has_failed:
            job.status = "failed"
        else:
            job.status = "done"
            exam_repo.update_exam(recompute_session, exam, status=ExamStatus.REVIEWING)

        job.updated_at = utcnow()
        exam_repo.update_exam_parse_job(
            recompute_session,
            job,
            pages_done=job.pages_done,
            cost_total=job.cost_total,
            input_tokens_total=job.input_tokens_total,
            output_tokens_total=job.output_tokens_total,
            status=job.status,
            updated_at=job.updated_at,
        )
        commit_repository_session(recompute_session)

        logger.info(
            "parse_job_totals job_id=%s pages_done=%s/%s status=%s input_tokens_total=%s output_tokens_total=%s cost_total=%s",
            job.id,
            job.pages_done,
            job.page_count,
            job.status,
            job.input_tokens_total,
            job.output_tokens_total,
            job.cost_total,
        )

        return {
            "pages_done": job.pages_done,
            "page_count": job.page_count,
            "status": job.status,
            "cost_total": job.cost_total,
            "input_tokens_total": job.input_tokens_total,
            "output_tokens_total": job.output_tokens_total,
        }


@router.post("/{exam_id}/key/parse/start")
def start_answer_key_parse(exam_id: int, session: DbSession = Depends(get_repository_session)) -> dict[str, object]:
    exam = _get_exam_or_404(exam_id, session)

    page_rows = exam_repo.list_exam_key_pages(session, exam_id)
    if not page_rows:
        raise HTTPException(status_code=400, detail="No key pages available. Upload and build pages first.")

    latest_job = _get_latest_job_for_exam(exam_id, session)
    if latest_job and (latest_job.status == "running" or _job_has_remaining_work(latest_job.id, session)):
        reused = True
        logger.info("parse_start exam_id=%s reused_job=%s job_id=%s", exam_id, reused, latest_job.id)
        return {
            "job_id": latest_job.id,
            "request_id": str(latest_job.id),
            "page_count": latest_job.page_count,
            "pages_done": latest_job.pages_done,
            "reused": reused,
        }

    now = utcnow()
    job = exam_repo.create_exam_parse_job(
        session,
        exam_id=exam_id,
        status="running",
        page_count=len(page_rows),
        pages_done=0,
        created_at=now,
        updated_at=now,
    )
    exam_repo.update_exam(session, exam, status=ExamStatus.KEY_PAGES_READY)

    for page in page_rows:
        exam_repo.create_exam_parse_page(session, job_id=job.id, page_number=page.page_number, status="pending", updated_at=utcnow())

    commit_repository_session(session)

    reused = False
    logger.info("parse_start exam_id=%s reused_job=%s job_id=%s", exam_id, reused, job.id)

    return {"job_id": job.id, "request_id": str(job.id), "page_count": job.page_count, "pages_done": job.pages_done, "reused": reused}


@router.post("/{exam_id}/key/parse/next")
async def parse_answer_key_next_page(
    exam_id: int,
    job_id: int | None = None,
    request_id: str | None = None,
    batch_size: int = 3,
    session: DbSession = Depends(get_repository_session),
    parser: AnswerKeyParser = Depends(get_answer_key_parser),
) -> dict[str, object]:
    _get_exam_or_404(exam_id, session)

    resolved_job_id = job_id or (int(request_id) if request_id and request_id.isdigit() else None)
    if not resolved_job_id:
        raise HTTPException(status_code=422, detail="job_id is required")
    job = _get_job_for_exam_or_error(exam_id, resolved_job_id, session)
    page_rows = exam_repo.list_exam_key_pages(session, exam_id)
    if not page_rows:
        raise HTTPException(status_code=400, detail="No key pages available. Upload and build pages first.")

    capped_batch_size = max(1, min(batch_size, 5))
    concurrency = min(capped_batch_size, 3)
    logger.info("parse_next job_id=%s batch_size=%s concurrency=%s", job.id, capped_batch_size, concurrency)

    target_parse_pages = exam_repo.list_pending_exam_parse_pages(session, job.id, limit=capped_batch_size)

    page_results: list[dict[str, Any]] = []
    if target_parse_pages:
        parser_factory: Callable[[], AnswerKeyParser] = lambda: parser
        page_results = await _process_parse_pages_concurrently(
            exam_id=exam_id,
            job_id=job.id,
            target_parse_pages=list(target_parse_pages),
            parser_factory=parser_factory,
            max_concurrency=concurrency,
        )
        page_results.sort(key=lambda result: int(result.get("page_number", 0)))

    job_state = _recompute_parse_job_state(exam_id, job.id)
    pages_processed = [int(result["page_number"]) for result in page_results]
    logger.info(
        "parse_next_complete job_id=%s pages_done=%s/%s status=%s",
        job.id,
        job_state["pages_done"],
        job_state["page_count"],
        job_state["status"],
    )

    return {
        "job_id": job.id,
        "request_id": str(job.id),
        "pages_processed": pages_processed,
        "pages_done": job_state["pages_done"],
        "page_count": job_state["page_count"],
        "status": job_state["status"],
        "page_results": page_results,
        "totals": {
            "cost_total": job_state["cost_total"],
            "input_tokens_total": job_state["input_tokens_total"],
            "output_tokens_total": job_state["output_tokens_total"],
        },
    }


@router.get("/{exam_id}/key/parse/status")
def get_answer_key_parse_status(exam_id: int, job_id: int | None = None, request_id: str | None = None, session: DbSession = Depends(get_repository_session)) -> dict[str, object]:
    exam = _get_exam_or_404(exam_id, session)
    resolved_job_id = job_id or (int(request_id) if request_id and request_id.isdigit() else None)
    if not resolved_job_id:
        raise HTTPException(status_code=422, detail="job_id is required")
    if not exam:
        _raise_parse_validation_error(status_code=404, detail="Exam not found", exam_exists=False, job_exists=False)
    job = exam_repo.get_exam_parse_job(session, resolved_job_id)
    if not job:
        _raise_parse_validation_error(status_code=404, detail="Parse job not found", exam_exists=True, job_exists=False)
    if job.exam_id != exam_id:
        _raise_parse_validation_error(status_code=409, detail="Parse job does not belong to this exam", exam_exists=True, job_exists=True, job_exam_id=job.exam_id)
    job_state = _recompute_parse_job_state(exam_id, job.id)
    job = _get_job_for_exam_or_error(exam_id, resolved_job_id, session)
    pages = exam_repo.list_exam_parse_pages(session, job.id)
    warnings = [f"Page {p.page_number} failed" for p in pages if p.status == "failed"]
    page_items = []
    for p in pages:
        timing = p.result_json.get("timing", {}) if isinstance(p.result_json, dict) else {}
        quality = p.result_json.get("quality", {}) if isinstance(p.result_json, dict) else {}
        page_items.append({
            "page_number": p.page_number,
            "status": p.status,
            "model_used": p.model_used,
            "confidence": p.confidence,
            "elapsed_ms": timing.get("elapsed_ms"),
            "started_at": timing.get("started_at"),
            "finished_at": timing.get("finished_at"),
            "input_tokens": p.input_tokens,
            "output_tokens": p.output_tokens,
            "cost": p.cost,
            "should_escalate": quality.get("should_escalate"),
            "escalation_reasons": quality.get("reasons", []),
            "tried_models": quality.get("tried_models", []),
        })
    return {
        "job_id": job.id,
        "request_id": str(job.id),
        "exam_exists": True,
        "job_exists": True,
        "page_count": job_state["page_count"],
        "pages_done": job_state["pages_done"],
        "status": job_state["status"],
        "pages": page_items,
        "totals": {"cost_total": job_state["cost_total"], "input_tokens_total": job_state["input_tokens_total"], "output_tokens_total": job_state["output_tokens_total"]},
        "warnings": warnings,
    }


@router.post("/{exam_id}/key/parse/retry")
def retry_answer_key_parse_page(
    exam_id: int,
    job_id: int | None = None,
    page_number: int = 0,
    request_id: str | None = None,
    session: DbSession = Depends(get_repository_session),
    parser: AnswerKeyParser = Depends(get_answer_key_parser),
) -> dict[str, object]:
    if page_number <= 0:
        raise HTTPException(status_code=422, detail="page_number is required")

    exam = _get_exam_or_404(exam_id, session)
    resolved_job_id = job_id or (int(request_id) if request_id and request_id.isdigit() else None)
    if not resolved_job_id:
        latest_job = _get_latest_job_for_exam(exam_id, session)
        if not latest_job:
            raise HTTPException(status_code=404, detail="Parse job not found")
        resolved_job_id = latest_job.id

    job = _get_job_for_exam_or_error(exam_id, resolved_job_id, session)
    parse_page = exam_repo.get_exam_parse_page(session, job_id=resolved_job_id, page_number=page_number)
    if not parse_page:
        raise HTTPException(status_code=404, detail="Parse page not found")
    if parse_page.status == "running":
        raise HTTPException(status_code=409, detail="Parse page is already running")

    with _get_exam_question_lock(exam_id):
        _clear_parse_artifacts_for_page(exam_id, page_number, session)
        exam_repo.update_exam_parse_page(
            session,
            parse_page,
            status="pending",
            confidence=0.0,
            model_used=None,
            error_json=None,
            result_json=None,
            cost=0.0,
            input_tokens=0,
            output_tokens=0,
            updated_at=utcnow(),
        )
        commit_repository_session(session)

    page_result = _process_single_parse_page_task(
        exam_id=exam_id,
        job_id=job.id,
        page_number=page_number,
        parser=parser,
    )
    invalidate_exam_reporting_cache(exam_id)
    job_state = _recompute_parse_job_state(exam_id, job.id)
    refreshed_questions = list_questions(exam_id, session)
    refreshed_page = get_answer_key_parse_status(exam_id=exam_id, job_id=job.id, session=session)
    page_status = next((page for page in refreshed_page["pages"] if int(page["page_number"]) == page_number), None)

    if job_state["status"] == "done":
        exam_repo.update_exam(session, exam, status=ExamStatus.REVIEWING)
        commit_repository_session(session)

    return {
        "job_id": job.id,
        "request_id": str(job.id),
        "page_number": page_number,
        "status": page_result.get("status", "failed"),
        "page": page_status,
        "pages_done": job_state["pages_done"],
        "page_count": job_state["page_count"],
        "job_status": job_state["status"],
        "totals": {
            "cost_total": job_state["cost_total"],
            "input_tokens_total": job_state["input_tokens_total"],
            "output_tokens_total": job_state["output_tokens_total"],
        },
        "questions": [q.model_dump() for q in refreshed_questions],
    }




@router.post("/{exam_id}/key/parse/finish")
def finish_answer_key_parse(exam_id: int, job_id: int | None = None, request_id: str | None = None, session: DbSession = Depends(get_repository_session)) -> dict[str, object]:
    resolved_job_id = job_id or (int(request_id) if request_id and request_id.isdigit() else None)
    if not resolved_job_id:
        raise HTTPException(status_code=422, detail="job_id is required")
    _get_exam_or_404(exam_id, session)
    job = _get_job_for_exam_or_error(exam_id, resolved_job_id, session)
    job_state = _recompute_parse_job_state(exam_id, job.id)
    invalidate_exam_reporting_cache(exam_id)
    questions = list_questions(exam_id, session)
    return {
        "job_id": job.id,
        "request_id": str(job.id),
        "status": job_state["status"],
        "totals": {
            "cost_total": job_state["cost_total"],
            "input_tokens_total": job_state["input_tokens_total"],
            "output_tokens_total": job_state["output_tokens_total"],
        },
        "pages_done": job_state["pages_done"],
        "page_count": job_state["page_count"],
        "questions": [q.model_dump() for q in questions],
    }


@router.get("/{exam_id}/key/parse/latest")
def get_latest_parse_job(exam_id: int, session: DbSession = Depends(get_repository_session)) -> dict[str, object]:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        return {"exam_exists": False, "job": None}

    latest_job = _get_latest_job_for_exam(exam_id, session)
    if not latest_job:
        return {"exam_exists": True, "job": None}

    pages = exam_repo.list_exam_parse_pages(session, latest_job.id)
    failed_pages = [p.page_number for p in pages if p.status == "failed"]
    pending_pages = [p.page_number for p in pages if p.status in {"pending", "running"}]
    has_remaining_work = latest_job.status == "running" or bool(pending_pages or failed_pages)

    return {
        "exam_exists": True,
        "job": {
            "job_id": latest_job.id,
            "request_id": str(latest_job.id),
            "status": latest_job.status,
            "page_count": latest_job.page_count,
            "pages_done": latest_job.pages_done,
            "has_remaining_work": has_remaining_work,
            "failed_pages": failed_pages,
            "pending_pages": pending_pages,
            "totals": {
                "cost_total": latest_job.cost_total,
                "input_tokens_total": latest_job.input_tokens_total,
                "output_tokens_total": latest_job.output_tokens_total,
            },
            "created_at": latest_job.created_at.isoformat(),
            "updated_at": latest_job.updated_at.isoformat(),
        },
    }


@router.post("/{exam_id}/key/parse")
def parse_answer_key(exam_id: int, session: DbSession = Depends(get_repository_session), parser: AnswerKeyParser = Depends(get_answer_key_parser)) -> dict[str, object]:
    """Deprecated: use /key/parse/start + /key/parse/next + /key/parse/status."""
    try:
        page_rows = exam_repo.list_exam_key_pages(session, exam_id)
        if not page_rows:
            build_key_pages_for_exam(exam_id, session)
        started = start_answer_key_parse(exam_id=exam_id, session=session)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "stage": "build_key_pages", "request_id": str(uuid.uuid4())})

    job_id = int(started["job_id"])
    _run_parse_job_background(exam_id, job_id, parser)
    status = get_answer_key_parse_status(exam_id=exam_id, job_id=job_id, session=session)
    return {
        "ok": True,
        "deprecated": True,
        "job_id": job_id,
        "request_id": str(job_id),
        "status": status.get("status", "running"),
        "stage": "job_started",
        "warnings": status.get("warnings", []),
        "questions": [],
        "questions_count": 0,
        "page_count": started.get("page_count", 0),
        "pages_done": status.get("pages_done", 0),
    }


@router.post("/{exam_id}/key/review/complete")
def complete_key_review(exam_id: int, session: DbSession = Depends(get_repository_session)) -> dict[str, object]:
    exam = _get_exam_or_404(exam_id, session)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    question_count = len(question_repo.list_exam_questions(session, exam_id))
    warnings: list[str] = []
    if question_count == 0:
        warnings.append("No questions exist. Exam marked READY for manual setup.")
    exam_repo.update_exam(session, exam, status=ExamStatus.READY)
    commit_repository_session(session)
    return {"exam_id": exam_id, "status": exam.status, "warnings": warnings}
