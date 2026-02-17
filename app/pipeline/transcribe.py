"""OCR provider factory/dispatcher."""

from app.ocr.base import OCRProvider
from app.ocr.pix2text_provider import Pix2TextProvider
from app.ocr.stub import StubOCRProvider


def get_ocr_provider(name: str) -> OCRProvider:
    provider = name.lower()
    if provider == "stub":
        return StubOCRProvider()
    if provider == "pix2text":
        return Pix2TextProvider()
    raise ValueError(f"Unknown OCR provider '{name}'. Use one of: stub, pix2text")
