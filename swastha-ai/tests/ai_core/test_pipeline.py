import json
from unittest.mock import AsyncMock

import pytest

from app.ai_core.pipeline import AICorePipeline
from app.ai_core.topics import DOCUMENTS_ANONYMISED, DOCUMENTS_CLASSIFIED, DOCUMENTS_SUMMARISED


class _Body:
    def __init__(self, payload: dict):
        self.payload = payload

    async def read(self):
        return json.dumps(self.payload).encode("utf-8")


@pytest.fixture
def mock_minio():
    client = AsyncMock()
    client.head_bucket = AsyncMock()
    client.create_bucket = AsyncMock()
    client.put_object = AsyncMock()
    return client


@pytest.fixture
def mock_producer():
    producer = AsyncMock()
    producer.publish = AsyncMock(return_value=True)
    return producer


@pytest.mark.asyncio
async def test_pipeline_anonymises_preprocessed_document(mock_minio, mock_producer):
    mock_minio.get_object = AsyncMock(
        return_value={
            "Body": _Body(
                {
                    "metadata": {"drug_name": "TestDrug"},
                    "chunks": [{"text": "Aadhaar 1234 5678 9012 and email applicant@example.org"}],
                }
            )
        }
    )
    pipeline = AICorePipeline(mock_minio, mock_producer)

    payload = await pipeline.handle_preprocessed(
        {
            "doc_id": "DOC-1",
            "submission_type": "drug",
            "processed_storage_path": "processed/drug/DOC-1/processed.json",
        }
    )

    assert payload["pii_entities_removed"] >= 2
    mock_producer.publish.assert_called_once()
    assert mock_producer.publish.call_args[0][0] == DOCUMENTS_ANONYMISED


@pytest.mark.asyncio
async def test_pipeline_summarises_and_classifies_anonymised_document(mock_minio, mock_producer):
    mock_minio.get_object = AsyncMock(
        return_value={
            "Body": _Body(
                {
                    "metadata": {"protocol_number": "CT-001"},
                    "anonymised_text": "Clinical Trial Phase 3 protocol includes adverse event monitoring.",
                }
            )
        }
    )
    pipeline = AICorePipeline(mock_minio, mock_producer)

    result = await pipeline.handle_anonymised(
        {
            "doc_id": "DOC-2",
            "submission_type": "clinical_trial",
            "anonymised_storage_path": "ai-core/clinical_trial/DOC-2/anonymised.json",
        }
    )

    topics = [call.args[0] for call in mock_producer.publish.call_args_list]
    assert DOCUMENTS_SUMMARISED in topics
    assert DOCUMENTS_CLASSIFIED in topics
    assert result["summary"]["key_findings"]
