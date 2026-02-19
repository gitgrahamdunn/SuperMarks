"""Page extraction and normalization pipeline."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


class PDFConverter:
    """Interface for PDF-to-image conversion."""

    def convert(self, pdf_path: Path, output_dir: Path) -> list[Path]:
        raise NotImplementedError


class Pdf2ImageConverter(PDFConverter):
    """PDF converter using pdf2image when available."""

    def __init__(self) -> None:
        try:
            from pdf2image import convert_from_path  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "pdf2image is not installed. Install pdf2image and poppler for PDF support."
            ) from exc
        self._convert_from_path = convert_from_path

    def convert(self, pdf_path: Path, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        pages = self._convert_from_path(str(pdf_path))
        out_paths: list[Path] = []
        for idx, page in enumerate(pages, 1):
            out = output_dir / f"page_{idx:04d}.png"
            page.save(out, format="PNG")
            out_paths.append(out)
        return out_paths


def normalize_image_to_png(input_path: Path, output_path: Path) -> tuple[int, int]:
    """Convert input image to PNG and return dimensions."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(input_path) as image:
        rgb = image.convert("RGB")
        rgb.save(output_path, format="PNG")
        return rgb.width, rgb.height
