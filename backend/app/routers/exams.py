"""Exam and question management endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx

from PIL import Image, ImageOps

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import Session, delete, select

from app.blob_service import create_signed_blob_url, normalize_blob_path
from app.blob_service import BlobDownloadError
from app.ai.openai_vision import (
    AnswerKeyParser,
    BulkNameDetectionResult,
    OpenAIRequestError,
    ParseResult,
    SchemaBuildError,
    build_answer_key_response_schema,
    get_answer_key_parser,
    get_bulk_name_detector,
)
from app.db import get_session
from app.models import BulkUploadPage, Exam, ExamBulkUploadFile, ExamKeyFile, ExamKeyPage, ExamKeyParseJob, ExamKeyParsePage, ExamStatus, Question, QuestionParseEvidence, QuestionRegion, Submission, SubmissionFile, SubmissionPage, SubmissionStatus, utcnow
from app.schemas import BlobRegisterRequest, BlobRegisterResponse, BulkUploadCandidate, BulkUploadFinalizeRequest, BulkUploadFinalizeResponse, BulkUploadPreviewResponse, ExamCreate, ExamDetail, ExamKeyPageRead, ExamKeyUploadResponse, ExamParseJobRead, ExamRead, NameEvidence, QuestionCreate, QuestionRead, QuestionUpdate, RegionRead, StoredFileRead, SubmissionFileRead, SubmissionPageRead, SubmissionRead
from app.settings import settings
from app.storage import ensure_dir, reset_dir, relative_to_data
from app.storage_provider import get_storage_provider, get_storage_signed_url, materialize_object_to_path
from app.blob_store import BlobUploadError, upload_bytes

router = APIRouter(prefix="/exams", tags=["exams"])
public_router = APIRouter(prefix="/exams", tags=["exams-public"])
logger = logging.getLogger(__name__)


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
_MAX_RENDERED_KEY_PAGES = 10
_VERCEL_SERVER_UPLOAD_LIMIT_BYTES = 4 * 1024 * 1024


def _exam_key_pages_dir(exam_id: int) -> Path:
    return settings.data_path / "exams" / str(exam_id) / "key_pages"


def _load_key_page_images(exam_id: int, session: Session) -> list[Path]:
    rows = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id).order_by(ExamKeyPage.page_number)).all()
    paths = [Path(row.image_path) for row in rows if Path(row.image_path).exists()]
    if paths:
        return paths

    legacy_dir = settings.data_path / "key_pages" / str(exam_id)
    if not legacy_dir.exists() or not legacy_dir.is_dir():
        return []
    return [path for path in sorted(legacy_dir.iterdir()) if path.suffix.lower() in {".png", ".jpg", ".jpeg"}]



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




def _get_exam_question_or_404(exam_id: int, question_id: int, session: Session) -> Question:
    question = session.get(Question, question_id)
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


def build_key_pages_for_exam(exam_id: int, session: Session) -> list[Path]:
    stage = "load_key_files"
    try:
        existing = _load_key_page_images(exam_id, session)
        if existing:
            return existing

        key_files = session.exec(select(ExamKeyFile).where(ExamKeyFile.exam_id == exam_id).order_by(ExamKeyFile.id)).all()
        if not key_files:
            raise HTTPException(status_code=400, detail=f"No key files uploaded. Call /api/exams/{exam_id}/key/upload first.")

        output_dir = reset_dir(_exam_key_pages_dir(exam_id))
        session.exec(delete(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id))

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
                session.add(ExamKeyPage(exam_id=exam_id, page_number=page_num, image_path=str(out_path), width=width, height=height))
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
                    session.add(ExamKeyPage(exam_id=exam_id, page_number=page_num, image_path=str(rendered), width=width, height=height))
                    created_paths.append(rendered)
                    page_num += 1

        session.commit()

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

        criteria = question.get("criteria", [])
        if not isinstance(criteria, list):
            raise ValueError("question criteria must be list")
        question["criteria"] = [c for c in criteria if isinstance(c, dict) and isinstance(c.get("marks"), (int, float))]

        evidence = question.get("evidence", [])
        if not isinstance(evidence, list):
            question["evidence"] = []

    return float(confidence), questions, warnings


def _allowed_parse_models() -> list[str]:
    configured = os.getenv("SUPERMARKS_KEY_PARSE_MODELS", "gpt-5-nano,gpt-5-mini")
    models = [m.strip() for m in configured.split(",") if m.strip()]
    return models


def _resolve_models() -> tuple[str, str]:
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
def create_exam(payload: ExamCreate, session: Session = Depends(get_session)) -> Exam:
    exam = Exam(name=payload.name)
    session.add(exam)
    session.commit()
    session.refresh(exam)
    return exam


@router.get("", response_model=list[ExamRead])
def list_exams(session: Session = Depends(get_session)) -> list[Exam]:
    exams = session.exec(select(Exam).order_by(Exam.created_at.desc(), Exam.id.desc())).all()
    return list(exams)


@router.get("/{exam_id}", response_model=ExamDetail)
def get_exam(exam_id: int, session: Session = Depends(get_session)) -> ExamDetail:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    submissions = session.exec(select(Submission).where(Submission.exam_id == exam_id).order_by(Submission.created_at.desc(), Submission.id.desc())).all()
    parse_jobs = session.exec(select(ExamKeyParseJob).where(ExamKeyParseJob.exam_id == exam_id).order_by(ExamKeyParseJob.created_at.desc(), ExamKeyParseJob.id.desc())).all()
    key_files = session.exec(select(ExamKeyFile).where(ExamKeyFile.exam_id == exam_id).order_by(ExamKeyFile.id)).all()

    submission_reads: list[SubmissionRead] = []
    for sub in submissions:
        files = session.exec(select(SubmissionFile).where(SubmissionFile.submission_id == sub.id).order_by(SubmissionFile.id)).all()
        submission_reads.append(
            SubmissionRead(
                id=sub.id,
                exam_id=sub.exam_id,
                student_name=sub.student_name,
                status=sub.status,
                created_at=sub.created_at,
                files=[SubmissionFileRead(id=f.id, file_kind=f.file_kind, original_filename=f.original_filename, stored_path=f.stored_path, blob_url=f.blob_url, content_type=f.content_type, size_bytes=f.size_bytes) for f in files],
                pages=[],
            )
        )

    return ExamDetail(
        exam=ExamRead(
            id=exam.id,
            name=exam.name,
            created_at=exam.created_at,
            teacher_style_profile_json=exam.teacher_style_profile_json,
            status=exam.status,
        ),
        key_files=[
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
        ],
        submissions=submission_reads,
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


@router.get("/{exam_id}/submissions", response_model=list[SubmissionRead])
def list_exam_submissions(exam_id: int, session: Session = Depends(get_session)) -> list[SubmissionRead]:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    submissions = session.exec(
        select(Submission)
        .where(Submission.exam_id == exam_id)
        .order_by(Submission.created_at.desc(), Submission.id.desc())
    ).all()

    output: list[SubmissionRead] = []
    for sub in submissions:
        files = session.exec(select(SubmissionFile).where(SubmissionFile.submission_id == sub.id).order_by(SubmissionFile.id)).all()
        output.append(
            SubmissionRead(
                id=sub.id,
                exam_id=sub.exam_id,
                student_name=sub.student_name,
                status=sub.status,
                created_at=sub.created_at,
                files=[SubmissionFileRead(id=f.id, file_kind=f.file_kind, original_filename=f.original_filename, stored_path=f.stored_path, blob_url=f.blob_url, content_type=f.content_type, size_bytes=f.size_bytes) for f in files],
                pages=[],
            )
        )
    return output


@router.post("/{exam_id}/submissions", response_model=SubmissionRead, status_code=status.HTTP_201_CREATED)
async def create_submission(
    exam_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> SubmissionRead:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    student_name = ""
    files: list[UploadFile] = []
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        student_name = str(payload.get("student_name", "")).strip() if isinstance(payload, dict) else ""
    else:
        form = await request.form()
        student_name = str(form.get("student_name", "")).strip()
        files = [item for item in form.getlist("files") if hasattr(item, "filename") and hasattr(item, "file")]

    if not student_name:
        raise HTTPException(status_code=400, detail="student_name is required")

    submission = Submission(exam_id=exam_id, student_name=student_name, status=SubmissionStatus.UPLOADED)
    session.add(submission)
    session.commit()
    session.refresh(submission)

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
            row = SubmissionFile(
                submission_id=submission.id,
                file_kind=kind,
                original_filename=filename,
                stored_path=stored["key"],
                content_type=upload_content_type,
                size_bytes=size,
            )
            session.add(row)
            session.flush()
            created_files.append(SubmissionFileRead(id=row.id, file_kind=row.file_kind, original_filename=row.original_filename, stored_path=row.stored_path))
        session.commit()

    return SubmissionRead(
        id=submission.id,
        exam_id=submission.exam_id,
        student_name=submission.student_name,
        status=submission.status,
        created_at=submission.created_at,
        files=created_files,
        pages=[],
    )



@router.post("/{exam_id}/key/register", response_model=BlobRegisterResponse)
def register_exam_key_files(exam_id: int, payload: BlobRegisterRequest, session: Session = Depends(get_session)) -> BlobRegisterResponse:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    registered = 0
    for file in payload.files:
        row = ExamKeyFile(
            exam_id=exam_id,
            original_filename=_sanitize_filename(file.original_filename),
            stored_path=normalize_blob_path(file.blob_pathname),
            content_type=file.content_type,
            size_bytes=file.size_bytes,
        )
        session.add(row)
        registered += 1

    if registered > 0:
        exam.status = ExamStatus.KEY_UPLOADED
        session.add(exam)
    session.commit()
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

    def finalize(end_page: int, needs_review: bool = False) -> None:
        nonlocal candidates, current_start, confidences, last_evidence
        if end_page < current_start:
            return
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        candidate = BulkUploadCandidate(
            candidate_id=uuid.uuid4().hex,
            student_name=current_name,
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


@router.post("/{exam_id}/submissions/bulk", response_model=BulkUploadPreviewResponse, status_code=status.HTTP_201_CREATED)
def create_bulk_submission_preview(
    exam_id: int,
    file: UploadFile = File(...),
    name_hint_regex: str | None = Form(default=None),
    roster: str | None = Form(default=None),
    min_pages_per_student: int = Form(default=1),
    session: Session = Depends(get_session),
) -> BulkUploadPreviewResponse:
    _ = name_hint_regex
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    filename = _sanitize_filename(file.filename or "bulk.pdf")
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Bulk upload requires a single PDF file")

    payload = file.file.read()
    storage = get_storage_provider()
    bulk = ExamBulkUploadFile(exam_id=exam_id, original_filename=filename, stored_path="")
    session.add(bulk)
    session.commit()
    session.refresh(bulk)

    object_key = f"exams/{exam_id}/bulk/{bulk.id}/{uuid.uuid4().hex}_{filename}"
    stored = _run_async(storage.put_bytes(object_key, payload, content_type=file.content_type or "application/pdf"))
    bulk.stored_path = stored["key"]
    session.add(bulk)
    session.commit()

    output_dir = reset_dir(_bulk_pages_dir(exam_id, bulk.id))
    try:
        source_path = _run_async(materialize_object_to_path(bulk.stored_path, settings.data_path / "cache" / "bulk" / str(exam_id) / str(bulk.id)))
    except BlobDownloadError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    rendered_paths = _render_pdf_pages(source_path, output_dir, start_page_number=1, max_pages=500)
    session.exec(delete(BulkUploadPage).where(BulkUploadPage.bulk_upload_id == bulk.id))

    detections: list[BulkNameDetectionResult] = []
    detector = get_bulk_name_detector()
    for idx, page_path in enumerate(rendered_paths, start=1):
        with Image.open(page_path) as image:
            w, h = image.width, image.height
        detection: BulkNameDetectionResult | None = None
        try:
            detection = detector.detect(page_path, idx, model="gpt-5-nano", request_id=uuid.uuid4().hex)
            if detection.student_name is None or detection.confidence < 0.5:
                detection = detector.detect(page_path, idx, model="gpt-5-mini", request_id=uuid.uuid4().hex)
        except OpenAIRequestError:
            detection = BulkNameDetectionResult(page_number=idx, student_name=None, confidence=0.0, evidence=None)
        row = BulkUploadPage(
            bulk_upload_id=bulk.id,
            page_number=idx,
            image_path=str(page_path),
            width=w,
            height=h,
            detected_student_name=detection.student_name,
            detection_confidence=detection.confidence,
            detection_evidence_json=json.dumps(detection.evidence or {}),
        )
        session.add(row)
        detections.append(detection)

    session.commit()

    roster_list: list[str] = []
    if roster:
        try:
            maybe_json = json.loads(roster)
            if isinstance(maybe_json, list):
                roster_list = [str(item).strip() for item in maybe_json if str(item).strip()]
        except json.JSONDecodeError:
            roster_list = [line.strip() for line in roster.splitlines() if line.strip()]

    candidates, warnings = _segment_bulk_candidates(detections, roster=roster_list, min_pages_per_student=max(min_pages_per_student, 1))
    return BulkUploadPreviewResponse(
        bulk_upload_id=bulk.id,
        page_count=len(rendered_paths),
        candidates=candidates,
        warnings=warnings,
    )


@router.get("/{exam_id}/submissions/bulk/{bulk_upload_id}", response_model=BulkUploadPreviewResponse)
def get_bulk_submission_preview(exam_id: int, bulk_upload_id: int, session: Session = Depends(get_session)) -> BulkUploadPreviewResponse:
    bulk = session.get(ExamBulkUploadFile, bulk_upload_id)
    if not bulk or bulk.exam_id != exam_id:
        raise HTTPException(status_code=404, detail="Bulk upload not found")

    pages = session.exec(select(BulkUploadPage).where(BulkUploadPage.bulk_upload_id == bulk_upload_id).order_by(BulkUploadPage.page_number)).all()
    detections = [BulkNameDetectionResult(page_number=p.page_number, student_name=p.detected_student_name, confidence=p.detection_confidence, evidence=json.loads(p.detection_evidence_json or "{}")) for p in pages]
    candidates, warnings = _segment_bulk_candidates(detections, roster=[], min_pages_per_student=1)
    return BulkUploadPreviewResponse(bulk_upload_id=bulk_upload_id, page_count=len(pages), candidates=candidates, warnings=warnings)


@router.get("/{exam_id}/submissions/bulk/{bulk_upload_id}/page/{page_number}")
def get_bulk_upload_page_image(exam_id: int, bulk_upload_id: int, page_number: int, session: Session = Depends(get_session)) -> FileResponse:
    bulk = session.get(ExamBulkUploadFile, bulk_upload_id)
    if not bulk or bulk.exam_id != exam_id:
        raise HTTPException(status_code=404, detail="Bulk upload not found")

    row = session.exec(
        select(BulkUploadPage).where(BulkUploadPage.bulk_upload_id == bulk_upload_id, BulkUploadPage.page_number == page_number)
    ).first()
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
    session: Session = Depends(get_session),
) -> BulkUploadFinalizeResponse:
    bulk = session.get(ExamBulkUploadFile, bulk_upload_id)
    if not bulk or bulk.exam_id != exam_id:
        raise HTTPException(status_code=404, detail="Bulk upload not found")

    pages = session.exec(select(BulkUploadPage).where(BulkUploadPage.bulk_upload_id == bulk_upload_id).order_by(BulkUploadPage.page_number)).all()
    if not pages:
        raise HTTPException(status_code=400, detail="No rendered pages available")

    page_map = {p.page_number: p for p in pages}
    max_page = pages[-1].page_number
    used_pages: set[int] = set()
    warnings: list[str] = []
    created: list[SubmissionRead] = []

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

    for candidate in payload.candidates:
        submission = Submission(exam_id=exam_id, student_name=candidate.student_name, status=SubmissionStatus.UPLOADED)
        session.add(submission)
        session.flush()

        page_reads = []
        file_row = SubmissionFile(
            submission_id=submission.id,
            file_kind="pdf",
            original_filename=bulk.original_filename,
            stored_path=bulk.stored_path,
            content_type="application/pdf",
            size_bytes=0,
        )
        session.add(file_row)
        session.flush()

        for idx, page_num in enumerate(range(candidate.page_start, candidate.page_end + 1), start=1):
            src = page_map[page_num]
            sp = SubmissionPage(submission_id=submission.id, page_number=idx, image_path=src.image_path, width=src.width, height=src.height)
            session.add(sp)
            session.flush()
            page_reads.append(SubmissionPageRead(id=sp.id, page_number=idx, image_path=relative_to_data(Path(src.image_path)), width=src.width, height=src.height))

        created.append(
            SubmissionRead(
                id=submission.id,
                exam_id=submission.exam_id,
                student_name=submission.student_name,
                status=submission.status,
                created_at=submission.created_at,
                files=[SubmissionFileRead(id=file_row.id, file_kind="pdf", original_filename=bulk.original_filename, stored_path=bulk.stored_path, blob_url=file_row.blob_url, content_type=file_row.content_type, size_bytes=file_row.size_bytes)],
                pages=page_reads,
            )
        )

    session.commit()
    return BulkUploadFinalizeResponse(submissions=created, warnings=warnings)


@router.post("/{exam_id}/key/upload", response_model=ExamKeyUploadResponse)
def upload_exam_key_files(
    exam_id: int,
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
) -> ExamKeyUploadResponse:
    exam = session.get(Exam, exam_id)
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
            raise HTTPException(status_code=413, detail="File too large for server upload on Vercel. Use client upload mode.")
        object_key = f"exams/{exam_id}/key/{uuid.uuid4().hex}_{filename}"
        try:
            stored = upload_bytes(object_key, payload, content_type)
        except BlobUploadError as exc:
            raise HTTPException(status_code=500, detail=f"Blob upload failed: {exc}") from exc

        row = ExamKeyFile(
            exam_id=exam_id,
            original_filename=filename,
            stored_path=stored["pathname"],
            blob_url=stored["url"],
            blob_pathname=stored["pathname"],
            content_type=stored["contentType"],
            size_bytes=len(payload),
        )
        session.add(row)
        uploaded += 1
        urls.append(stored["url"])

    exam.status = ExamStatus.KEY_UPLOADED
    session.add(exam)
    session.commit()
    return ExamKeyUploadResponse(uploaded=uploaded, urls=urls)


@router.get("/{exam_id}/key/files", response_model=list[StoredFileRead])
def list_exam_key_files(exam_id: int, session: Session = Depends(get_session)) -> list[StoredFileRead]:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    rows = session.exec(select(ExamKeyFile).where(ExamKeyFile.exam_id == exam_id).order_by(ExamKeyFile.id)).all()
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
def build_exam_key_pages(exam_id: int, session: Session = Depends(get_session)) -> list[ExamKeyPageRead]:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    stage = "build_key_pages"
    try:
        build_key_pages_for_exam(exam_id, session)
        exam.status = ExamStatus.KEY_PAGES_READY
        session.add(exam)
        session.commit()

        rows = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id).order_by(ExamKeyPage.page_number)).all()
        return [ExamKeyPageRead(id=r.id, exam_id=r.exam_id, page_number=r.page_number, image_path=relative_to_data(Path(r.image_path)), width=r.width, height=r.height) for r in rows]
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
def list_exam_key_pages(exam_id: int, session: Session = Depends(get_session)) -> list[ExamKeyPageRead]:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    rows = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id).order_by(ExamKeyPage.page_number)).all()
    return [ExamKeyPageRead(id=r.id, exam_id=r.exam_id, page_number=r.page_number, image_path=relative_to_data(Path(r.image_path)), width=r.width, height=r.height) for r in rows]


@router.post("/{exam_id}/questions", response_model=QuestionRead, status_code=status.HTTP_201_CREATED)
def create_question(exam_id: int, payload: QuestionCreate, session: Session = Depends(get_session)) -> QuestionRead:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    rubric = payload.rubric_json or {
        "total_marks": payload.max_marks,
        "criteria": [],
        "model_solution": "",
        "answer_key": "",
    }
    question = Question(
        exam_id=exam_id,
        label=payload.label,
        max_marks=payload.max_marks,
        rubric_json=json.dumps(rubric),
    )
    session.add(question)
    session.commit()
    session.refresh(question)

    return QuestionRead(
        id=question.id,
        exam_id=question.exam_id,
        label=question.label,
        max_marks=question.max_marks,
        rubric_json=rubric,
        regions=[],
    )


@router.get("/{exam_id}/questions", response_model=list[QuestionRead])
def list_questions(exam_id: int, session: Session = Depends(get_session), job_id: int | None = None) -> list[QuestionRead]:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    if job_id is not None:
        _get_job_for_exam_or_error(exam_id, job_id, session)

    questions = session.exec(select(Question).where(Question.exam_id == exam_id)).all()
    result: list[QuestionRead] = []
    for q in questions:
        regions = session.exec(select(QuestionRegion).where(QuestionRegion.question_id == q.id)).all()
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
    session: Session = Depends(get_session),
) -> QuestionRead:
    question = _get_exam_question_or_404(exam_id, question_id, session)

    if payload.label is not None:
        question.label = payload.label
    if payload.max_marks is not None:
        question.max_marks = payload.max_marks

    rubric = json.loads(question.rubric_json)
    if payload.rubric_json is not None:
        rubric = payload.rubric_json
        question.rubric_json = json.dumps(rubric)

    session.add(question)
    session.commit()
    session.refresh(question)

    regions = session.exec(select(QuestionRegion).where(QuestionRegion.question_id == question.id)).all()
    return QuestionRead(
        id=question.id,
        exam_id=question.exam_id,
        label=question.label,
        max_marks=question.max_marks,
        rubric_json=rubric,
        regions=[RegionRead(id=r.id, page_number=r.page_number, x=r.x, y=r.y, w=r.w, h=r.h) for r in regions],
    )


def _resolve_key_page_or_404(
    exam_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> tuple[Path, str]:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    page = session.exec(
        select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id, ExamKeyPage.page_number == page_number)
    ).first()
    if not page:
        raise HTTPException(status_code=404, detail="Key page not found")

    image_path = Path(page.image_path)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Key page image missing")

    media_type = "image/png"
    suffix = image_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        media_type = "image/jpeg"
    return image_path, media_type


@public_router.get("/{exam_id}/key/page/{page_number}")
def get_key_page_image(
    exam_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> FileResponse:
    image_path, media_type = _resolve_key_page_or_404(exam_id=exam_id, page_number=page_number, session=session)
    return FileResponse(path=image_path, media_type=media_type)


@public_router.get("/{exam_id}/questions/{question_id}/key-visual")
def get_question_key_visual(
    exam_id: int,
    question_id: int,
    session: Session = Depends(get_session),
) -> FileResponse:
    question = _get_exam_question_or_404(exam_id, question_id, session)
    rubric = json.loads(question.rubric_json)
    page_number = int(rubric.get("key_page_number") or 1)

    page = session.exec(
        select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id, ExamKeyPage.page_number == page_number)
    ).first()
    if not page:
        page = session.exec(
            select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id).order_by(ExamKeyPage.page_number)
        ).first()
    if not page:
        raise HTTPException(status_code=404, detail="Key page not found")

    image_path = Path(page.image_path)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Key page image missing")

    media_type = "image/png"
    if image_path.suffix.lower() in {".jpg", ".jpeg"}:
        media_type = "image/jpeg"
    return FileResponse(path=image_path, media_type=media_type)


def _extract_usage(result: ParseResult) -> tuple[int, int, float]:
    payload = result.payload if isinstance(result.payload, dict) else {}
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    cost = float(usage.get("cost") or usage.get("cost_usd") or 0.0)
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


def _upsert_questions_for_page(exam_id: int, page_number: int, questions_payload: list[dict[str, Any]], session: Session) -> list[dict[str, Any]]:
    existing = {q.label: q for q in session.exec(select(Question).where(Question.exam_id == exam_id)).all()}
    existing_labels = set(existing.keys())
    stored: list[dict[str, Any]] = []

    for parsed in questions_payload:
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
            "criteria": parsed.get("criteria", []),
            "answer_key": parsed.get("answer_key", ""),
            "model_solution": parsed.get("model_solution", ""),
            "question_text": parsed.get("question_text", ""),
            "marks_source": marks_source,
            "marks_confidence": marks_confidence,
            "warnings": parsed_warnings,
            "marks_reason": parsed.get("marks_reason", ""),
            "evidence": evidence_list,
            "needs_review": relabeled or bool(parsed.get("needs_review", False)),
            "source_page_number": page_number,
            "original_label": raw_label,
        }

        question = existing.get(label)
        if question:
            question.max_marks = max_marks
            question.rubric_json = json.dumps(rubric)
            session.add(question)
            session.flush()
        else:
            question = Question(exam_id=exam_id, label=label, max_marks=max_marks, rubric_json=json.dumps(rubric))
            session.add(question)
            session.flush()
            existing[label] = question

        session.exec(delete(QuestionParseEvidence).where(QuestionParseEvidence.question_id == question.id))
        for e in evidence_list:
            if not isinstance(e, dict):
                continue
            kind = str(e.get("kind") or "question_box")
            if kind not in {"question_box", "answer_box", "marks_box"}:
                continue
            session.add(QuestionParseEvidence(
                question_id=question.id,
                exam_id=exam_id,
                page_number=int(e.get("page_number") or page_number),
                x=float(e.get("x") or 0),
                y=float(e.get("y") or 0),
                w=float(e.get("w") or 0.1),
                h=float(e.get("h") or 0.1),
                evidence_kind=kind,
                confidence=float(e.get("confidence") or 0),
            ))

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


def _get_exam_or_404(exam_id: int, session: Session) -> Exam:
    exam = session.get(Exam, exam_id)
    if not exam:
        _raise_parse_validation_error(status_code=404, detail="Exam not found", exam_exists=False, job_exists=False)
    return exam


def _get_job_for_exam_or_error(exam_id: int, job_id: int, session: Session) -> ExamKeyParseJob:
    job = session.get(ExamKeyParseJob, job_id)
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


def _get_latest_job_for_exam(exam_id: int, session: Session) -> ExamKeyParseJob | None:
    return session.exec(
        select(ExamKeyParseJob)
        .where(ExamKeyParseJob.exam_id == exam_id)
        .order_by(ExamKeyParseJob.created_at.desc(), ExamKeyParseJob.id.desc())
    ).first()


def _job_has_remaining_work(job_id: int, session: Session) -> bool:
    remaining = session.exec(
        select(ExamKeyParsePage.id).where(
            ExamKeyParsePage.job_id == job_id,
            ExamKeyParsePage.status.in_(["pending", "running", "failed"]),
        )
    ).first()
    return remaining is not None


def _process_single_parse_page(
    *,
    exam_id: int,
    exam: Exam,
    job: ExamKeyParseJob,
    target_parse_page: ExamKeyParsePage,
    page_rows_by_number: dict[int, ExamKeyPage],
    session: Session,
    parser: AnswerKeyParser,
) -> dict[str, Any]:
    target_parse_page.status = "running"
    target_parse_page.updated_at = utcnow()
    session.add(target_parse_page)
    session.commit()

    page_number = target_parse_page.page_number
    key_page = page_rows_by_number.get(page_number)
    if key_page is None:
        target_parse_page.status = "failed"
        target_parse_page.error_json = {"detail": "Key page not found"}
        target_parse_page.updated_at = utcnow()
        session.add(target_parse_page)
        return {"page_number": page_number, "status": "failed", "cost": 0.0, "input_tokens": 0, "output_tokens": 0}

    nano_model, mini_model = _resolve_models()
    page_path = Path(key_page.image_path)
    if not page_path.exists():
        target_parse_page.status = "failed"
        target_parse_page.error_json = {"detail": "Page image missing"}
        target_parse_page.updated_at = utcnow()
        session.add(target_parse_page)
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
            confidence, questions_payload, warnings = _validate_parse_payload(result.payload)
            if len(tried_models) == 1:
                first_attempt_confidence = confidence
            if model_name == nano_model and (not questions_payload or confidence < 0.60):
                warnings.append("Low confidence on nano; escalating")
                logger.info("nano questions=0 or low confidence -> escalating to mini")
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
        _upsert_questions_for_page(exam_id, page_number, questions_payload, session)

    target_parse_page.model_used = used_model
    target_parse_page.confidence = confidence
    target_parse_page.status = page_status
    target_parse_page.cost = cost
    target_parse_page.input_tokens = input_tokens
    target_parse_page.output_tokens = output_tokens
    target_parse_page.result_json = {"questions": questions_payload, "warnings": warnings}
    target_parse_page.error_json = error_payload
    target_parse_page.updated_at = utcnow()
    session.add(target_parse_page)

    if page_status == "done":
        job.pages_done += 1
    job.cost_total += cost
    job.input_tokens_total += input_tokens
    job.output_tokens_total += output_tokens
    pending_pages = session.exec(select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job.id, ExamKeyParsePage.status == "pending")).all()
    failed_pages = session.exec(select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job.id, ExamKeyParsePage.status == "failed")).all()
    if not pending_pages:
        job.status = "failed" if failed_pages else "done"
        if job.status == "done":
            exam.status = ExamStatus.REVIEWING
            session.add(exam)
    else:
        job.status = "running"
    job.updated_at = utcnow()
    session.add(job)

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


@router.post("/{exam_id}/key/parse/start")
def start_answer_key_parse(exam_id: int, session: Session = Depends(get_session)) -> dict[str, object]:
    exam = _get_exam_or_404(exam_id, session)

    page_rows = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id).order_by(ExamKeyPage.page_number)).all()
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

    job = ExamKeyParseJob(
        exam_id=exam_id,
        status="running",
        page_count=len(page_rows),
        pages_done=0,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    exam.status = ExamStatus.KEY_PAGES_READY
    session.add(exam)
    session.add(job)
    session.flush()

    for page in page_rows:
        session.add(ExamKeyParsePage(job_id=job.id, page_number=page.page_number, status="pending", updated_at=utcnow()))

    session.commit()
    session.refresh(job)

    reused = False
    logger.info("parse_start exam_id=%s reused_job=%s job_id=%s", exam_id, reused, job.id)

    return {"job_id": job.id, "request_id": str(job.id), "page_count": job.page_count, "pages_done": job.pages_done, "reused": reused}


@router.post("/{exam_id}/key/parse/next")
def parse_answer_key_next_page(
    exam_id: int,
    job_id: int | None = None,
    request_id: str | None = None,
    batch_size: int = 3,
    session: Session = Depends(get_session),
    parser: AnswerKeyParser = Depends(get_answer_key_parser),
) -> dict[str, object]:
    exam = _get_exam_or_404(exam_id, session)

    resolved_job_id = job_id or (int(request_id) if request_id and request_id.isdigit() else None)
    if not resolved_job_id:
        raise HTTPException(status_code=422, detail="job_id is required")
    job = _get_job_for_exam_or_error(exam_id, resolved_job_id, session)
    page_rows = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id).order_by(ExamKeyPage.page_number)).all()
    if not page_rows:
        raise HTTPException(status_code=400, detail="No key pages available. Upload and build pages first.")
    page_rows_by_number = {row.page_number: row for row in page_rows}
    capped_batch_size = max(1, min(batch_size, 5))

    target_parse_pages = session.exec(
        select(ExamKeyParsePage)
        .where(ExamKeyParsePage.job_id == job.id, ExamKeyParsePage.status == "pending")
        .order_by(ExamKeyParsePage.page_number)
        .limit(capped_batch_size)
    ).all()

    if not target_parse_pages:
        failed_pages = session.exec(select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job.id, ExamKeyParsePage.status == "failed")).all()
        job.status = "failed" if failed_pages else "done"
        job.updated_at = utcnow()
        if job.status == "done":
            exam.status = ExamStatus.REVIEWING
            session.add(exam)
        session.add(job)
        session.commit()
        return {
            "job_id": job.id,
            "request_id": str(job.id),
            "pages_processed": [],
            "page_count": job.page_count,
            "pages_done": job.pages_done,
            "status": job.status,
            "totals": {"cost_total": job.cost_total, "input_tokens_total": job.input_tokens_total, "output_tokens_total": job.output_tokens_total},
        }

    pages_processed: list[int] = []
    page_results: list[dict[str, Any]] = []
    for target_parse_page in target_parse_pages:
        page_result = _process_single_parse_page(
            exam_id=exam_id,
            exam=exam,
            job=job,
            target_parse_page=target_parse_page,
            page_rows_by_number=page_rows_by_number,
            session=session,
            parser=parser,
        )
        page_results.append(page_result)
        pages_processed.append(page_result["page_number"])
        session.commit()

    return {
        "job_id": job.id,
        "request_id": str(job.id),
        "pages_processed": pages_processed,
        "pages_done": job.pages_done,
        "page_count": job.page_count,
        "status": job.status,
        "page_results": page_results,
        "totals": {"cost_total": job.cost_total, "input_tokens_total": job.input_tokens_total, "output_tokens_total": job.output_tokens_total},
    }


@router.get("/{exam_id}/key/parse/status")
def get_answer_key_parse_status(exam_id: int, job_id: int | None = None, request_id: str | None = None, session: Session = Depends(get_session)) -> dict[str, object]:
    exam = session.get(Exam, exam_id)
    resolved_job_id = job_id or (int(request_id) if request_id and request_id.isdigit() else None)
    if not resolved_job_id:
        raise HTTPException(status_code=422, detail="job_id is required")
    if not exam:
        _raise_parse_validation_error(status_code=404, detail="Exam not found", exam_exists=False, job_exists=False)
    job = session.get(ExamKeyParseJob, resolved_job_id)
    if not job:
        _raise_parse_validation_error(status_code=404, detail="Parse job not found", exam_exists=True, job_exists=False)
    if job.exam_id != exam_id:
        _raise_parse_validation_error(status_code=409, detail="Parse job does not belong to this exam", exam_exists=True, job_exists=True, job_exam_id=job.exam_id)
    pages = session.exec(
        select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job.id).order_by(ExamKeyParsePage.page_number)
    ).all()
    warnings = [f"Page {p.page_number} failed" for p in pages if p.status == "failed"]
    return {
        "job_id": job.id,
        "request_id": str(job.id),
        "exam_exists": True,
        "job_exists": True,
        "page_count": job.page_count,
        "pages_done": job.pages_done,
        "status": job.status,
        "pages": [{"page_number": p.page_number, "status": p.status} for p in pages],
        "totals": {"cost_total": job.cost_total, "input_tokens_total": job.input_tokens_total, "output_tokens_total": job.output_tokens_total},
        "warnings": warnings,
    }


@router.post("/{exam_id}/key/parse/retry")
def retry_answer_key_parse_page(exam_id: int, job_id: int | None = None, page_number: int = 0, request_id: str | None = None, session: Session = Depends(get_session)) -> dict[str, object]:
    resolved_job_id = job_id or (int(request_id) if request_id and request_id.isdigit() else None)
    if not resolved_job_id:
        raise HTTPException(status_code=422, detail="job_id is required")
    _get_exam_or_404(exam_id, session)
    _get_job_for_exam_or_error(exam_id, resolved_job_id, session)
    parse_page = session.exec(
        select(ExamKeyParsePage).where(
            ExamKeyParsePage.job_id == resolved_job_id,
            ExamKeyParsePage.page_number == page_number,
        )
    ).first()
    if not parse_page:
        raise HTTPException(status_code=404, detail="Parse page not found")

    parse_page.status = "pending"
    parse_page.error_json = None
    parse_page.result_json = None
    parse_page.updated_at = utcnow()
    session.add(parse_page)
    session.commit()
    return {"job_id": resolved_job_id, "request_id": str(resolved_job_id), "page_number": page_number, "status": "pending"}




@router.post("/{exam_id}/key/parse/finish")
def finish_answer_key_parse(exam_id: int, job_id: int | None = None, request_id: str | None = None, session: Session = Depends(get_session)) -> dict[str, object]:
    resolved_job_id = job_id or (int(request_id) if request_id and request_id.isdigit() else None)
    if not resolved_job_id:
        raise HTTPException(status_code=422, detail="job_id is required")
    _get_exam_or_404(exam_id, session)
    job = _get_job_for_exam_or_error(exam_id, resolved_job_id, session)
    pending = session.exec(select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job.id, ExamKeyParsePage.status == "pending")).all()
    failed = session.exec(select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job.id, ExamKeyParsePage.status == "failed")).all()
    job.status = "running" if pending else ("failed" if failed else "done")
    job.updated_at = utcnow()
    session.add(job)
    session.commit()
    questions = list_questions(exam_id, session)
    return {"job_id": job.id, "request_id": str(job.id), "status": job.status, "questions": [q.model_dump() for q in questions]}


@router.get("/{exam_id}/key/parse/latest")
def get_latest_parse_job(exam_id: int, session: Session = Depends(get_session)) -> dict[str, object]:
    exam = session.get(Exam, exam_id)
    if not exam:
        return {"exam_exists": False, "job": None}

    latest_job = _get_latest_job_for_exam(exam_id, session)
    if not latest_job:
        return {"exam_exists": True, "job": None}

    pages = session.exec(
        select(ExamKeyParsePage)
        .where(ExamKeyParsePage.job_id == latest_job.id)
        .order_by(ExamKeyParsePage.page_number)
    ).all()
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
def parse_answer_key(exam_id: int, session: Session = Depends(get_session), parser: AnswerKeyParser = Depends(get_answer_key_parser)) -> dict[str, object]:
    """Deprecated: use /key/parse/start + /key/parse/next + /key/parse/status."""
    try:
        page_rows = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id)).all()
        if not page_rows:
            build_key_pages_for_exam(exam_id, session)
        started = start_answer_key_parse(exam_id=exam_id, session=session)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "stage": "build_key_pages", "request_id": str(uuid.uuid4())})

    job_id = int(started["job_id"])
    page_count = int(started["page_count"])
    attempts: list[dict[str, object]] = []
    for _ in range(page_count):
        next_result = parse_answer_key_next_page(exam_id=exam_id, job_id=job_id, session=session, parser=parser)
        pages_processed = next_result.get("pages_processed") if isinstance(next_result.get("pages_processed"), list) else []
        if not pages_processed:
            break
        all_page_results = next_result.get("page_results") if isinstance(next_result.get("page_results"), list) else []
        for page_result in all_page_results:
            if not isinstance(page_result, dict):
                continue
            tried = page_result.get("tried_models") if isinstance(page_result.get("tried_models"), list) else []
            first_conf = float(page_result.get("first_attempt_confidence") or 0.0)
            final_conf = float(page_result.get("confidence") or 0.0)
            page_number = int(page_result.get("page_number") or 0)
            for idx, model in enumerate(tried):
                attempts.append({"page_index": page_number, "model": model, "confidence_score": first_conf if idx == 0 else final_conf})

    status = get_answer_key_parse_status(exam_id=exam_id, job_id=job_id, session=session)
    questions = list_questions(exam_id, session)
    return {
        "ok": True,
        "deprecated": True,
        "job_id": job_id,
        "request_id": str(job_id),
        "status": status.get("status", "done"),
        "stage": "save_questions",
        "model_used": "gpt-5-mini",
        "confidence_score": 0.0,
        "warnings": status.get("warnings", []),
        "timings": {"openai_ms": 0, "save_ms": 0, "build_pages_ms": 0, "validate_ms": 0},
        "attempts": attempts,
        "questions": [q.model_dump() for q in questions],
        "questions_count": len(questions),
        "page_count": page_count,
    }


@router.post("/{exam_id}/key/review/complete")
def complete_key_review(exam_id: int, session: Session = Depends(get_session)) -> dict[str, object]:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    question_count = len(session.exec(select(Question).where(Question.exam_id == exam_id)).all())
    warnings: list[str] = []
    if question_count == 0:
        warnings.append("No questions exist. Exam marked READY for manual setup.")
    exam.status = ExamStatus.READY
    session.add(exam)
    session.commit()
    return {"exam_id": exam_id, "status": exam.status, "warnings": warnings}
