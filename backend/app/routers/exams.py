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
    OPENAI_PRICING,
    AnswerKeyParser,
    OpenAIRequestError,
    ParseResult,
    SchemaBuildError,
    build_answer_key_response_schema,
    compute_usage_cost,
    get_answer_key_parser,
)
from app.db import get_session
from app.models import Exam, ExamKeyFile, ExamKeyPage, ExamKeyParseRun, ExamStatus, Question, QuestionParseEvidence, QuestionRegion, Submission, SubmissionFile, SubmissionStatus, utcnow
from app.schemas import ExamCostModelBreakdown, ExamCostResponse, ExamCreate, ExamDetail, ExamKeyPageRead, ExamKeyUploadResponse, ExamRead, QuestionCreate, QuestionMergeResponse, QuestionRead, QuestionSplitRequest, QuestionSplitResponse, QuestionUpdate, RegionRead, SubmissionFileRead, SubmissionRead
from app.settings import settings
from app.storage import ensure_dir, reset_dir, relative_to_data, save_upload_file, upload_dir

router = APIRouter(prefix="/exams", tags=["exams"])
public_router = APIRouter(prefix="/exams", tags=["exams-public"])
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




@router.get("/{exam_id}/cost", response_model=ExamCostResponse)
def get_exam_cost(exam_id: int, session: Session = Depends(get_session)) -> ExamCostResponse:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    runs = session.exec(select(ExamKeyParseRun).where(ExamKeyParseRun.exam_id == exam_id, ExamKeyParseRun.status == "success")).all()
    total_cost = float(sum(float(run.total_cost or 0.0) for run in runs))
    total_tokens = int(sum(int(run.input_tokens or 0) + int(run.output_tokens or 0) for run in runs))

    model_breakdown: dict[str, ExamCostModelBreakdown] = {}
    for model in OPENAI_PRICING.keys():
        model_runs = [run for run in runs if run.model_used == model]
        model_input_tokens = int(sum(int(run.input_tokens or 0) for run in model_runs))
        model_output_tokens = int(sum(int(run.output_tokens or 0) for run in model_runs))
        model_total_cost = float(sum(float(run.total_cost or 0.0) for run in model_runs))
        model_breakdown[model] = ExamCostModelBreakdown(
            input_tokens=model_input_tokens,
            output_tokens=model_output_tokens,
            total_tokens=model_input_tokens + model_output_tokens,
            total_cost=model_total_cost,
        )

    return ExamCostResponse(total_cost=total_cost, total_tokens=total_tokens, model_breakdown=model_breakdown)

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

    exam.status = ExamStatus.KEY_UPLOADED
    session.add(exam)
    session.commit()
    return ExamKeyUploadResponse(uploaded=uploaded)


@router.post("/{exam_id}/key/build-pages", response_model=list[ExamKeyPageRead])
def build_exam_key_pages(exam_id: int, session: Session = Depends(get_session)) -> list[ExamKeyPageRead]:
    exam = session.get(Exam, exam_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    build_key_pages_for_exam(exam_id, session)
    exam.status = ExamStatus.KEY_PAGES_READY
    session.add(exam)
    session.commit()

    rows = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id).order_by(ExamKeyPage.page_number)).all()
    return [ExamKeyPageRead(id=r.id, exam_id=r.exam_id, page_number=r.page_number, image_path=relative_to_data(Path(r.image_path)), width=r.width, height=r.height) for r in rows]


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


def _to_question_read(question: Question, session: Session) -> QuestionRead:
    rubric = json.loads(question.rubric_json)
    regions = session.exec(select(QuestionRegion).where(QuestionRegion.question_id == question.id)).all()
    return QuestionRead(
        id=question.id,
        exam_id=question.exam_id,
        label=question.label,
        max_marks=question.max_marks,
        rubric_json=rubric,
        regions=[RegionRead(id=r.id, page_number=r.page_number, x=r.x, y=r.y, w=r.w, h=r.h) for r in regions],
    )


@router.post("/{exam_id}/questions/{question_id}/merge-next", response_model=QuestionMergeResponse)
def merge_question_with_next(exam_id: int, question_id: int, session: Session = Depends(get_session)) -> QuestionMergeResponse:
    questions = session.exec(select(Question).where(Question.exam_id == exam_id).order_by(Question.id)).all()
    idx = next((i for i, q in enumerate(questions) if q.id == question_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Question not found")
    if idx >= len(questions) - 1:
        raise HTTPException(status_code=400, detail="Cannot merge the last question")

    current = questions[idx]
    nxt = questions[idx + 1]
    current_rubric = json.loads(current.rubric_json)
    next_rubric = json.loads(nxt.rubric_json)

    current_criteria = current_rubric.get("criteria") if isinstance(current_rubric.get("criteria"), list) else []
    next_criteria = next_rubric.get("criteria") if isinstance(next_rubric.get("criteria"), list) else []

    def _join_text(left: object, right: object, sep: str = "\n") -> str:
        a = str(left or "").strip()
        b = str(right or "").strip()
        if a and b:
            return f"{a}{sep}{b}"
        return a or b

    merged_warnings = current_rubric.get("warnings") if isinstance(current_rubric.get("warnings"), list) else []
    merged_warnings = [*merged_warnings, {"merged_by_teacher": True}]

    current.max_marks = max(0, int(current.max_marks or 0) + int(nxt.max_marks or 0))
    current.rubric_json = json.dumps({
        **current_rubric,
        "criteria": [*current_criteria, *next_criteria],
        "question_text": _join_text(current_rubric.get("question_text"), next_rubric.get("question_text")),
        "answer_key": _join_text(current_rubric.get("answer_key"), next_rubric.get("answer_key"), "\n\n---\n\n"),
        "model_solution": _join_text(current_rubric.get("model_solution"), next_rubric.get("model_solution"), "\n\n---\n\n"),
        "warnings": merged_warnings,
        "merged_from": [current.id, nxt.id],
    })

    session.delete(nxt)
    session.add(current)
    session.commit()
    session.refresh(current)

    count = len(session.exec(select(Question.id).where(Question.exam_id == exam_id)).all())
    return QuestionMergeResponse(question=_to_question_read(current, session), questions_count=count)


@router.post("/{exam_id}/questions/{question_id}/split", response_model=QuestionSplitResponse)
def split_question(exam_id: int, question_id: int, payload: QuestionSplitRequest, session: Session = Depends(get_session)) -> QuestionSplitResponse:
    if payload.mode != "criteria_index":
        raise HTTPException(status_code=400, detail="Unsupported split mode")

    question = _get_exam_question_or_404(exam_id, question_id, session)
    rubric = json.loads(question.rubric_json)
    criteria = rubric.get("criteria") if isinstance(rubric.get("criteria"), list) else []

    if payload.criteria_split_index <= 0 or payload.criteria_split_index >= len(criteria):
        raise HTTPException(status_code=400, detail="criteria_split_index must be between 1 and len(criteria)-1")

    left_criteria = criteria[:payload.criteria_split_index]
    right_criteria = criteria[payload.criteria_split_index:]

    def _sum_marks(items: list[object]) -> int:
        total = 0
        for item in items:
            if isinstance(item, dict):
                total += int(item.get("marks") or 0)
        return total

    left_marks = _sum_marks(left_criteria)
    right_marks = _sum_marks(right_criteria)

    question.max_marks = left_marks if left_marks > 0 else max(0, question.max_marks // 2)
    question.rubric_json = json.dumps({
        **rubric,
        "criteria": left_criteria,
        "split_by_teacher": True,
    })

    label = str(question.label)
    split_label = f"{label}b" if not label.endswith("b") else f"{label}_part2"
    new_question = Question(
        exam_id=exam_id,
        label=split_label,
        max_marks=right_marks if right_marks > 0 else max(0, int(rubric.get("max_marks") or question.max_marks)),
        rubric_json=json.dumps({
            **rubric,
            "criteria": right_criteria,
            "split_from": question.id,
            "split_by_teacher": True,
        }),
    )

    session.add(question)
    session.add(new_question)
    session.commit()
    session.refresh(question)
    session.refresh(new_question)

    count = len(session.exec(select(Question.id).where(Question.exam_id == exam_id)).all())
    return QuestionSplitResponse(original=_to_question_read(question, session), created=_to_question_read(new_question, session), questions_count=count)


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


@router.post("/{exam_id}/key/parse")
def parse_answer_key(
    exam_id: int,
    session: Session = Depends(get_session),
    parser: AnswerKeyParser = Depends(get_answer_key_parser),
) -> dict[str, object]:
    request_id = str(uuid.uuid4())
    stage = "load_exam"
    timings: dict[str, int] = {"build_pages_ms": 0, "openai_ms": 0, "validate_ms": 0, "save_ms": 0}
    page_index = 0
    page_count = 0

    def _err(
        status_code: int,
        detail: str,
        *,
        openai_status: int | None = None,
        openai_error: str | None = None,
        error_page_index: int | None = None,
    ) -> JSONResponse:
        payload: dict[str, object] = {
            "detail": detail,
            "request_id": request_id,
            "stage": stage,
            "openai_status": openai_status,
            "openai_error": openai_error,
            "page_index": error_page_index if error_page_index is not None else page_index,
            "page_count": page_count,
        }
        return JSONResponse(status_code=status_code, content=payload)

    run = ExamKeyParseRun(exam_id=exam_id, request_id=request_id, model_used="", status="running", started_at=utcnow())
    session.add(run)
    session.commit()
    session.refresh(run)

    try:
        exam = session.get(Exam, exam_id)
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found")

        stage = "build_key_pages"
        build_started = time.perf_counter()
        if not session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id)).first():
            legacy_paths = _load_key_page_images(exam_id, session)
            if legacy_paths:
                image_paths = legacy_paths
            else:
                build_key_pages_for_exam(exam_id, session)
                page_rows = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id).order_by(ExamKeyPage.page_number)).all()
                image_paths = [Path(r.image_path) for r in page_rows if Path(r.image_path).exists()]
        else:
            page_rows = session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id).order_by(ExamKeyPage.page_number)).all()
            image_paths = [Path(r.image_path) for r in page_rows if Path(r.image_path).exists()]
        exam.status = ExamStatus.KEY_PAGES_READY
        session.add(exam)
        session.commit()
        timings["build_pages_ms"] = int((time.perf_counter() - build_started) * 1000)

        if not image_paths:
            raise HTTPException(status_code=400, detail="No key pages available. Upload and build pages first.")

        page_count = len(image_paths)

        stage = "model_config"
        nano_model, mini_model = _resolve_models()

        attempts: list[dict[str, object]] = []
        merged_questions_payload: list[dict[str, object]] = []
        merged_warnings: list[str] = []
        confidence_scores: list[float] = []
        result_model = nano_model
        total_input_tokens = 0
        total_output_tokens = 0
        total_input_cost = 0.0
        total_output_cost = 0.0
        total_cost = 0.0

        for idx, page_path in enumerate(image_paths, start=1):
            page_index = idx
            nano_failures = 0
            mini_failures = 0

            stage = f"call_openai_nano_page_{idx}"
            nano_started = time.perf_counter()
            try:
                nano_result = _invoke_parser(parser, [page_path], nano_model, request_id)
            except OpenAIRequestError as exc:
                nano_failures = 2
                timings["openai_ms"] += int((time.perf_counter() - nano_started) * 1000)
                attempts.append({"model": nano_model, "openai_ms": int((time.perf_counter() - nano_started) * 1000), "page_index": idx, "failed": True})

                stage = f"call_openai_mini_page_{idx}"
                mini_started = time.perf_counter()
                try:
                    mini_result = _invoke_parser(parser, [page_path], mini_model, request_id)
                except OpenAIRequestError as mini_exc:
                    mini_failures = 1
                    timings["openai_ms"] += int((time.perf_counter() - mini_started) * 1000)
                    if nano_failures >= 2 and mini_failures >= 1:
                        stage = f"call_openai_mini_page_{idx}"
                        run.status = "failed"
                        run.finished_at = utcnow()
                        run.error_json = json.dumps({
                            "detail": f"OpenAI timed out on page {idx}",
                            "request_id": request_id,
                            "page_index": idx,
                            "page_count": page_count,
                            "stage": stage,
                        })
                        session.add(run)
                        session.commit()
                        return _err(504, f"OpenAI timed out on page {idx}", openai_status=mini_exc.status_code, openai_error=mini_exc.body[:2000], error_page_index=idx)
                    raise mini_exc

                attempts.append({"model": mini_model, "openai_ms": int((time.perf_counter() - mini_started) * 1000), "page_index": idx, "confidence_score": mini_result.payload.get("confidence_score")})
                total_input_tokens += mini_result.input_tokens
                total_output_tokens += mini_result.output_tokens
                mini_cost = compute_usage_cost(mini_result.model, mini_result.input_tokens, mini_result.output_tokens)
                total_input_cost += float(mini_cost["input_cost"])
                total_output_cost += float(mini_cost["output_cost"])
                total_cost += mini_result.total_cost
                result = mini_result
                result_model = mini_result.model
            else:
                nano_ms = int((time.perf_counter() - nano_started) * 1000)
                timings["openai_ms"] += nano_ms
                attempts.append({"model": nano_model, "openai_ms": nano_ms, "page_index": idx, "confidence_score": nano_result.payload.get("confidence_score")})
                total_input_tokens += nano_result.input_tokens
                total_output_tokens += nano_result.output_tokens
                nano_cost = compute_usage_cost(nano_result.model, nano_result.input_tokens, nano_result.output_tokens)
                total_input_cost += float(nano_cost["input_cost"])
                total_output_cost += float(nano_cost["output_cost"])
                total_cost += nano_result.total_cost
                result = nano_result

            stage = "validate_output"
            validate_started = time.perf_counter()
            try:
                confidence, questions_payload, warnings = _validate_parse_payload(result.payload)
            except ValueError:
                confidence, questions_payload, warnings = 0.0, [], ["Model output invalid; please add questions manually in review."]

            if result.model == nano_model and (not questions_payload or confidence < 0.60):
                logger.info("nano questions=0 or low confidence -> escalating to mini", extra={"request_id": request_id, "page_index": idx, "stage": f"call_openai_mini_page_{idx}"})
                stage = f"call_openai_mini_page_{idx}"
                mini_started = time.perf_counter()
                try:
                    mini_result = _invoke_parser(parser, [page_path], mini_model, request_id)
                except OpenAIRequestError as mini_exc:
                    mini_failures = 1
                    timings["openai_ms"] += int((time.perf_counter() - mini_started) * 1000)
                    if nano_failures >= 2 and mini_failures >= 1:
                        run.status = "failed"
                        run.finished_at = utcnow()
                        run.error_json = json.dumps({
                            "detail": f"OpenAI timed out on page {idx}",
                            "request_id": request_id,
                            "page_index": idx,
                            "page_count": page_count,
                            "stage": stage,
                        })
                        session.add(run)
                        session.commit()
                        return _err(504, f"OpenAI timed out on page {idx}", openai_status=mini_exc.status_code, openai_error=mini_exc.body[:2000], error_page_index=idx)
                    raise mini_exc

                mini_ms = int((time.perf_counter() - mini_started) * 1000)
                timings["openai_ms"] += mini_ms
                attempts.append({"model": mini_model, "openai_ms": mini_ms, "page_index": idx, "confidence_score": mini_result.payload.get("confidence_score")})
                total_input_tokens += mini_result.input_tokens
                total_output_tokens += mini_result.output_tokens
                mini_cost = compute_usage_cost(mini_result.model, mini_result.input_tokens, mini_result.output_tokens)
                total_input_cost += float(mini_cost["input_cost"])
                total_output_cost += float(mini_cost["output_cost"])
                total_cost += mini_result.total_cost
                try:
                    confidence, questions_payload, warnings = _validate_parse_payload(mini_result.payload)
                except ValueError:
                    confidence, questions_payload, warnings = 0.0, [], ["Model output invalid; please add questions manually in review."]
                result_model = mini_result.model

            timings["validate_ms"] += int((time.perf_counter() - validate_started) * 1000)
            confidence_scores.append(confidence)
            merged_questions_payload.extend(questions_payload)
            merged_warnings.extend(warnings)

        stage = "save_questions"
        save_started = time.perf_counter()
        existing = {q.label: q for q in session.exec(select(Question).where(Question.exam_id == exam_id)).all()}

        for parsed in merged_questions_payload:
            label = str(parsed.get("label") or "Q?")
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
                "needs_review": False,
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
                    page_number=int(e.get("page_number") or 1),
                    x=float(e.get("x") or 0),
                    y=float(e.get("y") or 0),
                    w=float(e.get("w") or 0.1),
                    h=float(e.get("h") or 0.1),
                    evidence_kind=kind,
                    confidence=float(e.get("confidence") or 0),
                ))

        exam.status = ExamStatus.PARSED
        session.add(exam)
        if merged_questions_payload:
            exam.status = ExamStatus.REVIEWING
            session.add(exam)

        timings["save_ms"] = int((time.perf_counter() - save_started) * 1000)
        run.model_used = result_model
        run.status = "success"
        run.input_tokens = total_input_tokens
        run.output_tokens = total_output_tokens
        run.total_cost = total_cost
        run.finished_at = utcnow()
        run.timings_json = json.dumps(timings)
        session.add(run)
        session.commit()

        usage = {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
        }
        cost_breakdown = {"input_cost": total_input_cost, "output_cost": total_output_cost, "total_cost": total_cost}

        return {
            "ok": True,
            "request_id": request_id,
            "stage": stage,
            "model_used": result_model,
            "usage": usage,
            "cost": cost_breakdown,
            "confidence_score": min(confidence_scores) if confidence_scores else 0.0,
            "questions": merged_questions_payload,
            "questions_count": len(merged_questions_payload),
            "warnings": merged_warnings,
            "timings": timings,
            "attempts": attempts,
            "page_index": page_count,
            "page_count": page_count,
        }
    except HTTPException as exc:
        run.status = "failed"
        run.finished_at = utcnow()
        run.error_json = json.dumps({"detail": str(exc.detail), "stage": stage, "page_index": page_index, "page_count": page_count})
        session.add(run)
        session.commit()
        return _err(exc.status_code, str(exc.detail))
    except OpenAIRequestError as exc:
        run.status = "failed"
        run.finished_at = utcnow()
        run.error_json = json.dumps({
            "detail": "OpenAI request failed",
            "stage": stage,
            "openai_status": exc.status_code,
            "page_index": page_index,
            "page_count": page_count,
        })
        session.add(run)
        session.commit()
        if exc.status_code == 504:
            return _err(504, "OpenAI request timed out", openai_status=exc.status_code, openai_error=exc.body[:2000])
        return _err(502, "OpenAI request failed", openai_status=exc.status_code, openai_error=exc.body[:2000])
    except Exception as exc:
        logger.exception("key/parse failed", extra={"stage": stage, "exam_id": exam_id, "request_id": request_id, "page_index": page_index, "page_count": page_count})
        run.status = "failed"
        run.finished_at = utcnow()
        run.error_json = json.dumps({"detail": str(exc)[:300], "stage": stage, "page_index": page_index, "page_count": page_count})
        session.add(run)
        session.commit()
        return _err(500, f"Key parsing failed: {type(exc).__name__}: {str(exc)[:300]}")


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