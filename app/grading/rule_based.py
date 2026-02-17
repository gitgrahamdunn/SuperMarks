"""Simple heuristic rubric-based grader."""

from __future__ import annotations

import re
from typing import Any

from app.grading.base import GradeOutcome, Grader


class RuleBasedGrader(Grader):
    name = "rule_based"

    def grade(self, transcription_text: str, rubric: dict[str, Any], max_marks: int) -> GradeOutcome:
        text = transcription_text.lower()
        criteria = rubric.get("criteria", [])
        answer_key = str(rubric.get("answer_key", "")).strip().lower()

        earned = 0.0
        breakdown_items: list[dict[str, Any]] = []
        comments: list[str] = []

        for crit in criteria:
            crit_id = str(crit.get("id", ""))
            desc = str(crit.get("desc", ""))
            marks = float(crit.get("marks", 0))

            tokens = [tok for tok in re.split(r"\W+", f"{crit_id} {desc}".lower()) if len(tok) > 3]
            hit_count = sum(1 for token in set(tokens) if token in text)
            score = marks if hit_count > 0 else 0.0
            earned += score

            breakdown_items.append(
                {
                    "criterion_id": crit_id,
                    "description": desc,
                    "max_marks": marks,
                    "awarded": score,
                    "matched_tokens": [token for token in set(tokens) if token in text],
                }
            )

        if answer_key:
            if answer_key in text:
                comments.append("Final answer key appears present in response.")
            else:
                comments.append("Final answer key not detected; final-mark credit may be missing.")

        total_marks = float(rubric.get("total_marks", max_marks))
        earned = min(earned, float(max_marks), total_marks)

        feedback = {
            "comments": comments or ["Heuristic rule-based grading applied."],
            "error_spans": [],
        }
        breakdown = {"criteria": breakdown_items, "total_marks": total_marks}
        return GradeOutcome(earned, breakdown, feedback, model_name=self.name)
