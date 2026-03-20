"""OpenAI Vision answer-key parsing client."""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.pipeline.key_pages import NormalizedImage, normalize_key_page_image

logger = logging.getLogger(__name__)


def _coerce_text_segments(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if content is None:
        return []
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            parts.extend(_coerce_text_segments(item))
        return parts
    if isinstance(content, dict):
        for key in ("text", "value", "content"):
            if key in content:
                return _coerce_text_segments(content[key])
        return []
    for attr in ("text", "value", "content"):
        if hasattr(content, attr):
            return _coerce_text_segments(getattr(content, attr))
    return []


def _normalize_model_response_text(content: Any) -> str:
    return "\n".join(segment.strip() for segment in _coerce_text_segments(content) if isinstance(segment, str) and segment.strip()).strip()


def _safe_preview(value: Any, max_chars: int = 1200) -> str:
    try:
        if hasattr(value, "model_dump"):
            rendered = json.dumps(value.model_dump(), ensure_ascii=True)
        elif isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=True)
        else:
            rendered = repr(value)
    except Exception:
        rendered = repr(value)
    if len(rendered) > max_chars:
        return f"{rendered[:max_chars]}...<truncated>"
    return rendered


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, count=1)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _extract_balanced_fragment(text: str, start_idx: int) -> str | None:
    if start_idx >= len(text):
        return None
    opening = text[start_idx]
    closing = "}" if opening == "{" else "]" if opening == "[" else ""
    if not closing:
        return None

    depth = 0
    in_string = False
    escaped = False
    for idx in range(start_idx, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start_idx : idx + 1]
    return None


def _first_json_object_fragment(text: str) -> str | None:
    brace_idx = text.find("{")
    if brace_idx == -1:
        return None
    return _extract_balanced_fragment(text, brace_idx) or text[brace_idx:].strip()


def _extract_key_value_fragment(text: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*', text)
    if not match:
        return None

    idx = match.end()
    while idx < len(text) and text[idx].isspace():
        idx += 1
    if idx >= len(text):
        return None

    if text.startswith("null", idx):
        return "null"
    if text[idx] in "{[":
        return _extract_balanced_fragment(text, idx)
    if text[idx] == '"':
        end_idx = idx + 1
        escaped = False
        while end_idx < len(text):
            ch = text[end_idx]
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                return text[idx : end_idx + 1]
            end_idx += 1
        return None

    end_idx = idx
    while end_idx < len(text) and text[end_idx] not in ",}]":
        end_idx += 1
    return text[idx:end_idx].strip() or None


def _parse_json_fragment(fragment: str | None) -> Any:
    if not fragment:
        return None
    return json.loads(fragment)


def _load_json_with_fallbacks(text: str) -> dict[str, object]:
    cleaned = _strip_code_fences(text)
    candidates: list[str] = []
    if cleaned:
        candidates.append(cleaned)
    object_fragment = _first_json_object_fragment(cleaned)
    if object_fragment and object_fragment not in candidates:
        candidates.append(object_fragment)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
            if repaired != candidate:
                try:
                    parsed = json.loads(repaired)
                except json.JSONDecodeError:
                    continue
            continue
        if isinstance(parsed, dict):
            return parsed
    raise json.JSONDecodeError("Could not parse model response as JSON object", cleaned, 0)


def _recover_front_page_payload(text: str) -> dict[str, object] | None:
    recovered: dict[str, object] = {
        "student_name": None,
        "overall_marks_awarded": None,
        "overall_max_marks": None,
        "objective_scores": [],
        "warnings": [],
    }
    recovered_any = False
    for key in ("student_name", "overall_marks_awarded", "overall_max_marks"):
        fragment = _extract_key_value_fragment(text, key)
        if fragment is None:
            continue
        try:
            recovered[key] = _parse_json_fragment(fragment)
            recovered_any = True
        except json.JSONDecodeError:
            logger.warning("front-page extractor could not recover field '%s' from malformed JSON", key)

    objective_scores_fragment = _extract_key_value_fragment(text, "objective_scores")
    if objective_scores_fragment is not None:
        try:
            parsed_scores = _parse_json_fragment(objective_scores_fragment)
            if isinstance(parsed_scores, list):
                recovered["objective_scores"] = parsed_scores
                recovered_any = True
        except json.JSONDecodeError:
            logger.warning("front-page extractor dropped malformed objective_scores payload")

    warnings_fragment = _extract_key_value_fragment(text, "warnings")
    if warnings_fragment is not None:
        try:
            parsed_warnings = _parse_json_fragment(warnings_fragment)
            if isinstance(parsed_warnings, list):
                recovered["warnings"] = [str(item) for item in parsed_warnings]
        except json.JSONDecodeError:
            logger.warning("front-page extractor dropped malformed warnings payload")

    if not recovered_any:
        return None

    warnings = recovered["warnings"]
    if isinstance(warnings, list):
        warnings.append("Recovered partial front-page candidates from malformed model output.")
    else:
        recovered["warnings"] = ["Recovered partial front-page candidates from malformed model output."]
    return recovered


def _provider_api_key() -> str:
    return (
        os.getenv("SUPERMARKS_LLM_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )


def _provider_base_url() -> str | None:
    value = (
        os.getenv("SUPERMARKS_LLM_BASE_URL", "").strip()
        or os.getenv("OPENAI_BASE_URL", "").strip()
    )
    return value or None


def _provider_name() -> str:
    return os.getenv("SUPERMARKS_LLM_PROVIDER", "openai_compatible").strip() or "openai_compatible"


def _front_page_provider_api_key() -> str:
    explicit = os.getenv("SUPERMARKS_FRONT_PAGE_API_KEY", "").strip()
    if explicit:
        return explicit
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if _front_page_provider_name() != "doubleword":
        return openai_key
    return openai_key or os.getenv("SUPERMARKS_LLM_API_KEY", "").strip()


def _front_page_provider_base_url() -> str | None:
    value = (
        os.getenv("SUPERMARKS_FRONT_PAGE_BASE_URL", "").strip()
        or os.getenv("OPENAI_BASE_URL", "").strip()
    )
    if value:
        return value
    if _front_page_provider_name() == "doubleword":
        fallback = os.getenv("SUPERMARKS_LLM_BASE_URL", "").strip()
        return fallback or None
    return None


def _front_page_provider_name() -> str:
    configured = os.getenv("SUPERMARKS_FRONT_PAGE_PROVIDER", "").strip()
    if configured:
        return configured
    return _provider_name()


def _front_page_model() -> str:
    configured = os.getenv("SUPERMARKS_FRONT_PAGE_MODEL", "").strip()
    if configured:
        return configured
    if _front_page_provider_name() == "doubleword":
        return (
            os.getenv("SUPERMARKS_KEY_PARSE_NANO_MODEL", "").strip()
            or os.getenv("SUPERMARKS_KEY_PARSE_MINI_MODEL", "").strip()
            or "gpt-5-mini"
        )
    return "gpt-5-nano"


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
                        "objective_codes": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "warnings": {"type": "array", "items": {"type": "string"}},
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


def build_key_parse_chat_request(
    model: str,
    prompt: str,
    images: list[bytes],
    mime_types: list[str],
    schema: dict[str, object],
) -> dict[str, object]:
    content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
    for image_bytes, mime_type in zip(images, mime_types, strict=True):
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        normalized_mime = mime_type.lower().strip()
        if normalized_mime not in {"image/png", "image/jpeg"}:
            normalized_mime = "image/jpeg"
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{normalized_mime};base64,{encoded}"},
            }
        )

    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "answer_key_parse",
                "strict": True,
                "schema": schema,
            },
        },
    }


def build_front_page_extract_request(
    model: str,
    prompt: str,
    image: bytes,
    mime_type: str,
    schema: dict[str, object],
) -> dict[str, object]:
    encoded = base64.b64encode(image).decode("utf-8")
    normalized_mime = mime_type.lower().strip()
    if normalized_mime not in {"image/png", "image/jpeg"}:
        normalized_mime = "image/jpeg"
    return {
        "model": model,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:{normalized_mime};base64,{encoded}"},
            ],
        }],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "front_page_totals_extract",
                "strict": True,
                "schema": schema,
            }
        },
    }


def build_front_page_extract_chat_request(
    model: str,
    prompt: str,
    image: bytes,
    mime_type: str,
    schema: dict[str, object],
) -> dict[str, object]:
    encoded = base64.b64encode(image).decode("utf-8")
    normalized_mime = mime_type.lower().strip()
    if normalized_mime not in {"image/png", "image/jpeg"}:
        normalized_mime = "image/jpeg"
    return {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{normalized_mime};base64,{encoded}"}},
            ],
        }],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "front_page_totals_extract",
                "strict": True,
                "schema": schema,
            },
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
        api_key = _provider_api_key()
        if not api_key:
            raise RuntimeError("SUPERMARKS_LLM_API_KEY / OPENAI_API_KEY is not set")

        from openai import OpenAI

        client_kwargs: dict[str, object] = {"api_key": api_key, "timeout": timeout_seconds}
        base_url = _provider_base_url()
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)
        self._max_images_per_request = max_images_per_request
        self._payload_limit_bytes = payload_limit_bytes
        self._retry_backoffs_seconds = retry_backoffs_seconds
        self._mini_retry_backoffs_seconds = mini_retry_backoffs_seconds

    def _build_prompt_for_batch(self, batch_number: int, total_batches: int) -> str:
        return (
            "You are parsing an exam answer key into lightweight scoring structure for a teacher-first marking workflow. "
            "This request contains only a subset of pages. Extract ONLY questions that appear on the provided pages for this batch. "
            f"Batch {batch_number} of {total_batches}. "
            "Prioritize speed and structural accuracy over deep interpretation. "
            "Identify question boundaries using patterns like Q1, Q2, Question 1, 1., 2), (a), (b). "
            "Identify marks using patterns like [3 marks], (5 marks), /5, out of 5, 5 pts. "
            "If the page shows objective or outcome tags such as OB1, OB2, Outcome 1, LO3, include them in objective_codes. "
            "For each question extract only: label, max_marks, marks_source, marks_confidence, marks_reason, question_text, answer_key, objective_codes, warnings[], and evidence[] boxes using normalized coordinates 0..1 plus page_number and kind. "
            "Do not invent model solutions or rich rubrics. Keep answer_key brief and grounded in visible content. "
            "If marks are not explicit, make a best guess for max_marks and explain briefly in marks_reason. "
            "IMPORTANT: If any problem text exists but reliable question splitting is not possible, return exactly one fallback question with label='Q1', max_marks=0, objective_codes=[], warnings=['Needs teacher review']. "
            "Return ONLY JSON matching the provided schema."
        )

    def _call_openai_with_retry(self, request_payload: dict[str, object], model: str, request_id: str, batch_number: int) -> dict[str, object]:
        last_exc: OpenAIRequestError | None = None
        backoffs = self._mini_retry_backoffs_seconds if model.endswith("mini") else self._retry_backoffs_seconds
        attempts = len(backoffs) + 1
        for attempt in range(attempts):
            try:
                if _provider_name() == "doubleword":
                    response = self._client.chat.completions.create(**request_payload)
                    content = _normalize_model_response_text(response.choices[0].message.content)
                    return _load_json_with_fallbacks(content)
                response = self._client.responses.create(**request_payload)
                return _load_json_with_fallbacks(_normalize_model_response_text(response.output_text))
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
                request_builder = build_key_parse_chat_request if _provider_name() == "doubleword" else build_key_parse_request
                request_payload = request_builder(
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
                        "objective_codes": ["OB1"],
                        "warnings": [],
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
                        "objective_codes": [],
                        "warnings": ["marks inferred"],
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


@dataclass
class BulkNameDetectionResult:
    page_number: int
    student_name: str | None
    confidence: float
    evidence: dict[str, float] | None


@dataclass
class FrontPageTotalsExtractResult:
    payload: dict[str, object]
    model: str


class FrontPageTotalsExtractor(Protocol):
    def extract(self, image_path: Path, request_id: str) -> FrontPageTotalsExtractResult:
        """Extract front-page totals candidates from a single rendered page image."""


class BulkNameDetector(Protocol):
    def detect(self, image_path: Path, page_number: int, model: str, request_id: str) -> BulkNameDetectionResult:
        """Detect student name from a single rendered page image."""


class OpenAIBulkNameDetector:
    def __init__(self, timeout_seconds: float = 60.0) -> None:
        api_key = _front_page_provider_api_key()
        if not api_key:
            raise RuntimeError("SUPERMARKS_FRONT_PAGE_API_KEY / OPENAI_API_KEY is not set")
        from openai import OpenAI
        client_kwargs: dict[str, object] = {"api_key": api_key, "timeout": timeout_seconds}
        base_url = _front_page_provider_base_url()
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)

    def _build_prompt(self) -> str:
        return (
            "Extract the student name from this exam page. Look for fields like 'Name: ____' and typical top header boxes. "
            "Return strict JSON only with keys: page_number (int), student_name (string or null), confidence (0..1), "
            "evidence ({x,y,w,h} normalized 0..1 or null)."
        )

    def detect(self, image_path: Path, page_number: int, model: str, request_id: str) -> BulkNameDetectionResult:
        raw = image_path.read_bytes()
        encoded = base64.b64encode(raw).decode("utf-8")
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["page_number", "student_name", "confidence", "evidence"],
            "properties": {
                "page_number": {"type": "integer"},
                "student_name": {"type": ["string", "null"]},
                "confidence": {"type": "number"},
                "evidence": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["x", "y", "w", "h"],
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "w": {"type": "number"},
                        "h": {"type": "number"},
                    },
                },
            },
        }
        request_builder = build_front_page_extract_chat_request if _front_page_provider_name() == "doubleword" else build_front_page_extract_request
        payload = request_builder(
            model=model,
            prompt=self._build_prompt(),
            image=raw,
            mime_type="image/jpeg",
            schema=schema,
        )
        try:
            if _front_page_provider_name() == "doubleword":
                response = self._client.chat.completions.create(**payload)
                content = _normalize_model_response_text(response.choices[0].message.content)
            else:
                response = self._client.responses.create(**payload)
                content = _normalize_model_response_text(response.output_text)
            parsed = _load_json_with_fallbacks(content)
            return BulkNameDetectionResult(
                page_number=page_number,
                student_name=parsed.get("student_name"),
                confidence=float(parsed.get("confidence") or 0.0),
                evidence=parsed.get("evidence"),
            )
        except Exception as exc:  # pragma: no cover
            status_code = getattr(exc, "status_code", None)
            if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                status_code = 504
            raise OpenAIRequestError(status_code=status_code, body=str(exc), message=f"OpenAI request failed: {exc}") from exc


class MockBulkNameDetector:
    def detect(self, image_path: Path, page_number: int, model: str, request_id: str) -> BulkNameDetectionResult:
        _ = (image_path, model, request_id)
        if page_number <= 2:
            return BulkNameDetectionResult(page_number=page_number, student_name="Alice Johnson", confidence=0.92, evidence={"x": 0.1, "y": 0.05, "w": 0.3, "h": 0.08})
        return BulkNameDetectionResult(page_number=page_number, student_name="Bob Smith", confidence=0.89, evidence={"x": 0.12, "y": 0.05, "w": 0.32, "h": 0.08})


def _front_page_candidate_value_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "value_text": {"type": "string"},
            "confidence": {"type": "number"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "page_number": {"type": "integer"},
                        "quote": {"type": "string"},
                        "x": {"type": ["number", "null"]},
                        "y": {"type": ["number", "null"]},
                        "w": {"type": ["number", "null"]},
                        "h": {"type": ["number", "null"]},
                    },
                },
            },
        },
    }


def build_front_page_totals_response_schema() -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "student_name": {"anyOf": [_front_page_candidate_value_schema(), {"type": "null"}]},
            "overall_marks_awarded": {"anyOf": [_front_page_candidate_value_schema(), {"type": "null"}]},
            "overall_max_marks": {"anyOf": [_front_page_candidate_value_schema(), {"type": "null"}]},
            "objective_scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "objective_code": _front_page_candidate_value_schema(),
                        "marks_awarded": _front_page_candidate_value_schema(),
                        "max_marks": {"anyOf": [_front_page_candidate_value_schema(), {"type": "null"}]},
                    },
                },
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    }
    _ensure_strict_schema_node(schema)
    validate_schema_strictness(schema)
    return schema


class OpenAIFrontPageTotalsExtractor:
    def __init__(self, timeout_seconds: float = 60.0) -> None:
        api_key = _front_page_provider_api_key()
        if not api_key:
            raise RuntimeError("SUPERMARKS_FRONT_PAGE_API_KEY / OPENAI_API_KEY is not set")
        from openai import OpenAI
        client_kwargs: dict[str, object] = {"api_key": api_key, "timeout": timeout_seconds}
        base_url = _front_page_provider_base_url()
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)

    def extract(self, image_path: Path, request_id: str) -> FrontPageTotalsExtractResult:
        normalized = normalize_key_page_image(image_path)
        schema = build_front_page_totals_response_schema()
        model = _front_page_model()
        prompt = (
            "Extract only front-page score summary candidates from this exam paper page for a teacher confirmation workflow. "
            "This is an internal teacher-owned workflow, not a public disclosure. If a student name is visible, return it exactly; do not redact, mask, or suppress it for privacy. "
            "Target only: student name, overall awarded total, overall possible total, and objective/category totals when visibly summarized on the page. "
            "Do not infer question-level marks. Do not hallucinate hidden totals. If something is not visible, return null or an empty list. "
            "For each returned value provide the exact visible text in value_text, a 0..1 confidence, and brief evidence quotes plus normalized box coordinates when possible. "
            "Objective/category totals should only include rows actually visible on the front page summary. Return strict JSON only."
        )
        request_builder = (
            build_front_page_extract_chat_request
            if _front_page_provider_name() == "doubleword"
            else build_front_page_extract_request
        )
        payload = request_builder(
            model=model,
            prompt=prompt,
            image=normalized.image_bytes,
            mime_type=normalized.mime_type,
            schema=schema,
        )
        try:
            if _front_page_provider_name() == "doubleword":
                response = self._client.chat.completions.create(**payload)
                message = response.choices[0].message
                content = _normalize_model_response_text(message.content)
                if not content:
                    logger.warning(
                        "front-page extractor received empty normalized chat content; raw message preview=%s",
                        _safe_preview(message),
                    )
            else:
                response = self._client.responses.create(**payload)
                content = _normalize_model_response_text(response.output_text)
            try:
                parsed = _load_json_with_fallbacks(content)
            except json.JSONDecodeError:
                logger.warning(
                    "front-page extractor received non-JSON model output preview=%s",
                    _safe_preview(content),
                )
                recovered = _recover_front_page_payload(content)
                if recovered is None:
                    raise
                parsed = recovered
            return FrontPageTotalsExtractResult(payload=parsed, model=model)
        except Exception as exc:  # pragma: no cover
            status_code = getattr(exc, "status_code", None)
            if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                status_code = 504
            raise OpenAIRequestError(status_code=status_code, body=str(exc), message=f"OpenAI request failed: {exc}") from exc


class MockFrontPageTotalsExtractor:
    def extract(self, image_path: Path, request_id: str) -> FrontPageTotalsExtractResult:
        _ = (image_path, request_id)
        return FrontPageTotalsExtractResult(
            payload={
                "student_name": {
                    "value_text": "Jordan Lee",
                    "confidence": 0.93,
                    "evidence": [{"page_number": 1, "quote": "Name: Jordan Lee", "x": 0.08, "y": 0.05, "w": 0.28, "h": 0.06}],
                },
                "overall_marks_awarded": {
                    "value_text": "42",
                    "confidence": 0.95,
                    "evidence": [{"page_number": 1, "quote": "Total: 42/50", "x": 0.68, "y": 0.1, "w": 0.18, "h": 0.06}],
                },
                "overall_max_marks": {
                    "value_text": "50",
                    "confidence": 0.95,
                    "evidence": [{"page_number": 1, "quote": "Total: 42/50", "x": 0.68, "y": 0.1, "w": 0.18, "h": 0.06}],
                },
                "objective_scores": [
                    {
                        "objective_code": {"value_text": "OB1", "confidence": 0.88, "evidence": [{"page_number": 1, "quote": "OB1 18/20", "x": 0.62, "y": 0.2, "w": 0.2, "h": 0.05}]},
                        "marks_awarded": {"value_text": "18", "confidence": 0.88, "evidence": [{"page_number": 1, "quote": "OB1 18/20", "x": 0.62, "y": 0.2, "w": 0.2, "h": 0.05}]},
                        "max_marks": {"value_text": "20", "confidence": 0.88, "evidence": [{"page_number": 1, "quote": "OB1 18/20", "x": 0.62, "y": 0.2, "w": 0.2, "h": 0.05}]},
                    }
                ],
                "warnings": [],
            },
            model="mock-front-page-totals",
        )


def get_front_page_totals_extractor() -> FrontPageTotalsExtractor:
    if os.getenv("OPENAI_MOCK", "").strip() == "1":
        return MockFrontPageTotalsExtractor()
    return OpenAIFrontPageTotalsExtractor()


def get_bulk_name_detector() -> BulkNameDetector:
    if os.getenv("OPENAI_MOCK", "").strip() == "1":
        return MockBulkNameDetector()
    return OpenAIBulkNameDetector()
