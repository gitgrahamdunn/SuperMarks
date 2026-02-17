"""Stub OCR provider for local/offline testing."""

from pathlib import Path

from app.ocr.base import OCRProvider, OCRResult


class StubOCRProvider(OCRProvider):
    name = "stub"

    def transcribe(self, image_path: Path) -> OCRResult:
        return OCRResult(
            text=f"[stub-ocr] Transcribed content from {image_path.name}",
            confidence=0.5,
            raw={"source": "stub", "image": str(image_path)},
        )
