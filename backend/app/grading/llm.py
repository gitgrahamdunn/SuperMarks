"""Stub LLM grader (not yet implemented)."""

from typing import Any

from app.grading.base import GradeOutcome, Grader


class LLMStubGrader(Grader):
    name = "llm"

    def grade(self, transcription_text: str, rubric: dict[str, Any], max_marks: int) -> GradeOutcome:
        raise NotImplementedError(
            "LLM grading is not implemented yet. Integrate your model/API in app/grading/llm.py."
        )
