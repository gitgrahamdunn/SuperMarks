"""Optional Pix2Text OCR provider."""

from pathlib import Path

from app.ocr.base import OCRProvider, OCRResult


class Pix2TextProvider(OCRProvider):
    name = "pix2text"

    def __init__(self) -> None:
        try:
            from pix2text import Pix2Text  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Pix2Text is not installed. Install with `pip install pix2text`."
            ) from exc
        self._engine = Pix2Text()

    def transcribe(self, image_path: Path) -> OCRResult:
        result = self._engine.recognize(str(image_path))
        text = result.get("text", "") if isinstance(result, dict) else str(result)
        return OCRResult(text=text, confidence=0.8, raw={"result": result})
