"""
The Preprocessing Pipeline Orchestrator.

Implements the 12-step pipeline described in the architecture.
Handles the end-to-end transformation from raw bytes to ChromaDB vectors
and JSON storage in MinIO.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import magic

from app.preprocessing.chunker.semantic_chunker import DocumentChunk, SemanticChunker
from app.preprocessing.chunker.table_chunker import TableChunker
from app.preprocessing.embedder.chroma_store import ChromaStore
from app.preprocessing.embedder.embedding_service import EmbeddingService
from app.preprocessing.extractors.base_extractor import ExtractionResult
from app.preprocessing.extractors.csv_extractor import CSVExtractor
from app.preprocessing.extractors.docx_extractor import DOCXExtractor
from app.preprocessing.extractors.pdf_extractor import PDFExtractor
from app.preprocessing.extractors.tika_extractor import TikaExtractor
from app.preprocessing.extractors.xml_extractor import XMLExtractor
from app.preprocessing.metadata.extractor import MetadataExtractor
from app.preprocessing.metadata.language_detector import LanguageDetector
from app.preprocessing.normaliser.text_normaliser import TextNormaliser
from app.preprocessing.ocr.image_preprocessor import PreprocessorConfig
from app.preprocessing.ocr.ocr_engine import OCREngine
from app.preprocessing.publisher import PreprocessingPublisher

logger = logging.getLogger(__name__)


class PreprocessingPipeline:
    """Orchestrates all preprocessing steps."""

    def __init__(
        self,
        minio_client: Any,
        db_session_factory: Any,
        embedding_service: EmbeddingService,
        chroma_store: ChromaStore,
        pipeline_version: str = "1.0.0",
        ocr_concurrency: int = 4,
    ) -> None:
        self.minio = minio_client
        self.db_session_factory = db_session_factory
        self.embedding_service = embedding_service
        self.chroma_store = chroma_store
        self.pipeline_version = pipeline_version

        # Initialize components
        self.normaliser = TextNormaliser()
        self.lang_detector = LanguageDetector()
        self.meta_extractor = MetadataExtractor()
        self.semantic_chunker = SemanticChunker()
        self.table_chunker = TableChunker()
        self.ocr_engine = OCREngine(concurrency=ocr_concurrency)
        self.publisher = PreprocessingPublisher()

        # Instantiate extractors
        self.extractors = {
            "pdf": PDFExtractor(),
            "docx": DOCXExtractor(),
            "xml": XMLExtractor(),
            "csv": CSVExtractor(),
            "tika": TikaExtractor(),
        }

    async def process_document(
        self,
        doc_id: str,
        file_bytes: bytes,
        submission_type: str,
        portal_source: str,
        submitted_by: str | None,
        original_filename: str,
    ) -> dict[str, Any]:
        """
        Run the full 12-step pipeline.
        Returns a summary dict for testing and logging.
        """
        start_time = time.perf_counter()
        logger.info(f"Starting pipeline for doc_id={doc_id}")

        try:
            # ── Step 1: Format Detection ──────────────────────────────────────
            mime_type = magic.from_buffer(file_bytes[:2048], mime=True)
            extractor_key = self._select_extractor(mime_type)
            extractor = self.extractors[extractor_key]
            extractors_used = [extractor.extractor_name]

            # ── Step 2: Content Extraction ────────────────────────────────────
            extraction_result = await extractor.extract(file_bytes)

            if extraction_result.is_empty and extractor_key != "tika":
                # Fallback to Tika if primary extractor fails completely
                logger.warning(
                    f"Primary extractor {extractor.extractor_name} returned empty. Falling back to Tika.",
                    extra={"doc_id": doc_id}
                )
                extraction_result = await self.extractors["tika"].extract(file_bytes)
                extractors_used.append("TikaExtractor")

            if extraction_result.is_empty:
                raise RuntimeError("All extraction attempts returned empty content.")

            # ── Step 3: OCR (Conditional) ─────────────────────────────────────
            pages_needing_ocr = []
            for page in extraction_result.pages:
                if page.has_images and page.char_count < 50:
                    pages_needing_ocr.append((page.page_number, file_bytes)) # Needs proper image extraction from PDF
            
            ocr_used = False
            ocr_pages = 0
            if pages_needing_ocr and extractor_key == "pdf":
                # PDF extractor didn't do OCR. Let's trigger our OCR engine
                # Extract images from the PDF bytes for the specific pages
                # For this implementation, we will just use the PyMuPDF page rendering
                # to get a clear image of the page for Tesseract.
                import fitz
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                
                ocr_tasks = []
                for pnum, _ in pages_needing_ocr:
                    page = doc[pnum - 1]
                    pix = page.get_pixmap(dpi=300)
                    img_bytes = pix.tobytes("png")
                    ocr_tasks.append((pnum, img_bytes))
                
                doc.close()

                if ocr_tasks:
                    ocr_used = True
                    ocr_pages = len(ocr_tasks)
                    ocr_results = await self.ocr_engine.process_pages(ocr_tasks)
                    
                    # Merge OCR text back into ExtractionResult
                    for ocr_res in ocr_results:
                        page_obj = extraction_result.pages[ocr_res.page_number - 1]
                        page_obj.text = f"{page_obj.text}\n\n{ocr_res.text}".strip()
                        page_obj.char_count = len(page_obj.text)
                        page_obj.word_count = len(page_obj.text.split())
                        page_obj.extraction_method = "hybrid" if page_obj.text else "ocr"
                        page_obj.ocr_confidence = ocr_res.confidence

            # ── Step 4: Language Detection ────────────────────────────────────
            full_raw_text = extraction_result.total_text
            lang_result = self.lang_detector.detect(full_raw_text)
            
            # ── Step 5: Text Normalisation ────────────────────────────────────
            normalised_text, norm_log = self.normaliser.normalise(full_raw_text)

            # ── Step 6: Metadata Extraction ───────────────────────────────────
            extracted_metadata = self.meta_extractor.extract(
                text=normalised_text,
                submission_type=submission_type,
                original_filename=original_filename,
                page_count=extraction_result.page_count,
                word_count=extraction_result.total_word_count,
            )
            extracted_metadata.update(extraction_result.form_fields)

            # ── Step 7: Table Extraction ──────────────────────────────────────
            # Already done by the extractor in Step 2, just referencing it here
            tables = extraction_result.tables

            # ── Step 8: Chunking ──────────────────────────────────────────────
            text_chunks = self.semantic_chunker.chunk_document(doc_id, normalised_text)
            table_chunks = self.table_chunker.chunk_tables(doc_id, tables, start_index=len(text_chunks))
            
            all_chunks = text_chunks + table_chunks
            
            # Post-process chunks to set total_chunks and linked list pointers
            total_chunks = len(all_chunks)
            for i, chunk in enumerate(all_chunks):
                chunk.total_chunks = total_chunks
                chunk.prev_chunk_id = all_chunks[i-1].chunk_id if i > 0 else None
                chunk.next_chunk_id = all_chunks[i+1].chunk_id if i < total_chunks - 1 else None

            # ── Step 9: Embedding Generation ──────────────────────────────────
            chunk_texts = [c.text for c in all_chunks]
            embeddings = await self.embedding_service.generate_embeddings(chunk_texts)

            # ── Step 10: Quality Assessment ───────────────────────────────────
            extraction_confidence = self._calculate_confidence(
                extraction_result, ocr_used, lang_result.confidence
            )
            
            flags = {
                "needs_human_review": extraction_confidence < 0.7,
                "has_tables": len(tables) > 0,
                "is_multilingual": lang_result.is_multilingual,
                "ocr_used": ocr_used,
                "low_confidence": extraction_confidence < 0.5,
                "possible_encrypted": "PDF_ENCRYPTED" in extraction_result.extraction_warnings,
                "possible_corrupted": any("CORRUPTED" in w for w in extraction_result.extraction_warnings),
                "translation_needed": lang_result.language != "en",
            }
            # Add PII flag if CSV detected it
            if any("PII" in w for w in extraction_result.extraction_warnings):
                flags["pii_columns_detected"] = True

            # ── Step 11a: Store in ChromaDB ───────────────────────────────────
            chroma_collection = ""
            if all_chunks:
                chroma_collection = await self.chroma_store.add_chunks(
                    submission_type=submission_type,
                    chunks=all_chunks,
                    embeddings=embeddings,
                    extraction_confidence=extraction_confidence,
                    language=lang_result.language,
                    portal_source=portal_source,
                )

            # ── Step 11b: Store Processed JSON in MinIO ───────────────────────
            processed_json = {
                "doc_id": doc_id,
                "pipeline_version": self.pipeline_version,
                "metadata": extracted_metadata,
                "chunks": [c.to_dict() for c in all_chunks],
                "tables_json": [t.to_json_rows() for t in tables],
                "warnings": extraction_result.extraction_warnings,
                "normalisation_log": norm_log.to_dict(),
                "extraction_confidence": extraction_confidence,
            }
            
            now = datetime.now(timezone.utc)
            minio_path = f"processed/{submission_type}/{now.year}/{now.month:02d}/{now.day:02d}/{doc_id}/processed.json"
            
            await self._save_to_minio(minio_path, processed_json)

            # ── Step 11c: Update PostgreSQL ───────────────────────────────────
            processing_duration_ms = int((time.perf_counter() - start_time) * 1000)
            await self._update_db(
                doc_id=doc_id,
                chunks=all_chunks,
                extraction_confidence=extraction_confidence,
                ocr_used=ocr_used,
                ocr_pages=ocr_pages,
                page_count=extraction_result.page_count,
                word_count=extraction_result.total_word_count,
                table_count=len(tables),
                language=lang_result.language,
                extractors_used=extractors_used,
                normalisation_changes=norm_log.to_dict(),
                flags=flags,
                duration_ms=processing_duration_ms,
                chroma_collection=chroma_collection,
            )

            # ── Step 12: Publish Downstream Events ────────────────────────────
            await self.publisher.publish_completion(
                doc_id=doc_id,
                processed_storage_path=minio_path,
                submission_type=submission_type,
                portal_source=portal_source,
                submitted_by=submitted_by,
                original_filename=original_filename,
                chunk_count=total_chunks,
                table_count=len(tables),
                page_count=extraction_result.page_count,
                word_count=extraction_result.total_word_count,
                extraction_confidence=extraction_confidence,
                language=lang_result.language,
                language_confidence=lang_result.confidence,
                is_multilingual=lang_result.is_multilingual,
                ocr_used=ocr_used,
                ocr_pages=ocr_pages,
                extractors_used=extractors_used,
                metadata=extracted_metadata,
                flags=flags,
                processing_duration_ms=processing_duration_ms,
                chroma_collection=chroma_collection,
                chunk_ids=[c.chunk_id for c in all_chunks],
            )

            logger.info(f"Pipeline completed for doc_id={doc_id} in {processing_duration_ms}ms")
            return {
                "doc_id": doc_id,
                "status": "success",
                "chunks": total_chunks,
                "duration_ms": processing_duration_ms,
            }

        except Exception as exc:
            logger.error(
                f"Pipeline failed for doc_id={doc_id}",
                extra={"error": str(exc)},
                exc_info=True
            )
            # Record failure in DB
            await self._record_failure(doc_id, str(exc))
            raise

    def _select_extractor(self, mime_type: str) -> str:
        """Map MIME type to extractor key."""
        if mime_type == "application/pdf":
            return "pdf"
        elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return "docx"
        elif mime_type in ("application/xml", "text/xml"):
            return "xml"
        elif mime_type in ("text/csv", "text/tab-separated-values", "text/plain"):
            return "csv"
        return "tika"

    def _calculate_confidence(
        self, result: ExtractionResult, ocr_used: bool, lang_conf: float
    ) -> float:
        """Calculate a 0.0 - 1.0 confidence score."""
        conf = 1.0
        
        if ocr_used:
            conf -= 0.15
            # Average OCR confidence of OCR'd pages
            ocr_confs = [p.ocr_confidence for p in result.pages if p.ocr_confidence is not None]
            if ocr_confs:
                avg_ocr_conf = sum(ocr_confs) / len(ocr_confs)
                if avg_ocr_conf < 0.8:
                    conf -= (0.8 - avg_ocr_conf)
        
        if lang_conf < 0.8:
            conf -= 0.1
            
        if result.extraction_warnings:
            conf -= (len(result.extraction_warnings) * 0.05)
            
        return max(0.0, round(conf, 3))

    async def _save_to_minio(self, path: str, data: dict) -> None:
        """Save JSON directly to MinIO using aiobotocore client."""
        bucket = "swastha-ai-processed-documents"
        json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        
        # Ensure bucket exists
        try:
            await self.minio.head_bucket(Bucket=bucket)
        except Exception:
            await self.minio.create_bucket(Bucket=bucket)
            
        await self.minio.put_object(
            Bucket=bucket,
            Key=path,
            Body=json_bytes,
            ContentType="application/json",
        )

    async def _update_db(
        self,
        doc_id: str,
        chunks: list[DocumentChunk],
        extraction_confidence: float,
        ocr_used: bool,
        ocr_pages: int,
        page_count: int,
        word_count: int,
        table_count: int,
        language: str,
        extractors_used: list[str],
        normalisation_changes: dict,
        flags: dict,
        duration_ms: int,
        chroma_collection: str,
    ) -> None:
        """Update PostgreSQL submissions and chunks tables."""
        from sqlalchemy import text
        
        async with self.db_session_factory() as session:
            # Insert log
            await session.execute(
                text("""
                    INSERT INTO document_processing_log 
                    (doc_id, pipeline_version, extraction_confidence, ocr_used, ocr_pages,
                     page_count, word_count, chunk_count, table_count, language,
                     extractors_used, normalisation_changes, flags, processing_duration_ms)
                    VALUES 
                    (:doc_id, :ver, :conf, :ocr, :ocr_p, :pc, :wc, :cc, :tc, :lang,
                     :ext, :norm, :flags, :dur)
                """),
                {
                    "doc_id": doc_id,
                    "ver": self.pipeline_version,
                    "conf": extraction_confidence,
                    "ocr": ocr_used,
                    "ocr_p": ocr_pages,
                    "pc": page_count,
                    "wc": word_count,
                    "cc": len(chunks),
                    "tc": table_count,
                    "lang": language,
                    "ext": extractors_used,
                    "norm": json.dumps(normalisation_changes),
                    "flags": json.dumps(flags),
                    "dur": duration_ms,
                }
            )
            
            # Update submission
            await session.execute(
                text("""
                    UPDATE submissions 
                    SET status = 'processed',
                        preprocessing_confidence = :conf,
                        chunk_count = :cc,
                        preprocessed_at = NOW()
                    WHERE doc_id = :doc_id
                """),
                {"conf": extraction_confidence, "cc": len(chunks), "doc_id": doc_id}
            )
            
            # Delete old chunks if reprocessing
            await session.execute(
                text("DELETE FROM document_chunks WHERE doc_id = :doc_id"),
                {"doc_id": doc_id}
            )
            
            # Insert new chunks
            if chunks:
                chunk_params = []
                for c in chunks:
                    chunk_params.append({
                        "doc_id": doc_id,
                        "chunk_id": c.chunk_id,
                        "chunk_index": c.chunk_index,
                        "total_chunks": c.total_chunks,
                        "section_title": c.section_title,
                        "page_range_start": c.page_range[0] if c.page_range else None,
                        "page_range_end": c.page_range[-1] if c.page_range else None,
                        "chunk_type": c.chunk_type,
                        "token_count": c.token_count,
                        "char_count": c.char_count,
                        "language": language,
                        "chroma_collection": chroma_collection,
                        "prev_chunk_id": c.prev_chunk_id,
                        "next_chunk_id": c.next_chunk_id,
                    })
                
                await session.execute(
                    text("""
                        INSERT INTO document_chunks
                        (doc_id, chunk_id, chunk_index, total_chunks, section_title,
                         page_range_start, page_range_end, chunk_type, token_count,
                         char_count, language, chroma_collection, prev_chunk_id, next_chunk_id)
                        VALUES
                        (:doc_id, :chunk_id, :chunk_index, :total_chunks, :section_title,
                         :page_range_start, :page_range_end, :chunk_type, :token_count,
                         :char_count, :language, :chroma_collection, :prev_chunk_id, :next_chunk_id)
                    """),
                    chunk_params
                )
                
            await session.commit()

    async def _record_failure(self, doc_id: str, error_msg: str) -> None:
        from sqlalchemy import text
        try:
            async with self.db_session_factory() as session:
                await session.execute(
                    text("""
                        INSERT INTO document_processing_log 
                        (doc_id, pipeline_version, error_detail)
                        VALUES (:doc_id, :ver, :err)
                    """),
                    {"doc_id": doc_id, "ver": self.pipeline_version, "err": error_msg}
                )
                # Note: we do NOT set status='failed' here. 
                # The consumer manages retries and sets 'failed' only after DLQ.
                await session.commit()
        except Exception:
            pass
