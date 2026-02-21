"""OpenAI Vision answer-key parsing client."""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.pipeline.key_pages import NormalizedImage, normalize_key_page_image

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
            "warnings": {"type": "array", "items": {"type": "string"}},
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "max_marks": {"type": "number"},
                        "marks_source": {"type": "string", "enum": ["explicit", "inferred", "unknown"]},
                        "marks_confidence": {"type": "number"},
                        "marks_reason": {"type": "string"},
                        "question_text": {"type": "string"},
                        "answer_key": {"type": "string"},
                        "model_solution": {"type": "string"},
                        "warnings": {"type": "array", "items": {"type": "string"}},
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
                        "evidence": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "page_number": {"type": "integer"},
                                    "x": {"type": "number"},
                                    "y": {"type": "number"},
                                    "w": {"type": "number"},
                                    "h": {"type": "number"},
                                    "kind": {"type": "string", "enum": ["question_box", "answer_box", "marks_box"]},
                                    "confidence": {"type": "number"},
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
    def __init__(
        self,
        timeout_seconds: float = 60.0,
        max_images_per_request: int = 1,
        payload_limit_bytes: int = 2_500_000,
        retry_backoffs_seconds: tuple[float, ...] = (1.0, 2.0),
        mini_retry_backoffs_seconds: tuple[float, ...] = (),
    ) -> None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, timeout=timeout_seconds)
        self._max_images_per_request = max_images_per_request
        self._payload_limit_bytes = payload_limit_bytes
        self._retry_backoffs_seconds = retry_backoffs_seconds
        self._mini_retry_backoffs_seconds = mini_retry_backoffs_seconds

    def _build_prompt_for_batch(self, batch_number: int, total_batches: int) -> str:
        return (
            "You are parsing an exam answer key and must produce exam-aware structured output. "
            "This request contains only a subset of pages. Extract ONLY questions that appear on the provided pages for this batch. "
            f"Batch {batch_number} of {total_batches}. "
            "Identify question boundaries using patterns like Q1, Q2, Question 1, 1., 2), (a), (b). "
            "Identify marks using patterns like [3 marks], (5 marks), /5, out of 5, 5 pts. "
            "For each question extract: label, max_marks, marks_source, marks_confidence, marks_reason, question_text, answer_key, model_solution, criteria[] with desc + marks, warnings[], and evidence[] boxes using normalized coordinates 0..1 plus page_number and kind. "
            "If marks are not explicit, make a best guess for max_marks and include uncertainty notes inside question_text or model_solution while still conforming to the schema. "
            "IMPORTANT: If any problem text exists but reliable question splitting is not possible, return exactly one fallback question with label='Q1', max_marks=0, criteria=[{\"desc\":\"Needs teacher review\",\"marks\":0}]. "
            "Return ONLY JSON matching the provided schema."
        )

    def _call_openai_with_retry(self, request_payload: dict[str, object], model: str, request_id: str, batch_number: int) -> dict[str, object]:
        last_exc: OpenAIRequestError | None = None
        backoffs = self._mini_retry_backoffs_seconds if model.endswith("mini") else self._retry_backoffs_seconds
        attempts = len(backoffs) + 1
        for attempt in range(attempts):
            try:
                response = self._client.responses.create(**request_payload)
                return json.loads(response.output_text)
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

                retryable = isinstance(exc, (httpx.TimeoutException, TimeoutError)) or status_code in {429, 503, 504}
                last_exc = OpenAIRequestError(status_code=status_code, body=body_text, message=f"OpenAI request failed: {exc}")

                if retryable and attempt < attempts - 1:
                    logger.warning(
                        "key/parse openai retry",
                        extra={
                            "request_id": request_id,
                            "stage": "openai_retry",
                            "model": model,
                            "batch_number": batch_number,
                            "attempt": attempt + 1,
                            "status_code": status_code,
                        },
                    )
                    time.sleep(backoffs[attempt])
                    continue
                raise last_exc from exc

        if last_exc:
            raise last_exc
        raise OpenAIRequestError(status_code=None, body="Unknown OpenAI error", message="OpenAI request failed")

    def _parse_model_batches(self, image_paths: list[Path], normalized: list[NormalizedImage], model: str, request_id: str, schema: dict[str, Any]) -> ParseResult:
        indexed_paths = list(range(len(image_paths)))
        chunks = [indexed_paths[i : i + self._max_images_per_request] for i in range(0, len(indexed_paths), self._max_images_per_request)]

        payloads: list[dict[str, object]] = []
        for batch_number, chunk in enumerate(chunks, start=1):
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
                payload_size_bytes = sum(len(item) for item in images)
                request_payload = build_key_parse_request(
                    model=model,
                    prompt=self._build_prompt_for_batch(batch_number=batch_number, total_batches=len(chunks)),
                    images=images,
                    mime_types=mime_types,
                    schema=schema,
                )
                started = time.perf_counter()
                response_payload = self._call_openai_with_retry(request_payload, model=model, request_id=request_id, batch_number=batch_number)
                logger.info(
                    "key/parse openai page timing",
                    extra={
                        "request_id": request_id,
                        "stage": "call_openai_page",
                        "model": model,
                        "batch_number": batch_number,
                        "payload_size_bytes": payload_size_bytes,
                        "openai_ms": int((time.perf_counter() - started) * 1000),
                    },
                )
                payloads.append(response_payload)

        if len(payloads) == 1:
            return ParseResult(payload=payloads[0], model=model)

        merged_questions: list[object] = []
        seen_labels: set[str] = set()
        confidence_scores: list[float] = []
        merged_warnings: list[str] = []
        for payload in payloads:
            questions = payload.get("questions", [])
            if isinstance(questions, list):
                for question in questions:
                    if isinstance(question, dict):
                        label = str(question.get("label") or "").strip()
                        if label and label in seen_labels:
                            continue
                        if label:
                            seen_labels.add(label)
                    merged_questions.append(question)
            confidence = payload.get("confidence_score")
            if isinstance(confidence, (int, float)):
                confidence_scores.append(float(confidence))
            warnings = payload.get("warnings")
            if isinstance(warnings, list):
                merged_warnings.extend([str(item) for item in warnings])
        merged_confidence = min(confidence_scores) if confidence_scores else 0.0
        return ParseResult(payload={"confidence_score": merged_confidence, "questions": merged_questions, "warnings": merged_warnings}, model=model)

    def parse(self, image_paths: list[Path], model: str, request_id: str) -> ParseResult:
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

        primary_result = self._parse_model_batches(image_paths, normalized, model=model, request_id=request_id, schema=schema)
        questions = primary_result.payload.get("questions")
        if isinstance(questions, list) and questions:
            return primary_result

        if model.endswith("nano"):
            fallback_model = model.replace("nano", "mini")
            logger.info(
                "key/parse nano batches returned empty questions -> retrying with mini",
                extra={"request_id": request_id, "stage": "call_openai_mini_fallback", "model": fallback_model},
            )
            return self._parse_model_batches(image_paths, normalized, model=fallback_model, request_id=request_id, schema=schema)

        return primary_result


class MockAnswerKeyParser:
    def parse(self, image_paths: list[Path], model: str, request_id: str) -> ParseResult:
        _ = (image_paths, request_id)
        if model == "gpt-5-nano":
            return ParseResult(payload={"confidence_score": 0.4, "questions": []}, model=model)

        return ParseResult(
            payload={
                "confidence_score": 0.8,
                "warnings": [],
                "questions": [
                    {
                        "label": "Q1",
                        "max_marks": 5,
                        "marks_source": "explicit",
                        "marks_confidence": 0.9,
                        "marks_reason": "Found (5 marks) near question heading",
                        "question_text": "Solve for x",
                        "answer_key": "x=4",
                        "model_solution": "x+3=7 so x=4",
                        "warnings": [],
                        "criteria": [
                            {"desc": "Correct algebra", "marks": 3},
                            {"desc": "Correct final answer", "marks": 2}
                        ],
                        "evidence": [{"page_number": 1, "x": 0.1, "y": 0.2, "w": 0.7, "h": 0.15, "kind": "question_box", "confidence": 0.88}]
                    },
                    {
                        "label": "Q2",
                        "max_marks": 3,
                        "marks_source": "inferred",
                        "marks_confidence": 0.66,
                        "marks_reason": "Summed subparts",
                        "question_text": "Find y",
                        "answer_key": "y=7",
                        "model_solution": "Substitute and solve",
                        "warnings": ["marks inferred"],
                        "criteria": [
                            {"desc": "Method", "marks": 1},
                            {"desc": "Correct final answer", "marks": 2}
                        ],
                        "evidence": [{"page_number": 1, "x": 0.1, "y": 0.4, "w": 0.7, "h": 0.15, "kind": "question_box", "confidence": 0.75}]
                    }
                ],
            },
            model=model,
        )


def get_answer_key_parser() -> AnswerKeyParser:
    if os.getenv("OPENAI_MOCK", "").strip() == "1":
        return MockAnswerKeyParser()
    return OpenAIAnswerKeyParser()
