"""Grader interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class GradeOutcome:
    marks_awarded: float
    breakdown: dict[str, Any]
    feedback: dict[str, Any]
    model_name: str


class Grader(Protocol):
    """Grader protocol for evaluating answer text with a rubric."""

    name: str

    def grade(self, transcription_text: str, rubric: dict[str, Any], max_marks: int) -> GradeOutcome:
        """Return grading outcome."""
