"""Repository contracts for the staged D1 migration."""

from __future__ import annotations

from typing import Protocol


class ExamRepository(Protocol):
    pass


class SubmissionRepository(Protocol):
    pass


class QuestionRepository(Protocol):
    pass


class ReportingRepository(Protocol):
    pass


class UserRepository(Protocol):
    pass


class RepositoryProvider(Protocol):
    exams: ExamRepository
    submissions: SubmissionRepository
    questions: QuestionRepository
    reporting: ReportingRepository
    users: UserRepository
