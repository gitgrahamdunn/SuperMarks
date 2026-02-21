from __future__ import annotations

from app.ai.openai_vision import ANSWER_KEY_SCHEMA, build_key_parse_request


def test_build_key_parse_request_uses_vision_and_schema() -> None:
    payload = build_key_parse_request(
        model="gpt-5-nano",
        prompt="Parse this key",
        images=[b"img-a", b"img-b"],
        mime_types=["image/png", "image/jpeg"],
        schema=ANSWER_KEY_SCHEMA,
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
    assert text_format["schema"] == ANSWER_KEY_SCHEMA
