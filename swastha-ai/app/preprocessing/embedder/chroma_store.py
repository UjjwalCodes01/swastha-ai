"""
ChromaDB Vector Store adapter.

Stores document chunks with embeddings and metadata.
Collections are scoped by submission_type.
Always uses upsert to handle reprocessing.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
except ImportError:  # pragma: no cover
    chromadb = None  # type: ignore[assignment]
    ChromaSettings = None  # type: ignore[assignment]

from app.preprocessing.chunker.semantic_chunker import DocumentChunk


class ChromaStore:
    """Async wrapper around ChromaDB async client."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._client: Any = None

    async def initialize(self) -> None:
        if chromadb is None:
            raise ImportError("chromadb not installed")
        
        # We use the new async client
        self._client = await chromadb.AsyncHttpClient(
            host=self.host,
            port=self.port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info(f"Connected to ChromaDB at {self.host}:{self.port}")

    def _get_collection_name(self, submission_type: str) -> str:
        """Map submission type to collection name."""
        mapping = {
            "drug": "drug_submissions",
            "medical_device": "medical_devices",
            "clinical_trial": "clinical_trials",
            "sae": "sae_reports",
        }
        return mapping.get(submission_type, "misc_documents")

    async def add_chunks(
        self,
        submission_type: str,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
        extraction_confidence: float,
        language: str,
        portal_source: str,
    ) -> str:
        """
        Batch upsert chunks to ChromaDB.
        Returns the collection name used.
        """
        if not chunks or not embeddings:
            return ""
        
        if len(chunks) != len(embeddings):
            raise ValueError(f"Length mismatch: {len(chunks)} chunks, {len(embeddings)} embeddings")

        collection_name = self._get_collection_name(submission_type)
        
        try:
            # Create if not exists using the async client
            collection = await self._client.get_or_create_collection(
                name=collection_name,
                metadata={"description": f"RxFlow {submission_type} vectors"}
            )

            ids = []
            texts = []
            metadatas = []

            for chunk in chunks:
                ids.append(chunk.chunk_id)
                texts.append(chunk.text)
                
                # Build metadata dict (Chroma only accepts str, int, float, bool)
                meta = {
                    "doc_id": chunk.doc_id,
                    "chunk_id": chunk.chunk_id,
                    "submission_type": submission_type,
                    "portal_source": portal_source,
                    "chunk_type": chunk.chunk_type,
                    "chunk_index": chunk.chunk_index,
                    "total_chunks": chunk.total_chunks,
                    "language": language,
                    "extraction_confidence": float(extraction_confidence),
                }
                if chunk.section_title:
                    meta["section_title"] = chunk.section_title
                if chunk.page_range:
                    meta["page_range_start"] = chunk.page_range[0]
                    meta["page_range_end"] = chunk.page_range[-1]
                
                metadatas.append(meta)

            # Upsert
            await collection.upsert(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=texts,
            )
            
            logger.debug(
                "Upserted chunks to ChromaDB",
                extra={"collection": collection_name, "count": len(chunks)}
            )
            return collection_name
            
        except Exception as exc:
            logger.error(
                "ChromaDB upsert failed",
                extra={"collection": collection_name, "error": str(exc)}
            )
            raise

    async def delete_document(self, doc_id: str, submission_type: str) -> None:
        """Delete all chunks for a document (used during reprocessing/rollback)."""
        collection_name = self._get_collection_name(submission_type)
        try:
            collection = await self._client.get_collection(name=collection_name)
            await collection.delete(where={"doc_id": doc_id})
        except Exception:
            pass  # Collection might not exist
