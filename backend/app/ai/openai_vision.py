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
    if _front_page_provider_name() == "gemini":
        return (
            os.getenv("GEMINI_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip()
        )
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
    if _front_page_provider_name() == "gemini":
        return "https://generativelanguage.googleapis.com"
    if _front_page_provider_name() == "doubleword":
        fallback = os.getenv("SUPERMARKS_LLM_BASE_URL", "").strip()
        return fallback or None
    return None


def _front_page_provider_name() -> str:
    configured = os.getenv("SUPERMARKS_FRONT_PAGE_PROVIDER", "").strip()
    if configured:
        return configured
    if os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip():
        return "gemini"
    return _provider_name()


def _front_page_model() -> str:
    configured = os.getenv("SUPERMARKS_FRONT_PAGE_MODEL", "").strip()
    if configured:
        return configured
    if _front_page_provider_name() == "gemini":
        return "gemini-2.5-flash"
    if _front_page_provider_name() == "doubleword":
        return (
            os.getenv("SUPERMARKS_KEY_PARSE_NANO_MODEL", "").strip()
            or os.getenv("SUPERMARKS_KEY_PARSE_MINI_MODEL", "").strip()
            or "gpt-5-mini"
        )
    return "gpt-5-nano"


def _front_page_mini_model() -> str:
    configured = os.getenv("SUPERMARKS_FRONT_PAGE_MODEL", "").strip()
    if configured:
        return configured
    if _front_page_provider_name() == "gemini":
        return _front_page_model()
    return (
        os.getenv("SUPERMARKS_KEY_PARSE_MINI_MODEL", "").strip()
        or "gpt-5-mini"
    )


def _front_page_nano_model() -> str:
    configured = os.getenv("SUPERMARKS_FRONT_PAGE_MODEL", "").strip()
    if configured:
        return configured
    if _front_page_provider_name() == "gemini":
        return _front_page_model()
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
    exam_name: str | None
    confidence: float
    evidence: dict[str, float] | None


@dataclass
class FrontPageTotalsExtractResult:
    payload: dict[str, object]
    model: str


class FrontPageTotalsExtractor(Protocol):
    def extract(
        self,
        image_path: Path,
        request_id: str,
        *,
        model_override: str | None = None,
        template: dict[str, object] | None = None,
    ) -> FrontPageTotalsExtractResult:
        """Extract front-page totals candidates from a single rendered page image."""


class BulkNameDetector(Protocol):
    def detect(self, image_path: Path, page_number: int, model: str, request_id: str) -> BulkNameDetectionResult:
        """Detect student name from a single rendered page image."""


class GeminiStructuredVisionClient:
    def __init__(self, timeout_seconds: float = 60.0) -> None:
        api_key = _front_page_provider_api_key()
        if not api_key:
            raise RuntimeError("SUPERMARKS_FRONT_PAGE_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY is not set")
        self._api_key = api_key
        self._base_url = (_front_page_provider_base_url() or "https://generativelanguage.googleapis.com").rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds)

    def generate_json(
        self,
        *,
        model: str,
        prompt: str,
        image: bytes,
        mime_type: str,
        response_json_schema: dict[str, object] | None = None,
    ) -> dict[str, object]:
        encoded = base64.b64encode(image).decode("utf-8")
        normalized_mime = mime_type.lower().strip()
        if normalized_mime not in {"image/png", "image/jpeg"}:
            normalized_mime = "image/jpeg"
        response = self._client.post(
            f"{self._base_url}/v1beta/models/{model}:generateContent",
            params={"key": self._api_key},
            json={
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": normalized_mime, "data": encoded}},
                    ],
                }],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    **({"responseJsonSchema": response_json_schema} if response_json_schema else {}),
                },
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenAIRequestError(
                status_code=response.status_code,
                body=response.text,
                message=f"Gemini request failed: {exc}",
            ) from exc
        payload = response.json()
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise OpenAIRequestError(
                status_code=response.status_code,
                body=response.text,
                message="Gemini request failed: empty candidate response",
            )
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        text = ""
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text += part["text"]
        if not text.strip():
            raise OpenAIRequestError(
                status_code=response.status_code,
                body=response.text,
                message="Gemini request failed: empty text response",
            )
        return _load_json_with_fallbacks(_normalize_model_response_text(text))


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
            "Extract two things from this exam page: "
            "1. the student name from fields like 'Name: ____' and typical top header boxes, and "
            "2. the test or exam title shown in the page header, such as the course exam name, assignment title, or assessment title. "
            "Do not return the student's name as the exam title. "
            "Return strict JSON only with keys: page_number (int), student_name (string or null), exam_name (string or null), confidence (0..1), "
            "evidence ({x,y,w,h} normalized 0..1 or null)."
        )

    def detect(self, image_path: Path, page_number: int, model: str, request_id: str) -> BulkNameDetectionResult:
        raw = image_path.read_bytes()
        encoded = base64.b64encode(raw).decode("utf-8")
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["page_number", "student_name", "exam_name", "confidence", "evidence"],
            "properties": {
                "page_number": {"type": "integer"},
                "student_name": {"type": ["string", "null"]},
                "exam_name": {"type": ["string", "null"]},
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
                exam_name=parsed.get("exam_name"),
                confidence=float(parsed.get("confidence") or 0.0),
                evidence=parsed.get("evidence"),
            )
        except Exception as exc:  # pragma: no cover
            status_code = getattr(exc, "status_code", None)
            if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                status_code = 504
            raise OpenAIRequestError(status_code=status_code, body=str(exc), message=f"OpenAI request failed: {exc}") from exc


class GeminiBulkNameDetector:
    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self._client = GeminiStructuredVisionClient(timeout_seconds=timeout_seconds)

    def _build_prompt(self) -> str:
        return (
            "Extract two things from this exam page and return strict JSON only. "
            "1. student_name: the student name from top header fields like Name or Student. "
            "2. exam_name: the test title or course assessment title shown in the page header. "
            "Do not return the student name as the exam title. "
            "Also return confidence (0..1) for the student name read and evidence as normalized x,y,w,h coordinates for the student name field when visible."
        )

    def detect(self, image_path: Path, page_number: int, model: str, request_id: str) -> BulkNameDetectionResult:
        _ = request_id
        normalized = normalize_key_page_image(image_path)
        parsed = self._client.generate_json(
            model=model,
            prompt=self._build_prompt(),
            image=normalized.image_bytes,
            mime_type=normalized.mime_type,
            response_json_schema=build_bulk_name_response_json_schema(),
        )
        evidence = parsed.get("evidence")
        return BulkNameDetectionResult(
            page_number=page_number,
            student_name=str(parsed.get("student_name") or "").strip() or None,
            exam_name=str(parsed.get("exam_name") or "").strip() or None,
            confidence=float(parsed.get("confidence") or 0.0),
            evidence=evidence if isinstance(evidence, dict) else None,
        )


class MockBulkNameDetector:
    def detect(self, image_path: Path, page_number: int, model: str, request_id: str) -> BulkNameDetectionResult:
        _ = (image_path, model, request_id)
        if page_number <= 2:
            return BulkNameDetectionResult(page_number=page_number, student_name="Alice Johnson", exam_name="Math 20-1 Unit Test", confidence=0.92, evidence={"x": 0.1, "y": 0.05, "w": 0.3, "h": 0.08})
        return BulkNameDetectionResult(page_number=page_number, student_name="Bob Smith", exam_name="Math 20-1 Unit Test", confidence=0.89, evidence={"x": 0.12, "y": 0.05, "w": 0.32, "h": 0.08})


def _front_page_candidate_value_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "One extracted value from the front-page summary, with the exact visible text and supporting evidence.",
        "propertyOrdering": ["value_text", "confidence", "evidence"],
        "properties": {
            "value_text": {"type": "string", "description": "Exact visible text from the page for this value."},
            "confidence": {"type": "number", "description": "Confidence from 0 to 1 for this value."},
            "evidence": {
                "type": "array",
                "description": "Short evidence quotes and normalized box coordinates for where this value was read.",
                "items": {
                    "type": "object",
                    "propertyOrdering": ["page_number", "quote", "x", "y", "w", "h"],
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
        "description": "Teacher-visible front-page score summary for one student paper page.",
        "propertyOrdering": [
            "exam_name",
            "student_name",
            "overall_marks_awarded",
            "overall_max_marks",
            "objective_scores",
            "warnings",
        ],
        "properties": {
            "exam_name": {
                "description": "Printed exam or test title from the page header. This is not the student name.",
                "anyOf": [_front_page_candidate_value_schema(), {"type": "null"}],
            },
            "student_name": {
                "description": "Student name written on the paper. This is not the exam title.",
                "anyOf": [_front_page_candidate_value_schema(), {"type": "null"}],
            },
            "overall_marks_awarded": {
                "description": "Final overall awarded total for the full test, not an outcome row total.",
                "anyOf": [_front_page_candidate_value_schema(), {"type": "null"}],
            },
            "overall_max_marks": {
                "description": "Final overall possible total for the full test, not an outcome row max.",
                "anyOf": [_front_page_candidate_value_schema(), {"type": "null"}],
            },
            "objective_scores": {
                "type": "array",
                "description": "All visible outcome or objective summary rows in page order.",
                "items": {
                    "type": "object",
                    "propertyOrdering": ["objective_code", "marks_awarded", "max_marks"],
                    "properties": {
                        "objective_code": {
                            **_front_page_candidate_value_schema(),
                            "description": "Visible outcome or objective row label exactly as shown on the page.",
                        },
                        "marks_awarded": {
                            **_front_page_candidate_value_schema(),
                            "description": "Awarded score for this outcome row.",
                        },
                        "max_marks": {
                            "description": "Possible score for this outcome row when shown.",
                            "anyOf": [_front_page_candidate_value_schema(), {"type": "null"}],
                        },
                    },
                },
            },
            "warnings": {
                "type": "array",
                "description": "Any ambiguity, conflicts, or missing-structure notes detected while extracting the summary.",
                "items": {"type": "string"},
            },
        },
    }
    _ensure_strict_schema_node(schema)
    validate_schema_strictness(schema)
    return schema


def build_bulk_name_response_json_schema() -> dict[str, Any]:
    schema = {
        "type": "object",
        "description": "Student-name and exam-title read from one exam page.",
        "propertyOrdering": ["page_number", "student_name", "exam_name", "confidence", "evidence"],
        "properties": {
            "page_number": {"type": "integer", "description": "1-based page number for this read."},
            "student_name": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Student name from fields like Name or Student. Not the exam title.",
            },
            "exam_name": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Printed exam or test title from the page header. Not the student name.",
            },
            "confidence": {"type": "number", "description": "Confidence from 0 to 1 for the student_name read."},
            "evidence": {
                "description": "Normalized box coordinates for the student-name field when visible.",
                "anyOf": [
                    {
                        "type": "object",
                        "propertyOrdering": ["x", "y", "w", "h"],
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "w": {"type": "number"},
                            "h": {"type": "number"},
                        },
                    },
                    {"type": "null"},
                ],
            },
        },
    }
    _ensure_strict_schema_node(schema)
    validate_schema_strictness(schema)
    return schema


def _build_front_page_totals_prompt(template: dict[str, object] | None = None) -> str:
    if template:
        outcome_codes = [str(item).strip() for item in template.get("outcome_codes", []) if str(item).strip()]
        expects_overall_total = bool(template.get("expects_overall_total"))
        expects_overall_max = bool(template.get("expects_overall_max"))
        stable = bool(template.get("stable"))
        outcome_hint = ", ".join(outcome_codes) if outcome_codes else "no outcome rows"
        stability_hint = "stable" if stable else "best-known"
        overall_hint_parts: list[str] = []
        if expects_overall_total:
            overall_hint_parts.append("overall awarded total")
        if expects_overall_max:
            overall_hint_parts.append("overall possible total")
        overall_hint = ", ".join(overall_hint_parts) if overall_hint_parts else "no overall total fields"
        return (
            "Extract only the teacher-visible front-page score summary from one student exam page. "
            "Also return the exam or test title from the printed page header in exam_name when visible. "
            "This exam already has a known front-page template. Match the printed summary table or score area to that template before reading values. "
            f"The {stability_hint} expected outcome rows are: {outcome_hint}. "
            f"The expected overall summary fields are: {overall_hint}. "
            "Your job is to fill that known structure, not rediscover a new one, unless the page clearly shows a conflicting summary table. "
            "Use printed labels, printed row layout, and printed score columns to identify the summary table. Scores inside that table may be typed or handwritten. Accept handwritten values only when they are clearly inside the summary row or score cell. Ignore handwritten numbers elsewhere on the page. "
            "Return every expected visible outcome row in page order. If a row is visible but one field is unclear, return the row and set only the unclear field to null. "
            "Do not confuse an outcome row total with the final overall total. If the page clearly shows an extra or conflicting summary row not in the known template, include it and add a warning. "
            "If a total is shown as 42/50, return awarded=42 and max=50. If an outcome row is shown as OB1 18/20, return objective_code='OB1', marks_awarded='18', and max_marks='20'. "
            "Do not invent rows, infer hidden totals, or sum values yourself. If a field is not clearly visible, return null, or return an empty list for objective_scores. "
            "For each returned value, copy the exact visible text into value_text, set confidence 0..1, and include short evidence quotes with normalized box coordinates when possible. "
            "If multiple candidate overall totals compete, choose the one most clearly shown as the final overall summary and add a warning. "
            "Return strict JSON only."
        )

    return (
        "Extract only the teacher-visible front-page score summary from one student exam page. "
        "If a student name is visible, return it exactly in student_name. If the printed page header shows the exam or test title, return that in exam_name. Do not confuse the student name with the test title. "
        "First find the printed summary table or score area that a teacher would copy from. Then return all visible outcome/objective/category rows from that summary, plus the final overall awarded total and overall max when shown. Do not stop after finding the overall total. "
        "Use printed labels, printed row layout, and printed score columns to identify the summary structure. Labels may be words like Outcome, Objective, Category, Strand, LO, OB, Total, or Name, and may also be short labels like 1, 2, 3, 4, K, T, C, A, LO1, or OB1 when they appear in the printed summary structure. "
        "Scores inside the summary table may be typed or handwritten. Accept handwritten values only when they are clearly inside the summary row or score cell. Ignore handwritten numbers elsewhere on the page. "
        "Return all visible outcome rows in page order. Do not omit a row just because the label is short or partially unclear. If a row is visible but one field is unclear, return the row and set only the unclear field to null. "
        "Do not confuse an outcome row total with the final overall total. If a total is shown as 42/50, return awarded=42 and max=50. If an outcome row is shown as OB1 18/20, return objective_code='OB1', marks_awarded='18', and max_marks='20'. "
        "Do not invent rows, infer hidden totals, or sum values yourself. If a field is not clearly visible, return null, or return an empty list for objective_scores. "
        "For each returned value, copy the exact visible text into value_text, set confidence 0..1, and include short evidence quotes with normalized box coordinates when possible. "
        "If multiple candidate overall totals compete, choose the one most clearly shown as the final overall summary and add a warning. "
        "Return strict JSON only."
    )


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

    def extract(
        self,
        image_path: Path,
        request_id: str,
        *,
        model_override: str | None = None,
        template: dict[str, object] | None = None,
    ) -> FrontPageTotalsExtractResult:
        normalized = normalize_key_page_image(image_path)
        schema = build_front_page_totals_response_schema()
        model = model_override or _front_page_model()
        prompt = _build_front_page_totals_prompt(template)
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


class GeminiFrontPageTotalsExtractor:
    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self._client = GeminiStructuredVisionClient(timeout_seconds=timeout_seconds)

    def extract(
        self,
        image_path: Path,
        request_id: str,
        *,
        model_override: str | None = None,
        template: dict[str, object] | None = None,
    ) -> FrontPageTotalsExtractResult:
        _ = request_id
        normalized = normalize_key_page_image(image_path)
        model = model_override or _front_page_model()
        prompt = _build_front_page_totals_prompt(template)
        parsed = self._client.generate_json(
            model=model,
            prompt=prompt,
            image=normalized.image_bytes,
            mime_type=normalized.mime_type,
            response_json_schema=build_front_page_totals_response_schema(),
        )
        return FrontPageTotalsExtractResult(payload=parsed, model=model)


class MockFrontPageTotalsExtractor:
    def extract(
        self,
        image_path: Path,
        request_id: str,
        *,
        model_override: str | None = None,
        template: dict[str, object] | None = None,
    ) -> FrontPageTotalsExtractResult:
        _ = (image_path, request_id, template)
        return FrontPageTotalsExtractResult(
            payload={
                "exam_name": {
                    "value_text": "Math 20-1 Unit Test",
                    "confidence": 0.9,
                    "evidence": [{"page_number": 1, "quote": "Math 20-1 Unit Test", "x": 0.12, "y": 0.02, "w": 0.36, "h": 0.05}],
                },
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
            model=model_override or "mock-front-page-totals",
        )


def get_front_page_totals_extractor() -> FrontPageTotalsExtractor:
    if os.getenv("OPENAI_MOCK", "").strip() == "1":
        return MockFrontPageTotalsExtractor()
    if _front_page_provider_name() == "gemini":
        return GeminiFrontPageTotalsExtractor()
    return OpenAIFrontPageTotalsExtractor()


def get_bulk_name_detector() -> BulkNameDetector:
    if os.getenv("OPENAI_MOCK", "").strip() == "1":
        return MockBulkNameDetector()
    if _front_page_provider_name() == "gemini":
        return GeminiBulkNameDetector()
    return OpenAIBulkNameDetector()
