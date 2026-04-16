from app.services.parsing import parse_source_bytes
from app.services.chunking import build_chunks
from app.services.types import NormalizedDocument, NormalizedSegment


def test_chunk_builder_keeps_overlap() -> None:
    document = NormalizedDocument(
        text=" ".join(f"token{i}" for i in range(700)),
        segments=[
            NormalizedSegment(segment_type="content", segment_number=index, title="Doc", text=f"token{index}")
            for index in range(700)
        ],
        metadata={},
    )
    chunks = build_chunks(document, min_tokens=300, max_tokens=400, overlap_tokens=50)
    assert len(chunks) >= 2
    assert chunks[0].token_count >= 300
    assert chunks[0].metadata["dedup_hash"]


def test_parse_preserves_list_and_table_segments() -> None:
    raw = b"""# Lecture 1

- first concept
- second concept

| term | definition |
| cell | unit of life |
"""
    document = parse_source_bytes(raw, "lecture.md")
    structure_kinds = [segment.metadata["structure_kind"] for segment in document.segments]
    assert "list" in structure_kinds
    assert "table" in structure_kinds


def test_chunk_builder_keeps_table_and_list_boundaries() -> None:
    raw = b"""# Topic A

Intro paragraph explaining the concept with enough words to fill some of the chunk budget for the test case.

- item one
- item two
- item three

Paragraph after the list with more explanatory content to force multiple chunks in a structure-aware way.

| col1 | col2 |
| a | b |
| c | d |
"""
    document = parse_source_bytes(raw, "topic.md")
    chunks = build_chunks(document, min_tokens=8, max_tokens=20, overlap_tokens=4)

    list_chunk = next(chunk for chunk in chunks if "item one" in chunk.text)
    table_chunk = next(chunk for chunk in chunks if "| col1 | col2 |" in chunk.text)

    assert "- item one\n- item two\n- item three" in list_chunk.text
    assert "| col1 | col2 |\n| a | b |\n| c | d |" in table_chunk.text
    assert "list" in list_chunk.metadata["structure_kinds"]
    assert "table" in table_chunk.metadata["structure_kinds"]
    assert list_chunk.metadata["heading_path"] == ["Topic A"]
    assert table_chunk.metadata["heading_path"] == ["Topic A"]
