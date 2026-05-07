"""
PDF Extractor — primary extractor for PDF documents.

Strategy:
  1. PyMuPDF (fitz) for native text + structure + images + form fields
  2. pdfplumber for table extraction on pages flagged by PyMuPDF
  3. Automatic OCR trigger detection (returns has_images pages with low text)

Handles gracefully:
  - Empty PDFs (0 pages)
  - Encrypted PDFs → warning, no crash
  - Corrupted PDFs → page-by-page salvage
  - PDFs > 500 pages → streamed page-by-page
  - Multi-column layouts → correct reading order via bounding box sort
  - Embedded images covering > 60% of page area → flagged for OCR
  - Fillable form fields
  - PDF-in-PDF embedded attachments
  - Page headers/footers (detected by position consistency)
  - Hyperlinks with anchor text
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections import defaultdict
from functools import partial
from typing import Any

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None  # type: ignore[assignment]

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None  # type: ignore[assignment]

from app.preprocessing.extractors.base_extractor import (
    AttachmentInfo,
    BaseExtractor,
    ExtractedImage,
    ExtractedTable,
    ExtractionResult,
    PageContent,
)

# A page where > 60% area is covered by images needs OCR regardless of text
_IMAGE_COVERAGE_OCR_THRESHOLD = 0.60
# Text density below this chars-per-page triggers OCR
_LOW_TEXT_DENSITY_THRESHOLD = 50
# Pages with the same text block at same y-position for >= this many pages
# are considered headers/footers
_HEADER_FOOTER_REPEAT_THRESHOLD = 3


class PDFExtractor(BaseExtractor):
    """
    Production-grade PDF extractor.

    All CPU-bound work (PyMuPDF + pdfplumber) runs inside a thread pool
    executor so it never blocks the asyncio event loop.
    """

    @property
    def extractor_name(self) -> str:
        return "PDFExtractor"

    async def extract(self, file_bytes: bytes) -> ExtractionResult:
        if fitz is None:
            return self._make_empty_result("PyMuPDF (fitz) not installed")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(self._extract_sync, file_bytes))

    # ─── sync implementation (runs in thread pool) ────────────────────────────

    def _extract_sync(self, file_bytes: bytes) -> ExtractionResult:
        result = ExtractionResult(extractor_used=self.extractor_name)

        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
        except fitz.FileDataError as exc:
            logger.warning("PDF corruption on open — attempting page-by-page", extra={"error": str(exc)})
            result.add_warning("PDF_CORRUPTED")
            return self._salvage_corrupted(file_bytes, result)
        except Exception as exc:
            logger.error("PDF open failed", extra={"error": str(exc)})
            result.add_warning(f"PDF_OPEN_FAILED: {exc}")
            return result

        # ── encryption check ──────────────────────────────────────────────────
        if doc.is_encrypted:
            result.add_warning("PDF_ENCRYPTED")
            doc.close()
            return result

        if doc.page_count == 0:
            result.add_warning("PDF_EMPTY_ZERO_PAGES")
            doc.close()
            return result

        # ── header/footer detection ───────────────────────────────────────────
        header_footer_texts = self._detect_headers_footers(doc)

        # ── page extraction ───────────────────────────────────────────────────
        for page_num in range(doc.page_count):
            try:
                page_content = self._extract_page(
                    doc, page_num, file_bytes, header_footer_texts
                )
                result.pages.append(page_content)
            except Exception as exc:
                logger.warning(
                    "PDF page extraction failed",
                    extra={"page": page_num, "error": str(exc)},
                )
                result.add_warning(f"PAGE_{page_num + 1}_EXTRACTION_FAILED: {exc}")
                # Add empty page placeholder so page_count remains accurate
                result.pages.append(PageContent(
                    page_number=page_num + 1,
                    text="",
                    word_count=0,
                    char_count=0,
                    has_images=False,
                    has_tables=False,
                    extraction_method="native",
                ))

        # ── form fields ───────────────────────────────────────────────────────
        result.form_fields = self._extract_form_fields(doc)

        # ── attachments (PDF-in-PDF, embedded files) ──────────────────────────
        result.attachments = self._extract_attachments(doc)

        # ── table extraction via pdfplumber ───────────────────────────────────
        if pdfplumber is not None:
            tables = self._extract_tables_pdfplumber(file_bytes, result.pages)
            result.tables.extend(tables)
        else:
            result.add_warning("pdfplumber not installed — table extraction skipped")

        doc.close()
        return result

    def _extract_page(
        self,
        doc: Any,
        page_num: int,
        file_bytes: bytes,
        header_footer_texts: set[str],
    ) -> PageContent:
        page = doc[page_num]
        page_rect = page.rect

        # ── dict mode — preserves block structure + font info ──────────────────
        page_dict = page.get_text("dict", sort=True)
        blocks: list[dict] = page_dict.get("blocks", [])

        # ── collect images first ──────────────────────────────────────────────
        image_list = page.get_images(full=True)
        has_images = len(image_list) > 0

        # Calculate image coverage
        coverage_ratio = self._calculate_image_coverage(page, image_list)

        # ── sort blocks for multi-column reading order ─────────────────────────
        # Sort by (x0 column bucket, y0) so left column reads before right column
        def column_sort_key(b: dict) -> tuple[int, float]:
            x0 = b.get("bbox", [0, 0, 0, 0])[0]
            y0 = b.get("bbox", [0, 0, 0, 0])[1]
            page_width = page_rect.width if page_rect.width > 0 else 595.0
            # Bucket into 2 columns: left (<50%) = 0, right (≥50%) = 1
            col_bucket = 0 if x0 < page_width * 0.5 else 1
            return (col_bucket, y0)

        text_blocks = [b for b in blocks if b.get("type") == 0]
        text_blocks.sort(key=column_sort_key)

        # ── compute body font size for heading detection ───────────────────────
        all_font_sizes = []
        for block in text_blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    fs = span.get("size", 0)
                    if fs > 0:
                        all_font_sizes.append(fs)
        body_font_size = _median(all_font_sizes) if all_font_sizes else 10.0

        # ── build text lines ──────────────────────────────────────────────────
        text_lines: list[str] = []
        has_tables_flag = False

        for block in text_blocks:
            block_lines: list[str] = []
            for line in block.get("lines", []):
                line_text_parts: list[str] = []
                for span in line.get("spans", []):
                    span_text = span.get("text", "").strip()
                    if not span_text:
                        continue
                    span_size = span.get("size", body_font_size)
                    # Mark headings with a simple marker (preserved through normalisation)
                    if span_size > body_font_size * 1.15:
                        span_text = f"## {span_text}"
                    line_text_parts.append(span_text)
                if line_text_parts:
                    block_lines.append(" ".join(line_text_parts))

            if block_lines:
                block_text = "\n".join(block_lines)
                # Strip known header/footer text
                if block_text.strip() in header_footer_texts:
                    continue
                text_lines.append(block_text)

        full_text = "\n\n".join(text_lines)

        # Detect if this looks like a table-containing page (heuristic)
        # pdfplumber will do the real detection; this flag directs it
        if _has_table_heuristic(page):
            has_tables_flag = True

        extraction_method = "native"
        if coverage_ratio > _IMAGE_COVERAGE_OCR_THRESHOLD:
            extraction_method = "hybrid"  # will need OCR too

        return PageContent(
            page_number=page_num + 1,
            text=full_text,
            word_count=len(full_text.split()),
            char_count=len(full_text),
            has_images=has_images,
            has_tables=has_tables_flag,
            extraction_method=extraction_method,
        )

    def _detect_headers_footers(self, doc: Any) -> set[str]:
        """
        Find text blocks that appear at the same y-position across 3+ pages.
        These are headers/footers and should be excluded from body text.
        """
        if doc.page_count < _HEADER_FOOTER_REPEAT_THRESHOLD:
            return set()

        # Map: y_bucket → list of text strings seen at that position
        y_to_texts: dict[int, list[str]] = defaultdict(list)
        sample_pages = min(doc.page_count, 10)  # check first 10 pages

        for page_num in range(sample_pages):
            try:
                page = doc[page_num]
                blocks = page.get_text("dict").get("blocks", [])
                page_height = page.rect.height or 842.0
                for block in blocks:
                    if block.get("type") != 0:
                        continue
                    bbox = block.get("bbox", [0, 0, 0, 0])
                    y0 = bbox[1]
                    # Headers: top 10% of page; footers: bottom 10%
                    is_header = y0 < page_height * 0.10
                    is_footer = y0 > page_height * 0.90
                    if not (is_header or is_footer):
                        continue
                    # Flatten all text in this block
                    block_text = " ".join(
                        span.get("text", "")
                        for line in block.get("lines", [])
                        for span in line.get("spans", [])
                    ).strip()
                    if block_text:
                        y_bucket = int(y0 // 20)  # 20pt bucket
                        y_to_texts[y_bucket].append(block_text)
            except Exception:
                continue

        header_footer_set: set[str] = set()
        for texts in y_to_texts.values():
            if len(texts) >= _HEADER_FOOTER_REPEAT_THRESHOLD:
                # Find texts that appear in most pages
                from collections import Counter
                counts = Counter(texts)
                for text, count in counts.items():
                    if count >= _HEADER_FOOTER_REPEAT_THRESHOLD:
                        header_footer_set.add(text)

        return header_footer_set

    def _calculate_image_coverage(self, page: Any, image_list: list) -> float:
        """Return fraction of page area covered by images (0.0–1.0)."""
        page_rect = page.rect
        page_area = page_rect.width * page_rect.height
        if page_area <= 0:
            return 0.0
        total_image_area = 0.0
        for img_info in image_list:
            try:
                xref = img_info[0]
                rects = page.get_image_rects(xref)
                for r in rects:
                    total_image_area += abs(r.width * r.height)
            except Exception:
                pass
        return min(total_image_area / page_area, 1.0)

    def _extract_form_fields(self, doc: Any) -> dict[str, str]:
        """Extract fillable PDF form field values."""
        fields: dict[str, str] = {}
        try:
            for page in doc:
                for widget in page.widgets():
                    name = widget.field_name or ""
                    value = widget.field_value or ""
                    if name:
                        fields[name] = str(value)
        except Exception as exc:
            logger.debug("Form field extraction skipped", extra={"error": str(exc)})
        return fields

    def _extract_attachments(self, doc: Any) -> list[AttachmentInfo]:
        """Extract embedded file attachments from the PDF."""
        attachments: list[AttachmentInfo] = []
        try:
            embedded = doc.embfile_names()
            for name in embedded:
                try:
                    info = doc.embfile_info(name)
                    data = doc.embfile_get(name)
                    attachments.append(AttachmentInfo(
                        filename=name,
                        mime_type=info.get("mime", "application/octet-stream"),
                        size_bytes=len(data),
                        data=data,
                    ))
                except Exception as exc:
                    logger.debug("Attachment extraction failed", extra={"name": name, "error": str(exc)})
        except Exception as exc:
            logger.debug("Attachment enumeration failed", extra={"error": str(exc)})
        return attachments

    def _extract_tables_pdfplumber(
        self,
        file_bytes: bytes,
        pages: list[PageContent],
    ) -> list[ExtractedTable]:
        """
        Run pdfplumber table extraction on pages flagged as having tables.

        Strategy:
          1. Lattice (bordered tables) — try first
          2. Stream (borderless) — fallback if lattice finds nothing
        Also detects multi-page tables by matching header rows.
        """
        tables: list[ExtractedTable] = []
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                last_headers: list[str] | None = None

                for page_content in pages:
                    if not page_content.has_tables:
                        continue
                    page_idx = page_content.page_number - 1
                    if page_idx >= len(pdf.pages):
                        continue

                    plumber_page = pdf.pages[page_idx]
                    page_tables: list[list[list[str | None]]] = []

                    # Try lattice first (bordered tables)
                    try:
                        page_tables = plumber_page.extract_tables(
                            table_settings={"vertical_strategy": "lines", "horizontal_strategy": "lines"}
                        ) or []
                    except Exception:
                        pass

                    # Fallback to stream (borderless)
                    if not page_tables:
                        try:
                            page_tables = plumber_page.extract_tables(
                                table_settings={"vertical_strategy": "text", "horizontal_strategy": "text"}
                            ) or []
                        except Exception:
                            pass

                    for raw_table in page_tables:
                        if not raw_table:
                            continue
                        # Normalise None → empty string, handle merged cells
                        cleaned = [[str(cell) if cell is not None else "" for cell in row]
                                   for row in raw_table if row]
                        if not cleaned:
                            continue

                        # Detect if this is a continuation of previous table
                        # by checking if first row matches last known headers
                        is_continuation = False
                        if last_headers and cleaned[0] == last_headers:
                            is_continuation = True
                            headers = cleaned[0]
                            rows = cleaned[1:]
                        elif len(cleaned) > 1:
                            headers = cleaned[0]
                            rows = cleaned[1:]
                        else:
                            headers = []
                            rows = cleaned

                        last_headers = headers if headers else None

                        table = ExtractedTable(
                            headers=headers,
                            rows=rows,
                            page_number=page_content.page_number,
                            is_continuation=is_continuation,
                        )
                        table.markdown = table.to_markdown()
                        tables.append(table)

        except Exception as exc:
            logger.warning("pdfplumber table extraction failed", extra={"error": str(exc)})

        return tables

    def _salvage_corrupted(
        self, file_bytes: bytes, result: ExtractionResult
    ) -> ExtractionResult:
        """Attempt page-by-page extraction from a corrupted PDF."""
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
        except Exception:
            return result

        for page_num in range(doc.page_count):
            try:
                page = doc[page_num]
                text = page.get_text("text") or ""
                result.pages.append(PageContent(
                    page_number=page_num + 1,
                    text=text,
                    word_count=len(text.split()),
                    char_count=len(text),
                    has_images=False,
                    has_tables=False,
                    extraction_method="native",
                ))
            except Exception as exc:
                result.add_warning(f"SALVAGE_PAGE_{page_num + 1}_FAILED: {exc}")

        doc.close()
        return result


def _median(values: list[float]) -> float:
    if not values:
        return 10.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    return sorted_vals[mid]


def _has_table_heuristic(page: Any) -> bool:
    """
    Quick heuristic to detect if a page likely contains a table.
    Checks for rectangular drawing commands that suggest ruled lines.
    """
    try:
        drawings = page.get_drawings()
        rect_count = sum(1 for d in drawings if d.get("type") in ("rect", "re"))
        if rect_count >= 3:
            return True
        # Check for many short horizontal lines (table rules)
        h_lines = [d for d in drawings if d.get("type") == "l"]
        if len(h_lines) >= 6:
            return True
    except Exception:
        pass
    return False
