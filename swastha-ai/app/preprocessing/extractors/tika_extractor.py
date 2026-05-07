"""
Tika Extractor — last-resort extractor for unsupported formats.

Calls the local Apache Tika server over HTTP.
Never silently returns empty text — raises TikaUnavailableError if
the server is down so the pipeline can handle it explicitly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.preprocessing.extractors.base_extractor import (
    BaseExtractor,
    ExtractionResult,
    PageContent,
)

logger = logging.getLogger(__name__)

_TIKA_TIMEOUT_SECONDS = 60


class TikaUnavailableError(RuntimeError):
    """Raised when the Tika server cannot be reached."""


class TikaExtractor(BaseExtractor):
    """
    Fallback extractor using Apache Tika.

    Tika supports 1000+ file formats. Used when no other extractor
    can handle the MIME type.

    Note: Tika returns a single text block with no page-level breakdown.
    """

    def __init__(self, tika_url: str = "http://localhost:9998") -> None:
        self._tika_url = tika_url.rstrip("/")

    @property
    def extractor_name(self) -> str:
        return "TikaExtractor"

    async def extract(self, file_bytes: bytes) -> ExtractionResult:
        result = ExtractionResult(extractor_used=self.extractor_name)

        try:
            text = await self._call_tika(file_bytes)
        except TikaUnavailableError:
            raise  # let pipeline handle this explicitly
        except Exception as exc:
            logger.error("Tika extraction failed", extra={"error": str(exc)})
            result.add_warning(f"TIKA_EXTRACTION_FAILED: {exc}")
            return result

        # Strip Tika metadata headers if they appear at the top
        text = self._strip_tika_metadata_headers(text)

        if not text.strip():
            result.add_warning("TIKA_RETURNED_EMPTY_TEXT")

        result.pages.append(PageContent(
            page_number=1,
            text=text,
            word_count=len(text.split()),
            char_count=len(text),
            has_images=False,
            has_tables=False,
            extraction_method="native",
        ))
        return result

    async def _call_tika(self, file_bytes: bytes) -> str:
        """
        POST file bytes to Tika /tika endpoint, accept text/plain.
        """
        url = f"{self._tika_url}/tika"
        try:
            async with httpx.AsyncClient(timeout=_TIKA_TIMEOUT_SECONDS) as client:
                response = await client.put(
                    url,
                    content=file_bytes,
                    headers={"Accept": "text/plain", "Content-Type": "application/octet-stream"},
                )
            if response.status_code == 200:
                return response.text
            elif response.status_code == 422:
                # Tika couldn't parse the file
                logger.warning("Tika returned 422 — file unparseable")
                return ""
            else:
                raise RuntimeError(f"Tika HTTP {response.status_code}: {response.text[:200]}")
        except httpx.ConnectError as exc:
            raise TikaUnavailableError(
                f"Tika server unreachable at {url}. "
                "Ensure the tika container is running and healthy. "
                f"Original error: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise TikaUnavailableError(
                f"Tika server timed out after {_TIKA_TIMEOUT_SECONDS}s. "
                f"Original error: {exc}"
            ) from exc

    def _strip_tika_metadata_headers(self, text: str) -> str:
        """
        Tika sometimes prepends metadata headers like:
          Content-Type: application/pdf
          Content-Length: 12345
        separated from the body by a blank line. Strip those.
        """
        lines = text.split("\n")
        body_start = 0
        for i, line in enumerate(lines):
            # Find the first blank line (separator between meta and body)
            if not line.strip() and i > 0:
                # Check if preceding lines look like HTTP headers
                preceding = [l for l in lines[:i] if l.strip()]
                if all(":" in l for l in preceding):
                    body_start = i + 1
                    break
        return "\n".join(lines[body_start:]).strip()
