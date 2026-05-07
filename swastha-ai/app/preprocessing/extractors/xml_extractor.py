"""
XML Extractor — XXE-safe XML parser supporting HL7 FHIR and generic regulatory XML.

Security: Uses defusedxml to prevent XXE (XML External Entity) injection attacks.
NEVER use stdlib xml.etree or lxml without disabling external entities here.

Handles:
  - HL7 FHIR R4 resources (Patient, Observation, MedicationStatement)
  - Generic CDSCO regulatory XML (key-value flattening)
  - Malformed XML (falls back to html.parser recovery)
  - Attribute values preserved (often contain regulatory codes)
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

logger = logging.getLogger(__name__)

try:
    import defusedxml.ElementTree as ET
    from defusedxml import ElementTree as SafeET
except ImportError:  # pragma: no cover
    ET = None  # type: ignore[assignment]
    SafeET = None  # type: ignore[assignment]

from app.preprocessing.extractors.base_extractor import (
    BaseExtractor,
    ExtractionResult,
    PageContent,
)

# HL7 FHIR namespace prefixes
_FHIR_NAMESPACES = {
    "fhir": "http://hl7.org/fhir",
    "xhtml": "http://www.w3.org/1999/xhtml",
}

_FHIR_ROOT_TAGS = {
    "Bundle", "Patient", "Observation", "MedicationStatement",
    "MedicationRequest", "Condition", "AllergyIntolerance",
    "AdverseEvent", "Device", "Practitioner", "Organization",
}


class XMLExtractor(BaseExtractor):
    """
    XXE-safe XML extractor with FHIR detection.
    """

    @property
    def extractor_name(self) -> str:
        return "XMLExtractor"

    async def extract(self, file_bytes: bytes) -> ExtractionResult:
        if ET is None:
            return self._make_empty_result("defusedxml not installed")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(self._extract_sync, file_bytes))

    def _extract_sync(self, file_bytes: bytes) -> ExtractionResult:
        result = ExtractionResult(extractor_used=self.extractor_name)

        xml_text: str
        try:
            xml_text = file_bytes.decode("utf-8", errors="replace")
        except Exception:
            xml_text = file_bytes.decode("latin-1", errors="replace")

        # ── primary parse: defusedxml ─────────────────────────────────────────
        try:
            root = ET.fromstring(xml_text.encode("utf-8"))
        except Exception as exc:
            logger.warning("defusedxml parse failed — trying html.parser recovery",
                           extra={"error": str(exc)})
            result.add_warning(f"XML_PARSE_ERROR_RECOVERY_ATTEMPTED: {exc}")
            text = self._recover_with_html_parser(xml_text)
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

        # ── detect format ─────────────────────────────────────────────────────
        root_local = root.tag.split("}")[-1] if "}" in root.tag else root.tag
        ns_uri = root.tag.split("}")[0].lstrip("{") if "}" in root.tag else ""

        is_fhir = (
            root_local in _FHIR_ROOT_TAGS
            or "hl7.org/fhir" in ns_uri
        )

        if is_fhir:
            text = self._extract_fhir(root)
            result.add_warning("FORMAT_DETECTED_HL7_FHIR")
        else:
            text = self._flatten_xml(root)

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

    def _extract_fhir(self, root: Any) -> str:
        """
        Extract human-readable text from HL7 FHIR XML.
        Pulls Patient, Observation, Medication resources into key-value lines.
        """
        lines: list[str] = []
        lines.append("[HL7 FHIR DOCUMENT]")

        root_local = root.tag.split("}")[-1] if "}" in root.tag else root.tag
        lines.append(f"Resource Type: {root_local}")

        # Walk all elements and emit meaningful key-value pairs
        # Skip internal FHIR metadata elements
        _SKIP_TAGS = {"text", "div", "status"}
        seen: set[str] = set()

        for elem in root.iter():
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local in _SKIP_TAGS:
                continue

            # Emit attribute values (contain regulatory codes)
            for attr_name, attr_val in elem.attrib.items():
                attr_local = attr_name.split("}")[-1] if "}" in attr_name else attr_name
                if attr_local in ("value", "system", "code", "display", "url"):
                    key = f"{local}.{attr_local}"
                    if key not in seen and attr_val:
                        lines.append(f"{key}: {attr_val}")
                        seen.add(key)

            # Emit text content
            if elem.text and elem.text.strip():
                key = local
                val = elem.text.strip()
                if key not in seen:
                    lines.append(f"{key}: {val}")
                    seen.add(key)

        return "\n".join(lines)

    def _flatten_xml(self, root: Any, prefix: str = "", max_depth: int = 12) -> str:
        """
        Recursively flatten an XML tree into key: value lines.
        Attribute values are always included (they contain regulatory codes).
        """
        lines: list[str] = []
        self._flatten_element(root, prefix, lines, depth=0, max_depth=max_depth)
        return "\n".join(lines)

    def _flatten_element(
        self,
        elem: Any,
        prefix: str,
        lines: list[str],
        depth: int,
        max_depth: int,
    ) -> None:
        if depth > max_depth:
            return

        local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        current_key = f"{prefix}.{local}" if prefix else local

        # Emit attributes
        for attr_name, attr_val in elem.attrib.items():
            attr_local = attr_name.split("}")[-1] if "}" in attr_name else attr_name
            if attr_val and attr_val.strip():
                lines.append(f"{current_key}[@{attr_local}]: {attr_val.strip()}")

        # Emit text content
        if elem.text and elem.text.strip():
            lines.append(f"{current_key}: {elem.text.strip()}")

        # Recurse into children
        for child in elem:
            self._flatten_element(child, current_key, lines, depth + 1, max_depth)

    def _recover_with_html_parser(self, xml_text: str) -> str:
        """
        Fallback: extract all text content from malformed XML using html.parser.
        This is a last-resort for well-formed-but-invalid XML.
        """
        from html.parser import HTMLParser

        class _TextCollector(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.texts: list[str] = []

            def handle_data(self, data: str) -> None:
                stripped = data.strip()
                if stripped:
                    self.texts.append(stripped)

        parser = _TextCollector()
        try:
            parser.feed(xml_text)
        except Exception:
            pass
        return "\n".join(parser.texts)
