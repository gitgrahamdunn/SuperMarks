"""Submission pipeline endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import Session, delete, select

from app.db import get_session
from app.blob_service import create_signed_blob_url, normalize_blob_path
from app.blob_service import BlobDownloadError
from app.models import (
    AnswerCrop,
    Exam,
    GradeResult,
    Question,
    QuestionRegion,
    Submission,
    SubmissionCaptureMode,
    SubmissionFile,
    SubmissionPage,
    SubmissionStatus,
    Transcription,
    utcnow,
)
from app.pipeline.crops import crop_regions_and_stitch
from app.pipeline.grade import get_grader
from app.pipeline.pages import Pdf2ImageConverter, normalize_image_to_png
from app.pipeline.transcribe import get_ocr_provider
from app.ai.openai_vision import OpenAIRequestError, get_front_page_totals_extractor
from app.reporting import accumulate_objective_totals, front_page_objective_totals, front_page_totals_read, objective_totals_read
from app.schemas import (
    BlobRegisterRequest,
    BlobRegisterResponse,
    FrontPageCandidateValue,
    FrontPageExtractionEvidence,
    FrontPageObjectiveScoreCandidate,
    FrontPageTotalsCandidateRead,
    FrontPageTotalsRead,
    FrontPageTotalsUpsert,
    GradeResultRead,
    ManualGradeUpsert,
    StoredFileRead,
    SubmissionFileRead,
    SubmissionPageRead,
    SubmissionPrepareQuestionStatus,
    SubmissionPrepareStatus,
    SubmissionRead,
    SubmissionResults,
    TranscriptionRead,
)
from app.storage import crops_dir, pages_dir, relative_to_data, reset_dir
from app.storage_provider import get_storage_signed_url, materialize_object_to_path

router = APIRouter(prefix="/submissions", tags=["submissions"])
logger = logging.getLogger(__name__)

_RATIO_VALUE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$")

def _submission_read(submission: Submission, files: list[SubmissionFile], pages: list[SubmissionPage]) -> SubmissionRead:
    return SubmissionRead(
        id=submission.id,
        exam_id=submission.exam_id,
        student_name=submission.student_name,
        status=submission.status,
        capture_mode=submission.capture_mode,
        front_page_totals=front_page_totals_read(submission),
        created_at=submission.created_at,
        files=[SubmissionFileRead(id=f.id, file_kind=f.file_kind, original_filename=f.original_filename, stored_path=f.stored_path, blob_url=f.blob_url, content_type=f.content_type, size_bytes=f.size_bytes) for f in files],
        pages=[SubmissionPageRead(id=p.id, page_number=p.page_number, image_path=p.image_path, width=p.width, height=p.height) for p in pages],
    )


def _run_async(coro):
    return asyncio.run(coro)


def _resolve_signed_url(pathname: str) -> str:
    try:
        return _run_async(create_signed_blob_url(pathname))
    except Exception:
        return _run_async(get_storage_signed_url(pathname))


def _split_ratio_value(value: FrontPageCandidateValue | None) -> tuple[str | None, str | None]:
    text = value.value_text.strip() if value else ""
    if not text:
        return None, None
    match = _RATIO_VALUE_PATTERN.match(text)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _normalize_objective_candidate(
    objective_code: FrontPageCandidateValue | None,
    marks_awarded: FrontPageCandidateValue | None,
    max_marks: FrontPageCandidateValue | None,
) -> tuple[FrontPageCandidateValue | None, FrontPageCandidateValue | None, FrontPageCandidateValue | None]:
    if marks_awarded is None:
        return objective_code, marks_awarded, max_marks

    awarded_text, max_text = _split_ratio_value(marks_awarded)
    if awarded_text is None or max_text is None:
        return objective_code, marks_awarded, max_marks

    normalized_marks_awarded = marks_awarded.model_copy(update={"value_text": awarded_text})
    if max_marks and max_marks.value_text.strip():
        return objective_code, normalized_marks_awarded, max_marks

    normalized_max_marks = FrontPageCandidateValue(
        value_text=max_text,
        confidence=marks_awarded.confidence,
        evidence=list(marks_awarded.evidence),
    )
    return objective_code, normalized_marks_awarded, normalized_max_marks




def _build_pages_for_submission(submission: Submission, session: Session) -> list[SubmissionPageRead]:
    files = session.exec(select(SubmissionFile).where(SubmissionFile.submission_id == submission.id)).all()
    if not files:
        raise HTTPException(status_code=400, detail="No files available for submission")

    out_dir = reset_dir(pages_dir(submission.exam_id, submission.id))
    session.exec(delete(SubmissionPage).where(SubmissionPage.submission_id == submission.id))

    created: list[SubmissionPageRead] = []
    if files[0].file_kind == "pdf":
        try:
            converter = Pdf2ImageConverter()
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            source_path = _run_async(materialize_object_to_path(files[0].stored_path, out_dir / "source"))
        except BlobDownloadError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        page_paths = converter.convert(source_path, out_dir)
        for idx, page_path in enumerate(page_paths, 1):
            w, h = normalize_image_to_png(page_path, page_path)
            row = SubmissionPage(submission_id=submission.id, page_number=idx, image_path=str(page_path), width=w, height=h)
            session.add(row)
            session.flush()
            created.append(SubmissionPageRead(id=row.id, page_number=idx, image_path=relative_to_data(page_path), width=w, height=h))
    else:
        for idx, file in enumerate(files, 1):
            out_path = out_dir / f"page_{idx:04d}.png"
            try:
                source_path = _run_async(materialize_object_to_path(file.stored_path, out_dir / "source"))
            except BlobDownloadError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            w, h = normalize_image_to_png(source_path, out_path)
            row = SubmissionPage(submission_id=submission.id, page_number=idx, image_path=str(out_path), width=w, height=h)
            session.add(row)
            session.flush()
            created.append(SubmissionPageRead(id=row.id, page_number=idx, image_path=relative_to_data(out_path), width=w, height=h))

    submission.status = SubmissionStatus.PAGES_READY
    session.add(submission)
    session.commit()
    return created


def _build_crops_for_submission(submission: Submission, session: Session) -> dict:
    if submission.status not in (SubmissionStatus.PAGES_READY, SubmissionStatus.CROPS_READY, SubmissionStatus.TRANSCRIBED, SubmissionStatus.GRADED):
        raise HTTPException(status_code=400, detail="Submission must be at least PAGES_READY")

    questions = session.exec(select(Question).where(Question.exam_id == submission.exam_id)).all()
    if not questions:
        raise HTTPException(status_code=400, detail="No questions configured for exam")

    pages = session.exec(select(SubmissionPage).where(SubmissionPage.submission_id == submission.id)).all()
    page_path_map = {p.page_number: Path(p.image_path) for p in pages}

    out_dir = reset_dir(crops_dir(submission.exam_id, submission.id))
    session.exec(delete(AnswerCrop).where(AnswerCrop.submission_id == submission.id))

    count = 0
    for question in questions:
        regions = session.exec(select(QuestionRegion).where(QuestionRegion.question_id == question.id)).all()
        if not regions:
            continue
        region_payload = [
            {"page_number": r.page_number, "x": r.x, "y": r.y, "w": r.w, "h": r.h}
            for r in regions
        ]
        missing = [r["page_number"] for r in region_payload if r["page_number"] not in page_path_map]
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing submission page(s) for region mapping: {missing}")

        out_path = out_dir / f"{question.label}.png"
        crop_regions_and_stitch(page_path_map, region_payload, out_path)
        row = AnswerCrop(submission_id=submission.id, question_id=question.id, image_path=str(out_path))
        session.add(row)
        count += 1

    submission.status = SubmissionStatus.CROPS_READY
    session.add(submission)
    session.commit()
    return {"message": "Crops built", "count": count}


def _transcribe_submission(submission: Submission, provider: str, session: Session) -> dict:
    if submission.status not in (SubmissionStatus.CROPS_READY, SubmissionStatus.TRANSCRIBED, SubmissionStatus.GRADED):
        raise HTTPException(status_code=400, detail="Submission must be CROPS_READY")

    try:
        ocr = get_ocr_provider(provider)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    crops = session.exec(select(AnswerCrop).where(AnswerCrop.submission_id == submission.id)).all()
    session.exec(delete(Transcription).where(Transcription.submission_id == submission.id))

    for crop in crops:
        result = ocr.transcribe(Path(crop.image_path))
        session.add(
            Transcription(
                submission_id=submission.id,
                question_id=crop.question_id,
                provider=provider,
                text=result.text,
                confidence=result.confidence,
                raw_json=json.dumps(result.raw),
            )
        )

    submission.status = SubmissionStatus.TRANSCRIBED
    session.add(submission)
    session.commit()
    return {"message": "Transcription complete", "count": len(crops), "provider": provider}


def _prepare_status(submission: Submission, session: Session, actions_run: list[str] | None = None) -> SubmissionPrepareStatus:
    questions = session.exec(select(Question).where(Question.exam_id == submission.exam_id).order_by(Question.id)).all()
    pages = session.exec(select(SubmissionPage).where(SubmissionPage.submission_id == submission.id)).all()
    crops = session.exec(select(AnswerCrop).where(AnswerCrop.submission_id == submission.id)).all()
    transcriptions = session.exec(select(Transcription).where(Transcription.submission_id == submission.id)).all()
    grades = session.exec(select(GradeResult).where(GradeResult.submission_id == submission.id)).all()

    page_numbers = {page.page_number for page in pages if Path(page.image_path).exists()}
    crop_map = {crop.question_id: crop for crop in crops if Path(crop.image_path).exists()}
    transcription_map = {item.question_id: item for item in transcriptions}
    grade_map = {grade.question_id: grade for grade in grades}

    missing_page_numbers: set[int] = set()
    question_statuses: list[SubmissionPrepareQuestionStatus] = []
    summary_reasons: list[str] = []
    suggested_actions: list[str] = []
    blocked_actions: list[str] = []
    unsafe_to_retry_reasons: list[str] = []

    if not pages:
        summary_reasons.append("No submission pages have been built yet.")
        suggested_actions.append("build_pages")

    for question in questions:
        regions = session.exec(select(QuestionRegion).where(QuestionRegion.question_id == question.id)).all()
        flagged_reasons: list[str] = []
        blocking_reasons: list[str] = []
        asset_state = "ready"
        has_regions = len(regions) > 0
        if not has_regions:
            flagged_reasons.append("No template regions saved for this question.")
            asset_state = "missing_regions"

        region_page_numbers = sorted({region.page_number for region in regions})
        missing_for_question = [page_number for page_number in region_page_numbers if page_number not in page_numbers]
        if missing_for_question:
            missing_page_numbers.update(missing_for_question)
            flagged_reasons.append(f"Missing submission page(s): {', '.join(str(n) for n in missing_for_question)}.")
            asset_state = "missing_pages"

        latest_region_change = max((region.created_at for region in regions), default=None)
        crop = crop_map.get(question.id)
        stale_crop = bool(crop and latest_region_change and crop.created_at < latest_region_change)
        if has_regions and not missing_for_question and not crop:
            flagged_reasons.append("Answer crop has not been built yet.")
            asset_state = "missing_crop"
        elif stale_crop:
            flagged_reasons.append("Answer crop is stale because the template regions changed after it was built.")
            asset_state = "stale_crop"

        transcription = transcription_map.get(question.id)
        stale_transcription = bool(
            transcription and (
                stale_crop
                or (crop and transcription.created_at < crop.created_at)
                or (latest_region_change and transcription.created_at < latest_region_change)
            )
        )
        if crop and not stale_crop and not transcription:
            flagged_reasons.append("Transcription has not been generated yet.")
            if asset_state == "ready":
                asset_state = "missing_transcription"
        elif transcription and stale_transcription:
            flagged_reasons.append("Transcription is stale because the crop/template changed after it was generated.")
            if asset_state == "ready":
                asset_state = "stale_transcription"
        elif transcription and not stale_transcription and not transcription.text.strip():
            flagged_reasons.append("Transcription is empty.")
            if asset_state == "ready":
                asset_state = "empty_transcription"
        elif transcription and not stale_transcription and transcription.confidence < 0.5:
            flagged_reasons.append("Transcription confidence is low.")
            if asset_state == "ready":
                asset_state = "low_confidence_transcription"

        grade = grade_map.get(question.id)
        has_manual_grade = bool(grade and grade.model_name == "teacher_manual")
        if has_manual_grade and asset_state in {"stale_crop", "stale_transcription", "missing_crop", "missing_transcription"}:
            blocking_reasons.append("Automatic recovery is blocked because teacher manual marking has already started for this question.")

        question_statuses.append(SubmissionPrepareQuestionStatus(
            question_id=question.id,
            question_label=question.label,
            ready=not flagged_reasons,
            flagged_reasons=flagged_reasons,
            blocking_reasons=blocking_reasons,
            asset_state=asset_state,
            has_regions=has_regions,
            has_crop=bool(crop),
            has_transcription=bool(transcription),
            has_manual_grade=has_manual_grade,
            stale_crop=stale_crop,
            stale_transcription=stale_transcription,
            transcription_confidence=transcription.confidence if transcription else None,
        ))

    if any(not question.has_regions for question in question_statuses):
        summary_reasons.append("Some questions are missing template regions, so crops cannot be prepared automatically.")
    if missing_page_numbers:
        summary_reasons.append(f"Some template regions point at submission page(s) that do not exist: {', '.join(str(n) for n in sorted(missing_page_numbers))}.")
    if any(question.asset_state in {"stale_crop", "stale_transcription"} for question in question_statuses):
        summary_reasons.append("Some prepared assets are stale because the template changed after they were built.")
    if pages and any(question.asset_state in {"missing_crop", "stale_crop"} for question in question_statuses):
        suggested_actions.append("build_crops")
    if any(question.asset_state in {"missing_transcription", "stale_transcription"} for question in question_statuses):
        suggested_actions.append("transcribe")

    manual_marked_questions = sum(1 for question in question_statuses if question.has_manual_grade)
    if any(question.blocking_reasons for question in question_statuses):
        blocked_actions.extend(["build_crops", "transcribe"])
        unsafe_to_retry_reasons.append("Teacher manual marks already exist on questions that would need assets rebuilt or re-transcribed.")
        summary_reasons.append("Automatic recovery is blocked on some questions to avoid overwriting work after manual marking started.")

    if not summary_reasons and any(not question.ready for question in question_statuses):
        summary_reasons.append("Some answers still need preparation before marking is friction-free.")

    deduped_actions = list(dict.fromkeys(suggested_actions))
    deduped_blocked_actions = list(dict.fromkeys(blocked_actions))
    deduped_unsafe_reasons = list(dict.fromkeys(unsafe_to_retry_reasons))
    questions_ready = sum(1 for question in question_statuses if question.ready)
    has_region_blockers = any(not question.has_regions for question in question_statuses)
    has_manual_recovery_blockers = any(question.blocking_reasons for question in question_statuses)
    can_prepare_now = not has_region_blockers and (not pages or not missing_page_numbers) and not has_manual_recovery_blockers

    return SubmissionPrepareStatus(
        submission_id=submission.id,
        ready_for_marking=questions_ready == len(question_statuses),
        can_prepare_now=can_prepare_now,
        summary_reasons=summary_reasons,
        suggested_actions=deduped_actions,
        blocked_actions=deduped_blocked_actions,
        unsafe_to_retry_reasons=deduped_unsafe_reasons,
        questions_total=len(question_statuses),
        questions_ready=questions_ready,
        manual_marked_questions=manual_marked_questions,
        pages_count=len(page_numbers),
        missing_page_numbers=sorted(missing_page_numbers),
        actions_run=actions_run or [],
        questions=question_statuses,
    )

@router.get("/{submission_id}", response_model=SubmissionRead)
def get_submission(submission_id: int, session: Session = Depends(get_session)) -> SubmissionRead:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    files = session.exec(select(SubmissionFile).where(SubmissionFile.submission_id == submission_id)).all()
    pages = session.exec(select(SubmissionPage).where(SubmissionPage.submission_id == submission_id)).all()

    return _submission_read(submission, files, pages)


@router.get("/{submission_id}/files", response_model=list[StoredFileRead])
def list_submission_files(submission_id: int, session: Session = Depends(get_session)) -> list[StoredFileRead]:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    rows = session.exec(select(SubmissionFile).where(SubmissionFile.submission_id == submission_id).order_by(SubmissionFile.id)).all()
    return [
        StoredFileRead(
            id=row.id,
            original_filename=row.original_filename,
            stored_path=row.stored_path,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            signed_url=_resolve_signed_url(row.stored_path),
        )
        for row in rows
    ]




@router.post("/{submission_id}/files/register", response_model=BlobRegisterResponse)
def register_submission_files(submission_id: int, payload: BlobRegisterRequest, session: Session = Depends(get_session)) -> BlobRegisterResponse:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    registered = 0
    for file in payload.files:
        lower_name = file.original_filename.lower()
        if lower_name.endswith(".pdf"):
            kind = "pdf"
        elif lower_name.endswith((".png", ".jpg", ".jpeg")):
            kind = "image"
        else:
            kind = "image" if file.content_type.startswith("image/") else "pdf" if file.content_type == "application/pdf" else "binary"

        row = SubmissionFile(
            submission_id=submission_id,
            file_kind=kind,
            original_filename=file.original_filename,
            stored_path=normalize_blob_path(file.blob_pathname),
            content_type=file.content_type,
            size_bytes=file.size_bytes,
        )
        session.add(row)
        registered += 1

    session.commit()
    return BlobRegisterResponse(registered=registered)


@router.post("/{submission_id}/build-pages", response_model=list[SubmissionPageRead])
def build_pages(submission_id: int, session: Session = Depends(get_session)) -> list[SubmissionPageRead]:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    return _build_pages_for_submission(submission, session)


@router.post("/{submission_id}/build-crops")
def build_crops(submission_id: int, session: Session = Depends(get_session)) -> dict:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    return _build_crops_for_submission(submission, session)


@router.post("/{submission_id}/transcribe")
def transcribe_submission(
    submission_id: int,
    provider: str = Query("stub"),
    session: Session = Depends(get_session),
) -> dict:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    return _transcribe_submission(submission, provider, session)


@router.post("/{submission_id}/grade")
def grade_submission(
    submission_id: int,
    grader: str = Query("rule_based"),
    session: Session = Depends(get_session),
) -> dict:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    if submission.status not in (SubmissionStatus.TRANSCRIBED, SubmissionStatus.GRADED):
        raise HTTPException(status_code=400, detail="Submission must be TRANSCRIBED")

    try:
        grader_impl = get_grader(grader)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    transcriptions = session.exec(select(Transcription).where(Transcription.submission_id == submission_id)).all()
    question_map = {
        q.id: q
        for q in session.exec(select(Question).where(Question.exam_id == submission.exam_id)).all()
    }
    session.exec(delete(GradeResult).where(GradeResult.submission_id == submission_id))

    for transcription in transcriptions:
        question = question_map.get(transcription.question_id)
        if not question:
            continue
        rubric = json.loads(question.rubric_json)
        try:
            outcome = grader_impl.grade(transcription.text, rubric, question.max_marks)
        except NotImplementedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session.add(
            GradeResult(
                submission_id=submission_id,
                question_id=question.id,
                marks_awarded=outcome.marks_awarded,
                breakdown_json=json.dumps(outcome.breakdown),
                feedback_json=json.dumps(outcome.feedback),
                model_name=outcome.model_name,
            )
        )

    submission.status = SubmissionStatus.GRADED
    session.add(submission)
    session.commit()
    return {"message": "Grading complete", "grader": grader}




@router.get("/{submission_id}/prepare-status", response_model=SubmissionPrepareStatus)
def get_prepare_status(submission_id: int, session: Session = Depends(get_session)) -> SubmissionPrepareStatus:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    return _prepare_status(submission, session)


@router.post("/{submission_id}/prepare", response_model=SubmissionPrepareStatus)
def prepare_submission_for_marking(
    submission_id: int,
    provider: str = Query("stub"),
    session: Session = Depends(get_session),
) -> SubmissionPrepareStatus:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    actions_run: list[str] = []
    status = _prepare_status(submission, session, actions_run=actions_run)

    pages = session.exec(select(SubmissionPage).where(SubmissionPage.submission_id == submission_id)).all()
    if not status.ready_for_marking and not pages:
        _build_pages_for_submission(submission, session)
        session.refresh(submission)
        actions_run.append("build_pages")
        status = _prepare_status(submission, session, actions_run=actions_run)

    if not status.ready_for_marking and status.can_prepare_now and "build_crops" in status.suggested_actions:
        _build_crops_for_submission(submission, session)
        session.refresh(submission)
        actions_run.append("build_crops")
        status = _prepare_status(submission, session, actions_run=actions_run)

    if not status.ready_for_marking and status.can_prepare_now and "transcribe" in status.suggested_actions:
        _transcribe_submission(submission, provider, session)
        session.refresh(submission)
        actions_run.append("transcribe")
        status = _prepare_status(submission, session, actions_run=actions_run)

    return status


@router.get("/{submission_id}/page/{page_number}")
def get_page_image(submission_id: int, page_number: int, session: Session = Depends(get_session)) -> FileResponse:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    page = session.exec(
        select(SubmissionPage).where(SubmissionPage.submission_id == submission_id, SubmissionPage.page_number == page_number)
    ).first()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    image_path = Path(page.image_path)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Page image not found")

    return FileResponse(image_path)


@router.get("/{submission_id}/crop/{question_id}")
def get_crop_image(submission_id: int, question_id: int, session: Session = Depends(get_session)) -> FileResponse:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    crop = session.exec(
        select(AnswerCrop).where(AnswerCrop.submission_id == submission_id, AnswerCrop.question_id == question_id)
    ).first()
    if not crop:
        raise HTTPException(status_code=404, detail="Crop not found")

    image_path = Path(crop.image_path)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Crop image not found")

    return FileResponse(image_path)


@router.get("/{submission_id}/front-page-totals", response_model=FrontPageTotalsRead | None)
def get_front_page_totals(submission_id: int, session: Session = Depends(get_session)) -> FrontPageTotalsRead | None:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    return front_page_totals_read(submission)


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
    if scale and scale > 1:
        normalized = numeric / scale
        if 0 <= normalized <= 1:
            return normalized
    return None


def _candidate_value(payload: object, *, page_width: float | None = None, page_height: float | None = None) -> FrontPageCandidateValue | None:
    if not isinstance(payload, dict):
        return None
    evidence_rows: list[FrontPageExtractionEvidence] = []
    raw_evidence = payload.get("evidence")
    if isinstance(raw_evidence, list):
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            x = _normalize_front_page_coordinate(item.get("x"), scale=page_width)
            y = _normalize_front_page_coordinate(item.get("y"), scale=page_height)
            w = _normalize_front_page_coordinate(item.get("w"), scale=page_width)
            h = _normalize_front_page_coordinate(item.get("h"), scale=page_height)
            evidence_rows.append(
                FrontPageExtractionEvidence(
                    page_number=int(item.get("page_number") or 1),
                    quote=str(item.get("quote") or ""),
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                )
            )
    return FrontPageCandidateValue(
        value_text=str(payload.get("value_text") or ""),
        confidence=float(payload.get("confidence") or 0),
        evidence=evidence_rows,
    )


@router.get("/{submission_id}/front-page-totals-candidates", response_model=FrontPageTotalsCandidateRead)
def get_front_page_totals_candidates(submission_id: int, session: Session = Depends(get_session)) -> FrontPageTotalsCandidateRead:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    page = session.exec(
        select(SubmissionPage).where(SubmissionPage.submission_id == submission_id).order_by(SubmissionPage.page_number)
    ).first()
    if not page:
        raise HTTPException(status_code=400, detail="Build submission pages first")

    image_path = Path(page.image_path)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Front page image not found")

    extractor = get_front_page_totals_extractor()
    try:
        result = extractor.extract(image_path=image_path, request_id=f"submission-{submission_id}-front-page")
    except OpenAIRequestError as exc:
        logger.warning("front-page totals extractor failed for submission %s: %s", submission_id, exc)
        return FrontPageTotalsCandidateRead(
            objective_scores=[],
            warnings=["Extractor unavailable for this paper right now. You can still confirm totals manually."],
            source="extractor_unavailable",
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("unexpected front-page totals extractor failure for submission %s", submission_id)
        return FrontPageTotalsCandidateRead(
            objective_scores=[],
            warnings=["Extractor failed for this paper. You can still confirm totals manually."],
            source="extractor_unavailable",
        )

    objective_scores: list[FrontPageObjectiveScoreCandidate] = []
    raw_objectives = result.payload.get("objective_scores")
    if isinstance(raw_objectives, list):
        for item in raw_objectives:
            if not isinstance(item, dict):
                continue
            objective_code = _candidate_value(item.get("objective_code"), page_width=page.width, page_height=page.height)
            marks_awarded = _candidate_value(item.get("marks_awarded"), page_width=page.width, page_height=page.height)
            max_marks = _candidate_value(item.get("max_marks"), page_width=page.width, page_height=page.height)
            objective_code, marks_awarded, max_marks = _normalize_objective_candidate(objective_code, marks_awarded, max_marks)
            if objective_code is None or marks_awarded is None:
                continue
            objective_scores.append(
                FrontPageObjectiveScoreCandidate(
                    objective_code=objective_code,
                    marks_awarded=marks_awarded,
                    max_marks=max_marks,
                )
            )

    warnings = result.payload.get("warnings")
    return FrontPageTotalsCandidateRead(
        student_name=_candidate_value(result.payload.get("student_name"), page_width=page.width, page_height=page.height),
        overall_marks_awarded=_candidate_value(result.payload.get("overall_marks_awarded"), page_width=page.width, page_height=page.height),
        overall_max_marks=_candidate_value(result.payload.get("overall_max_marks"), page_width=page.width, page_height=page.height),
        objective_scores=objective_scores,
        warnings=[str(item) for item in warnings] if isinstance(warnings, list) else [],
        source=result.model,
    )


@router.put("/{submission_id}/front-page-totals", response_model=FrontPageTotalsRead)
def upsert_front_page_totals(
    submission_id: int,
    payload: FrontPageTotalsUpsert,
    session: Session = Depends(get_session),
) -> FrontPageTotalsRead:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    cleaned_scores: list[dict[str, float | str | None]] = []
    for score in payload.objective_scores:
        objective_code = score.objective_code.strip()
        if not objective_code:
            continue
        cleaned_scores.append({
            "objective_code": objective_code,
            "marks_awarded": float(score.marks_awarded),
            "max_marks": float(score.max_marks) if score.max_marks is not None else None,
        })

    submission.capture_mode = SubmissionCaptureMode.FRONT_PAGE_TOTALS
    submission.front_page_totals_json = json.dumps({
        "overall_marks_awarded": float(payload.overall_marks_awarded),
        "overall_max_marks": float(payload.overall_max_marks) if payload.overall_max_marks is not None else None,
        "objective_scores": cleaned_scores,
        "teacher_note": payload.teacher_note.strip(),
        "confirmed": bool(payload.confirmed),
    })
    submission.front_page_reviewed_at = utcnow() if payload.confirmed else None
    submission.status = SubmissionStatus.GRADED if payload.confirmed else SubmissionStatus.UPLOADED
    session.add(submission)
    session.commit()
    session.refresh(submission)
    return front_page_totals_read(submission)


@router.put("/{submission_id}/questions/{question_id}/manual-grade", response_model=GradeResultRead)
def upsert_manual_grade(
    submission_id: int,
    question_id: int,
    payload: ManualGradeUpsert,
    session: Session = Depends(get_session),
) -> GradeResultRead:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    question = session.get(Question, question_id)
    if not question or question.exam_id != submission.exam_id:
        raise HTTPException(status_code=404, detail="Question not found for submission exam")

    marks_awarded = max(0.0, min(float(payload.marks_awarded), float(question.max_marks)))
    teacher_note = payload.teacher_note.strip()
    rubric = json.loads(question.rubric_json)
    breakdown = {
        "source": "teacher_manual",
        "objective_codes": rubric.get("objective_codes", []),
        "max_marks": question.max_marks,
        "teacher_note": teacher_note,
    }
    feedback = {
        "teacher_note": teacher_note,
    }

    grade = session.exec(
        select(GradeResult).where(GradeResult.submission_id == submission_id, GradeResult.question_id == question_id)
    ).first()
    if grade:
        grade.marks_awarded = marks_awarded
        grade.breakdown_json = json.dumps(breakdown)
        grade.feedback_json = json.dumps(feedback)
        grade.model_name = "teacher_manual"
    else:
        grade = GradeResult(
            submission_id=submission_id,
            question_id=question_id,
            marks_awarded=marks_awarded,
            breakdown_json=json.dumps(breakdown),
            feedback_json=json.dumps(feedback),
            model_name="teacher_manual",
        )

    session.add(grade)
    submission.capture_mode = SubmissionCaptureMode.QUESTION_LEVEL
    submission.status = SubmissionStatus.GRADED
    session.add(submission)
    session.commit()
    session.refresh(grade)

    return GradeResultRead(
        id=grade.id,
        submission_id=grade.submission_id,
        question_id=grade.question_id,
        marks_awarded=grade.marks_awarded,
        breakdown_json=json.loads(grade.breakdown_json),
        feedback_json=json.loads(grade.feedback_json),
        model_name=grade.model_name,
    )


@router.get("/{submission_id}/results", response_model=SubmissionResults)
def get_results(submission_id: int, session: Session = Depends(get_session)) -> SubmissionResults:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    front_page_totals = front_page_totals_read(submission)
    if submission.capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS:
        total_possible = (
            float(front_page_totals.overall_max_marks)
            if front_page_totals and front_page_totals.overall_max_marks is not None
            else 0.0
        )
        total_score = float(front_page_totals.overall_marks_awarded) if front_page_totals else 0.0
        return SubmissionResults(
            submission_id=submission_id,
            capture_mode=submission.capture_mode,
            total_score=round(total_score, 2),
            total_possible=round(total_possible, 2),
            objective_totals=objective_totals_read(front_page_objective_totals(front_page_totals)),
            front_page_totals=front_page_totals,
            transcriptions=[],
            grades=[],
        )

    transcriptions = session.exec(select(Transcription).where(Transcription.submission_id == submission_id)).all()
    grades = session.exec(select(GradeResult).where(GradeResult.submission_id == submission_id)).all()
    questions = session.exec(select(Question).where(Question.exam_id == submission.exam_id).order_by(Question.id)).all()
    grade_map = {grade.question_id: grade for grade in grades}
    objective_totals: dict[str, dict[str, float | int]] = {}
    for question in questions:
        awarded = float(grade_map.get(question.id).marks_awarded) if grade_map.get(question.id) else 0.0
        accumulate_objective_totals(objective_totals, question, awarded)

    return SubmissionResults(
        submission_id=submission_id,
        capture_mode=submission.capture_mode,
        total_score=round(sum(float(grade.marks_awarded) for grade in grades), 2),
        total_possible=round(sum(float(question.max_marks) for question in questions), 2),
        objective_totals=objective_totals_read(objective_totals),
        front_page_totals=front_page_totals,
        transcriptions=[
            TranscriptionRead(
                id=t.id,
                submission_id=t.submission_id,
                question_id=t.question_id,
                provider=t.provider,
                text=t.text,
                confidence=t.confidence,
                raw_json=json.loads(t.raw_json),
            )
            for t in transcriptions
        ],
        grades=[
            GradeResultRead(
                id=g.id,
                submission_id=g.submission_id,
                question_id=g.question_id,
                marks_awarded=g.marks_awarded,
                breakdown_json=json.loads(g.breakdown_json),
                feedback_json=json.loads(g.feedback_json),
                model_name=g.model_name,
            )
            for g in grades
        ],
    )
