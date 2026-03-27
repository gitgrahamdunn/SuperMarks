"""D1 bridge-backed question repository functions."""

from __future__ import annotations

import json
from typing import Any

from app.d1_bridge import D1Statement, get_d1_bridge_client
from app.models import Question, QuestionParseEvidence, QuestionRegion, utcnow
from app.persistence import DbSession
from app.repositories.questions import question_sort_key
from app.schemas import RegionIn


def _question_from_row(row: dict[str, Any] | None) -> Question | None:
    if not isinstance(row, dict):
        return None
    return Question.model_validate(row)


def _question_region_from_row(row: dict[str, Any] | None) -> QuestionRegion | None:
    if not isinstance(row, dict):
        return None
    return QuestionRegion.model_validate(row)


def _bridge():
    return get_d1_bridge_client()


def get_question(session: DbSession, question_id: int) -> Question | None:
    _ = session
    return _question_from_row(
        _bridge().query_first(
            """
            SELECT id, exam_id, label, max_marks, rubric_json, created_at
            FROM question
            WHERE id = ?
            """,
            [question_id],
        )
    )


def get_exam_question(session: DbSession, exam_id: int, question_id: int) -> Question | None:
    _ = session
    return _question_from_row(
        _bridge().query_first(
            """
            SELECT id, exam_id, label, max_marks, rubric_json, created_at
            FROM question
            WHERE exam_id = ? AND id = ?
            """,
            [exam_id, question_id],
        )
    )


def list_exam_questions(session: DbSession, exam_id: int) -> list[Question]:
    _ = session
    rows = _bridge().query_all(
        """
        SELECT id, exam_id, label, max_marks, rubric_json, created_at
        FROM question
        WHERE exam_id = ?
        ORDER BY id
        """,
        [exam_id],
    )
    return [_question_from_row(row) for row in rows if _question_from_row(row) is not None]


def create_question(
    session: DbSession,
    *,
    exam_id: int,
    label: str,
    max_marks: int,
    rubric_json: str,
) -> Question:
    _ = session
    row = _bridge().query_first(
        """
        INSERT INTO question (exam_id, label, max_marks, rubric_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        RETURNING id, exam_id, label, max_marks, rubric_json, created_at
        """,
        [exam_id, label, max_marks, rubric_json, utcnow().isoformat()],
    )
    question = _question_from_row(row)
    if question is None:
        raise RuntimeError("D1 bridge did not return the created question row")
    return question


def update_question(
    session: DbSession,
    *,
    question: Question,
    label: str | None = None,
    max_marks: int | None = None,
    rubric_json: str | None = None,
) -> Question:
    _ = session
    row = _bridge().query_first(
        """
        UPDATE question
        SET label = ?, max_marks = ?, rubric_json = ?
        WHERE id = ?
        RETURNING id, exam_id, label, max_marks, rubric_json, created_at
        """,
        [
            label if label is not None else question.label,
            max_marks if max_marks is not None else question.max_marks,
            rubric_json if rubric_json is not None else question.rubric_json,
            int(question.id or 0),
        ],
    )
    updated_question = _question_from_row(row)
    if updated_question is None:
        raise RuntimeError("D1 bridge did not return the updated question row")
    return updated_question


def delete_question_dependencies(session: DbSession, question_id: int) -> None:
    _ = session
    _bridge().batch(
        [
            D1Statement("DELETE FROM questionregion WHERE question_id = ?", [question_id]),
            D1Statement("DELETE FROM questionparseevidence WHERE question_id = ?", [question_id]),
            D1Statement("DELETE FROM answercrop WHERE question_id = ?", [question_id]),
            D1Statement("DELETE FROM transcription WHERE question_id = ?", [question_id]),
            D1Statement("DELETE FROM graderesult WHERE question_id = ?", [question_id]),
        ]
    )


def delete_question(session: DbSession, question: Question) -> None:
    _ = session
    _bridge().run("DELETE FROM question WHERE id = ?", [int(question.id or 0)])


def replace_question_parse_evidence(
    session: DbSession,
    *,
    question_id: int,
    exam_id: int,
    page_number: int,
    evidence_list: list[dict[str, object]],
) -> None:
    _ = session
    bridge = _bridge()
    created_at = utcnow().isoformat()
    statements: list[D1Statement] = [
        D1Statement("DELETE FROM questionparseevidence WHERE question_id = ?", [question_id]),
    ]
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
        statements.append(
            D1Statement(
                """
                INSERT INTO questionparseevidence
                    (question_id, exam_id, page_number, x, y, w, h, evidence_kind, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    question_id,
                    exam_id,
                    resolved_page_number,
                    float(evidence.get("x") or 0),
                    float(evidence.get("y") or 0),
                    float(evidence.get("w") or 0.1),
                    float(evidence.get("h") or 0.1),
                    kind,
                    float(evidence.get("confidence") or 0),
                    created_at,
                ],
            )
        )
    bridge.batch(statements)


def replace_question_regions(session: DbSession, question_id: int, regions: list[RegionIn]) -> list[QuestionRegion]:
    _ = session
    bridge = _bridge()
    statements: list[D1Statement] = [
        D1Statement("DELETE FROM questionregion WHERE question_id = ?", [question_id]),
    ]
    for region in regions:
        statements.append(
            D1Statement(
                """
                INSERT INTO questionregion (question_id, page_number, x, y, w, h, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    question_id,
                    int(region.page_number),
                    float(region.x),
                    float(region.y),
                    float(region.w),
                    float(region.h),
                    utcnow().isoformat(),
                ],
            )
        )
    bridge.batch(statements)
    rows = bridge.query_all(
        """
        SELECT id, question_id, page_number, x, y, w, h, created_at
        FROM questionregion
        WHERE question_id = ?
        ORDER BY id
        """,
        [question_id],
    )
    return [_question_region_from_row(row) for row in rows if _question_region_from_row(row) is not None]


__all__ = [
    "create_question",
    "delete_question",
    "delete_question_dependencies",
    "get_exam_question",
    "get_question",
    "list_exam_questions",
    "question_sort_key",
    "replace_question_parse_evidence",
    "replace_question_regions",
    "update_question",
]
