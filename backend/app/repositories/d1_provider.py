"""D1-backed repository provider entrypoint.

For now this is a hybrid provider:
- `d1-bridge` uses the Worker HTTP bridge for the question repository
- parse-related exam methods and read-heavy submission methods are bridged
- remaining surfaces still fall back to SQLModel

This keeps the first bridge slice real without forcing an incomplete cutover.
The plain `d1` backend remains unimplemented.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast

from app.settings import settings
from app.repositories.contracts import ExamRepository, QuestionRepository, ReportingRepository, RepositoryProvider, SubmissionRepository
from app.repositories.sqlmodel_provider import provider as sqlmodel_provider
from . import d1_bridge_exams, d1_bridge_questions, d1_bridge_reporting, d1_bridge_submissions


def _bridge_is_configured() -> bool:
    bridge_url = (
        os.getenv("SUPERMARKS_D1_BRIDGE_URL")
        or os.getenv("D1_BRIDGE_URL")
        or str(settings.d1_bridge_url or "")
    ).strip()
    bridge_token = (
        os.getenv("SUPERMARKS_D1_BRIDGE_TOKEN")
        or os.getenv("D1_BRIDGE_TOKEN")
        or os.getenv("BACKEND_API_KEY")
        or str(settings.d1_bridge_token or "")
    ).strip()
    return bool(bridge_url and bridge_token)


def _strict_bridge_attr(module, name: str):
    if not _bridge_is_configured():
        return None
    if hasattr(module, name):
        return getattr(module, name)
    raise AttributeError(f"d1-bridge repository method is not implemented: {module.__name__}.{name}")


@dataclass(frozen=True)
class D1BridgeHybridExamRepository:
    def __getattr__(self, name: str):
        bridge_attr = _strict_bridge_attr(d1_bridge_exams, name)
        if bridge_attr is not None:
            return bridge_attr
        return getattr(sqlmodel_provider.exams, name)

    def get_exam(self, session, exam_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.get_exam(session, exam_id)
        return d1_bridge_exams.get_exam(session, exam_id)

    def update_exam(self, session, exam, **fields):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.update_exam(session, exam, **fields)
        return d1_bridge_exams.update_exam(session, exam, **fields)

    def list_exam_key_pages(self, session, exam_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.list_exam_key_pages(session, exam_id)
        return d1_bridge_exams.list_exam_key_pages(session, exam_id)

    def get_exam_key_page(self, session, *, exam_id: int, page_number: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.get_exam_key_page(session, exam_id=exam_id, page_number=page_number)
        return d1_bridge_exams.get_exam_key_page(session, exam_id=exam_id, page_number=page_number)

    def list_exam_parse_jobs(self, session, exam_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.list_exam_parse_jobs(session, exam_id)
        return d1_bridge_exams.list_exam_parse_jobs(session, exam_id)

    def get_exam_parse_job(self, session, job_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.get_exam_parse_job(session, job_id)
        return d1_bridge_exams.get_exam_parse_job(session, job_id)

    def update_exam_parse_job(self, session, job, **fields):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.update_exam_parse_job(session, job, **fields)
        return d1_bridge_exams.update_exam_parse_job(session, job, **fields)

    def get_latest_exam_parse_job(self, session, exam_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.get_latest_exam_parse_job(session, exam_id)
        return d1_bridge_exams.get_latest_exam_parse_job(session, exam_id)

    def create_exam_parse_job(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.create_exam_parse_job(session, **kwargs)
        return d1_bridge_exams.create_exam_parse_job(session, **kwargs)

    def list_exam_parse_pages(self, session, job_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.list_exam_parse_pages(session, job_id)
        return d1_bridge_exams.list_exam_parse_pages(session, job_id)

    def update_exam_parse_page(self, session, page, **fields):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.update_exam_parse_page(session, page, **fields)
        return d1_bridge_exams.update_exam_parse_page(session, page, **fields)

    def list_pending_exam_parse_pages(self, session, job_id: int, *, limit: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.list_pending_exam_parse_pages(session, job_id, limit=limit)
        return d1_bridge_exams.list_pending_exam_parse_pages(session, job_id, limit=limit)

    def get_exam_parse_page(self, session, *, job_id: int, page_number: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.get_exam_parse_page(session, job_id=job_id, page_number=page_number)
        return d1_bridge_exams.get_exam_parse_page(session, job_id=job_id, page_number=page_number)

    def exam_parse_job_has_remaining_work(self, session, job_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.exam_parse_job_has_remaining_work(session, job_id)
        return d1_bridge_exams.exam_parse_job_has_remaining_work(session, job_id)

    def create_exam_parse_page(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.create_exam_parse_page(session, **kwargs)
        return d1_bridge_exams.create_exam_parse_page(session, **kwargs)

    def get_latest_exam_intake_job(self, session, exam_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.get_latest_exam_intake_job(session, exam_id)
        return d1_bridge_exams.get_latest_exam_intake_job(session, exam_id)

    def list_latest_exam_intake_jobs_by_exam_id(self, session, exam_ids: list[int]):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.list_latest_exam_intake_jobs_by_exam_id(session, exam_ids)
        return d1_bridge_exams.list_latest_exam_intake_jobs_by_exam_id(session, exam_ids)

    def get_exam_intake_job(self, session, job_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.get_exam_intake_job(session, job_id)
        return d1_bridge_exams.get_exam_intake_job(session, job_id)

    def update_exam_intake_job(self, session, job, **fields):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.update_exam_intake_job(session, job, **fields)
        return d1_bridge_exams.update_exam_intake_job(session, job, **fields)

    def list_queued_or_running_exam_intake_jobs(self, session):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.list_queued_or_running_exam_intake_jobs(session)
        return d1_bridge_exams.list_queued_or_running_exam_intake_jobs(session)

    def create_exam_intake_job(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.create_exam_intake_job(session, **kwargs)
        return d1_bridge_exams.create_exam_intake_job(session, **kwargs)

    def list_bulk_upload_pages(self, session, bulk_upload_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.list_bulk_upload_pages(session, bulk_upload_id)
        return d1_bridge_exams.list_bulk_upload_pages(session, bulk_upload_id)

    def get_exam_bulk_upload(self, session, bulk_upload_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.get_exam_bulk_upload(session, bulk_upload_id)
        return d1_bridge_exams.get_exam_bulk_upload(session, bulk_upload_id)

    def create_exam_bulk_upload(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.create_exam_bulk_upload(session, **kwargs)
        return d1_bridge_exams.create_exam_bulk_upload(session, **kwargs)

    def update_exam_bulk_upload(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.update_exam_bulk_upload(session, **kwargs)
        return d1_bridge_exams.update_exam_bulk_upload(session, **kwargs)

    def clear_bulk_upload_pages(self, session, *, bulk_upload_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.clear_bulk_upload_pages(session, bulk_upload_id=bulk_upload_id)
        return d1_bridge_exams.clear_bulk_upload_pages(session, bulk_upload_id=bulk_upload_id)

    def create_bulk_upload_page(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.create_bulk_upload_page(session, **kwargs)
        return d1_bridge_exams.create_bulk_upload_page(session, **kwargs)

    def update_bulk_upload_page(self, session, row, **fields):
        if not _bridge_is_configured():
            return sqlmodel_provider.exams.update_bulk_upload_page(session, row, **fields)
        return d1_bridge_exams.update_bulk_upload_page(session, row, **fields)


@dataclass(frozen=True)
class D1BridgeHybridSubmissionRepository:
    def __getattr__(self, name: str):
        bridge_attr = _strict_bridge_attr(d1_bridge_submissions, name)
        if bridge_attr is not None:
            return bridge_attr
        return getattr(sqlmodel_provider.submissions, name)

    def get_submission(self, session, submission_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.get_submission(session, submission_id)
        return d1_bridge_submissions.get_submission(session, submission_id)

    def create_submission(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.create_submission(session, **kwargs)
        return d1_bridge_submissions.create_submission(session, **kwargs)

    def update_submission_front_page_data(self, session, submission, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.update_submission_front_page_data(session, submission, **kwargs)
        return d1_bridge_submissions.update_submission_front_page_data(session, submission, **kwargs)

    def list_submission_files(self, session, submission_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_submission_files(session, submission_id)
        return d1_bridge_submissions.list_submission_files(session, submission_id)

    def list_submission_files_for_submission_ids(self, session, submission_ids):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_submission_files_for_submission_ids(session, submission_ids)
        return d1_bridge_submissions.list_submission_files_for_submission_ids(session, submission_ids)

    def create_submission_file(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.create_submission_file(session, **kwargs)
        return d1_bridge_submissions.create_submission_file(session, **kwargs)

    def list_submission_pages(self, session, submission_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_submission_pages(session, submission_id)
        return d1_bridge_submissions.list_submission_pages(session, submission_id)

    def list_submission_crops(self, session, submission_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_submission_crops(session, submission_id)
        return d1_bridge_submissions.list_submission_crops(session, submission_id)

    def list_submission_transcriptions(self, session, submission_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_submission_transcriptions(session, submission_id)
        return d1_bridge_submissions.list_submission_transcriptions(session, submission_id)

    def list_submission_grades(self, session, submission_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_submission_grades(session, submission_id)
        return d1_bridge_submissions.list_submission_grades(session, submission_id)

    def list_exam_questions(self, session, exam_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_exam_questions(session, exam_id)
        return d1_bridge_submissions.list_exam_questions(session, exam_id)

    def list_exam_front_page_total_submissions(self, session, exam_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_exam_front_page_total_submissions(session, exam_id)
        return d1_bridge_submissions.list_exam_front_page_total_submissions(session, exam_id)

    def list_submissions_by_ids(self, session, submission_ids):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_submissions_by_ids(session, submission_ids)
        return d1_bridge_submissions.list_submissions_by_ids(session, submission_ids)

    def list_submission_pages_for_submission_ids(self, session, submission_ids):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_submission_pages_for_submission_ids(session, submission_ids)
        return d1_bridge_submissions.list_submission_pages_for_submission_ids(session, submission_ids)

    def list_question_regions(self, session, question_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.list_question_regions(session, question_id)
        return d1_bridge_submissions.list_question_regions(session, question_id)

    def get_submission_page(self, session, submission_id: int, page_number: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.get_submission_page(session, submission_id, page_number)
        return d1_bridge_submissions.get_submission_page(session, submission_id, page_number)

    def clear_submission_pages(self, session, submission_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.clear_submission_pages(session, submission_id)
        return d1_bridge_submissions.clear_submission_pages(session, submission_id)

    def create_submission_page(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.create_submission_page(session, **kwargs)
        return d1_bridge_submissions.create_submission_page(session, **kwargs)

    def get_submission_crop(self, session, submission_id: int, question_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.get_submission_crop(session, submission_id, question_id)
        return d1_bridge_submissions.get_submission_crop(session, submission_id, question_id)

    def clear_submission_crops(self, session, submission_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.clear_submission_crops(session, submission_id)
        return d1_bridge_submissions.clear_submission_crops(session, submission_id)

    def create_submission_crop(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.create_submission_crop(session, **kwargs)
        return d1_bridge_submissions.create_submission_crop(session, **kwargs)

    def get_submission_grade(self, session, submission_id: int, question_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.get_submission_grade(session, submission_id, question_id)
        return d1_bridge_submissions.get_submission_grade(session, submission_id, question_id)

    def clear_submission_transcriptions(self, session, submission_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.clear_submission_transcriptions(session, submission_id)
        return d1_bridge_submissions.clear_submission_transcriptions(session, submission_id)

    def create_submission_transcription(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.create_submission_transcription(session, **kwargs)
        return d1_bridge_submissions.create_submission_transcription(session, **kwargs)

    def clear_submission_grades(self, session, submission_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.clear_submission_grades(session, submission_id)
        return d1_bridge_submissions.clear_submission_grades(session, submission_id)

    def upsert_submission_grade(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.upsert_submission_grade(session, **kwargs)
        return d1_bridge_submissions.upsert_submission_grade(session, **kwargs)

    def update_submission_status(self, session, submission, status):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.update_submission_status(session, submission, status)
        return d1_bridge_submissions.update_submission_status(session, submission, status)

    def update_submission_capture_mode(self, session, submission, capture_mode):
        if not _bridge_is_configured():
            return sqlmodel_provider.submissions.update_submission_capture_mode(session, submission, capture_mode)
        return d1_bridge_submissions.update_submission_capture_mode(session, submission, capture_mode)


@dataclass(frozen=True)
class D1BridgeHybridQuestionRepository:
    def __getattr__(self, name: str):
        bridge_attr = _strict_bridge_attr(d1_bridge_questions, name)
        if bridge_attr is not None:
            return bridge_attr
        return getattr(sqlmodel_provider.questions, name)

    def get_question(self, session, question_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.questions.get_question(session, question_id)
        return d1_bridge_questions.get_question(session, question_id)

    def get_exam_question(self, session, exam_id: int, question_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.questions.get_exam_question(session, exam_id, question_id)
        return d1_bridge_questions.get_exam_question(session, exam_id, question_id)

    def list_exam_questions(self, session, exam_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.questions.list_exam_questions(session, exam_id)
        return d1_bridge_questions.list_exam_questions(session, exam_id)

    def create_question(self, session, **kwargs):
        if not _bridge_is_configured():
            return sqlmodel_provider.questions.create_question(session, **kwargs)
        return d1_bridge_questions.create_question(session, **kwargs)

    def update_question(self, session, question, **fields):
        if not _bridge_is_configured():
            return sqlmodel_provider.questions.update_question(session, question=question, **fields)
        return d1_bridge_questions.update_question(session, question=question, **fields)

    def delete_question_dependencies(self, session, question_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.questions.delete_question_dependencies(session, question_id)
        return d1_bridge_questions.delete_question_dependencies(session, question_id)

    def delete_question(self, session, question):
        if not _bridge_is_configured():
            return sqlmodel_provider.questions.delete_question(session, question)
        return d1_bridge_questions.delete_question(session, question)

    def replace_question_parse_evidence(self, session, *, question_id: int, exam_id: int, page_number: int, evidence_list):
        if not _bridge_is_configured():
            return sqlmodel_provider.questions.replace_question_parse_evidence(
                session, question_id=question_id, exam_id=exam_id, page_number=page_number, evidence_list=evidence_list
            )
        return d1_bridge_questions.replace_question_parse_evidence(
            session,
            question_id=question_id,
            exam_id=exam_id,
            page_number=page_number,
            evidence_list=evidence_list,
        )

    def replace_question_regions(self, session, question_id: int, regions):
        if not _bridge_is_configured():
            return sqlmodel_provider.questions.replace_question_regions(session, question_id, regions)
        return d1_bridge_questions.replace_question_regions(session, question_id, regions)


@dataclass(frozen=True)
class D1BridgeHybridReportingRepository:
    def __getattr__(self, name: str):
        bridge_attr = _strict_bridge_attr(d1_bridge_reporting, name)
        if bridge_attr is not None:
            return bridge_attr
        return getattr(sqlmodel_provider.reporting, name)

    def load_submission_reporting_collections(self, session, submission_id: int, exam_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.reporting.load_submission_reporting_collections(session, submission_id, exam_id)
        return d1_bridge_reporting.load_submission_reporting_collections(session, submission_id, exam_id)

    def load_exam_reporting_collections(self, session, exam_id: int):
        if not _bridge_is_configured():
            return sqlmodel_provider.reporting.load_exam_reporting_collections(session, exam_id)
        return d1_bridge_reporting.load_exam_reporting_collections(session, exam_id)


def get_provider() -> RepositoryProvider:
    backend = (os.getenv("SUPERMARKS_REPOSITORY_BACKEND", "sqlmodel") or "sqlmodel").strip().lower()
    if backend == "d1-bridge":
        @dataclass(frozen=True)
        class D1BridgeHybridRepositoryProvider:
            exams: ExamRepository = cast(ExamRepository, D1BridgeHybridExamRepository())
            submissions: SubmissionRepository = cast(SubmissionRepository, D1BridgeHybridSubmissionRepository())
            questions: QuestionRepository = cast(QuestionRepository, D1BridgeHybridQuestionRepository())
            reporting: ReportingRepository = cast(ReportingRepository, D1BridgeHybridReportingRepository())

        return cast(RepositoryProvider, D1BridgeHybridRepositoryProvider())

    raise NotImplementedError(
        "Direct D1 repository provider is not implemented yet. "
        "Use SUPERMARKS_REPOSITORY_BACKEND=d1-bridge for the staged hybrid bridge path. "
        "See backend/d1/README.md and backend/d1/migrations/0001_initial.sql."
    )
