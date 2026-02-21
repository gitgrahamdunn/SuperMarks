"""OpenAI Vision answer-key parsing client."""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.pipeline.key_pages import normalize_key_page_image

logger = logging.getLogger(__name__)


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


@dataclass
class SchemaBuildError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class AnswerKeyParser(Protocol):
    def parse(self, image_paths: list[Path], model: str, request_id: str) -> ParseResult:
        """Parse answer key images into structured data."""


def _base_answer_key_schema() -> dict[str, Any]:
    return {
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
                        "notes": {"type": "string"},
                        "criteria": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "desc": {"type": "string"},
                                    "marks": {"type": "number"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def _ensure_strict_schema_node(node: object) -> None:
    if isinstance(node, list):
        for item in node:
            _ensure_strict_schema_node(item)
        return

    if not isinstance(node, dict):
        return

    if node.get("type") == "object":
        properties = node.get("properties")
        if not isinstance(properties, dict):
            properties = {}
            node["properties"] = properties
        node["additionalProperties"] = False
        node["required"] = list(properties.keys())

    properties = node.get("properties")
    if isinstance(properties, dict):
        for value in properties.values():
            _ensure_strict_schema_node(value)

    items = node.get("items")
    if items is not None:
        _ensure_strict_schema_node(items)

    for key in ("anyOf", "oneOf", "allOf"):
        variants = node.get(key)
        if isinstance(variants, list):
            for variant in variants:
                _ensure_strict_schema_node(variant)


def build_answer_key_response_schema() -> dict[str, Any]:
    schema = copy.deepcopy(_base_answer_key_schema())
    _ensure_strict_schema_node(schema)
    validate_schema_strictness(schema)
    return schema


def validate_schema_strictness(schema: dict[str, Any]) -> None:
    def _walk(node: object, path: str) -> None:
        if isinstance(node, list):
            for idx, item in enumerate(node):
                _walk(item, f"{path}[{idx}]")
            return

        if not isinstance(node, dict):
            return

        if node.get("type") == "object":
            if node.get("additionalProperties") is not False:
                raise SchemaBuildError(f"Object at {path} missing additionalProperties=false")
            required = node.get("required")
            if not isinstance(required, list):
                raise SchemaBuildError(f"Object at {path} missing required list")

        if node.get("type") == "array" and isinstance(node.get("items"), dict):
            items = node["items"]
            if items.get("type") == "object" and not isinstance(items.get("required"), list):
                raise SchemaBuildError(f"Array items object at {path}.items missing required list")

        for key, value in node.items():
            _walk(value, f"{path}.{key}")

    _walk(schema, "schema")


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
            normalized_mime = "image/jpeg"
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


class OpenAIAnswerKeyParser:
    def __init__(self, timeout_seconds: float = 20.0, max_images_per_request: int = 6, payload_limit_bytes: int = 2_500_000) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        self._max_images_per_request = max_images_per_request
        self._payload_limit_bytes = payload_limit_bytes

    def parse(self, image_paths: list[Path], model: str, request_id: str) -> ParseResult:
        prompt = (
            "You are parsing an exam answer key and must produce exam-aware structured output. "
            "Identify question boundaries using patterns like Q1, Q2, Question 1, 1., 2), (a), (b). "
            "Identify marks using patterns like [3 marks], (5 marks), /5, out of 5, 5 pts. "
            "For each question extract: label, max_marks, question_text, answer_key (final answer token), "
            "model_solution, and criteria[] with desc + marks. "
            "If marks are not explicit, make a best guess for max_marks and include uncertainty notes inside "
            "question_text or model_solution while still conforming to the schema. "
            "IMPORTANT: If any problem text exists but reliable question splitting is not possible, return exactly "
            "one fallback question with label='Q1', max_marks=0, criteria=[{\"desc\":\"Needs teacher review\",\"marks\":0}]. "
            "Return ONLY JSON matching the provided schema."
        )
        schema = build_answer_key_response_schema()

        normalized = [normalize_key_page_image(path) for path in image_paths]
        for path, norm in zip(image_paths, normalized, strict=True):
            logger.info(
                "key/parse normalized image",
                extra={
                    "request_id": request_id,
                    "stage": "prepare_openai_request",
                    "image": str(path),
                    "original_size_bytes": norm.original_size_bytes,
                    "final_size_bytes": norm.final_size_bytes,
                    "width": norm.width,
                    "height": norm.height,
                    "model": model,
                },
            )

        indexed_paths = list(range(len(image_paths)))
        chunks = [indexed_paths[i : i + self._max_images_per_request] for i in range(0, len(indexed_paths), self._max_images_per_request)]

        payloads: list[dict[str, object]] = []
        for chunk in chunks:
            sub_batches: list[list[int]] = []
            current: list[int] = []
            current_bytes = 0
            for idx in chunk:
                image_size = normalized[idx].final_size_bytes
                if current and current_bytes + image_size > self._payload_limit_bytes:
                    sub_batches.append(current)
                    current = [idx]
                    current_bytes = image_size
                else:
                    current.append(idx)
                    current_bytes += image_size
            if current:
                sub_batches.append(current)

            for sub_batch in sub_batches:
                images = [normalized[idx].image_bytes for idx in sub_batch]
                mime_types = [normalized[idx].mime_type for idx in sub_batch]
                request_payload = build_key_parse_request(
                    model=model,
                    prompt=prompt,
                    images=images,
                    mime_types=mime_types,
                    schema=schema,
                )
                try:
                    response = self._client.responses.create(**request_payload)
                except Exception as exc:  # pragma: no cover
                    status_code = getattr(exc, "status_code", None)
                    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                        status_code = 504
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
    def parse(self, image_paths: list[Path], model: str, request_id: str) -> ParseResult:
        _ = (image_paths, request_id)
        if model == "gpt-5-nano":
            return ParseResult(payload={"confidence_score": 0.4, "questions": []}, model=model)

        return ParseResult(
            payload={
                "confidence_score": 0.8,
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
