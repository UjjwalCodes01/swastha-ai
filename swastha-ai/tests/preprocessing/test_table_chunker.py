import pytest
from app.preprocessing.chunker.table_chunker import TableChunker
from app.preprocessing.extractors.base_extractor import ExtractedTable


@pytest.fixture
def chunker():
    return TableChunker()


def test_small_table_chunking(chunker):
    # Table with 5 rows
    table = ExtractedTable(
        headers=["Drug", "Dose"],
        rows=[["Aspirin", "50mg"], ["Paracetamol", "500mg"], ["Ibuprofen", "200mg"], ["Amoxicillin", "250mg"], ["Ciprofloxacin", "500mg"]],
        page_number=1,
    )
    table.markdown = table.to_markdown()

    chunks = chunker.chunk_tables(doc_id="DOC-123", tables=[table], start_index=10)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_id == "DOC-123-00010"
    assert chunk.chunk_type == "table"
    assert "The following table has 5 rows and 2 columns" in chunk.text
    assert "The columns are: Drug, Dose" in chunk.text
    assert "Aspirin" in chunk.text


def test_large_table_splitting(chunker):
    # Table with 60 rows (> 50 limit)
    rows = [[f"Drug{i}", f"{i}mg"] for i in range(60)]
    table = ExtractedTable(
        headers=["Drug", "Dose"],
        rows=rows,
        page_number=2,
        table_id="BIG-TABLE"
    )
    table.markdown = table.to_markdown()

    chunks = chunker.chunk_tables(doc_id="DOC-123", tables=[table], start_index=0)

    # 60 rows split by 25 -> 3 parts (25, 25, 10)
    assert len(chunks) == 3
    
    # Check part 1
    assert "part 1 of 3" in chunks[0].text
    assert "Drug0" in chunks[0].text
    assert "Drug24" in chunks[0].text
    assert "Drug25" not in chunks[0].text

    # Check part 2
    assert "part 2 of 3" in chunks[1].text
    assert "Drug25" in chunks[1].text
    assert "Drug49" in chunks[1].text
    
    # Check part 3
    assert "part 3 of 3" in chunks[2].text
    assert "Drug50" in chunks[2].text
    assert "Drug59" in chunks[2].text
