import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.compliance.pipeline import CompliancePipeline


class _Body:
    def __init__(self, payload: dict):
        self.payload = payload

    async def read(self):
        return json.dumps(self.payload).encode("utf-8")


@pytest.fixture
def mock_session_factory():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = session
    return factory


@pytest.fixture
def mock_producer():
    producer = AsyncMock()
    producer.publish = AsyncMock(return_value=True)
    return producer


@pytest.fixture
def mock_minio():
    minio = AsyncMock()
    return minio


@pytest.mark.asyncio
async def test_pipeline_blocks_anonymised_document_with_pii_leak(mock_minio, mock_producer, mock_session_factory):
    mock_minio.get_object = AsyncMock(
        return_value={
            "Body": _Body(
                {
                    "doc_id": "DOC-1",
                    "submission_type": "drug",
                    "anonymised_text": "Patient Aadhaar 1234 5678 9012 remains.",
                    "entities": [],
                    "source_processed_path": "processed/doc.json",
                }
            )
        }
    )
    pipeline = CompliancePipeline(mock_minio, mock_producer, mock_session_factory)

    decision = await pipeline.assess(
        "documents.anonymised",
        {
            "doc_id": "DOC-1",
            "submission_type": "drug",
            "anonymised_storage_path": "ai-core/drug/DOC-1/anonymised.json",
            "confidence": 0.9,
        },
    )

    assert decision.decision == "blocked"
    assert decision.blocked is True
    assert any(finding.rule_id == "DPDP-PII-LEAK" for finding in decision.findings)
    mock_producer.publish.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_requires_review_for_bad_sae_classification(mock_minio, mock_producer, mock_session_factory):
    pipeline = CompliancePipeline(mock_minio, mock_producer, mock_session_factory)

    decision = await pipeline.assess(
        "documents.classified",
        {
            "doc_id": "DOC-SAE",
            "submission_type": "sae",
            "model_id": "classifier",
            "model_version": "1",
            "classification": {
                "category": "SAE",
                "priority": "critical",
                "risk_score": 0.98,
                "sae_severity": "death",
                "requires_review": False,
                "labels": ["sae:death"],
                "confidence": 0.91,
            },
        },
    )

    assert decision.decision == "review_required"
    assert decision.human_review_required is True
    assert decision.blocked is False
    assert mock_session_factory.return_value.__aenter__.return_value.execute.call_count == 2
    mock_producer.publish.assert_called_once()
