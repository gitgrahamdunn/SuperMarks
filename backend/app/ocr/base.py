"""OCR provider interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class OCRResult:
    text: str
    confidence: float
    raw: dict


class OCRProvider(Protocol):
    """OCR provider protocol."""

    name: str

    def transcribe(self, image_path: Path) -> OCRResult:
        """Extract text from an image."""
