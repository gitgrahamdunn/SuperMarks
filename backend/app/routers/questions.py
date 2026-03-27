"""Question region management endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import can_access_owned_resource
from app.persistence import DbSession, get_repository_session, repository_provider
from app.models import Question, QuestionRegion
from app.schemas import RegionIn, RegionRead

router = APIRouter(prefix="/questions", tags=["questions"])
question_repo = repository_provider().questions
exam_repo = repository_provider().exams


@router.post("/{question_id}/regions", response_model=list[RegionRead])
def replace_regions(
    question_id: int,
    regions: list[RegionIn],
    session: DbSession = Depends(get_repository_session),
) -> list[RegionRead]:
    question = question_repo.get_question(session, question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    exam = exam_repo.get_exam(session, question.exam_id)
    if not exam or not can_access_owned_resource(exam.owner_user_id):
        raise HTTPException(status_code=404, detail="Question not found")

    created = question_repo.replace_question_regions(session, question_id, regions)
    return [RegionRead(id=row.id, x=row.x, y=row.y, w=row.w, h=row.h, page_number=row.page_number) for row in created]
