"""Submission pipeline endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import Session, delete, select

from app.db import get_session
from app.models import (
    AnswerCrop,
    Exam,
    GradeResult,
    Question,
    QuestionRegion,
    Submission,
    SubmissionFile,
    SubmissionPage,
    SubmissionStatus,
    Transcription,
)
from app.pipeline.crops import crop_regions_and_stitch
from app.pipeline.grade import get_grader
from app.pipeline.pages import Pdf2ImageConverter, normalize_image_to_png
from app.pipeline.transcribe import get_ocr_provider
from app.schemas import (
    GradeResultRead,
    SubmissionFileRead,
    SubmissionPageRead,
    SubmissionRead,
    SubmissionResults,
    TranscriptionRead,
)
from app.storage import crops_dir, pages_dir, relative_to_data, reset_dir

router = APIRouter(prefix="/submissions", tags=["submissions"])


@router.get("/{submission_id}", response_model=SubmissionRead)
def get_submission(submission_id: int, session: Session = Depends(get_session)) -> SubmissionRead:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    files = session.exec(select(SubmissionFile).where(SubmissionFile.submission_id == submission_id)).all()
    pages = session.exec(select(SubmissionPage).where(SubmissionPage.submission_id == submission_id)).all()

    return SubmissionRead(
        id=submission.id,
        exam_id=submission.exam_id,
        student_name=submission.student_name,
        status=submission.status,
        created_at=submission.created_at,
        files=[SubmissionFileRead(id=f.id, file_kind=f.file_kind, original_filename=f.original_filename, stored_path=f.stored_path) for f in files],
        pages=[SubmissionPageRead(id=p.id, page_number=p.page_number, image_path=p.image_path, width=p.width, height=p.height) for p in pages],
    )


@router.post("/{submission_id}/build-pages", response_model=list[SubmissionPageRead])
def build_pages(submission_id: int, session: Session = Depends(get_session)) -> list[SubmissionPageRead]:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    exam = session.get(Exam, submission.exam_id)
    files = session.exec(select(SubmissionFile).where(SubmissionFile.submission_id == submission_id)).all()
    if not files:
        raise HTTPException(status_code=400, detail="No files available for submission")

    out_dir = reset_dir(pages_dir(submission.exam_id, submission_id))
    session.exec(delete(SubmissionPage).where(SubmissionPage.submission_id == submission_id))

    created: list[SubmissionPageRead] = []
    if files[0].file_kind == "pdf":
        try:
            converter = Pdf2ImageConverter()
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        page_paths = converter.convert(Path(files[0].stored_path), out_dir)
        for idx, page_path in enumerate(page_paths, 1):
            w, h = normalize_image_to_png(page_path, page_path)
            row = SubmissionPage(submission_id=submission_id, page_number=idx, image_path=str(page_path), width=w, height=h)
            session.add(row)
            session.flush()
            created.append(SubmissionPageRead(id=row.id, page_number=idx, image_path=relative_to_data(page_path), width=w, height=h))
    else:
        for idx, file in enumerate(files, 1):
            out_path = out_dir / f"page_{idx:04d}.png"
            w, h = normalize_image_to_png(Path(file.stored_path), out_path)
            row = SubmissionPage(submission_id=submission_id, page_number=idx, image_path=str(out_path), width=w, height=h)
            session.add(row)
            session.flush()
            created.append(SubmissionPageRead(id=row.id, page_number=idx, image_path=relative_to_data(out_path), width=w, height=h))

    submission.status = SubmissionStatus.PAGES_READY
    session.add(submission)
    session.commit()
    return created


@router.post("/{submission_id}/build-crops")
def build_crops(submission_id: int, session: Session = Depends(get_session)) -> dict:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    if submission.status not in (SubmissionStatus.PAGES_READY, SubmissionStatus.CROPS_READY, SubmissionStatus.TRANSCRIBED, SubmissionStatus.GRADED):
        raise HTTPException(status_code=400, detail="Submission must be at least PAGES_READY")

    questions = session.exec(select(Question).where(Question.exam_id == submission.exam_id)).all()
    if not questions:
        raise HTTPException(status_code=400, detail="No questions configured for exam")

    pages = session.exec(select(SubmissionPage).where(SubmissionPage.submission_id == submission_id)).all()
    page_path_map = {p.page_number: Path(p.image_path) for p in pages}

    out_dir = reset_dir(crops_dir(submission.exam_id, submission_id))
    session.exec(delete(AnswerCrop).where(AnswerCrop.submission_id == submission_id))

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
        row = AnswerCrop(submission_id=submission_id, question_id=question.id, image_path=str(out_path))
        session.add(row)
        count += 1

    submission.status = SubmissionStatus.CROPS_READY
    session.add(submission)
    session.commit()
    return {"message": "Crops built", "count": count}


@router.post("/{submission_id}/transcribe")
def transcribe_submission(
    submission_id: int,
    provider: str = Query("stub"),
    session: Session = Depends(get_session),
) -> dict:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    if submission.status not in (SubmissionStatus.CROPS_READY, SubmissionStatus.TRANSCRIBED, SubmissionStatus.GRADED):
        raise HTTPException(status_code=400, detail="Submission must be CROPS_READY")

    try:
        ocr = get_ocr_provider(provider)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    crops = session.exec(select(AnswerCrop).where(AnswerCrop.submission_id == submission_id)).all()
    session.exec(delete(Transcription).where(Transcription.submission_id == submission_id))

    for crop in crops:
        result = ocr.transcribe(Path(crop.image_path))
        session.add(
            Transcription(
                submission_id=submission_id,
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

@router.get("/{submission_id}/results", response_model=SubmissionResults)
def get_results(submission_id: int, session: Session = Depends(get_session)) -> SubmissionResults:
    submission = session.get(Submission, submission_id)
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    transcriptions = session.exec(select(Transcription).where(Transcription.submission_id == submission_id)).all()
    grades = session.exec(select(GradeResult).where(GradeResult.submission_id == submission_id)).all()

    return SubmissionResults(
        submission_id=submission_id,
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
