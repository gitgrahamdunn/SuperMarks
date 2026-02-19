"""OpenAI Vision answer-key parsing client."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


ANSWER_KEY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "confidence_score": {"type": "number"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "max_marks": {"type": "number"},
                    "question_text": {"type": "string"},
                    "answer_key": {"type": "string"},
                    "model_solution": {"type": "string"},
                    "criteria": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "desc": {"type": "string"},
                                "marks": {"type": "number"},
                            },
                            "required": ["desc", "marks"],
                        },
                    },
                },
                "required": ["label", "max_marks", "criteria"],
            },
        },
    },
    "required": ["confidence_score", "questions"],
}


@dataclass
class ParseResult:
    payload: dict[str, object]
    model: str


class AnswerKeyParser(Protocol):
    def parse(self, image_paths: list[Path], model: str) -> ParseResult:
        """Parse answer key images into structured data."""


class OpenAIAnswerKeyParser:
    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)

    def parse(self, image_paths: list[Path], model: str) -> ParseResult:
        content: list[dict[str, object]] = [
            {
                "type": "input_text",
                "text": (
                    "You are parsing an exam answer key. Identify questions, marks, and a draft rubric. "
                    "Return JSON only matching schema."
                ),
            }
        ]

        for image_path in image_paths:
            encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
            suffix = image_path.suffix.lower().lstrip(".") or "png"
            mime = "jpeg" if suffix in {"jpg", "jpeg"} else "png"
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/{mime};base64,{encoded}",
                }
            )

        response = self._client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You are parsing an exam answer key. Identify questions, marks, draft rubric. "
                                "Return JSON only matching schema."
                            ),
                        }
                    ],
                },
                {"role": "user", "content": content},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "answer_key_parse",
                    "strict": True,
                    "schema": ANSWER_KEY_SCHEMA,
                }
            },
        )
        output = response.output_text
        return ParseResult(payload=json.loads(output), model=model)


class MockAnswerKeyParser:
    def parse(self, image_paths: list[Path], model: str) -> ParseResult:
        return ParseResult(
            payload={
                "confidence_score": 0.92,
                "questions": [
                    {
                        "label": "Q1",
                        "max_marks": 5,
                        "question_text": "Solve for x",
                        "answer_key": "x=4",
                        "model_solution": "x+3=7 so x=4",
                        "criteria": [
                            {"desc": "Correct algebra", "marks": 3},
                            {"desc": "Correct final answer", "marks": 2},
                        ],
                    },
                    {
                        "label": "Q2",
                        "max_marks": 3,
                        "question_text": "Find y",
                        "answer_key": "y=7",
                        "model_solution": "Substitute and solve",
                        "criteria": [
                            {"desc": "Method", "marks": 1},
                            {"desc": "Correct final answer", "marks": 2},
                        ],
                    },
                ],
            },
            model=model,
        )


def get_answer_key_parser() -> AnswerKeyParser:
    if os.getenv("OPENAI_MOCK", "").strip() == "1":
        return MockAnswerKeyParser()
    return OpenAIAnswerKeyParser()
