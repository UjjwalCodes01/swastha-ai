"""
Semantic text chunker for LLM retrieval.

Uses section-based splitting first (detecting headings), followed by character-based
splitting with overlap if a section is too large. Keeps adjacent small sections
merged to preserve context.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:  # pragma: no cover
    RecursiveCharacterTextSplitter = None  # type: ignore[assignment, misc]


@dataclass
class DocumentChunk:
    """A single piece of text or table ready for embedding."""
    chunk_id: str
    doc_id: str
    chunk_index: int
    total_chunks: int
    text: str
    token_count: int
    char_count: int
    chunk_type: str  # text, table, heading, form_field
    section_title: str | None = None
    page_range: list[int] | None = None
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "text": self.text,
            "token_count": self.token_count,
            "char_count": self.char_count,
            "chunk_type": self.chunk_type,
            "section_title": self.section_title,
            "page_range": self.page_range,
            "prev_chunk_id": self.prev_chunk_id,
            "next_chunk_id": self.next_chunk_id,
        }


# Match structural headings
_HEADING_PATTERNS = [
    # Markdown-style heading from our extractors
    re.compile(r"^(#{1,6})\s+(.+)$"),
    # Numbered sections: 1. or 1.1 or 1.1.1
    re.compile(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z].+)$"),
    # ALL CAPS line under 100 chars
    re.compile(r"^([A-Z\s0-9.,&-]{5,100})$"),
    # Underlined sections: line followed by "----" or "____"
    # Handled via multi-line regex in `_find_sections`
]

_MIN_SECTION_TOKENS = 200
_MAX_CHUNK_TOKENS = 1000
_CHARS_PER_TOKEN_ESTIMATE = 4.0  # rough estimate for English text


class SemanticChunker:
    """
    Chunks document text into semantically cohesive blocks.
    """

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 150) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        if RecursiveCharacterTextSplitter is not None:
            self._text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size * int(_CHARS_PER_TOKEN_ESTIMATE),
                chunk_overlap=self.chunk_overlap * int(_CHARS_PER_TOKEN_ESTIMATE),
                separators=["\n\n", "\n", ".", " ", ""],
            )
        else:
            self._text_splitter = None

    def chunk_document(
        self,
        doc_id: str,
        text: str,
        start_index: int = 0,
    ) -> list[DocumentChunk]:
        """
        Split text into a list of DocumentChunks.
        Does NOT set total_chunks, prev_chunk_id, or next_chunk_id —
        those must be set by the caller after assembling all chunk types.
        """
        if not text.strip():
            return []

        sections = self._split_into_sections(text)
        merged_sections = self._merge_small_sections(sections)

        chunks: list[DocumentChunk] = []
        current_index = start_index

        for section_title, section_text in merged_sections:
            if not section_text.strip():
                continue

            est_tokens = int(len(section_text) / _CHARS_PER_TOKEN_ESTIMATE)

            # If section is small enough, make it a single chunk
            if est_tokens <= _MAX_CHUNK_TOKENS or self._text_splitter is None:
                chunks.append(DocumentChunk(
                    chunk_id=f"{doc_id}-{current_index:05d}",
                    doc_id=doc_id,
                    chunk_index=current_index,
                    total_chunks=0,  # to be filled
                    text=section_text,
                    token_count=est_tokens,
                    char_count=len(section_text),
                    chunk_type="text",
                    section_title=section_title,
                ))
                current_index += 1
            else:
                # Section too large, use character splitter with overlap
                sub_texts = self._text_splitter.split_text(section_text)
                for sub_text in sub_texts:
                    if not sub_text.strip():
                        continue
                    chunks.append(DocumentChunk(
                        chunk_id=f"{doc_id}-{current_index:05d}",
                        doc_id=doc_id,
                        chunk_index=current_index,
                        total_chunks=0,
                        text=sub_text,
                        token_count=int(len(sub_text) / _CHARS_PER_TOKEN_ESTIMATE),
                        char_count=len(sub_text),
                        chunk_type="text",
                        section_title=section_title,
                    ))
                    current_index += 1

        return chunks

    def _split_into_sections(self, text: str) -> list[tuple[str | None, str]]:
        """
        Scan text for headings and split into (heading, body) pairs.
        """
        lines = text.split("\n")
        sections: list[tuple[str | None, str]] = []
        current_title: str | None = None
        current_lines: list[str] = []

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            is_heading = False

            if line:
                # 1. Check for underline heading (line followed by ----)
                if i + 1 < len(lines) and re.match(r"^[-_]{4,}$", lines[i + 1].strip()):
                    is_heading = True
                    # If we have accumulated text, save it as a section
                    if current_lines:
                        sections.append((current_title, "\n".join(current_lines)))
                        current_lines = []
                    current_title = line
                    current_lines.append(line)
                    i += 2  # skip the underline
                    continue

                # 2. Check regex patterns
                for pattern in _HEADING_PATTERNS:
                    match = pattern.match(line)
                    if match:
                        is_heading = True
                        if current_lines:
                            sections.append((current_title, "\n".join(current_lines)))
                            current_lines = []
                        # Use the extracted heading text (group 2 if exists, else the whole match)
                        if len(match.groups()) > 1:
                            current_title = match.group(2)
                        else:
                            # Trim '#' for markdown headings
                            current_title = line.lstrip("#").strip()
                        break

            current_lines.append(lines[i])
            i += 1

        if current_lines:
            sections.append((current_title, "\n".join(current_lines)))

        return sections

    def _merge_small_sections(
        self, sections: list[tuple[str | None, str]]
    ) -> list[tuple[str | None, str]]:
        """
        Merge adjacent small sections to avoid creating tiny chunks.
        """
        merged: list[tuple[str | None, str]] = []

        for title, body in sections:
            if not merged:
                merged.append((title, body))
                continue

            last_title, last_body = merged[-1]
            last_tokens = len(last_body) / _CHARS_PER_TOKEN_ESTIMATE

            if last_tokens < _MIN_SECTION_TOKENS:
                # Merge current into last
                combined = f"{last_body}\n\n{body}"
                merged[-1] = (last_title, combined)
            else:
                merged.append((title, body))

        return merged
