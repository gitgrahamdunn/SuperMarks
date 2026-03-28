"""Exam-oriented repository functions for the staged D1 migration."""

from __future__ import annotations

from collections.abc import Sequence

from sqlmodel import delete, select

from app.models import (
    AnswerCrop,
    BulkUploadPage,
    ClassList,
    Exam,
    ExamBulkUploadFile,
    ExamIntakeJob,
    ExamKeyFile,
    ExamKeyPage,
    ExamKeyParseJob,
    ExamKeyParsePage,
    GradeResult,
    Question,
    QuestionParseEvidence,
    QuestionRegion,
    Submission,
    SubmissionCaptureMode,
    SubmissionFile,
    SubmissionPage,
    Transcription,
)
from app.persistence import DbSession


def get_exam(session: DbSession, exam_id: int) -> Exam | None:
    return session.get(Exam, exam_id)


def create_exam(session: DbSession, *, name: str, owner_user_id: int | None = None) -> Exam:
    exam = Exam(name=name, owner_user_id=owner_user_id)
    session.add(exam)
    session.flush()
    return exam


def update_exam(session: DbSession, exam: Exam, **fields) -> Exam:
    for key, value in fields.items():
        setattr(exam, key, value)
    session.add(exam)
    return exam


def list_exams(session: DbSession, owner_user_id: int | None = None) -> list[Exam]:
    statement = select(Exam)
    if owner_user_id is not None:
        statement = statement.where(Exam.owner_user_id == owner_user_id)
    return session.exec(statement.order_by(Exam.created_at.desc(), Exam.id.desc())).all()


def list_class_lists(session: DbSession, owner_user_id: int | None = None) -> list[ClassList]:
    statement = select(ClassList)
    if owner_user_id is not None:
        statement = statement.where(ClassList.owner_user_id == owner_user_id)
    return session.exec(statement.order_by(ClassList.created_at.desc(), ClassList.id.desc())).all()


def get_class_list(session: DbSession, class_list_id: int) -> ClassList | None:
    return session.get(ClassList, class_list_id)


def create_class_list(session: DbSession, *, name: str, owner_user_id: int | None = None) -> ClassList:
    class_list = ClassList(name=name, owner_user_id=owner_user_id, names_json="[]", source_json=None)
    session.add(class_list)
    session.flush()
    return class_list


def update_class_list_payload(session: DbSession, *, class_list: ClassList, names_json: str, source_json: str, name: str | None = None) -> ClassList:
    if name is not None:
        class_list.name = name
    class_list.names_json = names_json
    class_list.source_json = source_json
    session.add(class_list)
    session.flush()
    return class_list


def delete_class_list(session: DbSession, *, class_list: ClassList) -> None:
    session.delete(class_list)


def update_exam_class_list_payload(session: DbSession, *, exam: Exam, class_list_json: str, class_list_source_json: str) -> Exam:
    exam.class_list_json = class_list_json
    exam.class_list_source_json = class_list_source_json
    session.add(exam)
    return exam


def list_exam_questions(session: DbSession, exam_id: int) -> list[Question]:
    return session.exec(select(Question).where(Question.exam_id == exam_id).order_by(Question.id)).all()


def list_exam_submissions(session: DbSession, exam_id: int) -> list[Submission]:
    return session.exec(select(Submission).where(Submission.exam_id == exam_id).order_by(Submission.id)).all()


def list_front_page_unreviewed_submissions(session: DbSession, exam_id: int) -> list[Submission]:
    return session.exec(
        select(Submission).where(
            Submission.exam_id == exam_id,
            Submission.capture_mode == SubmissionCaptureMode.FRONT_PAGE_TOTALS,
            Submission.front_page_reviewed_at == None,  # noqa: E711
        )
    ).all()


def list_exam_key_files(session: DbSession, exam_id: int) -> list[ExamKeyFile]:
    return session.exec(select(ExamKeyFile).where(ExamKeyFile.exam_id == exam_id).order_by(ExamKeyFile.id)).all()


def list_exam_key_pages(session: DbSession, exam_id: int) -> list[ExamKeyPage]:
    return session.exec(select(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id).order_by(ExamKeyPage.page_number)).all()


def get_exam_key_page(session: DbSession, *, exam_id: int, page_number: int) -> ExamKeyPage | None:
    return session.exec(
        select(ExamKeyPage).where(
            ExamKeyPage.exam_id == exam_id,
            ExamKeyPage.page_number == page_number,
        )
    ).first()


def clear_exam_key_pages(session: DbSession, exam_id: int) -> None:
    session.exec(delete(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id))


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
    row = ExamKeyPage(
        exam_id=exam_id,
        page_number=page_number,
        image_path=image_path,
        blob_pathname=blob_pathname,
        blob_url=blob_url,
        width=width,
        height=height,
    )
    session.add(row)
    return row


def update_exam_key_page(session: DbSession, page: ExamKeyPage, **fields) -> ExamKeyPage:
    for key, value in fields.items():
        setattr(page, key, value)
    session.add(page)
    return page


def list_exam_parse_jobs(session: DbSession, exam_id: int) -> list[ExamKeyParseJob]:
    return session.exec(
        select(ExamKeyParseJob).where(ExamKeyParseJob.exam_id == exam_id).order_by(ExamKeyParseJob.created_at.desc(), ExamKeyParseJob.id.desc())
    ).all()


def get_exam_parse_job(session: DbSession, job_id: int) -> ExamKeyParseJob | None:
    return session.get(ExamKeyParseJob, job_id)


def update_exam_parse_job(session: DbSession, job: ExamKeyParseJob, **fields) -> ExamKeyParseJob:
    for key, value in fields.items():
        setattr(job, key, value)
    session.add(job)
    return job


def get_latest_exam_parse_job(session: DbSession, exam_id: int) -> ExamKeyParseJob | None:
    return session.exec(
        select(ExamKeyParseJob)
        .where(ExamKeyParseJob.exam_id == exam_id)
        .order_by(ExamKeyParseJob.created_at.desc(), ExamKeyParseJob.id.desc())
    ).first()


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
    job = ExamKeyParseJob(
        exam_id=exam_id,
        status=status,
        page_count=page_count,
        pages_done=pages_done,
        created_at=created_at,
        updated_at=updated_at,
    )
    session.add(job)
    session.flush()
    return job


def list_exam_parse_pages(session: DbSession, job_id: int) -> list[ExamKeyParsePage]:
    return session.exec(
        select(ExamKeyParsePage).where(ExamKeyParsePage.job_id == job_id).order_by(ExamKeyParsePage.page_number)
    ).all()


def update_exam_parse_page(session: DbSession, page: ExamKeyParsePage, **fields) -> ExamKeyParsePage:
    for key, value in fields.items():
        setattr(page, key, value)
    session.add(page)
    return page


def list_pending_exam_parse_pages(session: DbSession, job_id: int, *, limit: int) -> list[ExamKeyParsePage]:
    return session.exec(
        select(ExamKeyParsePage)
        .where(ExamKeyParsePage.job_id == job_id, ExamKeyParsePage.status == "pending")
        .order_by(ExamKeyParsePage.page_number)
        .limit(limit)
    ).all()


def get_exam_parse_page(session: DbSession, *, job_id: int, page_number: int) -> ExamKeyParsePage | None:
    return session.exec(
        select(ExamKeyParsePage).where(
            ExamKeyParsePage.job_id == job_id,
            ExamKeyParsePage.page_number == page_number,
        )
    ).first()


def exam_parse_job_has_remaining_work(session: DbSession, job_id: int) -> bool:
    remaining = session.exec(
        select(ExamKeyParsePage.id).where(
            ExamKeyParsePage.job_id == job_id,
            ExamKeyParsePage.status.in_(["pending", "running", "failed"]),
        )
    ).first()
    return remaining is not None


def create_exam_parse_page(
    session: DbSession,
    *,
    job_id: int,
    page_number: int,
    status: str,
    updated_at,
) -> ExamKeyParsePage:
    page = ExamKeyParsePage(job_id=job_id, page_number=page_number, status=status, updated_at=updated_at)
    session.add(page)
    return page


def get_latest_exam_intake_job(session: DbSession, exam_id: int) -> ExamIntakeJob | None:
    return session.exec(
        select(ExamIntakeJob)
        .where(ExamIntakeJob.exam_id == exam_id)
        .order_by(ExamIntakeJob.created_at.desc(), ExamIntakeJob.id.desc())
    ).first()


def list_latest_exam_intake_jobs_by_exam_id(session: DbSession, exam_ids: list[int]) -> dict[int, ExamIntakeJob]:
    if not exam_ids:
        return {}
    jobs = session.exec(
        select(ExamIntakeJob)
        .where(ExamIntakeJob.exam_id.in_(exam_ids))
        .order_by(ExamIntakeJob.exam_id.asc(), ExamIntakeJob.created_at.desc(), ExamIntakeJob.id.desc())
    ).all()
    latest_by_exam_id: dict[int, ExamIntakeJob] = {}
    for job in jobs:
        latest_by_exam_id.setdefault(job.exam_id, job)
    return latest_by_exam_id


def get_exam_intake_job(session: DbSession, job_id: int) -> ExamIntakeJob | None:
    return session.get(ExamIntakeJob, job_id)


def update_exam_intake_job(session: DbSession, job: ExamIntakeJob, **fields) -> ExamIntakeJob:
    for key, value in fields.items():
        setattr(job, key, value)
    session.add(job)
    return job


def list_queued_or_running_exam_intake_jobs(session: DbSession) -> list[ExamIntakeJob]:
    return session.exec(
        select(ExamIntakeJob)
        .where(ExamIntakeJob.status.in_(["queued", "running"]))
        .order_by(ExamIntakeJob.created_at.asc(), ExamIntakeJob.id.asc())
    ).all()


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
    job = ExamIntakeJob(
        exam_id=exam_id,
        bulk_upload_id=bulk_upload_id,
        status=status,
        stage=stage,
        page_count=page_count,
        pages_built=pages_built,
        pages_processed=pages_processed,
        submissions_created=submissions_created,
        candidates_ready=candidates_ready,
        review_open_threshold=review_open_threshold,
        initial_review_ready=initial_review_ready,
        fully_warmed=fully_warmed,
        review_ready=review_ready,
        thinking_level=thinking_level,
        last_progress_at=last_progress_at,
        metrics_json=metrics_json,
    )
    session.add(job)
    session.flush()
    return job


def list_bulk_upload_pages(session: DbSession, bulk_upload_id: int) -> list[BulkUploadPage]:
    return session.exec(
        select(BulkUploadPage).where(BulkUploadPage.bulk_upload_id == bulk_upload_id).order_by(BulkUploadPage.page_number)
    ).all()


def get_exam_bulk_upload(session: DbSession, bulk_upload_id: int) -> ExamBulkUploadFile | None:
    return session.get(ExamBulkUploadFile, bulk_upload_id)


def create_exam_bulk_upload(session: DbSession, *, exam_id: int, original_filename: str, stored_path: str) -> ExamBulkUploadFile:
    bulk = ExamBulkUploadFile(exam_id=exam_id, original_filename=original_filename, stored_path=stored_path)
    session.add(bulk)
    session.flush()
    return bulk


def update_exam_bulk_upload(
    session: DbSession,
    *,
    bulk: ExamBulkUploadFile,
    original_filename: str,
    stored_path: str,
    source_manifest_json: str | None = None,
) -> ExamBulkUploadFile:
    bulk.original_filename = original_filename
    bulk.stored_path = stored_path
    if source_manifest_json is not None:
        bulk.source_manifest_json = source_manifest_json
    session.add(bulk)
    return bulk


def clear_bulk_upload_pages(session: DbSession, *, bulk_upload_id: int) -> None:
    session.exec(delete(BulkUploadPage).where(BulkUploadPage.bulk_upload_id == bulk_upload_id))


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
    row = BulkUploadPage(
        bulk_upload_id=bulk_upload_id,
        page_number=page_number,
        image_path=image_path,
        width=width,
        height=height,
        detected_student_name=detected_student_name,
        detection_confidence=detection_confidence,
        detection_evidence_json=detection_evidence_json,
    )
    session.add(row)
    return row


def update_bulk_upload_page(session: DbSession, row: BulkUploadPage, **fields) -> BulkUploadPage:
    for key, value in fields.items():
        setattr(row, key, value)
    session.add(row)
    return row


def delete_exam_data(session: DbSession, *, exam: Exam) -> list[int]:
    exam_id = int(exam.id or 0)
    parse_job_ids = session.exec(select(ExamKeyParseJob.id).where(ExamKeyParseJob.exam_id == exam_id)).all()
    submission_ids = select(Submission.id).where(Submission.exam_id == exam_id)
    question_ids = select(Question.id).where(Question.exam_id == exam_id)
    bulk_upload_ids = select(ExamBulkUploadFile.id).where(ExamBulkUploadFile.exam_id == exam_id)

    session.exec(delete(SubmissionPage).where(SubmissionPage.submission_id.in_(submission_ids)))
    session.exec(delete(SubmissionFile).where(SubmissionFile.submission_id.in_(submission_ids)))
    session.exec(delete(AnswerCrop).where(AnswerCrop.submission_id.in_(submission_ids)))
    session.exec(delete(Transcription).where(Transcription.submission_id.in_(submission_ids)))
    session.exec(delete(GradeResult).where(GradeResult.submission_id.in_(submission_ids)))
    session.exec(delete(QuestionRegion).where(QuestionRegion.question_id.in_(question_ids)))
    session.exec(delete(QuestionParseEvidence).where(QuestionParseEvidence.exam_id == exam_id))
    session.exec(delete(AnswerCrop).where(AnswerCrop.question_id.in_(question_ids)))
    session.exec(delete(Transcription).where(Transcription.question_id.in_(question_ids)))
    session.exec(delete(GradeResult).where(GradeResult.question_id.in_(question_ids)))
    if parse_job_ids:
        session.exec(delete(ExamKeyParsePage).where(ExamKeyParsePage.job_id.in_(parse_job_ids)))
    session.exec(delete(Submission).where(Submission.exam_id == exam_id))
    session.exec(delete(Question).where(Question.exam_id == exam_id))
    session.exec(delete(ExamKeyParseJob).where(ExamKeyParseJob.exam_id == exam_id))
    session.exec(delete(ExamKeyPage).where(ExamKeyPage.exam_id == exam_id))
    session.exec(delete(ExamKeyFile).where(ExamKeyFile.exam_id == exam_id))
    session.exec(delete(ExamIntakeJob).where(ExamIntakeJob.exam_id == exam_id))
    session.exec(delete(BulkUploadPage).where(BulkUploadPage.bulk_upload_id.in_(bulk_upload_ids)))
    session.exec(delete(ExamBulkUploadFile).where(ExamBulkUploadFile.exam_id == exam_id))
    session.delete(exam)
    return [job_id for job_id in parse_job_ids if job_id is not None]


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
    row = ExamKeyFile(
        exam_id=exam_id,
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
