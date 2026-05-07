"""
Structured Metadata Extractor.

Extracts key fields using regex and keyword proximity from the normalised text.
Deterministic and fast — no LLMs used here (avoids hallucination risk).
Extracted metadata is returned as a structured JSONB-ready dict.
"""

from __future__ import annotations

import re
from typing import Any

# ── Universal Patterns ─────────────────────────────────────────────────────────

_VERSION_PATTERNS = [
    re.compile(r"\b(?:v|version)\s*([0-9]+\.[0-9]+(?:\.[0-9]+)?)\b", re.IGNORECASE),
    re.compile(r"\b(?:rev|revision)\s*([0-9]+)\b", re.IGNORECASE),
]

_APP_NUMBER_PATTERNS = [
    re.compile(r"\b(?:application|app|file)\s*(?:no|num|number)?\s*[:.-]?\s*([A-Z0-9-]{6,15})\b", re.IGNORECASE),
]

# ISO 8601 date lookahead (dates were normalised in previous step)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


# ── Submission Type Specific Keywords ────────────────────────────────────────

_DRUG_KEYWORDS = {
    "applicant": re.compile(r"(?:applicant|sponsor|company)[\s\w]*?:\s*([A-Za-z0-9\s.,&]{5,50})(?:\n|$)"),
    "inn": re.compile(r"(?:generic name|inn|active ingredient)[\s\w]*?:\s*([A-Za-z\s-]{3,40})(?:\n|$)"),
    "brand": re.compile(r"(?:brand name|trade name)[\s\w]*?:\s*([A-Za-z0-9\s-]{3,40})(?:\n|$)"),
    "indication": re.compile(r"(?:proposed indication|indication|therapeutic use)[\s\w]*?:\s*([A-Za-z0-9\s.,-]{10,150})(?:\n|$)"),
}

_SAE_KEYWORDS = {
    "event_type": re.compile(r"(?:event|reaction)[\s\w]*?:\s*(death|hospitali[zs]ation|disability|life[- ]threatening|anomaly)", re.IGNORECASE),
    "suspect_drug": re.compile(r"(?:suspect(?:ed)? drug|medication)[\s\w]*?:\s*([A-Za-z0-9\s-]{3,40})(?:\n|$)"),
    "report_type": re.compile(r"(?:report type)[\s\w]*?:\s*(initial|follow[- ]up|final)", re.IGNORECASE),
}

_DEVICE_KEYWORDS = {
    "device_name": re.compile(r"(?:device name|product name)[\s\w]*?:\s*([A-Za-z0-9\s-]{3,40})(?:\n|$)"),
    "device_class": re.compile(r"(?:device class|class of device)[\s\w]*?:\s*class\s*([A-D])", re.IGNORECASE),
    "manufacturer": re.compile(r"(?:manufacturer)[\s\w]*?:\s*([A-Za-z0-9\s.,&]{5,50})(?:\n|$)"),
}


class MetadataExtractor:
    """Extracts structured metadata from plain text."""

    def extract(
        self,
        text: str,
        submission_type: str,
        original_filename: str,
        page_count: int,
        word_count: int,
        doc_properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Run all metadata extractors.
        Returns a dict suitable for PostgreSQL JSONB.
        """
        doc_properties = doc_properties or {}
        meta: dict[str, Any] = {}

        # 1. Universal basic stats
        meta["page_count"] = page_count
        meta["word_count"] = word_count

        # 2. Document properties (if available from PyMuPDF/python-docx)
        meta["creation_date"] = doc_properties.get("creationDate")
        meta["modification_date"] = doc_properties.get("modDate")
        meta["author"] = doc_properties.get("author")

        # 3. Universal text extraction
        meta["document_title"] = self._extract_title(text, original_filename)
        meta["document_version"] = self._extract_version(text)
        meta["application_number"] = self._extract_application_number(text)
        meta["submission_date_mentioned"] = self._extract_first_date(text)

        # 4. Type-specific extraction
        if submission_type == "drug":
            meta.update(self._extract_drug_metadata(text))
        elif submission_type == "sae":
            meta.update(self._extract_sae_metadata(text))
        elif submission_type == "medical_device":
            meta.update(self._extract_device_metadata(text))

        # Filter out None/empty values
        return {k: v for k, v in meta.items() if v is not None and v != ""}

    def _extract_title(self, text: str, original_filename: str) -> str:
        """
        Attempt to find the first real heading.
        Fallback to filename if nothing found.
        """
        # Look for the first line that looks like a heading (marked by normaliser)
        for line in text.split("\n")[:20]:
            if line.startswith("# "):
                title = line.lstrip("# ").strip()
                if 5 < len(title) < 200:
                    return title

        # Fallback to filename (strip extension)
        name = original_filename.rsplit(".", 1)[0]
        return name.replace("_", " ").replace("-", " ")

    def _extract_version(self, text: str) -> str | None:
        """Search first 2000 chars for a version string."""
        head = text[:2000]
        for pattern in _VERSION_PATTERNS:
            match = pattern.search(head)
            if match:
                return match.group(1)
        return None

    def _extract_application_number(self, text: str) -> str | None:
        """Search first 2000 chars for an application number."""
        head = text[:2000]
        for pattern in _APP_NUMBER_PATTERNS:
            match = pattern.search(head)
            if match:
                return match.group(1).upper()
        return None

    def _extract_first_date(self, text: str) -> str | None:
        """Return the first ISO date found in the text."""
        match = _ISO_DATE_RE.search(text[:2000])
        if match:
            return match.group(1)
        return None

    def _extract_drug_metadata(self, text: str) -> dict[str, str]:
        """Regex extractions for drug applications."""
        return self._run_patterns(text[:5000], _DRUG_KEYWORDS)

    def _extract_sae_metadata(self, text: str) -> dict[str, str]:
        """Regex extractions for Serious Adverse Event reports."""
        return self._run_patterns(text[:5000], _SAE_KEYWORDS)

    def _extract_device_metadata(self, text: str) -> dict[str, str]:
        """Regex extractions for medical device submissions."""
        return self._run_patterns(text[:5000], _DEVICE_KEYWORDS)

    def _run_patterns(self, text: str, patterns: dict[str, re.Pattern]) -> dict[str, str]:
        """Run a dict of regex patterns against text, returning matched groups."""
        results: dict[str, str] = {}
        for key, pattern in patterns.items():
            match = pattern.search(text)
            if match:
                val = match.group(1).strip()
                if val:
                    results[key] = val
        return results
