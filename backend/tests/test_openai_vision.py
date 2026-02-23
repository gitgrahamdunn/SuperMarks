from __future__ import annotations

import pytest

from app.ai.openai_vision import (
    SchemaBuildError,
    build_answer_key_response_schema,
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
