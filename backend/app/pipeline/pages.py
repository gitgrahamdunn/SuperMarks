"""Page extraction and normalization pipeline."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

_PREVIEW_MAX_WIDTH = max(400, int(os.getenv("SUPERMARKS_PAGE_PREVIEW_MAX_WIDTH", "1400") or "1400"))
_PREVIEW_JPEG_QUALITY = max(40, min(95, int(os.getenv("SUPERMARKS_PAGE_PREVIEW_JPEG_QUALITY", "75") or "75")))


class PDFConverter:
    """Interface for PDF-to-image conversion."""

    def convert(self, pdf_path: Path, output_dir: Path) -> list[Path]:
        raise NotImplementedError


class Pdf2ImageConverter(PDFConverter):
    """PDF converter using PyMuPDF, which is already bundled with the app."""

    def __init__(self) -> None:
        try:
            import fitz  # pymupdf
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("PyMuPDF is not installed. Install pymupdf for PDF support.") from exc
        self._fitz = fitz

    def convert(self, pdf_path: Path, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_paths: list[Path] = []
        try:
            with self._fitz.open(pdf_path) as doc:
                for idx, page in enumerate(doc, 1):
                    out = output_dir / f"page_{idx:04d}.png"
                    page.get_pixmap(matrix=self._fitz.Matrix(2, 2)).save(str(out))
                    out_paths.append(out)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("PDF render failed. Try uploading images.") from exc
        return out_paths


def normalize_image_to_png(input_path: Path, output_path: Path) -> tuple[int, int]:
    """Convert input image to PNG and return dimensions."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(input_path) as image:
        rgb = image.convert("RGB")
        rgb.save(output_path, format="PNG")
        return rgb.width, rgb.height


def preview_image_path_for_page(image_path: Path) -> Path:
    """Return the deterministic sidecar preview path for a rendered page image."""
    return image_path.with_name(f"{image_path.stem}.preview.jpg")


def build_page_preview_image(input_path: Path, output_path: Path | None = None) -> Path:
    """Build a lighter JPEG preview for a rendered page image and return its path."""
    preview_path = output_path or preview_image_path_for_page(input_path)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(input_path) as image:
        rgb = image.convert("RGB")
        if rgb.width > _PREVIEW_MAX_WIDTH:
            scaled_height = max(1, round(rgb.height * (_PREVIEW_MAX_WIDTH / rgb.width)))
            rgb = rgb.resize((_PREVIEW_MAX_WIDTH, scaled_height), Image.Resampling.LANCZOS)
        rgb.save(
            preview_path,
            format="JPEG",
            quality=_PREVIEW_JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
    return preview_path
