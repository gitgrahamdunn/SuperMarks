"""Grader factory/dispatcher."""

from app.grading.base import Grader
from app.grading.llm import LLMStubGrader
from app.grading.rule_based import RuleBasedGrader


def get_grader(name: str) -> Grader:
    grader = name.lower()
    if grader == "rule_based":
        return RuleBasedGrader()
    if grader == "llm":
        return LLMStubGrader()
    raise ValueError(f"Unknown grader '{name}'. Use one of: rule_based, llm")
