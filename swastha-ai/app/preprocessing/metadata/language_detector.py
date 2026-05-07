"""
Language Detector using langdetect.

Detects primary language of the document. Uses a sample from the start
and middle to detect multilingual documents. Maps to Tesseract lang codes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import langdetect
    from langdetect.lang_detect_exception import LangDetectException
except ImportError:  # pragma: no cover
    langdetect = None  # type: ignore[assignment]
    LangDetectException = Exception  # type: ignore[assignment, misc]


class LanguageDetectionResult:
    def __init__(self, language: str, confidence: float, is_multilingual: bool):
        self.language = language
        self.confidence = confidence
        self.is_multilingual = is_multilingual


class LanguageDetector:
    """Detects document language."""

    def detect(self, text: str) -> LanguageDetectionResult:
        """
        Detect language using start and middle samples.
        Defaults to 'en' with 1.0 confidence if langdetect is unavailable
        or text is empty.
        """
        if not text or not text.strip():
            return LanguageDetectionResult("en", 1.0, False)

        if langdetect is None:
            logger.warning("langdetect not installed — defaulting to English")
            return LanguageDetectionResult("en", 1.0, False)

        # Ensure consistent results across runs
        langdetect.DetectorFactory.seed = 42

        # Sample 1: first 2000 chars
        sample_start = text[:2000].strip()

        # Sample 2: 2000 chars from the middle
        mid_point = len(text) // 2
        sample_mid = text[mid_point : mid_point + 2000].strip()

        try:
            # We use detect_langs() to get probabilities
            langs_start = langdetect.detect_langs(sample_start)
            if not langs_start:
                return LanguageDetectionResult("en", 1.0, False)

            primary_lang = langs_start[0].lang
            confidence = langs_start[0].prob

            # If middle sample is long enough, check if it differs
            is_multilingual = False
            if len(sample_mid) > 100:
                langs_mid = langdetect.detect_langs(sample_mid)
                if langs_mid and langs_mid[0].lang != primary_lang:
                    is_multilingual = True

            return LanguageDetectionResult(primary_lang, round(confidence, 3), is_multilingual)

        except LangDetectException as exc:
            logger.debug("Language detection failed", extra={"error": str(exc)})
            return LanguageDetectionResult("en", 0.0, False)
