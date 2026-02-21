"""Exam and question management endpoints."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from PIL import Image, ImageOps

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import Session, delete, select

from app.ai.openai_vision import (
    AnswerKeyParser,
    OpenAIRequestError,
    ParseResult,
    SchemaBuildError,
    build_answer_key_response_schema,
    get_answer_key_parser,
)
from app.db import get_session
from app.models import Exam, ExamKeyFile, ExamKeyPage, ExamStatus, Question, QuestionRegion, Submission, SubmissionFile, SubmissionStatus
from app.schemas import ExamCreate, ExamDetail, ExamKeyUploadResponse, ExamRead, QuestionCreate, QuestionRead, QuestionUpdate, RegionRead, SubmissionFileRead, SubmissionRead
from app.settings import settings
from app.storage import ensure_dir, reset_dir, relative_to_data, save_upload_file, upload_dir

router = APIRouter(prefix="/exams", tags=["exams"])
logger = logging.getLogger(__name__)

_ALLOWED_TYPES = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
}

_ALLOWED_KEY_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
_MAX_RENDERED_KEY_PAGES = 10


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
        source_path = Path(key_file.stored_path)
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
            rendered_paths = _render_pdf_pages(
                source_path,
                output_dir,
                start_page_number=page_num,
                max_pages=remaining_pages,
            )
            for rendered in rendered_paths:
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


def _validate_parse_payload(payload: dict[str, Any]) -> tuple[float, list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
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

        criteria = question.get("criteria", [])
        if not isinstance(criteria, list):
            raise ValueError("question criteria must be list")
        for criterion in criteria:
            if not isinstance(criterion, dict):
                raise ValueError("criteria item must be object")
            if not isinstance(criterion.get("marks"), (int, float)):
                raise ValueError("criteria marks missing")

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

def _exam_key_dir(exam_id: int) -> Path:
    return ensure_dir(settings.data_path / "exams" / str(exam_id) / "key")


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

    submissions = session.exec(select(Submission).where(Submission.exam_id == exam_id)).all()
    questions = session.exec(select(Question).where(Question.exam_id == exam_id)).all()

    submission_reads: list[SubmissionRead] = []
    for sub in submissions:
        files = session.exec(select(SubmissionFile).where(SubmissionFile.submission_id == sub.id)).all()
        submission_reads.append(
            SubmissionRead(
                id=sub.id,
                exam_id=sub.exam_id,
                student_name=sub.student_name,
                status=sub.status,
                created_at=sub.created_at,
                files=[SubmissionFileRead(id=f.id, file_kind=f.file_kind, original_filename=f.original_filename, stored_path=f.stored_path) for f in files],
                pages=[],
            )
        )

    question_reads: list[QuestionRead] = []
    for q in questions:
        regions = session.exec(select(QuestionRegion).where(QuestionRegion.question_id == q.id)).all()
        question_reads.append(
            QuestionRead(
                id=q.id,
                exam_id=q.exam_id,
                label=q.label,
                max_marks=q.max_marks,
                rubric_json=json.loads(q.rubric_json),
                regions=[RegionRead(id=r.id, page_number=r.page_number, x=r.x, y=r.y, w=r.w, h=r.h) for r in regions],
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
        submissions=submission_reads,
        questions=question_reads,
    )


@router.post("/{exam_id}/submissions", response_model=SubmissionRead, status_code=status.HTTP_201_CREATED)
def create_submission(
    exam_id: int,
    student_name: str = Form(...),
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
) -> SubmissionRead:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")

    kinds = [_ALLOWED_TYPES.get(f.content_type or "") for f in files]
    if any(kind is None for kind in kinds):
        raise HTTPException(status_code=400, detail="Unsupported file type. Use pdf/png/jpg/jpeg")
    if "pdf" in kinds and len(files) > 1:
        raise HTTPException(status_code=400, detail="Upload one PDF OR multiple images, not mixed")

    submission = Submission(exam_id=exam_id, student_name=student_name, status=SubmissionStatus.UPLOADED)
    session.add(submission)
    session.commit()
    session.refresh(submission)

    sub_upload_dir = upload_dir(exam_id, submission.id)
    created_files: list[SubmissionFileRead] = []
    max_size = settings.max_upload_mb * 1024 * 1024

    for upload, kind in zip(files, kinds, strict=True):
        upload.file.seek(0, 2)
        size = upload.file.tell()
        upload.file.seek(0)
        if size > max_size:
            raise HTTPException(status_code=400, detail=f"File {upload.filename} exceeds {settings.max_upload_mb}MB")

        filename = Path(upload.filename or "upload.bin").name
        destination = sub_upload_dir / filename
        save_upload_file(upload, destination)

        row = SubmissionFile(
            submission_id=submission.id,
            file_kind=kind,
            original_filename=filename,
            stored_path=str(destination),
        )
        session.add(row)
        session.flush()
        created_files.append(
            SubmissionFileRead(id=row.id, file_kind=row.file_kind, original_filename=row.original_filename, stored_path=relative_to_data(destination))
        )

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

    key_dir = _exam_key_dir(exam_id)
    uploaded = 0

    for idx, upload in enumerate(files, start=1):
        filename = Path(upload.filename or f"key-{idx}").name
        extension = Path(filename).suffix.lower()
        if extension not in _ALLOWED_KEY_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Unsupported file type. Use pdf/png/jpg/jpeg")

        destination = key_dir / filename
        save_upload_file(upload, destination)

        row = ExamKeyFile(
            exam_id=exam_id,
            original_filename=filename,
            stored_path=str(destination),
        )
        session.add(row)
        uploaded += 1

    session.commit()
    return ExamKeyUploadResponse(uploaded=uploaded)


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
def list_questions(exam_id: int, session: Session = Depends(get_session)) -> list[QuestionRead]:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

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


@router.get("/{exam_id}/key/page/{page_number}")
def get_key_page_image(
    exam_id: int,
    page_number: int,
    session: Session = Depends(get_session),
) -> FileResponse:
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
    return FileResponse(path=image_path, media_type=media_type)


@router.get("/{exam_id}/questions/{question_id}/key-visual")
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
    suffix = image_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        media_type = "image/jpeg"
    return FileResponse(path=image_path, media_type=media_type)


@router.post("/{exam_id}/key/parse")
def parse_answer_key(
    exam_id: int,
    session: Session = Depends(get_session),
    parser: AnswerKeyParser = Depends(get_answer_key_parser),
) -> dict[str, object]:
    request_id = str(uuid.uuid4())
    stage = "load_exam"
    timings: dict[str, int] = {"build_pages_ms": 0, "openai_ms": 0, "validate_ms": 0, "save_ms": 0}

    def _err(status_code: int, detail: str, *, openai_status: int | None = None, openai_error: str | None = None) -> JSONResponse:
        payload: dict[str, object] = {
            "detail": detail,
            "request_id": request_id,
            "stage": stage,
            "openai_status": openai_status,
            "openai_error": openai_error,
        }
        return JSONResponse(status_code=status_code, content=payload)

    try:
        exam = session.get(Exam, exam_id)
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found")

        schema_started = time.perf_counter()
        stage = "build_schema"
        try:
            build_answer_key_response_schema()
        except SchemaBuildError as exc:
            logger.exception("key/parse schema build failed", extra={"request_id": request_id, "stage": stage, "exam_id": exam_id})
            return _err(500, f"Failed to build OpenAI schema: {exc}")
        timings["validate_ms"] += int((time.perf_counter() - schema_started) * 1000)

        stage = "model_config"
        try:
            nano_model, mini_model = _resolve_models()
        except ValueError as exc:
            return _err(500, str(exc))

        pages_started = time.perf_counter()
        stage = "build_key_pages"
        image_paths = build_key_pages_for_exam(exam_id=exam_id, session=session)
        timings["build_pages_ms"] = int((time.perf_counter() - pages_started) * 1000)

        attempts: list[dict[str, object]] = []

        def _run_attempt(model_name: str, attempt_stage: str) -> ParseResult:
            stage_start = time.perf_counter()
            result_inner = _invoke_parser(parser, image_paths, model=model_name, request_id=request_id)
            openai_ms = int((time.perf_counter() - stage_start) * 1000)
            timings["openai_ms"] += openai_ms

            attempt_confidence = result_inner.payload.get("confidence_score")
            attempt_questions = result_inner.payload.get("questions")
            question_count = len(attempt_questions) if isinstance(attempt_questions, list) else 0

            logger.info(
                "key/parse attempt completed",
                extra={
                    "request_id": request_id,
                    "stage": attempt_stage,
                    "exam_id": exam_id,
                    "model": model_name,
                    "num_images": len(image_paths),
                    "question_count": question_count,
                    "confidence_score": attempt_confidence if isinstance(attempt_confidence, (int, float)) else None,
                    "openai_ms": openai_ms,
                },
            )
            attempts.append(
                {
                    "model": model_name,
                    "question_count": question_count,
                    "confidence_score": float(attempt_confidence) if isinstance(attempt_confidence, (int, float)) else None,
                    "openai_ms": openai_ms,
                }
            )
            return result_inner

        stage = "call_openai_nano"
        nano_result = _run_attempt(nano_model, stage)

        validate_started = time.perf_counter()
        stage = "validate_output"
        try:
            confidence, questions_payload, warnings = _validate_parse_payload(nano_result.payload)
        except ValueError:
            confidence, questions_payload, warnings = 0.0, [], ["Model output invalid; please add questions manually in review."]

        escalate_to_mini = (not questions_payload) or confidence < 0.60
        if escalate_to_mini:
            logger.info(
                "nano questions=0 or low confidence -> escalating to mini",
                extra={"request_id": request_id, "exam_id": exam_id, "stage": "call_openai_mini"},
            )
            stage = "call_openai_mini"
            mini_result = _run_attempt(mini_model, stage)
            stage = "validate_output"
            try:
                confidence, questions_payload, warnings = _validate_parse_payload(mini_result.payload)
            except ValueError:
                confidence, questions_payload, warnings = 0.0, [], ["Model output invalid; please add questions manually in review."]
            result = mini_result
        else:
            result = nano_result

        timings["validate_ms"] += int((time.perf_counter() - validate_started) * 1000)

        if not questions_payload:
            return {
                "request_id": request_id,
                "stage": stage,
                "ok": True,
                "model_used": result.model,
                "confidence_score": 0.0,
                "questions": [],
                "questions_count": 0,
                "warnings": ["No questions extracted. Please add questions manually in review."],
                "timings": timings,
                "attempts": attempts,
            }

        save_started = time.perf_counter()
        stage = "save_questions"
        existing = {q.label: q for q in session.exec(select(Question).where(Question.exam_id == exam_id)).all()}
        for parsed in questions_payload:
            label = str(parsed["label"])
            max_marks = int(parsed["max_marks"])
            parsed_marks_source = str(parsed.get("marks_source") or "").strip().lower()
            if parsed_marks_source not in {"explicit", "inferred", "unknown"}:
                parsed_marks_source = "explicit" if max_marks > 0 else "unknown"
            parsed_marks_confidence = parsed.get("marks_confidence")
            if isinstance(parsed_marks_confidence, (int, float)):
                marks_confidence = max(0.0, min(1.0, float(parsed_marks_confidence)))
            else:
                marks_confidence = 0.95 if parsed_marks_source == "explicit" else (0.6 if parsed_marks_source == "inferred" else 0.3)

            rubric = {
                "total_marks": max_marks,
                "criteria": parsed.get("criteria", []),
                "answer_key": parsed.get("answer_key", ""),
                "model_solution": parsed.get("model_solution", ""),
                "question_text": parsed.get("question_text", ""),
                "notes": parsed.get("notes", ""),
                "marks_source": parsed_marks_source,
                "marks_confidence": marks_confidence,
                "key_page_number": int(parsed.get("key_page_number") or 1),
            }

            question = existing.get(label)
            if question:
                question.max_marks = max_marks
                question.rubric_json = json.dumps(rubric)
                session.add(question)
            else:
                session.add(Question(exam_id=exam_id, label=label, max_marks=max_marks, rubric_json=json.dumps(rubric)))

        exam.status = ExamStatus.REVIEWING
        session.add(exam)
        session.commit()
        timings["save_ms"] = int((time.perf_counter() - save_started) * 1000)

        logger.info(
            "key/parse completed",
            extra={
                "request_id": request_id,
                "stage": stage,
                "exam_id": exam_id,
                "model": result.model,
                "num_images": len(image_paths),
                "timings": timings,
            },
        )
        return {
            "ok": True,
            "request_id": request_id,
            "stage": stage,
            "model_used": result.model,
            "confidence_score": confidence,
            "questions_count": len(questions_payload),
            "warnings": warnings,
            "timings": timings,
            "attempts": attempts,
        }
    except HTTPException as exc:
        return _err(exc.status_code, str(exc.detail))
    except OpenAIRequestError as exc:
        stage = "call_openai_timeout" if exc.status_code == 504 else stage
        status_code = 504 if exc.status_code == 504 else (exc.status_code or 502)
        return _err(status_code, "OpenAI request failed", openai_status=exc.status_code, openai_error=exc.body[:2000])
    except httpx.TimeoutException as exc:
        stage = "call_openai_timeout"
        return _err(504, "OpenAI request timeout", openai_status=504, openai_error=str(exc))
    except Exception as exc:
        logger.exception("key/parse failed", extra={"stage": stage, "exam_id": exam_id, "request_id": request_id})
        return _err(500, f"Key parsing failed: {type(exc).__name__}: {str(exc)[:300]}")
