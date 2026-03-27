"""Reporting-oriented repository functions for the staged D1 migration."""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import select

from app.models import AnswerCrop, GradeResult, Question, QuestionRegion, Submission, SubmissionPage, Transcription
from app.persistence import DbSession


@dataclass
class SubmissionReportingCollections:
    pages: list[SubmissionPage]
    crops: list[AnswerCrop]
    transcriptions: list[Transcription]
    grades: list[GradeResult]


@dataclass
class ExamReportingCollections:
    questions: list[Question]
    submissions: list[Submission]
    question_regions: list[QuestionRegion]
    pages: list[SubmissionPage]
    crops: list[AnswerCrop]
    transcriptions: list[Transcription]
    grades: list[GradeResult]


def load_submission_reporting_collections(session: DbSession, submission_id: int, exam_id: int) -> SubmissionReportingCollections:
    del exam_id
    return SubmissionReportingCollections(
        pages=session.exec(select(SubmissionPage).where(SubmissionPage.submission_id == submission_id)).all(),
        crops=session.exec(select(AnswerCrop).where(AnswerCrop.submission_id == submission_id)).all(),
        transcriptions=session.exec(select(Transcription).where(Transcription.submission_id == submission_id)).all(),
        grades=session.exec(select(GradeResult).where(GradeResult.submission_id == submission_id)).all(),
    )


def load_exam_reporting_collections(session: DbSession, exam_id: int) -> ExamReportingCollections:
    questions = session.exec(select(Question).where(Question.exam_id == exam_id).order_by(Question.id)).all()
    submissions = session.exec(select(Submission).where(Submission.exam_id == exam_id).order_by(Submission.id)).all()
    submission_ids = [submission.id for submission in submissions if submission.id is not None]
    question_ids = [question.id for question in questions if question.id is not None]

    if submission_ids:
        pages = session.exec(select(SubmissionPage).where(SubmissionPage.submission_id.in_(submission_ids))).all()
        crops = session.exec(select(AnswerCrop).where(AnswerCrop.submission_id.in_(submission_ids))).all()
        transcriptions = session.exec(select(Transcription).where(Transcription.submission_id.in_(submission_ids))).all()
        grades = session.exec(select(GradeResult).where(GradeResult.submission_id.in_(submission_ids))).all()
    else:
        pages = []
        crops = []
        transcriptions = []
        grades = []

    if question_ids:
        question_regions = session.exec(select(QuestionRegion).where(QuestionRegion.question_id.in_(question_ids))).all()
    else:
        question_regions = []

    return ExamReportingCollections(
        questions=questions,
        submissions=submissions,
        question_regions=question_regions,
        pages=pages,
        crops=crops,
        transcriptions=transcriptions,
        grades=grades,
    )
