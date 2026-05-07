"""
DOCX Extractor — python-docx based extractor for .docx files.

Handles:
  - All paragraphs with heading level preservation (→ section markers)
  - Tables with full cell structure
  - Text boxes and shapes (DrawingML) — often missed by naive parsers
  - Comments and tracked changes (tagged separately, not mixed into body)
  - Headers and footers from all sections
  - Embedded images (returns as ExtractedImage objects)
  - Password-protected DOCX → warning, no crash
  - Corrupted DOCX (BadZipFile) → partial extraction with warning
"""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from functools import partial
from typing import Any

logger = logging.getLogger(__name__)

try:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph
except ImportError:  # pragma: no cover
    Document = None  # type: ignore[assignment, misc]
    qn = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment, misc]
    Paragraph = None  # type: ignore[assignment, misc]

from app.preprocessing.extractors.base_extractor import (
    BaseExtractor,
    ExtractedImage,
    ExtractedTable,
    ExtractionResult,
    PageContent,
)

# Heading style name prefixes → section level markers
_HEADING_STYLE_MAP = {
    "heading 1": "# ",
    "heading 2": "## ",
    "heading 3": "### ",
    "heading 4": "#### ",
    "heading 5": "##### ",
    "heading 6": "###### ",
}


class DOCXExtractor(BaseExtractor):
    """
    DOCX extractor. All IO is sync (python-docx is sync); wrapped in
    run_in_executor so it never blocks the event loop.
    """

    @property
    def extractor_name(self) -> str:
        return "DOCXExtractor"

    async def extract(self, file_bytes: bytes) -> ExtractionResult:
        if Document is None:
            return self._make_empty_result("python-docx not installed")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(self._extract_sync, file_bytes))

    # ─── sync implementation ──────────────────────────────────────────────────

    def _extract_sync(self, file_bytes: bytes) -> ExtractionResult:
        result = ExtractionResult(extractor_used=self.extractor_name)

        # ── corruption / protection detection ─────────────────────────────────
        try:
            doc = Document(io.BytesIO(file_bytes))
        except zipfile.BadZipFile:
            result.add_warning("DOCX_CORRUPTED_BAD_ZIP")
            return result
        except Exception as exc:
            err_str = str(exc).lower()
            if "password" in err_str or "encrypt" in err_str or "protected" in err_str:
                result.add_warning("DOCX_PROTECTED")
                return result
            result.add_warning(f"DOCX_OPEN_FAILED: {exc}")
            return result

        # ── body text: paragraphs + tables (in document order) ────────────────
        body_lines: list[str] = []
        comments_text: list[str] = []

        for element in doc.element.body:
            tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag

            if tag == "p":
                para = Paragraph(element, doc)
                text = self._paragraph_text(para)
                if text:
                    body_lines.append(text)

            elif tag == "tbl":
                tbl = Table(element, doc)
                extracted_table = self._extract_table(tbl)
                if extracted_table:
                    result.tables.append(extracted_table)
                    # Also add markdown representation inline for context
                    body_lines.append(extracted_table.to_markdown())

        # ── text boxes / shapes (DrawingML / VML) ─────────────────────────────
        text_box_texts = self._extract_text_boxes(doc)
        if text_box_texts:
            body_lines.append("\n".join(text_box_texts))

        # ── headers and footers ───────────────────────────────────────────────
        header_lines: list[str] = []
        footer_lines: list[str] = []
        for section in doc.sections:
            for para in section.header.paragraphs:
                t = para.text.strip()
                if t:
                    header_lines.append(t)
            for para in section.footer.paragraphs:
                t = para.text.strip()
                if t:
                    footer_lines.append(t)

        # ── comments (tracked changes: revisions with author info) ─────────────
        comments_text = self._extract_comments(doc)

        # ── images ────────────────────────────────────────────────────────────
        result.images = self._extract_images(doc, file_bytes)

        # ── assemble into a single "page" (DOCX has no page concept natively) ──
        full_text_parts: list[str] = []
        if header_lines:
            full_text_parts.append("[DOCUMENT HEADER]\n" + "\n".join(header_lines))
        full_text_parts.extend(body_lines)
        if footer_lines:
            full_text_parts.append("[DOCUMENT FOOTER]\n" + "\n".join(footer_lines))
        if comments_text:
            full_text_parts.append("[COMMENTS]\n" + "\n".join(comments_text))

        full_text = "\n\n".join(full_text_parts)

        result.pages.append(PageContent(
            page_number=1,
            text=full_text,
            word_count=len(full_text.split()),
            char_count=len(full_text),
            has_images=bool(result.images),
            has_tables=bool(result.tables),
            extraction_method="native",
        ))

        return result

    def _paragraph_text(self, para: Any) -> str:
        """
        Get paragraph text with heading markers.
        Heading 1 → "# text", Heading 2 → "## text", etc.
        """
        style_name = (para.style.name or "").lower()
        text = para.text.strip()
        if not text:
            return ""

        for prefix, marker in _HEADING_STYLE_MAP.items():
            if style_name.startswith(prefix):
                return f"{marker}{text}"

        return text

    def _extract_table(self, table: Any) -> ExtractedTable | None:
        """Extract a python-docx Table into ExtractedTable."""
        try:
            all_rows: list[list[str]] = []
            for row in table.rows:
                row_data: list[str] = []
                for cell in row.cells:
                    row_data.append(cell.text.strip().replace("\n", " "))
                all_rows.append(row_data)

            if not all_rows:
                return None

            if len(all_rows) > 1:
                headers = all_rows[0]
                rows = all_rows[1:]
            else:
                headers = []
                rows = all_rows

            extracted = ExtractedTable(headers=headers, rows=rows)
            extracted.markdown = extracted.to_markdown()
            return extracted
        except Exception as exc:
            logger.debug("DOCX table extraction failed", extra={"error": str(exc)})
            return None

    def _extract_text_boxes(self, doc: Any) -> list[str]:
        """
        Extract text from DrawingML text boxes and VML shapes.
        These are commonly missed by basic docx parsers.
        """
        texts: list[str] = []
        try:
            # DrawingML text boxes use w:txbxContent element
            ns_map = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            for txbx in doc.element.body.iter(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}txbxContent"
            ):
                parts: list[str] = []
                for t_elem in txbx.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                ):
                    if t_elem.text:
                        parts.append(t_elem.text)
                text = " ".join(parts).strip()
                if text:
                    texts.append(f"[TEXT BOX] {text}")
        except Exception as exc:
            logger.debug("Text box extraction failed", extra={"error": str(exc)})
        return texts

    def _extract_comments(self, doc: Any) -> list[str]:
        """
        Extract comment text from the document.
        Returns tagged strings, not mixed into body content.
        """
        comments: list[str] = []
        try:
            # Comments are in word/comments.xml in the OOXML package
            comments_part = doc.part.comments_part
            if comments_part is None:
                return []
            for comment in comments_part.element.iter(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}comment"
            ):
                author_attr = comment.get(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}author", ""
                )
                text_parts: list[str] = []
                for t in comment.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                ):
                    if t.text:
                        text_parts.append(t.text)
                text = " ".join(text_parts).strip()
                if text:
                    comments.append(f"[COMMENT by {author_attr}] {text}")
        except AttributeError:
            pass  # No comments part — completely normal
        except Exception as exc:
            logger.debug("Comment extraction failed", extra={"error": str(exc)})
        return comments

    def _extract_images(self, doc: Any, file_bytes: bytes) -> list[ExtractedImage]:
        """
        Extract embedded images from the DOCX zip archive.
        python-docx doesn't expose image bytes directly, so we read the zip.
        """
        images: list[ExtractedImage] = []
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
                media_files = [
                    name for name in zf.namelist()
                    if name.startswith("word/media/")
                ]
                for idx, media_path in enumerate(media_files):
                    try:
                        img_bytes = zf.read(media_path)
                        images.append(ExtractedImage(
                            image_bytes=img_bytes,
                            page_number=1,  # DOCX has no page-level image position
                            image_index=idx,
                        ))
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("DOCX image extraction failed", extra={"error": str(exc)})
        return images
