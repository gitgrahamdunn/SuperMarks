from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.pipeline.key_pages import normalize_key_page_header_image, normalize_key_page_image


def test_normalize_key_page_image_resizes_and_emits_jpeg(tmp_path: Path) -> None:
    source = tmp_path / "big.png"
    image = Image.new("RGB", (2600, 1200), color=(120, 10, 10))
    image.save(source, format="PNG")

    normalized = normalize_key_page_image(source)

    assert normalized.width == 1024
    assert normalized.height < 1024
    assert normalized.mime_type == "image/jpeg"
    assert normalized.final_size_bytes > 0
    assert normalized.image_bytes[:2] == b"\xff\xd8"


def test_normalize_key_page_image_caps_tall_pages_within_1024_box(tmp_path: Path) -> None:
    source = tmp_path / "tall.png"
    image = Image.new("RGB", (1200, 2600), color=(10, 10, 120))
    image.save(source, format="PNG")

    normalized = normalize_key_page_image(source)

    assert normalized.width < 1024
    assert normalized.height == 1024
    assert normalized.mime_type == "image/jpeg"


def test_normalize_key_page_header_image_crops_top_region_before_resizing(tmp_path: Path) -> None:
    source = tmp_path / "header-source.png"
    image = Image.new("RGB", (1600, 2400), color=(20, 20, 20))
    image.save(source, format="PNG")

    normalized = normalize_key_page_header_image(source, top_fraction=0.25)

    assert normalized.width == 1024
    assert normalized.height < 500
    assert normalized.mime_type == "image/jpeg"
