"""
CSV Extractor — handles CSV, TSV, and pipe-delimited tabular files.

Features:
  - Auto-detect delimiter (comma, tab, semicolon, pipe)
  - Auto-detect encoding (UTF-8, UTF-8-BOM, Latin-1, CP1252) via chardet
  - Multi-line cell handling
  - Header row auto-detection
  - Markdown + structured (list of dicts) output
  - PII column flagging (name, patient, address, phone, email, aadhaar, dob)
  - Inconsistent column counts: log warning, don't crash
  - Cap at 10,000 rows; batch-note if larger
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from functools import partial

logger = logging.getLogger(__name__)

try:
    import chardet
except ImportError:  # pragma: no cover
    chardet = None  # type: ignore[assignment]

from app.preprocessing.extractors.base_extractor import (
    BaseExtractor,
    ExtractedTable,
    ExtractionResult,
    PageContent,
)

_MAX_ROWS = 10_000
_PII_COLUMN_NAMES = {
    "name", "patient", "patient_name", "patientname",
    "address", "addr", "phone", "mobile", "telephone",
    "email", "email_address", "emailaddress",
    "aadhaar", "aadhar", "aadhaar_number",
    "dob", "date_of_birth", "dateofbirth", "birthdate",
    "nid", "national_id",
}


class CSVExtractor(BaseExtractor):
    """
    CSV / TSV extractor with PII column detection.
    """

    @property
    def extractor_name(self) -> str:
        return "CSVExtractor"

    async def extract(self, file_bytes: bytes) -> ExtractionResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(self._extract_sync, file_bytes))

    def _extract_sync(self, file_bytes: bytes) -> ExtractionResult:
        result = ExtractionResult(extractor_used=self.extractor_name)

        # ── encoding detection ─────────────────────────────────────────────────
        text = self._decode_bytes(file_bytes, result)

        # ── delimiter detection ────────────────────────────────────────────────
        delimiter = self._detect_delimiter(text)

        # ── parse CSV ─────────────────────────────────────────────────────────
        reader = csv.reader(io.StringIO(text), delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
        all_rows: list[list[str]] = []
        inconsistent_rows: list[int] = []
        max_cols = 0
        truncated = False

        for row_idx, row in enumerate(reader):
            if row_idx >= _MAX_ROWS:
                truncated = True
                result.add_warning(
                    f"CSV_TRUNCATED: file exceeds {_MAX_ROWS} rows — only first {_MAX_ROWS} processed"
                )
                break
            if not any(cell.strip() for cell in row):
                continue  # skip blank rows
            max_cols = max(max_cols, len(row))
            all_rows.append(row)

        if not all_rows:
            result.add_warning("CSV_EMPTY_NO_ROWS")
            result.pages.append(PageContent(
                page_number=1,
                text="",
                word_count=0,
                char_count=0,
                has_images=False,
                has_tables=False,
                extraction_method="native",
            ))
            return result

        # ── check for inconsistent column counts ───────────────────────────────
        expected_cols = len(all_rows[0]) if all_rows else max_cols
        for idx, row in enumerate(all_rows):
            if len(row) != expected_cols:
                inconsistent_rows.append(idx)
                # Pad or truncate to normalise
                if len(row) < expected_cols:
                    all_rows[idx] = row + [""] * (expected_cols - len(row))
                else:
                    all_rows[idx] = row[:expected_cols]

        if inconsistent_rows:
            result.add_warning(
                f"CSV_INCONSISTENT_COLUMNS: {len(inconsistent_rows)} rows had wrong column count (padded/truncated)"
            )

        # ── header row ────────────────────────────────────────────────────────
        if len(all_rows) > 1:
            headers = [h.strip() for h in all_rows[0]]
            data_rows = [[cell.strip() for cell in row] for row in all_rows[1:]]
        else:
            headers = [f"col_{i}" for i in range(len(all_rows[0]))]
            data_rows = [[cell.strip() for cell in row] for row in all_rows]

        # ── PII column detection ──────────────────────────────────────────────
        pii_columns: list[str] = []
        for header in headers:
            normalised_header = header.lower().strip().replace(" ", "_").replace("-", "_")
            if normalised_header in _PII_COLUMN_NAMES:
                pii_columns.append(header)

        if pii_columns:
            result.add_warning(f"PII_COLUMNS_DETECTED: {', '.join(pii_columns)}")

        # ── build ExtractedTable ──────────────────────────────────────────────
        table = ExtractedTable(headers=headers, rows=data_rows, page_number=1)
        table.markdown = table.to_markdown()
        result.tables.append(table)

        # ── build text page (markdown + summary) ──────────────────────────────
        summary_parts = [
            f"CSV file: {len(data_rows)} data rows × {len(headers)} columns.",
        ]
        if truncated:
            summary_parts.append(f"NOTE: File was truncated to first {_MAX_ROWS} rows.")
        if pii_columns:
            summary_parts.append(f"PII columns detected: {', '.join(pii_columns)}.")
        if inconsistent_rows:
            summary_parts.append(f"WARNING: {len(inconsistent_rows)} rows had inconsistent column counts.")

        summary_parts.append("\nColumn names: " + ", ".join(headers))
        summary_parts.append("")
        summary_parts.append(table.markdown)

        full_text = "\n".join(summary_parts)

        result.pages.append(PageContent(
            page_number=1,
            text=full_text,
            word_count=len(full_text.split()),
            char_count=len(full_text),
            has_images=False,
            has_tables=True,
            extraction_method="native",
        ))

        return result

    def _decode_bytes(self, file_bytes: bytes, result: ExtractionResult) -> str:
        """Try encodings in order; use chardet as last resort."""
        # Strip UTF-8 BOM if present
        if file_bytes.startswith(b"\xef\xbb\xbf"):
            file_bytes = file_bytes[3:]

        for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                return file_bytes.decode(encoding)
            except (UnicodeDecodeError, ValueError):
                continue

        # chardet fallback
        if chardet is not None:
            detection = chardet.detect(file_bytes)
            detected_enc = detection.get("encoding") or "utf-8"
            try:
                return file_bytes.decode(detected_enc, errors="replace")
            except Exception:
                pass

        result.add_warning("CSV_ENCODING_FALLBACK_USED")
        return file_bytes.decode("utf-8", errors="replace")

    def _detect_delimiter(self, text: str) -> str:
        """
        Auto-detect CSV delimiter by counting occurrences in the first line.
        Returns the most common of: comma, tab, semicolon, pipe.
        """
        first_line = text.split("\n")[0] if "\n" in text else text[:500]
        candidates = {
            ",": first_line.count(","),
            "\t": first_line.count("\t"),
            ";": first_line.count(";"),
            "|": first_line.count("|"),
        }
        best = max(candidates, key=lambda k: candidates[k])
        # Default to comma if all counts are 0
        return best if candidates[best] > 0 else ","
