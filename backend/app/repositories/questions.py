"""Question-oriented repository functions for the staged D1 migration."""

from __future__ import annotations

import json

from sqlmodel import delete
from sqlmodel import select

from app.models import AnswerCrop, GradeResult, Question, QuestionParseEvidence, QuestionRegion, Transcription
from app.persistence import DbSession
from app.schemas import RegionIn


def get_question(session: DbSession, question_id: int) -> Question | None:
    return session.get(Question, question_id)


def get_exam_question(session: DbSession, exam_id: int, question_id: int) -> Question | None:
    question = session.get(Question, question_id)
    if not question or question.exam_id != exam_id:
        return None
    return question


def list_exam_questions(session: DbSession, exam_id: int) -> list[Question]:
    return session.exec(select(Question).where(Question.exam_id == exam_id)).all()


def create_question(
    session: DbSession,
    *,
    exam_id: int,
    label: str,
    max_marks: int,
    rubric_json: str,
) -> Question:
    question = Question(exam_id=exam_id, label=label, max_marks=max_marks, rubric_json=rubric_json)
    session.add(question)
    session.flush()
    return question


def update_question(
    session: DbSession,
    *,
    question: Question,
    label: str | None = None,
    max_marks: int | None = None,
    rubric_json: str | None = None,
) -> Question:
    if label is not None:
        question.label = label
    if max_marks is not None:
        question.max_marks = max_marks
    if rubric_json is not None:
        question.rubric_json = rubric_json
    session.add(question)
    session.flush()
    return question


def delete_question_dependencies(session: DbSession, question_id: int) -> None:
    session.exec(delete(QuestionRegion).where(QuestionRegion.question_id == question_id))
    session.exec(delete(QuestionParseEvidence).where(QuestionParseEvidence.question_id == question_id))
    session.exec(delete(AnswerCrop).where(AnswerCrop.question_id == question_id))
    session.exec(delete(Transcription).where(Transcription.question_id == question_id))
    session.exec(delete(GradeResult).where(GradeResult.question_id == question_id))


def delete_question(session: DbSession, question: Question) -> None:
    session.delete(question)


def replace_question_parse_evidence(
    session: DbSession,
    *,
    question_id: int,
    exam_id: int,
    page_number: int,
    evidence_list: list[dict[str, object]],
) -> None:
    session.exec(delete(QuestionParseEvidence).where(QuestionParseEvidence.question_id == question_id))
    for evidence in evidence_list:
        kind = str(evidence.get("kind") or "question_box")
        if kind not in {"question_box", "answer_box", "marks_box"}:
            continue
        evidence_page_number = evidence.get("page_number")
        try:
            resolved_page_number = int(evidence_page_number)
        except (TypeError, ValueError):
            resolved_page_number = page_number
        if resolved_page_number <= 0:
            resolved_page_number = page_number
        session.add(
            QuestionParseEvidence(
                question_id=question_id,
                exam_id=exam_id,
                page_number=resolved_page_number,
                x=float(evidence.get("x") or 0),
                y=float(evidence.get("y") or 0),
                w=float(evidence.get("w") or 0.1),
                h=float(evidence.get("h") or 0.1),
                evidence_kind=kind,
                confidence=float(evidence.get("confidence") or 0),
            )
        )


def question_sort_key(question: Question) -> tuple[int, int, int]:
    rubric = json.loads(question.rubric_json)
    parse_order = int(rubric.get("parse_order") or 0)
    source_page_number = int(rubric.get("source_page_number") or rubric.get("key_page_number") or 0)
    if parse_order > 0:
        return (0, parse_order, int(question.id or 0))
    return (1, source_page_number, int(question.id or 0))


def replace_question_regions(session: DbSession, question_id: int, regions: list[RegionIn]) -> list[QuestionRegion]:
    session.exec(delete(QuestionRegion).where(QuestionRegion.question_id == question_id))

    created: list[QuestionRegion] = []
    for region in regions:
        row = QuestionRegion(question_id=question_id, **region.model_dump())
        session.add(row)
        session.flush()
        created.append(row)

    session.commit()
    return created
