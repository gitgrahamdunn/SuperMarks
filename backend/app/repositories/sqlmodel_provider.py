"""Current SQLModel-backed repository provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from app.repositories.contracts import ExamRepository, QuestionRepository, ReportingRepository, RepositoryProvider, SubmissionRepository, UserRepository
from . import exams, questions, reporting, submissions, users


@dataclass(frozen=True)
class SqlModelRepositoryProvider:
    exams: ExamRepository = cast(ExamRepository, exams)
    submissions: SubmissionRepository = cast(SubmissionRepository, submissions)
    questions: QuestionRepository = cast(QuestionRepository, questions)
    reporting: ReportingRepository = cast(ReportingRepository, reporting)
    users: UserRepository = cast(UserRepository, users)


provider = cast(RepositoryProvider, SqlModelRepositoryProvider())
