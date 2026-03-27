"""Submission-oriented repository functions for the staged D1 migration."""

from __future__ import annotations

from collections.abc import Sequence

from sqlmodel import delete, select

from app.models import (
    AnswerCrop,
    GradeResult,
    Question,
    QuestionRegion,
    Submission,
    SubmissionCaptureMode,
    SubmissionFile,
    SubmissionPage,
    Transcription,
)
from app.persistence import DbSession


def get_submission(session: DbSession, submission_id: int) -> Submission | None:
    return session.get(Submission, submission_id)


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
    submission = Submission(
        exam_id=exam_id,
        student_name=student_name,
        first_name=first_name,
        last_name=last_name,
        status=status,
        capture_mode=capture_mode,
    )
    session.add(submission)
    session.flush()
    return submission


def update_submission(session: DbSession, submission: Submission, **fields) -> Submission:
    for key, value in fields.items():
        setattr(submission, key, value)
    session.add(submission)
    return submission


def update_submission_front_page_data(
    session: DbSession,
    submission: Submission,
    *,
    front_page_candidates_json: str | None = None,
    front_page_usage_json: str | None = None,
) -> Submission:
    submission.front_page_candidates_json = front_page_candidates_json
    submission.front_page_usage_json = front_page_usage_json
    session.add(submission)
    return submission


def list_submission_files(session: DbSession, submission_id: int) -> list[SubmissionFile]:
    return session.exec(select(SubmissionFile).where(SubmissionFile.submission_id == submission_id).order_by(SubmissionFile.id)).all()


def list_submission_files_for_submission_ids(session: DbSession, submission_ids: Sequence[int]) -> list[SubmissionFile]:
    if not submission_ids:
        return []
    return session.exec(
        select(SubmissionFile)
        .where(SubmissionFile.submission_id.in_(submission_ids))
        .order_by(SubmissionFile.submission_id.asc(), SubmissionFile.id.asc())
    ).all()


def list_submission_pages(session: DbSession, submission_id: int) -> list[SubmissionPage]:
    return session.exec(select(SubmissionPage).where(SubmissionPage.submission_id == submission_id).order_by(SubmissionPage.page_number)).all()


def list_submission_crops(session: DbSession, submission_id: int) -> list[AnswerCrop]:
    return session.exec(select(AnswerCrop).where(AnswerCrop.submission_id == submission_id)).all()


def list_submission_transcriptions(session: DbSession, submission_id: int) -> list[Transcription]:
    return session.exec(select(Transcription).where(Transcription.submission_id == submission_id)).all()


def list_submission_grades(session: DbSession, submission_id: int) -> list[GradeResult]:
    return session.exec(select(GradeResult).where(GradeResult.submission_id == submission_id)).all()


def list_exam_questions(session: DbSession, exam_id: int) -> list[Question]:
    return session.exec(select(Question).where(Question.exam_id == exam_id).order_by(Question.id)).all()


def list_exam_front_page_total_submissions(session: DbSession, exam_id: int) -> list[Submission]:
    return session.exec(
        select(Submission)
        .where(
            Submission.exam_id == exam_id,
            Submission.capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS,
        )
        .order_by(Submission.id)
    ).all()


def list_submissions_by_ids(session: DbSession, submission_ids: Sequence[int]) -> list[Submission]:
    if not submission_ids:
        return []
    return session.exec(
        select(Submission)
        .where(Submission.id.in_(submission_ids))
        .order_by(Submission.id.asc())
    ).all()


def list_submission_pages_for_submission_ids(session: DbSession, submission_ids: Sequence[int]) -> list[SubmissionPage]:
    if not submission_ids:
        return []
    return session.exec(select(SubmissionPage).where(SubmissionPage.submission_id.in_(submission_ids))).all()


def list_question_regions(session: DbSession, question_id: int) -> list[QuestionRegion]:
    return session.exec(select(QuestionRegion).where(QuestionRegion.question_id == question_id)).all()


def get_submission_page(session: DbSession, submission_id: int, page_number: int) -> SubmissionPage | None:
    return session.exec(
        select(SubmissionPage).where(SubmissionPage.submission_id == submission_id, SubmissionPage.page_number == page_number)
    ).first()


def clear_submission_pages(session: DbSession, submission_id: int) -> None:
    session.exec(delete(SubmissionPage).where(SubmissionPage.submission_id == submission_id))


def create_submission_page(
    session: DbSession,
    *,
    submission_id: int,
    page_number: int,
    image_path: str,
    width: int,
    height: int,
) -> SubmissionPage:
    row = SubmissionPage(
        submission_id=submission_id,
        page_number=page_number,
        image_path=image_path,
        width=width,
        height=height,
    )
    session.add(row)
    session.flush()
    return row


def clear_submission_crops(session: DbSession, submission_id: int) -> None:
    session.exec(delete(AnswerCrop).where(AnswerCrop.submission_id == submission_id))


def get_submission_crop(session: DbSession, submission_id: int, question_id: int) -> AnswerCrop | None:
    return session.exec(
        select(AnswerCrop).where(AnswerCrop.submission_id == submission_id, AnswerCrop.question_id == question_id)
    ).first()


def create_submission_crop(
    session: DbSession,
    *,
    submission_id: int,
    question_id: int,
    image_path: str,
) -> AnswerCrop:
    row = AnswerCrop(submission_id=submission_id, question_id=question_id, image_path=image_path)
    session.add(row)
    session.flush()
    return row


def clear_submission_transcriptions(session: DbSession, submission_id: int) -> None:
    session.exec(delete(Transcription).where(Transcription.submission_id == submission_id))


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
    row = Transcription(
        submission_id=submission_id,
        question_id=question_id,
        provider=provider,
        text=text,
        confidence=confidence,
        raw_json=raw_json,
    )
    session.add(row)
    session.flush()
    return row


def clear_submission_grades(session: DbSession, submission_id: int) -> None:
    session.exec(delete(GradeResult).where(GradeResult.submission_id == submission_id))


def get_submission_grade(session: DbSession, submission_id: int, question_id: int) -> GradeResult | None:
    return session.exec(
        select(GradeResult).where(GradeResult.submission_id == submission_id, GradeResult.question_id == question_id)
    ).first()


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
    grade = get_submission_grade(session, submission_id, question_id)
    if grade:
        grade.marks_awarded = marks_awarded
        grade.breakdown_json = breakdown_json
        grade.feedback_json = feedback_json
        grade.model_name = model_name
    else:
        grade = GradeResult(
            submission_id=submission_id,
            question_id=question_id,
            marks_awarded=marks_awarded,
            breakdown_json=breakdown_json,
            feedback_json=feedback_json,
            model_name=model_name,
        )
    session.add(grade)
    session.flush()
    return grade


def update_submission_status(session: DbSession, submission: Submission, status) -> Submission:
    submission.status = status
    session.add(submission)
    return submission


def update_submission_capture_mode(session: DbSession, submission: Submission, capture_mode) -> Submission:
    submission.capture_mode = capture_mode
    session.add(submission)
    return submission


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
    row = SubmissionFile(
        submission_id=submission_id,
        file_kind=file_kind,
        original_filename=original_filename,
        stored_path=stored_path,
        blob_url=blob_url,
        blob_pathname=blob_pathname,
        content_type=content_type,
        size_bytes=size_bytes,
    )
    session.add(row)
    session.flush()
    return row


def register_submission_files(
    session: DbSession,
    *,
    submission_id: int,
    files: Sequence[dict[str, str | int | None]],
) -> int:
    registered = 0
    for file in files:
        create_submission_file(
            session,
            submission_id=submission_id,
            file_kind=str(file["file_kind"]),
            original_filename=str(file["original_filename"]),
            stored_path=str(file["stored_path"]),
            content_type=str(file["content_type"]),
            size_bytes=int(file["size_bytes"]),
            blob_url=str(file["blob_url"]) if file.get("blob_url") is not None else None,
            blob_pathname=str(file["blob_pathname"]) if file.get("blob_pathname") is not None else None,
        )
        registered += 1
    return registered
