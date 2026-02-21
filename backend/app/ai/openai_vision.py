"""OpenAI Vision answer-key parsing client."""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


logger = logging.getLogger(__name__)


ANSWER_KEY_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "confidence_score": {"type": "number"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
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
                            "additionalProperties": False,
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


@dataclass
class OpenAIRequestError(Exception):
    status_code: int | None
    body: str
    message: str

    def __str__(self) -> str:
        return self.message


class AnswerKeyParser(Protocol):
    def parse(self, image_paths: list[Path], model: str) -> ParseResult:
        """Parse answer key images into structured data."""


def build_key_parse_request(
    model: str,
    prompt: str,
    images: list[bytes],
    mime_types: list[str],
    schema: dict[str, object],
) -> dict[str, object]:
    content: list[dict[str, str]] = [{"type": "input_text", "text": prompt}]
    for image_bytes, mime_type in zip(images, mime_types, strict=True):
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        normalized_mime = mime_type.lower().strip()
        if normalized_mime not in {"image/png", "image/jpeg"}:
            normalized_mime = "image/png"
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{normalized_mime};base64,{encoded}",
            }
        )

    return {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "answer_key_parse",
                "strict": True,
                "schema": schema,
            }
        },
    }


def force_questions_items_schema_requirements(schema: dict[str, object]) -> None:
    schema["type"] = "object"
    schema["additionalProperties"] = False
    schema.setdefault("properties", {})
    schema["properties"]["confidence_score"] = {"type": "number"}
    q = schema.setdefault("properties", {}).setdefault("questions", {})
    q["type"] = "array"
    items = q.get("items")
    if not isinstance(items, dict):
        items = {}
    q["items"] = items

    items["type"] = "object"
    items["additionalProperties"] = False
    items["properties"] = items.get("properties", {})
    items["properties"]["label"] = {"type": "string"}
    items["properties"]["max_marks"] = {"type": "number"}
    items["properties"]["criteria"] = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"desc": {"type": "string"}, "marks": {"type": "number"}},
            "required": ["desc", "marks"],
        },
    }
    items["required"] = ["label", "max_marks", "criteria"]
    schema["properties"]["questions"] = q
    schema["required"] = ["confidence_score", "questions"]


def make_schema_strict(schema: dict) -> dict:
    def _walk(node: object) -> None:
        if not isinstance(node, dict):
            if isinstance(node, list):
                for item in node:
                    _walk(item)
            return

        if node.get("type") == "object":
            properties = node.get("properties")
            if not isinstance(properties, dict):
                properties = {}
                node["properties"] = properties

            node["additionalProperties"] = False

            required = node.get("required")
            if not isinstance(required, list):
                node["required"] = list(properties.keys())

        properties = node.get("properties")
        if isinstance(properties, dict):
            for value in properties.values():
                _walk(value)

        items = node.get("items")
        if items is not None:
            _walk(items)

        for key in ("anyOf", "oneOf", "allOf"):
            variants = node.get(key)
            if isinstance(variants, list):
                for variant in variants:
                    _walk(variant)

    _walk(schema)
    return schema


class OpenAIAnswerKeyParser:
    _max_images_per_request = 6

    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)

    def parse(self, image_paths: list[Path], model: str) -> ParseResult:
        prompt = (
            "You are parsing an exam answer key. Identify questions, marks, and a draft rubric. "
            "Return ONLY JSON matching the provided schema."
        )
        chunks: list[list[Path]] = [
            image_paths[i : i + self._max_images_per_request] for i in range(0, len(image_paths), self._max_images_per_request)
        ]
        payloads: list[dict[str, object]] = []

        for chunk in chunks:
            images = [path.read_bytes() for path in chunk]
            mime_types = ["image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png" for path in chunk]
            schema = copy.deepcopy(ANSWER_KEY_SCHEMA)
            force_questions_items_schema_requirements(schema)
            strict_schema = make_schema_strict(schema)
            questions_schema = strict_schema.get("properties", {}).get("questions", {})
            questions_items_schema = questions_schema.get("items") if isinstance(questions_schema, dict) else None
            questions_items_value = json.dumps(questions_items_schema, default=str)[:500]
            logger.debug(
                "OpenAI answer_key_parse schema diagnostics: questions_type=%s items_type=%s items=%s items_required_type=%s",
                type(questions_schema).__name__,
                type(questions_items_schema).__name__,
                questions_items_value,
                type(questions_items_schema.get("required")).__name__ if isinstance(questions_items_schema, dict) else None,
            )
            request_payload = build_key_parse_request(
                model=model,
                prompt=prompt,
                images=images,
                mime_types=mime_types,
                schema=strict_schema,
            )
            try:
                response = self._client.responses.create(**request_payload)
            except Exception as exc:  # pragma: no cover - network errors are integration-level
                status_code = getattr(exc, "status_code", None)
                response_obj = getattr(exc, "response", None)
                body_text = ""
                if response_obj is not None:
                    body_text = getattr(response_obj, "text", "") or ""
                if not body_text:
                    body_text = str(exc)
                raise OpenAIRequestError(status_code=status_code, body=body_text, message=f"OpenAI request failed: {exc}") from exc
            payloads.append(json.loads(response.output_text))

        if len(payloads) == 1:
            return ParseResult(payload=payloads[0], model=model)

        merged_questions: list[object] = []
        confidence_scores: list[float] = []
        for payload in payloads:
            questions = payload.get("questions", [])
            if isinstance(questions, list):
                merged_questions.extend(questions)
            confidence = payload.get("confidence_score")
            if isinstance(confidence, (int, float)):
                confidence_scores.append(float(confidence))
        merged_confidence = min(confidence_scores) if confidence_scores else 0.0
        return ParseResult(payload={"confidence_score": merged_confidence, "questions": merged_questions}, model=model)


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
