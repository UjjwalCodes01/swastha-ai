import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.preprocessing.pipeline import PreprocessingPipeline
from app.preprocessing.chunker.semantic_chunker import DocumentChunk
from app.preprocessing.metadata.language_detector import LanguageDetectionResult


@pytest.fixture
def mock_minio():
    client = AsyncMock()
    return client


@pytest.fixture
def mock_db_session():
    session = AsyncMock()
    # Mock the execute/commit
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    
    # Factory returns context manager
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = session
    return factory


@pytest.fixture
def mock_embedding_service():
    svc = AsyncMock()
    svc.generate_embeddings = AsyncMock(return_value=[[0.1] * 768])
    return svc


@pytest.fixture
def mock_chroma_store():
    store = AsyncMock()
    store.add_chunks = AsyncMock(return_value="clinical_trials")
    return store


@pytest.fixture
def pipeline(mock_minio, mock_db_session, mock_embedding_service, mock_chroma_store):
    pipe = PreprocessingPipeline(
        minio_client=mock_minio,
        db_session_factory=mock_db_session,
        embedding_service=mock_embedding_service,
        chroma_store=mock_chroma_store
    )
    
    # Mock Publisher so we don't try to connect to Kafka
    pipe.publisher = AsyncMock()
    
    # Mock Extractors so we don't need real files or libraries
    mock_extractor = AsyncMock()
    mock_result = MagicMock()
    mock_result.is_empty = False
    mock_result.total_text = "Clinical Trial Phase 3. The patient was treated with Aspirin."
    mock_result.page_count = 1
    mock_result.total_word_count = 10
    mock_result.tables = []
    mock_result.form_fields = {}
    mock_result.extraction_warnings = []
    # Mock the pages property specifically to avoid iteration errors
    page_mock = MagicMock()
    page_mock.has_images = False
    mock_result.pages = [page_mock]
    
    mock_extractor.extract = AsyncMock(return_value=mock_result)
    mock_extractor.extractor_name = "MockExtractor"
    
    pipe.extractors = {
        "pdf": mock_extractor,
        "docx": mock_extractor,
        "xml": mock_extractor,
        "csv": mock_extractor,
        "tika": mock_extractor
    }
    
    # Mock magic to always return PDF
    with patch("magic.from_buffer", return_value="application/pdf"):
        yield pipe


@pytest.mark.asyncio
async def test_pipeline_end_to_end(pipeline, mock_db_session, mock_minio, mock_chroma_store):
    # This patches magic again within the test scope just in case
    with patch("magic.from_buffer", return_value="application/pdf"):
        result = await pipeline.process_document(
            doc_id="DOC-999",
            file_bytes=b"fake pdf content",
            submission_type="clinical_trial",
            portal_source="sugam",
            submitted_by="user123",
            original_filename="trial.pdf"
        )
    
    assert result["status"] == "success"
    assert result["doc_id"] == "DOC-999"
    assert result["chunks"] > 0
    
    # Verify MinIO upload was called
    assert mock_minio.put_object.called
    call_args = mock_minio.put_object.call_args[1]
    assert call_args["Bucket"] == "swastha-ai-processed-documents"
    assert "processed/clinical_trial/" in call_args["Key"]
    assert "/DOC-999/processed.json" in call_args["Key"]
    
    # Verify DB was updated
    session = mock_db_session.return_value.__aenter__.return_value
    assert session.execute.call_count >= 3 # Insert log, update sub, delete old chunks, insert new
    assert session.commit.called
    
    # Verify ChromaDB was called
    assert mock_chroma_store.add_chunks.called
    
    # Verify Publisher was called
    assert pipeline.publisher.publish_completion.called
    pub_args = pipeline.publisher.publish_completion.call_args[1]
    assert pub_args["doc_id"] == "DOC-999"
    assert pub_args["submission_type"] == "clinical_trial"
    assert pub_args["extractors_used"] == ["MockExtractor"]
    assert pub_args["ocr_used"] is False
    assert pub_args["language"] == "en"
