"""Question region management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, delete, select

from app.db import get_session
from app.models import Question, QuestionRegion
from app.schemas import RegionIn, RegionRead

router = APIRouter(prefix="/questions", tags=["questions"])


@router.post("/{question_id}/regions", response_model=list[RegionRead])
def replace_regions(
    question_id: int,
    regions: list[RegionIn],
    session: Session = Depends(get_session),
) -> list[RegionRead]:
    question = session.get(Question, question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    session.exec(delete(QuestionRegion).where(QuestionRegion.question_id == question_id))
    created: list[RegionRead] = []
    for region in regions:
        row = QuestionRegion(question_id=question_id, **region.model_dump())
        session.add(row)
        session.flush()
        created.append(RegionRead(id=row.id, **region.model_dump()))

    session.commit()
    return created
