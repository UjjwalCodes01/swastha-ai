"""
Unit tests for file validators.

Tests each validator with valid and invalid inputs, verifying:
- Correct pass/fail results
- Accurate reason messages
- Edge cases and boundary conditions
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.ingestion.validators import (
    ValidationResult,
    validate_file_size,
    validate_filename,
    validate_mime_by_magic_bytes,
    validate_pdf_not_encrypted,
    validate_xml_not_xxe,
    validate_zip_bomb,
)


# ── validate_file_size ────────────────────────────────────────────────────────


class TestValidateFileSize:
    def test_file_within_limit_passes(self):
        data = b"x" * (5 * 1024 * 1024)  # 5MB
        result = validate_file_size(data, max_mb=10)
        assert result.passed is True

    def test_file_exactly_at_limit_passes(self):
        data = b"x" * (10 * 1024 * 1024)  # exactly 10MB
        result = validate_file_size(data, max_mb=10)
        assert result.passed is True

    def test_file_over_limit_fails(self):
        data = b"x" * (10 * 1024 * 1024 + 1)  # 1 byte over 10MB
        result = validate_file_size(data, max_mb=10)
        assert result.passed is False
        assert "exceeds" in result.reason.lower()
        assert "10MB" in result.reason

    def test_empty_file_passes(self):
        result = validate_file_size(b"", max_mb=100)
        assert result.passed is True

    def test_reason_includes_actual_size(self):
        data = b"x" * (200 * 1024 * 1024)  # 200MB
        result = validate_file_size(data, max_mb=100)
        assert result.passed is False
        assert "200" in result.reason or "200.0" in result.reason


# ── validate_filename ─────────────────────────────────────────────────────────


class TestValidateFilename:
    def test_valid_filename_passes(self):
        result = validate_filename("drug_application_2024.pdf")
        assert result.passed is True

    def test_filename_with_spaces_passes(self):
        result = validate_filename("clinical trial report.pdf")
        assert result.passed is True

    def test_path_traversal_unix_fails(self):
        result = validate_filename("../../etc/passwd")
        assert result.passed is False
        assert "traversal" in result.reason.lower() or "path" in result.reason.lower()

    def test_path_traversal_windows_fails(self):
        result = validate_filename("..\\..\\windows\\system32\\config")
        assert result.passed is False

    def test_null_byte_fails(self):
        result = validate_filename("report\x00.pdf")
        assert result.passed is False
        assert "null" in result.reason.lower()

    def test_filename_over_255_chars_fails(self):
        long_name = "a" * 256 + ".pdf"
        result = validate_filename(long_name)
        assert result.passed is False
        assert "255" in result.reason

    def test_filename_exactly_255_chars_passes(self):
        name = "a" * 251 + ".pdf"  # 255 total
        result = validate_filename(name)
        assert result.passed is True

    def test_empty_filename_fails(self):
        result = validate_filename("")
        assert result.passed is False
        assert "empty" in result.reason.lower()

    def test_absolute_unix_path_fails(self):
        result = validate_filename("/etc/passwd")
        assert result.passed is False

    def test_windows_drive_path_fails(self):
        result = validate_filename("C:\\Windows\\System32\\config")
        assert result.passed is False


# ── validate_zip_bomb ─────────────────────────────────────────────────────────


class TestValidateZipBomb:
    def test_normal_zip_passes(self, sample_zip_bytes: bytes):
        result = validate_zip_bomb(sample_zip_bytes)
        assert result.passed is True

    def test_high_ratio_zip_fails(self):
        """Create a zip with a compression ratio > 100:1."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            # 10MB of zeros compresses to ~10KB → ratio ~1000:1
            zf.writestr("zeros.bin", b"\x00" * (10 * 1024 * 1024))
        zip_bytes = buf.getvalue()

        result = validate_zip_bomb(zip_bytes)
        assert result.passed is False
        assert "ratio" in result.reason.lower() or "bomb" in result.reason.lower()

    def test_invalid_zip_fails(self):
        result = validate_zip_bomb(b"this is not a zip file")
        assert result.passed is False

    def test_empty_zip_passes(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            pass
        result = validate_zip_bomb(buf.getvalue())
        assert result.passed is True

    def test_stored_zip_passes(self):
        """ZIP_STORED (no compression) should not trigger the ratio check."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("doc.txt", "Hello World " * 1000)
        result = validate_zip_bomb(buf.getvalue())
        assert result.passed is True


# ── validate_mime_by_magic_bytes ──────────────────────────────────────────────


class TestValidateMimeByMagicBytes:
    ALLOWED = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/xml",
        "text/csv",
        "application/json",
        "application/zip",
    ]

    def test_valid_pdf_magic_passes(self, sample_pdf_bytes: bytes):
        """PDF files start with %PDF — magic should detect this."""
        result = validate_mime_by_magic_bytes(
            sample_pdf_bytes, self.ALLOWED, "application/pdf"
        )
        # May skip if python-magic not installed in test env
        if "not available" not in result.reason.lower():
            assert result.passed is True

    def test_disallowed_type_fails(self):
        """EXE files should not be accepted."""
        exe_bytes = b"MZ" + b"\x00" * 100  # DOS/PE header magic
        result = validate_mime_by_magic_bytes(exe_bytes, self.ALLOWED)
        if "not available" not in result.reason.lower():
            assert result.passed is False

    def test_mime_mismatch_fails(self, sample_pdf_bytes: bytes):
        """Claiming text/csv for a PDF file should be rejected."""
        result = validate_mime_by_magic_bytes(
            sample_pdf_bytes, self.ALLOWED, "text/csv"
        )
        if "not available" not in result.reason.lower():
            assert result.passed is False
            assert "mismatch" in result.reason.lower()

    def test_empty_allowed_list_fails(self, sample_pdf_bytes: bytes):
        """With an empty allowlist, everything fails."""
        result = validate_mime_by_magic_bytes(sample_pdf_bytes, [])
        if "not available" not in result.reason.lower():
            assert result.passed is False


# ── validate_xml_not_xxe ──────────────────────────────────────────────────────


class TestValidateXmlNotXXE:
    def test_safe_xml_passes(self, sample_xml_bytes: bytes):
        result = validate_xml_not_xxe(sample_xml_bytes)
        assert result.passed is True

    def test_xxe_injection_fails(self, malicious_xml_bytes: bytes):
        """XML with SYSTEM entity reference should be rejected."""
        result = validate_xml_not_xxe(malicious_xml_bytes)
        assert result.passed is False
        assert "entity" in result.reason.lower() or "xxe" in result.reason.lower()

    def test_doctype_with_entity_fails(self):
        """DOCTYPE declaration with an entity should fail."""
        xml = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE doc [<!ENTITY test "value">]>'
            b"<doc>&test;</doc>"
        )
        result = validate_xml_not_xxe(xml)
        assert result.passed is False

    def test_malformed_xml_fails(self):
        """Malformed XML should fail the validation."""
        result = validate_xml_not_xxe(b"<unclosed>")
        assert result.passed is False

    def test_empty_xml_element_passes(self):
        """Valid minimal XML with no entities should pass."""
        result = validate_xml_not_xxe(
            b'<?xml version="1.0"?><root />'
        )
        assert result.passed is True


# ── validate_pdf_not_encrypted ────────────────────────────────────────────────


class TestValidatePdfNotEncrypted:
    def test_valid_unencrypted_pdf_passes(self, sample_pdf_bytes: bytes):
        result = validate_pdf_not_encrypted(sample_pdf_bytes)
        # Skip if PyMuPDF not installed
        if "not available" not in result.reason.lower():
            assert result.passed is True

    def test_non_pdf_bytes_fails(self):
        result = validate_pdf_not_encrypted(b"this is not a pdf at all")
        if "not available" not in result.reason.lower():
            assert result.passed is False

    def test_encrypted_pdf_fails(self):
        """
        Test with a minimal encrypted PDF marker.
        We mock the PyMuPDF call to return is_encrypted=True.
        """
        from unittest.mock import MagicMock, patch

        mock_doc = MagicMock()
        mock_doc.is_encrypted = True
        mock_doc.__enter__ = MagicMock(return_value=mock_doc)
        mock_doc.__exit__ = MagicMock(return_value=None)
        mock_doc.close = MagicMock()

        try:
            import fitz  # noqa: F401
            with patch("fitz.open", return_value=mock_doc):
                result = validate_pdf_not_encrypted(b"%PDF-1.4 encrypted content")
            assert result.passed is False
            assert "encrypted" in result.reason.lower()
        except ImportError:
            pytest.skip("PyMuPDF not installed")
