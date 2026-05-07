"""
Table Chunker — converts extracted tables into chunks.

Tables are treated specially:
- Each table becomes its own chunk.
- Large tables (>50 rows) are split into sub-tables of 25 rows each.
- The header row is preserved in every sub-table.
- A natural language preamble is added to improve LLM comprehension.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from app.preprocessing.chunker.semantic_chunker import DocumentChunk, _CHARS_PER_TOKEN_ESTIMATE
from app.preprocessing.extractors.base_extractor import ExtractedTable

_MAX_TABLE_ROWS = 50
_SPLIT_TABLE_ROWS = 25


class TableChunker:
    """Chunks structured tables for embedding and retrieval."""

    def chunk_tables(
        self,
        doc_id: str,
        tables: list[ExtractedTable],
        start_index: int = 0,
    ) -> list[DocumentChunk]:
        """
        Convert tables into DocumentChunks.
        Does NOT set total_chunks, prev/next links.
        """
        chunks: list[DocumentChunk] = []
        current_index = start_index

        for table_idx, table in enumerate(tables):
            # Assign table ID if not present
            if not table.table_id:
                table.table_id = f"{doc_id}-TABLE-{table_idx + 1}"

            if table.row_count > _MAX_TABLE_ROWS:
                # Split large table
                sub_chunks = self._split_large_table(doc_id, table, current_index)
                chunks.extend(sub_chunks)
                current_index += len(sub_chunks)
            else:
                # Single chunk
                chunk = self._create_table_chunk(doc_id, table, current_index, part=None, total_parts=None)
                chunks.append(chunk)
                current_index += 1

        return chunks

    def _split_large_table(
        self, doc_id: str, table: ExtractedTable, start_index: int
    ) -> list[DocumentChunk]:
        """Split a large table into smaller pieces, repeating the header."""
        chunks: list[DocumentChunk] = []
        total_parts = (table.row_count + _SPLIT_TABLE_ROWS - 1) // _SPLIT_TABLE_ROWS

        for i in range(total_parts):
            start_row = i * _SPLIT_TABLE_ROWS
            end_row = min((i + 1) * _SPLIT_TABLE_ROWS, table.row_count)

            sub_table = ExtractedTable(
                table_id=f"{table.table_id}-PART-{i + 1}",
                headers=table.headers,
                rows=table.rows[start_row:end_row],
                page_number=table.page_number,
                is_continuation=(i > 0 or table.is_continuation),
            )
            sub_table.markdown = sub_table.to_markdown()

            chunk = self._create_table_chunk(
                doc_id, sub_table, start_index + i, part=i + 1, total_parts=total_parts
            )
            chunks.append(chunk)

        return chunks

    def _create_table_chunk(
        self,
        doc_id: str,
        table: ExtractedTable,
        chunk_index: int,
        part: int | None,
        total_parts: int | None,
    ) -> DocumentChunk:
        """Construct the natural language preamble and the DocumentChunk."""
        cols = table.col_count
        rows = table.row_count

        preamble = f"The following table has {rows} rows and {cols} columns."
        if table.headers:
            preamble += f" The columns are: {', '.join(table.headers)}."
        if part is not None and total_parts is not None:
            preamble += f" This is part {part} of {total_parts} for this table."

        text = f"{preamble}\n\n{table.markdown}"

        return DocumentChunk(
            chunk_id=f"{doc_id}-{chunk_index:05d}",
            doc_id=doc_id,
            chunk_index=chunk_index,
            total_chunks=0,
            text=text,
            token_count=int(len(text) / _CHARS_PER_TOKEN_ESTIMATE),
            char_count=len(text),
            chunk_type="table",
            section_title=f"Table on page {table.page_number}",
            page_range=[table.page_number, table.page_number],
        )
