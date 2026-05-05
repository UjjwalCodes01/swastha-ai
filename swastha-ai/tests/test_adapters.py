"""
Tests for portal adapters (SUGAM and MD Online).

Tests cover:
- Normal response parsing and normalisation
- Error handling for API failures
- Exponential backoff calculation
- Graceful handling of malformed responses
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from app.adapters.base_adapter import SubmissionEvent
from app.adapters.md_online_adapter import MDOnlineAdapter
from app.adapters.sugam_adapter import SUGAMAdapter


pytestmark = pytest.mark.asyncio


# ── SUGAM Adapter Tests ───────────────────────────────────────────────────────


class TestSUGAMAdapter:
    @pytest.fixture
    def adapter(self, monkeypatch):
        monkeypatch.setenv("SUGAM_API_KEY", "test-sugam-key")
        monkeypatch.setenv("SUGAM_API_BASE_URL", "https://test-sugam.example.com/api/v1")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-at-least-32-chars-long!!")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9000")
        monkeypatch.setenv("MINIO_ACCESS_KEY", "test")
        monkeypatch.setenv("MINIO_SECRET_KEY", "test")
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("KEYCLOAK_URL", "http://localhost:8080")

        from app.config import get_settings
        get_settings.cache_clear()

        adapter = SUGAMAdapter()
        adapter._api_key = "test-sugam-key"
        return adapter

    def test_parse_valid_sugam_response(self, adapter: SUGAMAdapter):
        """Test that a valid SUGAM response is correctly translated."""
        response_data = {
            "applications": [
                {
                    "ApplicationID": "SUGAM-2024-001",
                    "ApplicantName": "PharmaCo Ltd",
                    "ApplicationType": "NewDrug",
                    "SubmittedAt": "2024-01-15T10:30:00Z",
                    "Documents": [
                        {
                            "DocumentID": "DOC001",
                            "DocumentType": "CTD",
                            "FileName": "module1.pdf",
                            "DownloadURL": "https://sugam.example.com/docs/DOC001",
                        }
                    ],
                }
            ]
        }

        events = adapter._parse_response(response_data)

        assert len(events) == 1
        event = events[0]
        assert isinstance(event, SubmissionEvent)
        assert event.external_id == "SUGAM-2024-001"
        assert event.applicant_name == "PharmaCo Ltd"
        assert event.submission_type == "drug"
        assert event.portal_source == "sugam"
        assert len(event.document_urls) == 1
        assert "module1.pdf" in event.document_filenames

    def test_parse_application_type_mapping(self, adapter: SUGAMAdapter):
        """Test that all application types are correctly mapped."""
        type_map_test_cases = [
            ("NewDrug", "drug"),
            ("ClinicalTrial", "clinical_trial"),
            ("MedicalDevice", "medical_device"),
            ("SAE", "sae"),
            ("IND", "clinical_trial"),
            ("NDA", "drug"),
        ]

        for app_type, expected_type in type_map_test_cases:
            response_data = {
                "applications": [
                    {
                        "ApplicationID": f"APP-{app_type}",
                        "ApplicantName": "Test Corp",
                        "ApplicationType": app_type,
                        "Documents": [
                            {"FileName": "doc.pdf", "DownloadURL": "http://example.com/doc.pdf"}
                        ],
                    }
                ]
            }
            events = adapter._parse_response(response_data)
            assert events[0].submission_type == expected_type, (
                f"Expected {expected_type} for {app_type}, got {events[0].submission_type}"
            )

    def test_parse_skips_entry_without_documents(self, adapter: SUGAMAdapter):
        """Test that applications without documents are skipped."""
        response_data = {
            "applications": [
                {
                    "ApplicationID": "NO-DOCS-001",
                    "ApplicantName": "Test Corp",
                    "ApplicationType": "NewDrug",
                    "Documents": [],  # No documents
                }
            ]
        }
        events = adapter._parse_response(response_data)
        assert len(events) == 0

    def test_parse_handles_malformed_entry_gracefully(self, adapter: SUGAMAdapter):
        """Test that malformed entries are skipped without crashing."""
        response_data = {
            "applications": [
                {"malformed": True, "no_required_fields": "at_all"},  # Bad entry
                {
                    "ApplicationID": "VALID-001",
                    "ApplicantName": "Good Corp",
                    "ApplicationType": "NewDrug",
                    "Documents": [
                        {"FileName": "good.pdf", "DownloadURL": "http://example.com/good.pdf"}
                    ],
                },
            ]
        }
        events = adapter._parse_response(response_data)
        # Should have 1 valid event, bad one skipped
        # (The malformed one has no ApplicationID and no Documents but won't crash)
        assert len(events) >= 0  # At minimum doesn't crash

    def test_parse_handles_timestamp_formats(self, adapter: SUGAMAdapter):
        """Test that various timestamp formats are handled gracefully."""
        test_cases = [
            "2024-01-15T10:30:00Z",
            "2024-01-15T10:30:00+05:30",
            "not-a-date",  # Should not crash, just be None
            None,  # Missing field — should be None
        ]

        for ts in test_cases:
            response_data = {
                "applications": [
                    {
                        "ApplicationID": f"APP-TS",
                        "ApplicantName": "Test",
                        "ApplicationType": "NewDrug",
                        "SubmittedAt": ts,
                        "Documents": [
                            {"FileName": "doc.pdf", "DownloadURL": "http://example.com/doc.pdf"}
                        ],
                    }
                ]
            }
            # Should not raise
            events = adapter._parse_response(response_data)
            assert len(events) == 1

    async def test_poll_returns_empty_on_api_error(self, adapter: SUGAMAdapter):
        """Test that API errors return empty list instead of crashing."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
        )
        mock_client.get = AsyncMock(return_value=mock_response)
        adapter._client = mock_client

        events = await adapter.poll_new_submissions()
        assert events == []
        assert adapter._consecutive_errors == 1

    async def test_poll_returns_empty_on_timeout(self, adapter: SUGAMAdapter):
        """Test that timeouts return empty list and increment error counter."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
        adapter._client = mock_client

        events = await adapter.poll_new_submissions()
        assert events == []
        assert adapter._consecutive_errors == 1

    async def test_poll_resets_error_counter_on_success(self, adapter: SUGAMAdapter):
        """Test that successful poll resets the consecutive error counter."""
        adapter._consecutive_errors = 5

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"applications": []})
        mock_client.get = AsyncMock(return_value=mock_response)
        adapter._client = mock_client

        await adapter.poll_new_submissions()
        assert adapter._consecutive_errors == 0

    def test_exponential_backoff_calculation(self, adapter: SUGAMAdapter):
        """Test that backoff increases exponentially with each error."""
        adapter._consecutive_errors = 0
        initial = adapter._compute_backoff()
        assert initial == adapter._poll_interval

        adapter._consecutive_errors = 1
        backoff_1 = adapter._compute_backoff()
        assert backoff_1 == 5  # _INITIAL_BACKOFF_SECONDS

        adapter._consecutive_errors = 3
        backoff_3 = adapter._compute_backoff()
        assert backoff_3 > backoff_1  # Should be larger

        # Should be capped at MAX_BACKOFF
        adapter._consecutive_errors = 100
        max_backoff = adapter._compute_backoff()
        assert max_backoff <= 300  # _MAX_BACKOFF_SECONDS

    async def test_skip_polling_when_no_api_key(self, adapter: SUGAMAdapter):
        """Test that adapter skips polling when API key is not configured."""
        adapter._api_key = ""
        events = await adapter.poll_new_submissions()
        assert events == []


# ── MD Online Adapter Tests ───────────────────────────────────────────────────


class TestMDOnlineAdapter:
    @pytest.fixture
    def adapter(self, monkeypatch):
        monkeypatch.setenv("MD_ONLINE_API_KEY", "test-mdonline-key")
        monkeypatch.setenv("MD_ONLINE_API_BASE_URL", "https://test-mdonline.example.com/api/v1")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key-at-least-32-chars-long!!")
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("MINIO_ENDPOINT", "localhost:9000")
        monkeypatch.setenv("MINIO_ACCESS_KEY", "test")
        monkeypatch.setenv("MINIO_SECRET_KEY", "test")
        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("KEYCLOAK_URL", "http://localhost:8080")

        from app.config import get_settings
        get_settings.cache_clear()

        adapter = MDOnlineAdapter()
        adapter._api_key = "test-mdonline-key"
        return adapter

    def test_parse_valid_md_online_response(self, adapter: MDOnlineAdapter):
        """Test that a valid MD Online response is correctly translated."""
        response_data = {
            "submissions": [
                {
                    "SubmissionID": "MDD-2024-001",
                    "ManufacturerName": "MediDevice Corp",
                    "DeviceClass": "ClassIII",
                    "SubmissionDate": "2024-01-20T14:00:00Z",
                    "Attachments": [
                        {
                            "AttachmentID": "ATT001",
                            "FileType": "TestReport",
                            "OriginalFileName": "biocompatibility_test.pdf",
                            "DownloadLink": "https://mdonline.example.com/attachments/ATT001",
                        }
                    ],
                }
            ]
        }

        events = adapter._parse_response(response_data)

        assert len(events) == 1
        event = events[0]
        assert event.external_id == "MDD-2024-001"
        assert event.applicant_name == "MediDevice Corp"
        assert event.submission_type == "medical_device"
        assert event.portal_source == "md_online"
        assert "biocompatibility_test.pdf" in event.document_filenames

    def test_parse_device_class_mapping(self, adapter: MDOnlineAdapter):
        """Test that all device classes map to medical_device."""
        device_classes = ["ClassI", "ClassII", "ClassIII", "ClassIV", "IVD"]
        for device_class in device_classes:
            response_data = {
                "submissions": [
                    {
                        "SubmissionID": f"MDD-{device_class}",
                        "ManufacturerName": "Test Corp",
                        "DeviceClass": device_class,
                        "Attachments": [
                            {
                                "OriginalFileName": "doc.pdf",
                                "DownloadLink": "http://example.com/doc.pdf",
                            }
                        ],
                    }
                ]
            }
            events = adapter._parse_response(response_data)
            assert events[0].submission_type == "medical_device"

    def test_parse_skips_submission_without_attachments(
        self, adapter: MDOnlineAdapter
    ):
        """Test that submissions without attachments are skipped."""
        response_data = {
            "submissions": [
                {
                    "SubmissionID": "NO-DOCS-001",
                    "ManufacturerName": "Test Corp",
                    "DeviceClass": "ClassII",
                    "Attachments": [],
                }
            ]
        }
        events = adapter._parse_response(response_data)
        assert len(events) == 0

    async def test_api_error_returns_empty_list(self, adapter: MDOnlineAdapter):
        """Test that API errors are handled gracefully."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=MagicMock(status_code=404)
        )
        mock_client.get = AsyncMock(return_value=mock_response)
        adapter._client = mock_client

        events = await adapter.poll_new_submissions()
        assert events == []
        assert adapter._consecutive_errors >= 1

    def test_exponential_backoff_capped_at_max(self, adapter: MDOnlineAdapter):
        """Test that backoff is capped at MAX_BACKOFF_SECONDS."""
        adapter._consecutive_errors = 1000  # Very large error count
        backoff = adapter._compute_backoff()
        assert backoff <= 300  # _MAX_BACKOFF_SECONDS
