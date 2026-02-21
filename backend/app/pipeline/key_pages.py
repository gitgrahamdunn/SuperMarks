"""Answer-key page normalization and batching helpers."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


_MAX_WIDTH = 1280
_JPEG_QUALITY = 75


@dataclass
class NormalizedImage:
    """Normalized image blob ready for OpenAI vision payload."""

    image_bytes: bytes
    mime_type: str
    width: int
    height: int
    original_size_bytes: int
    final_size_bytes: int


def normalize_key_page_image(image_path: Path, max_width: int = _MAX_WIDTH, jpeg_quality: int = _JPEG_QUALITY) -> NormalizedImage:
    """Normalize image for key parsing payloads and size constraints."""

    original_size = image_path.stat().st_size
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if image.width > max_width:
            scale = max_width / float(image.width)
            image = image.resize((max_width, int(image.height * scale)), Image.Resampling.LANCZOS)

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=jpeg_quality, optimize=True)

    payload = output.getvalue()
    return NormalizedImage(
        image_bytes=payload,
        mime_type="image/jpeg",
        width=image.width,
        height=image.height,
        original_size_bytes=original_size,
        final_size_bytes=len(payload),
    )


def batch_image_paths(image_paths: list[Path], max_images: int = 6) -> list[list[Path]]:
    """Split page paths into chunks of max_images."""

    if max_images <= 0:
        raise ValueError("max_images must be positive")
    return [image_paths[i : i + max_images] for i in range(0, len(image_paths), max_images)]
