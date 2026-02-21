from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.pipeline.key_pages import normalize_key_page_image


def test_normalize_key_page_image_resizes_and_emits_jpeg(tmp_path: Path) -> None:
    source = tmp_path / "big.png"
    image = Image.new("RGB", (2600, 1200), color=(120, 10, 10))
    image.save(source, format="PNG")

    normalized = normalize_key_page_image(source)

    assert normalized.width == 1280
    assert normalized.height < 1200
    assert normalized.mime_type == "image/jpeg"
    assert normalized.final_size_bytes > 0
    assert normalized.image_bytes[:2] == b"\xff\xd8"
