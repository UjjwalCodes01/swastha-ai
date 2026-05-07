"""
OCR Engine — Tesseract 5.x (LSTM) orchestration.

Features:
  - PSM 6 default → PSM 3 fallback → PSM 11 for forms
  - Language hints (eng default, eng+hin if Hindi detected)
  - Per-word confidence scoring → page-level confidence
  - Concurrent page processing via asyncio.gather + semaphore (max 4)
  - Runs in thread pool to avoid blocking event loop
  - Returns (text, confidence_score) per page
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from functools import partial
from typing import Any

logger = logging.getLogger(__name__)

try:
    import pytesseract
    from pytesseract import Output
except ImportError:  # pragma: no cover
    pytesseract = None  # type: ignore[assignment]
    Output = None  # type: ignore[assignment]

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment, misc]

from app.preprocessing.ocr.image_preprocessor import ImagePreprocessor, PreprocessorConfig

# Maximum concurrent OCR operations (CPU-bound — more causes memory pressure)
_DEFAULT_OCR_CONCURRENCY = 4

# PSM modes in order of attempt
_PSM_SEQUENCE = [6, 3, 11]
_PSM_MIN_CHARS = 100  # minimum chars before trying next PSM

# Tesseract language code mappings
_LANGDETECT_TO_TESSERACT: dict[str, str] = {
    "en": "eng",
    "hi": "hin",
    "ta": "tam",
    "te": "tel",
    "kn": "kan",
    "mr": "mar",
    "gu": "guj",
    "pa": "pan",
}


@dataclass
class OCRPageResult:
    """OCR result for a single page."""
    page_number: int
    text: str
    confidence: float  # 0.0 – 1.0 (mean word confidence / 100)
    psm_used: int
    char_count: int = 0
    word_count: int = 0

    def __post_init__(self) -> None:
        self.char_count = len(self.text)
        self.word_count = len(self.text.split())


class OCREngine:
    """
    Tesseract 5 OCR engine with parallel page processing.

    Usage:
        engine = OCREngine(language="eng", concurrency=4)
        results = await engine.process_pages([(page_num, image_bytes), ...])
    """

    def __init__(
        self,
        language: str = "eng",
        concurrency: int = _DEFAULT_OCR_CONCURRENCY,
        preprocessor_config: PreprocessorConfig | None = None,
    ) -> None:
        self._language = language
        self._semaphore = asyncio.Semaphore(concurrency)
        self._preprocessor = ImagePreprocessor(preprocessor_config)

    def set_language(self, lang_code: str) -> None:
        """Update language based on detected document language."""
        tess_lang = _LANGDETECT_TO_TESSERACT.get(lang_code, "eng")
        if tess_lang == "eng":
            self._language = "eng"
        else:
            # Combine with English for better accuracy on mixed-language docs
            self._language = f"eng+{tess_lang}"
        logger.debug("OCR language set", extra={"language": self._language})

    async def process_pages(
        self,
        pages: list[tuple[int, bytes]],
    ) -> list[OCRPageResult]:
        """
        Process multiple pages concurrently.

        Args:
            pages: list of (page_number, image_bytes)

        Returns:
            list of OCRPageResult in page_number order.
        """
        tasks = [self._process_page_with_semaphore(pnum, img) for pnum, img in pages]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: list[OCRPageResult] = []
        for i, (pnum, _) in enumerate(pages):
            r = results[i]
            if isinstance(r, Exception):
                logger.warning(
                    "OCR failed for page",
                    extra={"page": pnum, "error": str(r)},
                )
                output.append(OCRPageResult(
                    page_number=pnum,
                    text="",
                    confidence=0.0,
                    psm_used=6,
                ))
            else:
                output.append(r)  # type: ignore[arg-type]
        return output

    async def _process_page_with_semaphore(
        self, page_number: int, image_bytes: bytes
    ) -> OCRPageResult:
        async with self._semaphore:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                partial(self._ocr_page_sync, page_number, image_bytes),
            )

    def _ocr_page_sync(self, page_number: int, image_bytes: bytes) -> OCRPageResult:
        """
        Run Tesseract on a single page image (sync, runs in thread pool).

        Tries PSM modes in sequence: PSM 6 → PSM 3 → PSM 11
        Returns the result from the first PSM that produces >= _PSM_MIN_CHARS.
        """
        if pytesseract is None:
            raise ImportError("pytesseract not installed")
        if Image is None:
            raise ImportError("Pillow not installed")

        # Load and preprocess image
        img = Image.open(io.BytesIO(image_bytes))
        try:
            img = self._preprocessor.process(img)
        except Exception as exc:
            logger.debug("Image preprocessing failed — using raw image", extra={"error": str(exc)})

        best_result: OCRPageResult | None = None

        for psm in _PSM_SEQUENCE:
            try:
                result = self._run_tesseract(img, psm, page_number)
                if result.char_count >= _PSM_MIN_CHARS:
                    return result
                if best_result is None or result.char_count > best_result.char_count:
                    best_result = result
            except Exception as exc:
                logger.debug(
                    "Tesseract PSM failed",
                    extra={"page": page_number, "psm": psm, "error": str(exc)},
                )
                continue

        return best_result or OCRPageResult(
            page_number=page_number,
            text="",
            confidence=0.0,
            psm_used=6,
        )

    def _run_tesseract(self, img: Any, psm: int, page_number: int) -> OCRPageResult:
        """
        Run pytesseract.image_to_data for confidence scoring.
        Falls back to image_to_string if data mode fails.
        """
        config = f"--psm {psm} --oem 1"  # OEM 1 = LSTM engine

        try:
            data = pytesseract.image_to_data(
                img,
                lang=self._language,
                config=config,
                output_type=Output.DICT,
            )
            words = [
                (data["text"][i], int(data["conf"][i]))
                for i in range(len(data["text"]))
                if data["text"][i].strip() and int(data["conf"][i]) >= 0
            ]
            if not words:
                return OCRPageResult(
                    page_number=page_number,
                    text="",
                    confidence=0.0,
                    psm_used=psm,
                )

            text = " ".join(w[0] for w in words)
            mean_conf = sum(w[1] for w in words) / len(words) / 100.0

            if mean_conf < 0.60:
                logger.debug(
                    "Low OCR confidence on page",
                    extra={"page": page_number, "confidence": mean_conf, "psm": psm},
                )

            return OCRPageResult(
                page_number=page_number,
                text=text,
                confidence=round(mean_conf, 4),
                psm_used=psm,
            )

        except Exception:
            # Fallback: just get the text without confidence scores
            text = pytesseract.image_to_string(
                img, lang=self._language, config=config
            )
            return OCRPageResult(
                page_number=page_number,
                text=text,
                confidence=0.5,  # unknown confidence
                psm_used=psm,
            )
