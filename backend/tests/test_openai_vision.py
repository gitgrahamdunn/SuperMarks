from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai.openai_vision import (
    OpenAIFrontPageTotalsExtractor,
    OpenAIBulkNameDetector,
    SchemaBuildError,
    _front_page_model,
    _front_page_provider_name,
    _normalize_model_response_text,
    _recover_front_page_payload,
    build_answer_key_response_schema,
    build_front_page_totals_response_schema,
    build_key_parse_chat_request,
    build_key_parse_request,
    validate_schema_strictness,
)


def test_build_key_parse_request_uses_vision_and_schema() -> None:
    schema = build_answer_key_response_schema()
    payload = build_key_parse_request(
        model="gpt-5-nano",
        prompt="Parse this key",
        images=[b"img-a", b"img-b"],
        mime_types=["image/png", "image/jpeg"],
        schema=schema,
    )

    assert payload["model"] == "gpt-5-nano"
    assert isinstance(payload["input"], list)

    message = payload["input"][0]
    content = message["content"]

    assert content[0]["type"] == "input_text"
    assert content[0]["text"] == "Parse this key"
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/")
    assert content[2]["type"] == "input_image"
    assert content[2]["image_url"].startswith("data:image/")

    text_format = payload["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["strict"] is True


def test_build_key_parse_chat_request_uses_chat_messages_and_schema() -> None:
    schema = build_answer_key_response_schema()
    payload = build_key_parse_chat_request(
        model="Qwen/Qwen3.5-397B-A17B-FP8",
        prompt="Parse this key",
        images=[b"img-a"],
        mime_types=["image/png"],
        schema=schema,
    )

    assert payload["model"] == "Qwen/Qwen3.5-397B-A17B-FP8"
    assert payload["messages"][0]["role"] == "user"
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True


def test_answer_key_schema_includes_objective_codes_and_excludes_model_solution() -> None:
    schema = build_answer_key_response_schema()
    question_props = schema["properties"]["questions"]["items"]["properties"]
    assert "objective_codes" in question_props
    assert "model_solution" not in question_props
    assert "criteria" not in question_props


def test_answer_key_schema_is_strict_for_all_object_nodes() -> None:
    schema = build_answer_key_response_schema()

    def _assert_object_nodes(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert isinstance(node.get("required"), list)
                assert node.get("additionalProperties") is False
            for value in node.values():
                _assert_object_nodes(value)
        elif isinstance(node, list):
            for item in node:
                _assert_object_nodes(item)

    _assert_object_nodes(schema)

    questions_items_required = schema["properties"]["questions"]["items"]["required"]
    assert isinstance(questions_items_required, list)
    assert "label" in questions_items_required
    assert "max_marks" in questions_items_required
    assert "marks_source" in questions_items_required
    assert "marks_confidence" in questions_items_required
    assert "evidence" in questions_items_required


def test_schema_validation_rejects_non_strict_shape() -> None:
    invalid_schema = {"type": "object", "properties": {"x": {"type": "object", "properties": {"a": {"type": "string"}}}}}
    with pytest.raises(SchemaBuildError):
        validate_schema_strictness(invalid_schema)


def test_front_page_totals_schema_is_strict_and_narrow() -> None:
    schema = build_front_page_totals_response_schema()
    props = schema["properties"]
    assert "student_name" in props
    assert "overall_marks_awarded" in props
    assert "overall_max_marks" in props
    assert "objective_scores" in props
    assert "warnings" in props
    validate_schema_strictness(schema)


def test_recover_front_page_payload_salvages_name_and_totals_from_malformed_json() -> None:
    malformed = """
    {
      "student_name": {
        "value_text": "Jordan Lee",
        "confidence": 0.93,
        "evidence": [{"page_number": 1, "quote": "Name: Jordan Lee", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.05}]
      },
      "overall_marks_awarded": {
        "value_text": "42",
        "confidence": 0.95,
        "evidence": [{"page_number": 1, "quote": "42/50", "x": 0.7, "y": 0.1, "w": 0.1, "h": 0.05}]
      },
      "overall_max_marks": {
        "value_text": "50",
        "confidence": 0.95,
        "evidence": [{"page_number": 1, "quote": "42/50", "x": 0.7, "y": 0.1, "w": 0.1, "h": 0.05}]
      },
      "objective_scores": [
        {
          "objective_code": {"value_text": "OB1",
    """

    recovered = _recover_front_page_payload(malformed)

    assert recovered is not None
    assert recovered["student_name"]["value_text"] == "Jordan Lee"
    assert recovered["overall_marks_awarded"]["value_text"] == "42"
    assert recovered["overall_max_marks"]["value_text"] == "50"
    assert recovered["objective_scores"] == []
    assert "Recovered partial front-page candidates" in recovered["warnings"][-1]


def test_front_page_extractor_recovers_partial_candidates_from_doubleword_chat_output(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SUPERMARKS_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SUPERMARKS_LLM_PROVIDER", "doubleword")
    monkeypatch.setenv("SUPERMARKS_KEY_PARSE_NANO_MODEL", "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8")

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            _ = kwargs
            malformed = """
            ```json
            {
              "student_name": {
                "value_text": "Jordan Lee",
                "confidence": 0.93,
                "evidence": [{"page_number": 1, "quote": "Name: Jordan Lee", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.05}]
              },
              "overall_marks_awarded": {
                "value_text": "42",
                "confidence": 0.95,
                "evidence": [{"page_number": 1, "quote": "42/50", "x": 0.7, "y": 0.1, "w": 0.1, "h": 0.05}]
              },
              "overall_max_marks": {
                "value_text": "50",
                "confidence": 0.95,
                "evidence": [{"page_number": 1, "quote": "42/50", "x": 0.7, "y": 0.1, "w": 0.1, "h": 0.05}]
              },
              "objective_scores": [
                {
                  "objective_code": {"value_text": "OB1",
            """
            message = SimpleNamespace(content=malformed)
            choice = SimpleNamespace(message=message)
            completion = SimpleNamespace(choices=[choice])
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **payload: completion))

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    image_path = tmp_path / "front-page.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
        b"\x0b\xe7\x02\x9d"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    extractor = OpenAIFrontPageTotalsExtractor()
    result = extractor.extract(Path(image_path), request_id="req-1")

    assert result.model == "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8"
    assert result.payload["student_name"]["value_text"] == "Jordan Lee"
    assert result.payload["overall_marks_awarded"]["value_text"] == "42"
    assert result.payload["overall_max_marks"]["value_text"] == "50"
    assert result.payload["objective_scores"] == []


def test_front_page_model_prefers_provider_specific_doubleword_default(monkeypatch) -> None:
    monkeypatch.delenv("SUPERMARKS_FRONT_PAGE_MODEL", raising=False)
    monkeypatch.setenv("SUPERMARKS_LLM_PROVIDER", "doubleword")
    monkeypatch.setenv("SUPERMARKS_KEY_PARSE_NANO_MODEL", "Qwen/vision-nano")

    assert _front_page_model() == "Qwen/vision-nano"


def test_normalize_model_response_text_reads_object_based_chat_parts() -> None:
    content = [SimpleNamespace(text=SimpleNamespace(value='{"student_name": null}'))]

    assert _normalize_model_response_text(content) == '{"student_name": null}'


def test_bulk_name_detector_uses_front_page_provider_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SUPERMARKS_FRONT_PAGE_PROVIDER", "openai_compatible")
    monkeypatch.setenv("SUPERMARKS_FRONT_PAGE_API_KEY", "front-page-openai-key")
    monkeypatch.setenv("SUPERMARKS_FRONT_PAGE_MODEL", "gpt-5-mini")
    monkeypatch.setenv("SUPERMARKS_LLM_PROVIDER", "doubleword")

    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured["client_kwargs"] = kwargs
            completion = SimpleNamespace(output_text='{"page_number": 1, "student_name": "Jordan Lee", "confidence": 0.91, "evidence": {"x": 0.1, "y": 0.08, "w": 0.3, "h": 0.06}}')
            self.responses = SimpleNamespace(create=lambda **payload: completion)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    image_path = tmp_path / "front-page.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
        b"\x0b\xe7\x02\x9d"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    detector = OpenAIBulkNameDetector()
    result = detector.detect(Path(image_path), page_number=1, model=_front_page_model(), request_id="bulk-name-1")

    assert _front_page_provider_name() == "openai_compatible"
    assert captured["client_kwargs"]["api_key"] == "front-page-openai-key"
    assert result.student_name == "Jordan Lee"
    assert result.confidence == 0.91
