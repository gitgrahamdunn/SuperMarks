"""Exam and question management endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlmodel import Session, select

from app.db import get_session
from app.models import Exam, Question, QuestionRegion, Submission, SubmissionFile, SubmissionStatus
from app.schemas import ExamCreate, ExamDetail, ExamRead, QuestionCreate, QuestionRead, RegionRead, SubmissionFileRead, SubmissionRead
from app.settings import settings
from app.storage import relative_to_data, save_upload_file, upload_dir

router = APIRouter(prefix="/exams", tags=["exams"])

_ALLOWED_TYPES = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
}


@router.post("", response_model=ExamRead, status_code=status.HTTP_201_CREATED)
def create_exam(payload: ExamCreate, session: Session = Depends(get_session)) -> Exam:
    exam = Exam(name=payload.name)
    session.add(exam)
    session.commit()
    session.refresh(exam)
    return exam


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
        exam=ExamRead(id=exam.id, name=exam.name, created_at=exam.created_at, teacher_style_profile_json=exam.teacher_style_profile_json),
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
