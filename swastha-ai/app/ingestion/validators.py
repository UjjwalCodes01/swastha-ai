"""
File validators for the SwasthaAI ingestion layer.

Each validator is a standalone function returning ValidationResult.
Validators are deterministic and have no side effects — they only read
the provided bytes and return a pass/fail result with a reason string.

Validators implemented:
1. validate_mime_by_magic_bytes — libmagic-based MIME detection
2. validate_file_size — size limit enforcement
3. validate_zip_bomb — compressed ratio check
4. validate_pdf_not_encrypted — PyMuPDF encryption check
5. validate_filename — path traversal and null byte detection
6. validate_xml_not_xxe — XML external entity injection detection
"""

from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Result Type ───────────────────────────────────────────────────────────────


@dataclass
class ValidationResult:
    """
    Result of a single validation check.

    Attributes:
        passed: True if the file passed the validation.
        reason: Human-readable explanation. On failure, describes why it failed.
    """

    passed: bool
    reason: str


# ── MIME Type Configuration ───────────────────────────────────────────────────

# Maps python-magic detected MIME types to canonical allowed types.
# Multiple magic-detected variants can map to the same canonical type.
_MAGIC_TO_CANONICAL: dict[str, str] = {
    "application/pdf": "application/pdf",
    # DOCX (ZIP-based Office XML)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/zip": "application/zip",
    "application/x-zip-compressed": "application/zip",
    "application/x-zip": "application/zip",
    # XML variants
    "application/xml": "application/xml",
    "text/xml": "application/xml",
    # CSV
    "text/csv": "text/csv",
    # Plain text — includes CSV without BOM, JSON blobs, and plain text docs.
    # We accept text/plain as its own type; downstream processing will handle detection.
    "text/plain": "text/plain",
    # JSON
    "application/json": "application/json",
    "text/json": "application/json",
}

# Max ratio of uncompressed-to-compressed content in a zip (zip bomb detection)
_MAX_ZIP_RATIO = 100

# Max uncompressed size when checking zip bombs (1GB safety ceiling)
_MAX_ZIP_UNCOMPRESSED_BYTES = 1_073_741_824


# ── Validator 1: MIME Type by Magic Bytes ─────────────────────────────────────


def validate_mime_by_magic_bytes(
    file_bytes: bytes,
    allowed_mime_types: list[str],
    claimed_mime_type: str | None = None,
) -> ValidationResult:
    """
    Verify the file's actual type using libmagic (python-magic).

    This is resistant to MIME type spoofing — a malicious user cannot
    rename a .exe to .pdf and bypass this check.

    If `claimed_mime_type` is provided, also checks that it matches
    the detected type.
    """
    try:
        import magic  # type: ignore[import]
        detected_mime = magic.from_buffer(file_bytes[:8192], mime=True)
    except ImportError:
        logger.warning("python-magic not available — skipping magic byte validation")
        return ValidationResult(passed=True, reason="python-magic not available; skipped")
    except Exception as exc:
        return ValidationResult(passed=False, reason=f"MIME detection error: {exc}")

    canonical = _MAGIC_TO_CANONICAL.get(detected_mime)
    if canonical is None:
        return ValidationResult(
            passed=False,
            reason=f"Detected MIME type '{detected_mime}' is not in the allowlist",
        )

    if canonical not in allowed_mime_types:
        return ValidationResult(
            passed=False,
            reason=f"Detected MIME type '{canonical}' is not permitted for upload",
        )

    # Check for spoofing: claimed MIME doesn't match detected
    if claimed_mime_type and claimed_mime_type != "application/octet-stream":
        claimed_canonical = _MAGIC_TO_CANONICAL.get(claimed_mime_type, claimed_mime_type)
        if canonical != claimed_canonical:
            # Allow text/plain detected when the claimed type is also text-based.
            # Magic cannot reliably distinguish JSON, CSV, and XML from plain text.
            text_based = {"text/plain", "text/csv", "application/json", "application/xml"}
            if not (canonical in text_based and claimed_canonical in text_based):
                return ValidationResult(
                    passed=False,
                    reason=(
                        f"MIME type mismatch: file content is '{canonical}' "
                        f"but Content-Type claims '{claimed_mime_type}'"
                    ),
                )

    return ValidationResult(passed=True, reason=f"MIME type '{canonical}' is allowed")


# ── Validator 2: File Size ────────────────────────────────────────────────────


def validate_file_size(file_bytes: bytes, max_mb: int) -> ValidationResult:
    """
    Verify the file size does not exceed the configured maximum.

    The limit is enforced on the raw bytes, not the Content-Length header,
    to prevent HTTP header spoofing attacks.
    """
    size_bytes = len(file_bytes)
    max_bytes = max_mb * 1024 * 1024

    if size_bytes > max_bytes:
        size_mb = size_bytes / (1024 * 1024)
        return ValidationResult(
            passed=False,
            reason=f"File size {size_mb:.1f}MB exceeds the {max_mb}MB limit",
        )

    return ValidationResult(
        passed=True,
        reason=f"File size {size_bytes / 1024:.1f}KB is within the {max_mb}MB limit",
    )


# ── Validator 3: Zip Bomb ─────────────────────────────────────────────────────


def validate_zip_bomb(file_bytes: bytes) -> ValidationResult:
    """
    Detect zip bomb attacks by checking the compression ratio.

    A zip bomb is a zip file containing a tiny compressed payload that
    expands to a massive uncompressed size (e.g., 42.zip: 42KB → 4.5PB).

    Rejection criteria:
      - Uncompressed total > compressed total × 100 (100:1 ratio)
      - Any single file uncompressed size > 1GB
    """
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes), "r") as zf:
            total_compressed = 0
            total_uncompressed = 0

            for info in zf.infolist():
                total_compressed += info.compress_size
                total_uncompressed += info.file_size

                # Check individual file size ceiling
                if info.file_size > _MAX_ZIP_UNCOMPRESSED_BYTES:
                    return ValidationResult(
                        passed=False,
                        reason=(
                            f"File '{info.filename}' in zip has uncompressed size "
                            f"{info.file_size / (1024**3):.1f}GB which exceeds the 1GB limit"
                        ),
                    )

            # Avoid division by zero for empty zips or stored (uncompressed) files
            if total_compressed > 0:
                ratio = total_uncompressed / total_compressed
                if ratio > _MAX_ZIP_RATIO:
                    return ValidationResult(
                        passed=False,
                        reason=(
                            f"Zip compression ratio {ratio:.0f}:1 exceeds the "
                            f"maximum allowed ratio of {_MAX_ZIP_RATIO}:1. "
                            "Possible zip bomb detected."
                        ),
                    )

    except zipfile.BadZipFile:
        return ValidationResult(
            passed=False,
            reason="File is not a valid zip archive",
        )
    except Exception as exc:
        return ValidationResult(
            passed=False,
            reason=f"Zip validation error: {exc}",
        )

    return ValidationResult(passed=True, reason="Zip file is within safe compression ratio")


# ── Validator 4: PDF Not Encrypted ───────────────────────────────────────────


def validate_pdf_not_encrypted(file_bytes: bytes) -> ValidationResult:
    """
    Verify that a PDF is not password-protected.

    Encrypted PDFs cannot be processed by downstream services (OCR, extraction).
    Uses PyMuPDF (fitz) which can detect encryption without trying to decrypt.
    """
    try:
        import fitz  # type: ignore[import]  # PyMuPDF

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            if doc.is_encrypted:
                return ValidationResult(
                    passed=False,
                    reason="PDF is password-protected. Encrypted PDFs cannot be processed.",
                )
        finally:
            doc.close()

    except ImportError:
        logger.warning("PyMuPDF not available — skipping PDF encryption check")
        return ValidationResult(passed=True, reason="PyMuPDF not available; skipped")
    except Exception as exc:
        return ValidationResult(
            passed=False,
            reason=f"PDF validation error: {exc}",
        )

    return ValidationResult(passed=True, reason="PDF is not encrypted")


# ── Validator 5: Filename Safety ──────────────────────────────────────────────

# Forbidden patterns in filenames
_PATH_TRAVERSAL_PATTERNS = re.compile(r"(\.\.[/\\]|[/\\]\.\.)")
_NULL_BYTE_PATTERN = re.compile(r"\x00")
_DANGEROUS_CHARS = re.compile(r"[<>:\"|?*\x00-\x1f]")


def validate_filename(filename: str) -> ValidationResult:
    """
    Verify the filename is safe for storage and processing.

    Rejects:
      - Path traversal sequences (../ or ..\\)
      - Null bytes (can trick some C libraries into truncating the path)
      - Names longer than 255 characters
      - Dangerous characters that could cause issues on Windows or Unix filesystems
      - Empty filenames
    """
    if not filename:
        return ValidationResult(passed=False, reason="Filename cannot be empty")

    # Strip leading/trailing whitespace for the check (but don't modify the actual name)
    stripped = filename.strip()

    if len(stripped) > 255:
        return ValidationResult(
            passed=False,
            reason=f"Filename length {len(stripped)} exceeds the 255 character limit",
        )

    if _NULL_BYTE_PATTERN.search(stripped):
        return ValidationResult(
            passed=False,
            reason="Filename contains null bytes",
        )

    if _PATH_TRAVERSAL_PATTERNS.search(stripped):
        return ValidationResult(
            passed=False,
            reason="Filename contains path traversal sequences (../ or ..\\)",
        )

    # Check for absolute path attempts
    if stripped.startswith(("/", "\\")) or (len(stripped) > 1 and stripped[1] == ":"):
        return ValidationResult(
            passed=False,
            reason="Filename appears to be an absolute path",
        )

    # Check for dangerous characters
    if _DANGEROUS_CHARS.search(stripped):
        return ValidationResult(
            passed=False,
            reason="Filename contains illegal characters",
        )

    return ValidationResult(passed=True, reason="Filename is safe")


# ── Validator 6: XML XXE Detection ───────────────────────────────────────────


def validate_xml_not_xxe(file_bytes: bytes) -> ValidationResult:
    """
    Detect XML External Entity (XXE) injection attempts.

    XXE attacks embed external entity references in XML that cause the
    parser to fetch external resources (file system, network, etc.).

    Defence:
      - Parse with lxml using a safe, hardened parser configuration
      - Detect DOCTYPE declarations with SYSTEM or PUBLIC identifiers
      - Detect entity references in the content

    We use a byte-level heuristic scan first (fast path) and then
    attempt to parse with entity expansion disabled.
    """
    # Fast path: check for DOCTYPE/ENTITY keywords in the raw bytes
    # This catches simple injection without needing a full parse
    content_lower = file_bytes[:65536].lower()

    has_doctype = b"<!doctype" in content_lower
    has_entity = b"<!entity" in content_lower
    has_system = b"system" in content_lower and b"<!" in content_lower

    if has_doctype and (has_entity or has_system):
        return ValidationResult(
            passed=False,
            reason=(
                "XML contains a DOCTYPE declaration with ENTITY or SYSTEM reference. "
                "This is a potential XML External Entity (XXE) injection attack."
            ),
        )

    # Full parse with a hardened lxml parser
    try:
        from lxml import etree  # type: ignore[import]

        parser = etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            dtd_validation=False,
            load_dtd=False,
        )
        etree.fromstring(file_bytes, parser=parser)

    except ImportError:
        logger.warning("lxml not available — using heuristic-only XXE check")
    except etree.XMLSyntaxError as exc:
        # Check if the error is related to entity resolution being blocked
        error_msg = str(exc).lower()
        if "entity" in error_msg or "system" in error_msg:
            return ValidationResult(
                passed=False,
                reason=f"XML contains potentially dangerous entity references: {exc}",
            )
        return ValidationResult(
            passed=False,
            reason=f"XML is malformed: {exc}",
        )
    except Exception as exc:
        return ValidationResult(
            passed=False,
            reason=f"XML validation error: {exc}",
        )

    return ValidationResult(passed=True, reason="XML is safe — no external entity references detected")
