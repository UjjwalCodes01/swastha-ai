"""
Abstract base class for all document extractors.

Every extractor returns an ExtractionResult containing structured data:
pages of text, tables, images, form fields, and attachments.

Concrete extractors:
  - PDFExtractor     → PyMuPDF + pdfplumber
  - DOCXExtractor    → python-docx
  - XMLExtractor     → defusedxml (XXE-safe)
  - CSVExtractor     → csv stdlib + chardet
  - TikaExtractor    → Apache Tika HTTP (last resort)
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PageContent:
    """
    The extracted content of a single page (or logical page unit).

    extraction_method is one of: "native", "ocr", "hybrid"
    ocr_confidence is None when extraction_method == "native"
    """

    page_number: int
    text: str
    word_count: int
    char_count: int
    has_images: bool
    has_tables: bool
    extraction_method: str  # "native" | "ocr" | "hybrid"
    ocr_confidence: float | None = None

    def __post_init__(self) -> None:
        if self.word_count == 0 and self.text:
            self.word_count = len(self.text.split())
        if self.char_count == 0:
            self.char_count = len(self.text)


@dataclass
class ExtractedTable:
    """
    A single table extracted from the document.

    table_id uses the pattern: {doc_id}-TABLE-{n}  (assigned by the pipeline)
    headers: list of column header strings
    rows: list of rows, each row is a list of cell values (str)
    page_number: page where the table appears
    markdown: Markdown-formatted string representation
    """

    table_id: str = ""          # set by pipeline after extraction
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    page_number: int = 0
    markdown: str = ""
    is_continuation: bool = False   # True when table spans from previous page

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def col_count(self) -> int:
        return len(self.headers) if self.headers else (len(self.rows[0]) if self.rows else 0)

    def to_json_rows(self) -> list[dict[str, Any]]:
        """Return rows as list of dicts keyed by header name."""
        if not self.headers:
            return [{"col_" + str(i): v for i, v in enumerate(row)} for row in self.rows]
        return [dict(zip(self.headers, row)) for row in self.rows]

    def to_markdown(self) -> str:
        """Regenerate markdown from headers + rows."""
        if not self.headers and not self.rows:
            return ""
        cols = self.headers or [f"col_{i}" for i in range(len(self.rows[0]))] if self.rows else []
        lines: list[str] = []
        lines.append("| " + " | ".join(str(c) for c in cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for row in self.rows:
            padded = list(row) + [""] * (len(cols) - len(row))
            lines.append("| " + " | ".join(str(c) for c in padded) + " |")
        return "\n".join(lines)


@dataclass
class ExtractedImage:
    """
    An image extracted from a document page.

    image_bytes: raw bytes (PNG preferred, JPEG acceptable)
    page_number: 0-indexed page where the image appears
    image_index: sequential index of image on that page
    width, height: pixel dimensions
    coverage_ratio: fraction of page area occupied (0.0–1.0)
    """

    image_bytes: bytes
    page_number: int
    image_index: int
    width: int = 0
    height: int = 0
    coverage_ratio: float = 0.0
    xref: int | None = None  # PyMuPDF internal reference


@dataclass
class AttachmentInfo:
    """Describes an embedded attachment (PDF-in-PDF, zip, etc.)."""

    filename: str
    mime_type: str
    size_bytes: int
    data: bytes = field(default_factory=bytes)


@dataclass
class ExtractionResult:
    """
    The complete output of an extractor.

    pages: ordered list of PageContent (index == page_number - 1)
    tables: all tables found across all pages
    images: all images across all pages
    form_fields: key → value for fillable form fields
    attachments: embedded files found inside the document
    extractor_used: name of the extractor class that produced this result
    extraction_warnings: non-fatal issues encountered (e.g. encrypted, partial)
    """

    pages: list[PageContent] = field(default_factory=list)
    tables: list[ExtractedTable] = field(default_factory=list)
    images: list[ExtractedImage] = field(default_factory=list)
    form_fields: dict[str, str] = field(default_factory=dict)
    attachments: list[AttachmentInfo] = field(default_factory=list)
    extractor_used: str = ""
    extraction_warnings: list[str] = field(default_factory=list)

    @property
    def total_text(self) -> str:
        """All page text concatenated in order."""
        return "\n\n".join(p.text for p in self.pages if p.text)

    @property
    def total_word_count(self) -> int:
        return sum(p.word_count for p in self.pages)

    @property
    def total_char_count(self) -> int:
        return sum(p.char_count for p in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def is_empty(self) -> bool:
        return self.total_char_count == 0

    def add_warning(self, warning: str) -> None:
        if warning not in self.extraction_warnings:
            self.extraction_warnings.append(warning)


class BaseExtractor(abc.ABC):
    """
    Abstract base for all document extractors.

    Subclasses must implement extract(file_bytes) and return an ExtractionResult.
    The extractor_name property is used for logging and the extractor_used field.
    """

    @property
    def extractor_name(self) -> str:
        return self.__class__.__name__

    @abc.abstractmethod
    async def extract(self, file_bytes: bytes) -> ExtractionResult:
        """
        Extract content from the raw file bytes.

        Must be async-safe. CPU-bound work must be offloaded to
        asyncio.get_event_loop().run_in_executor() to avoid blocking.

        Never raises — on fatal failure, return an ExtractionResult with
        extraction_warnings populated and empty pages.
        """
        ...

    def _make_empty_result(self, warning: str | None = None) -> ExtractionResult:
        result = ExtractionResult(extractor_used=self.extractor_name)
        if warning:
            result.add_warning(warning)
        return result
